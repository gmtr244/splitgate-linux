"""DNS yönlendirmesi uçtan uca: gerçek nftables REDIRECT + conntrack, UDP cevabının kaynağı.

Hata (1.1.7 öncesi): gateway modunda UDP DNS soketi 0.0.0.0'a bağlıydı. Bu bilgisayarın kendi
sorgusu (ör. resolved -> 1.0.0.1:53) 127.0.0.1:<dns_port>'a yönlendirilir; joker soketten giden
cevabın kaynağını çekirdek 127.0.0.1 değil arayüz adresi seçer, conntrack cevabı tanımaz ve
1.0.0.1:53'e geri çevirmez. resolved cevabı "yabancı kaynak" diye atar -> DNS çalışmaz.

Test kendi kullanıcı + ağ ad alanında (`unshare -rn`) çalışır; root gerekmez, ana sisteme dokunmaz.
unshare, nft veya ip yoksa ya da kullanıcı ad alanı kapalıysa atlanır.
"""
import asyncio
import os
import shutil
import socket
import struct
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src")
sys.path.insert(0, SRC)

UPSTREAM4 = "1.0.0.1"
UPSTREAM6 = "2606:4700:4700::1001"


def _query(name: str, qid: int) -> bytes:
    hdr = struct.pack("!HHHHHH", qid, 0x0100, 1, 0, 0, 1)
    q = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0" + struct.pack("!HH", 1, 1)
    opt = b"\0" + struct.pack("!HHIH", 41, 1232, 0, 0)  # EDNS0, resolved gibi
    return hdr + q + opt


class FakeDoh:
    """DoH yerine: sorguyu cevap bayrağıyla geri döndürür (ağa çıkmaz)."""

    async def query(self, q: bytes) -> bytes:
        flags = struct.unpack_from("!H", q, 2)[0] | 0x8000 | 0x0080
        return q[:2] + struct.pack("!H", flags) + q[4:]


def _sh(*cmd):
    subprocess.run(cmd, check=True, capture_output=True)


async def _inner(gateway: bool) -> None:
    from dpigec import config, firewall
    from dpigec.dnsserver import DnsProxy
    from dpigec.stats import Stats

    _sh("ip", "link", "set", "lo", "up")
    _sh("ip", "link", "add", "d0", "type", "dummy")
    _sh("ip", "addr", "add", "10.77.0.2/24", "dev", "d0")
    _sh("ip", "-6", "addr", "add", "fd77::2/64", "dev", "d0", "nodad")
    _sh("ip", "link", "set", "d0", "up")
    _sh("ip", "route", "add", "default", "via", "10.77.0.1")
    _sh("ip", "-6", "route", "add", "default", "via", "fd77::1")
    cfg = config.normalize({"gateway": gateway, "quic_block": False})
    proxy = DnsProxy(FakeDoh(), Stats(), cache_seconds=0)
    await proxy.start(cfg["dns_port"], gateway, True)
    firewall.apply(cfg)
    loop = asyncio.get_running_loop()
    try:
        for fam, server in ((socket.AF_INET, UPSTREAM4), (socket.AF_INET6, UPSTREAM6)):
            s = socket.socket(fam, socket.SOCK_DGRAM)
            s.setblocking(False)
            qid = 0x4242 + fam
            await loop.sock_sendto(s, _query("example.com", qid), (server, 53))
            data, src = await asyncio.wait_for(loop.sock_recvfrom(s, 4096), 3)
            s.close()
            assert struct.unpack_from("!H", data, 0)[0] == qid, "wrong id"
            # Asıl kontrol: cevap sorgunun gittiği sunucudan geliyor gibi görünmeli (resolved bunu şart koşar)
            assert (src[0], src[1]) == (server, 53), f"reply came from {src[:2]}, expected {(server, 53)}"
    finally:
        firewall.remove()
        await proxy.stop()
    print("DNS-NETNS-OK")


def _can_unshare() -> bool:
    if not (shutil.which("unshare") and shutil.which("nft") and shutil.which("ip")):
        return False
    r = subprocess.run(["unshare", "-rn", "true"], capture_output=True)
    return r.returncode == 0


@unittest.skipUnless(sys.platform.startswith("linux") and _can_unshare(),
                     "unshare -rn / nft / ip gerekli (kullanıcı ad alanı)")
class DnsRedirectNetnsTests(unittest.TestCase):
    def _run(self, gateway: bool):
        code = ("import asyncio, sys; sys.path.insert(0, %r); sys.path.insert(0, %r); "
                "import test_dns_redirect_netns as t; asyncio.run(t._inner(%r))") % (SRC, HERE, gateway)
        r = subprocess.run(["unshare", "-rn", sys.executable, "-I", "-c", code],
                           capture_output=True, text=True, timeout=60)
        self.assertIn("DNS-NETNS-OK", r.stdout, r.stdout + r.stderr)

    def test_udp_reply_source_matches_redirected_server_gateway_mode(self):
        self._run(True)

    def test_udp_reply_source_matches_redirected_server_local_mode(self):
        self._run(False)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--inner":
        asyncio.run(_inner(sys.argv[2] == "1"))
    else:
        unittest.main()
