"""Yerel DNS vekili: UDP/TCP 53 sorgularını DoH'a aktarır (önbellekli)."""
from __future__ import annotations

import asyncio
import logging
import socket
import struct
import time
from typing import Dict, List, Optional, Tuple

from . import dnswire
from .doh import DohClient, DohError
from .sockutil import listen_socket
from .stats import Stats

log = logging.getLogger("dpigec.dns")


class DnsProxy:
    def __init__(self, doh: DohClient, stats: Stats, cache_seconds: int = 300) -> None:
        self.doh = doh
        self.stats = stats
        self.cache_seconds = cache_seconds
        self._cache: Dict[bytes, Tuple[float, bytes]] = {}
        self._servers: List[asyncio.AbstractServer] = []
        self._transports: List["_UdpEndpoint"] = []

    def flush(self) -> None:
        """Ağ değişince: önceki ağa özgü olabilecek cevaplar (CDN adresleri) atılır."""
        self._cache.clear()

    # ---- çekirdek ----------------------------------------------------------
    async def handle(self, query: bytes) -> bytes:
        """Tek bir DNS sorgusunu yanıtlar (hata olursa SERVFAIL)."""
        self.stats.dns_queries += 1
        try:
            if not dnswire.is_query(query):
                raise dnswire.DNSError("not a query")
            key = dnswire.question_key(query)
        except dnswire.DNSError:
            self.stats.dns_failed += 1
            return b""
        qid = struct.unpack_from("!H", query, 0)[0]
        hit = self._cache.get(key)
        if hit and hit[0] > time.monotonic():
            self.stats.dns_cached += 1
            return dnswire.set_id(hit[1], qid)
        try:
            resp = await self.doh.query(query)
        except (DohError, OSError, asyncio.TimeoutError) as e:
            self.stats.dns_failed += 1
            self.stats.error(f"DoH: {e}")
            return dnswire.servfail(query)
        resp = dnswire.set_id(resp, qid)
        self._store(key, resp)
        return resp

    def _store(self, key: bytes, resp: bytes) -> None:
        if not self.cache_seconds:
            return
        try:
            rcode, addrs, ttl = dnswire.parse_answers(resp)
        except dnswire.DNSError:
            return
        if rcode != 0 or not addrs:
            return
        if len(self._cache) > 2048:
            for k in list(self._cache)[:512]:
                self._cache.pop(k, None)
        self._cache[key] = (time.monotonic() + min(max(ttl, 10), self.cache_seconds), resp)

    # ---- UDP ---------------------------------------------------------------
    async def _udp_answer(self, ep: "_UdpEndpoint", data: bytes, addr, pktinfo) -> None:
        resp = await self.handle(data)
        if not resp:
            return
        if len(resp) > 4096:  # UDP için çok büyükse TC bitiyle kısalt
            try:
                resp = dnswire.truncate(resp)
            except dnswire.DNSError:
                return
        ep.send(resp, addr, pktinfo)

    # ---- TCP ---------------------------------------------------------------
    async def _tcp_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                hdr = await asyncio.wait_for(reader.readexactly(2), 30)
                n = struct.unpack("!H", hdr)[0]
                if n < 12:
                    break
                query = await asyncio.wait_for(reader.readexactly(n), 10)
                resp = await self.handle(query)
                if not resp:
                    break
                writer.write(struct.pack("!H", len(resp)) + resp)
                await writer.drain()
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError, OSError):
            pass
        finally:
            writer.close()

    # ---- yaşam döngüsü -----------------------------------------------------
    async def start(self, port: int, gateway: bool, ipv6: bool) -> None:
        loop = asyncio.get_running_loop()
        hosts = [(socket.AF_INET, "0.0.0.0" if gateway else "127.0.0.1")]
        if ipv6:
            hosts.append((socket.AF_INET6, "::" if gateway else "::1"))
        for fam, host in hosts:
            try:
                tcp = listen_socket(fam, host, port)
                self._servers.append(await asyncio.start_server(self._tcp_client, sock=tcp))
                udp = socket.socket(fam, socket.SOCK_DGRAM)
                udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                if fam == socket.AF_INET6:
                    udp.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                udp.bind((host, port))
                udp.setblocking(False)
                self._transports.append(_UdpEndpoint(self, udp, loop))
            except OSError as e:
                if fam == socket.AF_INET6:
                    log.warning("could not open the IPv6 DNS listener: %s", e)
                    continue
                raise
        log.info("DNS proxy listening: port %d", port)

    async def stop(self) -> None:
        for s in self._servers:
            s.close()
        for t in self._transports:
            t.close()
        self._servers.clear()
        self._transports.clear()


class _UdpEndpoint:
    """UDP DNS dinleyicisi: cevabı, sorgunun GELDİĞİ adresten gönderir (IP_PKTINFO / IPV6_PKTINFO).

    nftables REDIRECT ile gelen bir sorguda (ör. resolved -> 1.0.0.1:53 -> 127.0.0.1:15353) cevap
    conntrack'in beklediği adresten (127.0.0.1) çıkmalı ki 1.0.0.1:53'e geri çevrilsin. Joker (0.0.0.0)
    bir soketten sendto() kaynağı rotaya göre seçer (ör. 192.168.1.13); conntrack cevabı tanımaz ve
    istemci (resolved) cevabı yabancı kaynaktan geldiği için reddeder.
    """

    MAX_BATCH = 64
    MAX_INFLIGHT = 256  # aynı anda cevaplanan UDP sorgusu; fazlası düşürülür (istemci yeniden dener)

    def __init__(self, proxy: DnsProxy, sock: socket.socket, loop: asyncio.AbstractEventLoop) -> None:
        self.proxy, self.sock, self.loop = proxy, sock, loop
        self.v6 = sock.family == socket.AF_INET6
        if self.v6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_RECVPKTINFO, 1)
        else:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_PKTINFO, 1)
        self._anc = socket.CMSG_SPACE(64)
        self._closed = False
        self._tasks: set = set()  # güçlü referans: çöp toplayıcı yarıda kesmesin
        loop.add_reader(sock.fileno(), self._readable)

    def _pktinfo(self, anc) -> Optional[bytes]:
        """Gelen paketin hedef adresinden, cevabın kaynağını belirleyen pktinfo verisi."""
        for level, typ, data in anc:
            if not self.v6 and level == socket.IPPROTO_IP and typ == socket.IP_PKTINFO and len(data) >= 12:
                # struct in_pktinfo { int ifindex; in_addr spec_dst; in_addr addr; } -> spec_dst = gelen hedef
                return struct.pack("=i4s4s", 0, data[8:12], b"\0" * 4)
            if self.v6 and level == socket.IPPROTO_IPV6 and typ == socket.IPV6_PKTINFO and len(data) >= 20:
                # struct in6_pktinfo { in6_addr addr; unsigned ifindex; } (bağlantı yerel adres için ifindex kalır)
                return bytes(data[:20])
        return None

    def _readable(self) -> None:
        for _ in range(self.MAX_BATCH):
            try:
                data, anc, _flags, addr = self.sock.recvmsg(4096, self._anc)
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                return
            if not data:
                continue
            if len(self._tasks) >= self.MAX_INFLIGHT:
                self.proxy.stats.dns_failed += 1
                continue  # sel: DoH'a sınırsız iş yığılmasın
            task = asyncio.ensure_future(self.proxy._udp_answer(self, data, addr, self._pktinfo(anc)))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    def send(self, resp: bytes, addr, pktinfo: Optional[bytes]) -> None:
        if self._closed:
            return
        level, typ = ((socket.IPPROTO_IPV6, socket.IPV6_PKTINFO) if self.v6
                      else (socket.IPPROTO_IP, socket.IP_PKTINFO))
        anc = [(level, typ, pktinfo)] if pktinfo else []
        try:
            self.sock.sendmsg([resp], anc, 0, addr)
        except OSError:
            pass  # UDP: kayıp cevap istemcide yeniden denenir

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.loop.remove_reader(self.sock.fileno())
        except (ValueError, OSError):
            pass
        self.sock.close()
