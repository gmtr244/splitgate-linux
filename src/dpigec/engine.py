# Copyright (C) 2026 Project Gamers
# SPDX-License-Identifier: GPL-3.0-or-later
"""Motor: DNS vekili + (şeffaf | SOCKS) vekil + güvenlik duvarı kurallarını yönetir."""
from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
import signal
import sys
import time
from typing import Any, Dict, Optional

from . import __version__, firewall, netwatch
from .checker import CHECKING, IDLE, NOT_BLOCKED, WORKING, Checker
from .config import LOG_FILE, PRESETS, RUN_DIR, STATUS_FILE, ConfigError, effective_strategy
from .dnsserver import DnsProxy
from .doh import DohClient, DohResolver, SystemResolver, providers_from_config
from .relay import Relay
from .socks import ProxyServer
from .stats import Stats
from .status import remove_status, write_status
from .transparent import TransparentServer

log = logging.getLogger("dpigec")

# Kural koruyucusu sınırları (pil/olay döngüsü güvenliği)
REAPPLY_MAX = 5           # REAPPLY_WINDOW içinde en çok bu kadar yeniden kurma
REAPPLY_WINDOW = 60.0
REAPPLY_BACKOFF = 60
MONITOR_FAST_EXIT = 5.0   # bundan kısa yaşayan monitor "hemen kapandı" sayılır
MONITOR_MAX_FAST_EXITS = 3


def setup_logging(level: str, logfile: Optional[str] = LOG_FILE, console: bool = True) -> None:
    root = logging.getLogger("dpigec")
    root.handlers.clear()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S")
    if console:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(fmt)
        root.addHandler(h)
    if logfile:
        try:
            os.makedirs(os.path.dirname(logfile), exist_ok=True)
            fh = logging.handlers.RotatingFileHandler(logfile, maxBytes=512 * 1024, backupCount=1,
                                                      encoding="utf-8")
            fh.setFormatter(fmt)
            root.addHandler(fh)
            try:
                os.chmod(logfile, 0o644)
            except OSError:
                pass
        except OSError:
            pass


class Engine:
    def __init__(self, cfg: Dict[str, Any], status_path: str = STATUS_FILE,
                 manage_firewall: Optional[bool] = None, service: bool = False) -> None:
        self.cfg = cfg
        # service=True (systemd unit): çıkışta kuralları motor KALDIRMAZ; unit'in ExecStopPost=+ cleanup'ı
        # önce resolved'un DoT'unu geri açar, sonra kuralları kaldırır. Böylece hiçbir çıkış yolunda
        # "DoT kapalı + yönlendirme yok" (düz DNS sızıntısı) anı oluşmaz; arada DNS yalnızca başarısız olur.
        self.service = service
        self.status_path = status_path
        self.mode = cfg["mode"]
        self.manage_firewall = (self.mode == "transparent") if manage_firewall is None else manage_firewall
        self.stats = Stats()
        self.state = "starting"
        self._stop = asyncio.Event()
        self.doh: Optional[DohClient] = None
        self.dns: Optional[DnsProxy] = None
        self.transparent: Optional[TransparentServer] = None
        self.proxy: Optional[ProxyServer] = None
        self._fw_active = False
        self.relay: Optional[Relay] = None
        self.resolver = None
        self.checker: Optional[Checker] = None
        self.network: Optional[netwatch.NetId] = None
        self.active_preset = cfg["strategy"]["preset"]
        self.check = IDLE
        self.check_detail = ""
        self.verdict = ""
        self.check_results: list = []
        self._check_task: Optional[asyncio.Task] = None
        self._check_gen = 0
        self._had_network = False
        self._reapplies: list = []          # son yeniden kurma zamanları (oran sınırı)
        self._guard_paused_until = 0.0

    # ---- yaşam döngüsü -----------------------------------------------------
    async def start(self) -> None:
        cfg = self.cfg
        dns_cfg = cfg["dns"]
        self.doh = DohClient(providers_from_config(dns_cfg), cfg["fwmark"], dns_cfg["timeout"],
                             cfg["ipv6"], self.stats)
        if dns_cfg["enabled"]:
            resolver = DohResolver(self.doh, cfg["ipv6"], dns_cfg["cache_seconds"])
        else:
            resolver = SystemResolver(cfg["ipv6"])
        relay = Relay(cfg, self.stats, resolver)
        self.relay, self.resolver = relay, resolver
        self.checker = Checker(cfg)

        if self.mode == "transparent":
            if os.geteuid() != 0 and self.manage_firewall:
                raise PermissionError("transparent mode requires root privileges")
            self.transparent = TransparentServer(relay, cfg["listen_port"])
            await self.transparent.start(cfg["gateway"], cfg["ipv6"])
            if dns_cfg["enabled"]:
                self.dns = DnsProxy(self.doh, self.stats, dns_cfg["cache_seconds"])
                await self.dns.start(cfg["dns_port"], cfg["gateway"], cfg["ipv6"])
            if self.manage_firewall:
                firewall.apply(cfg)
                self._fw_active = True
        else:
            self.proxy = ProxyServer(relay)
            await self.proxy.start(cfg["socks_port"], cfg["gateway"], cfg["ipv6"])

        self.state = "running"
        st = effective_strategy(cfg)
        log.info("SplitGate %s running (mode=%s, strategy=%s %s)", __version__, self.mode,
                 cfg["strategy"]["preset"], ",".join(st["positions"]))

    async def stop(self) -> None:
        if self.state == "stopped":
            return
        self.state = "stopping"
        self._publish()
        if self._fw_active:
            if self.service:
                log.info("leaving the nftables rules to the service cleanup (ExecStopPost)")
            else:
                firewall.remove()
            self._fw_active = False
        for srv in (self.transparent, self.proxy, self.dns):
            if srv is not None:
                try:
                    await srv.stop()
                except Exception:  # noqa: BLE001
                    log.exception("shutdown error")
        if self.doh is not None:
            self.doh.close()
        self.state = "stopped"
        remove_status(self.status_path)
        log.info("stopped")

    def request_stop(self) -> None:
        self._stop.set()

    # ---- ağ izleme ve gerçek kontrol ----------------------------------------
    def apply_preset(self, key: str) -> None:
        if key not in PRESETS or self.relay is None:
            return
        st = dict(effective_strategy(self.cfg))
        p = PRESETS[key]
        st.update(positions=list(p["positions"]), delay_ms=p["delay_ms"], oob=p["oob"],
                  tlsrec=p["tlsrec"], disorder=p["disorder"])
        self.relay.set_strategy(st)
        if key != self.active_preset:
            log.info("method: %s (works on %s)", p["label"], self.network.label if self.network else "?")
        self.active_preset = key

    def _on_network(self, net: Optional[netwatch.NetId]) -> None:
        old = self.network
        if netwatch.same(net, old):
            if net is not None and old is not None and net.mac and not old.mac:
                self.network = net  # ARP kaydı geç geldi: aynı ağ, yalnızca MAC öğrenildi
            return
        self.network = net
        self.check_results = []
        self._check_gen += 1
        if self._check_task is not None:
            self._check_task.cancel()
            self._check_task = None
        # İlk ağ hariç her geçişte (ağ yok -> ağ var dahil) bağlantılar ve önbellekler sıfırlanır.
        if old is not None or self._had_network:
            n = self.relay.reset_connections() if self.relay else 0
            if self.doh is not None:
                self.doh.close()
            if hasattr(self.resolver, "flush"):
                self.resolver.flush()
            if self.dns is not None:
                self.dns.flush()
            log.info("network changed: %s -> %s, reset %d connections",
                     old.label if old else "none", net.label if net else "none", n)
        if net is not None:
            self._had_network = True
        if net is None:
            self.check, self.check_detail, self.verdict = IDLE, "", ""
            return
        self.request_check()

    def request_check(self) -> None:
        if self.checker is None or self.network is None or self.state != "running":
            return
        if self._check_task is not None:
            self._check_task.cancel()
        self._check_gen += 1
        self.check, self.check_detail = CHECKING, ""
        self._check_task = asyncio.ensure_future(self._run_check(self.network, self._check_gen))

    async def _run_check(self, net: netwatch.NetId, gen: int) -> None:
        await asyncio.sleep(1.5)  # yeni ağ yerleşsin
        # ARP kaydı bu arada oluşmuş olabilir: yöntem, ağ geçidinin MAC'iyle birlikte kaydedilsin/aransın.
        fresh = netwatch.current()
        if fresh is not None and netwatch.same(fresh, net) and fresh.mac and not net.mac:
            net = fresh
            if gen == self._check_gen:
                self.network = fresh
        log.info("checking %s on %s", self.active_preset, net.label)
        try:
            res = await self.checker.verify(net, self.active_preset, self._apply_if_current(gen),
                                            lambda: gen == self._check_gen and netwatch.same(netwatch.current(), net))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            if gen == self._check_gen:
                log.warning("check failed: %s", type(e).__name__)
                self.check = IDLE
            return
        if gen != self._check_gen:
            return
        self.check, self.check_detail, self.verdict = res["check"], res["detail"], res["verdict"]
        self.check_results = res["results"]
        label = PRESETS[self.active_preset]["label"] if self.active_preset in PRESETS else self.active_preset
        if self.check == WORKING:
            log.info("check: %s opens %s sites on %s", label, self.check_detail, net.label)
        elif self.check == NOT_BLOCKED:
            log.info("check: the test sites are not blocked on %s", net.label)
        else:
            log.warning("check: NOT working on %s (%s)", net.label, self.verdict)

    def _apply_if_current(self, gen: int):
        def apply(key: str) -> None:
            if gen == self._check_gen:
                self.apply_preset(key)
        return apply

    async def _net_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._on_network(netwatch.current())
            except Exception:  # noqa: BLE001
                log.exception("network watch error")
            try:
                await asyncio.wait_for(self._stop.wait(), 3.0)
            except asyncio.TimeoutError:
                pass

    def _publish(self) -> None:
        write_status({
            "state": self.state,
            "version": __version__,
            "mode": self.mode,
            "preset": self.cfg["strategy"]["preset"],
            "active_preset": self.active_preset,
            "network": self.network.label if self.network else None,
            "check": self.check,
            "check_detail": self.check_detail,
            "verdict": self.verdict,
            "check_results": self.check_results,
            "firewall": self._fw_active,
            "doh": self.doh.health() if self.doh else [],
            "stats": self.stats.as_dict(),
        }, self.status_path)

    async def _guard_firewall(self) -> None:
        """Kurallar dışarıdan silindiyse/bozulduysa (ör. `systemctl restart nftables` -> flush ruleset) geri kur.

        Servis çalışırken resolved'un DoT'u duraklatılmış olabilir; kurallar yoksa düz DNS dışarı çıkar.
        """
        if not (self._fw_active and self.manage_firewall) or self.state != "running":
            return
        loop = asyncio.get_running_loop()
        if await loop.run_in_executor(None, firewall.is_intact, self.cfg):
            return
        # is_intact iş parçacığında beklerken stop() başlamış olabilir: kapanırken kural geri kurulmamalı.
        if not (self._fw_active and self.state == "running" and not self._stop.is_set()):
            return
        now = time.monotonic()
        if now < self._guard_paused_until:
            return
        # Oran sınırı: kurduktan sonra bile "bozuk" görünüyorsa (ör. farklı nft çıktı biçimi) sonsuz
        # apply -> delete olayı -> apply döngüsü pil ve olay döngüsü tüketmesin.
        self._reapplies = [t for t in self._reapplies if now - t < REAPPLY_WINDOW]
        if len(self._reapplies) >= REAPPLY_MAX:
            self._guard_paused_until = now + REAPPLY_BACKOFF
            log.error("nftables rules keep disappearing or do not match; pausing the rule guard for %d s",
                      REAPPLY_BACKOFF)
            self.stats.error("nftables rules keep disappearing")
            return
        self._reapplies.append(now)
        log.warning("nftables rules disappeared (ruleset flushed?), re-applying")
        try:
            # Eşzamanlı (olay döngüsünde): iş parçacığı iptal edilemez; stop() ile yarışıp kapanıştan
            # sonra kuralları geri kurmasın. nft -f kısa sürer ve bu yol yalnızca dış bir flush'ta çalışır.
            firewall.apply(self.cfg)
        except firewall.FirewallError as e:
            log.error("could not re-apply nftables rules: %s", e)
            self.stats.error(str(e))

    async def _guard_safe(self) -> None:
        try:
            await self._guard_firewall()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("firewall guard error")

    async def _fw_watch(self, safety_seconds: float = 60.0, fallback_seconds: float = 5.0) -> None:
        """Kural koruyucusu, olay tabanlı: `nft monitor` çekirdekten silme/flush olaylarını bildirir.

        Her saniye nft çalıştırmak (yoklama) pil tüketir; bunun yerine yalnızca bizim tablomuzu etkileyen
        bir "delete"/"flush" olayında kontrol edilir. Ayrıca seyrek bir güvenlik kontrolü yapılır;
        monitor açılamazsa yoklamaya (fallback_seconds) düşülür.
        """
        if not (self.manage_firewall and self._fw_active):
            return
        fast_exits = 0
        while not self._stop.is_set():
            proc = None
            started = time.monotonic()
            if fast_exits < MONITOR_MAX_FAST_EXITS:
                try:
                    proc = await asyncio.create_subprocess_exec(
                        firewall._nft(), "monitor", stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE, stdin=asyncio.subprocess.DEVNULL)
                except (OSError, firewall.FirewallError) as e:
                    log.warning("nft monitor unavailable (%s); checking the rules every %d s instead",
                                e, fallback_seconds)
                    fast_exits = MONITOR_MAX_FAST_EXITS
            try:
                if proc is not None:
                    # monitor'ün netlink aboneliği oturana kadar kısa bekle; sonra arada kaçmış olayları kapsayan
                    # ilk kontrol (abonelikten sonra gelen her olayı monitor zaten bildirir).
                    await asyncio.sleep(0.2)
                await self._guard_safe()
                if proc is None or proc.stdout is None:
                    await asyncio.wait_for(self._stop.wait(), fallback_seconds)
                    continue
                while not self._stop.is_set():
                    try:
                        line = await asyncio.wait_for(proc.stdout.readline(), safety_seconds)
                    except asyncio.TimeoutError:
                        await self._guard_safe()
                        continue
                    if not line:
                        # monitor kapandı. Hemen kapanıyorsa (yetki/çekirdek sorunu) saniyede bir yeniden
                        # başlatmak pil tüketir: birkaç denemeden sonra seyrek yoklamaya geç.
                        err = b""
                        if proc.stderr is not None:
                            try:
                                err = await asyncio.wait_for(proc.stderr.read(400), 1)
                            except (asyncio.TimeoutError, OSError):
                                pass
                        if time.monotonic() - started < MONITOR_FAST_EXIT:
                            fast_exits += 1
                        else:
                            fast_exits = 0
                        log.warning("nft monitor exited%s%s", ": " if err else "",
                                    err.decode("utf-8", "replace").strip()[:200])
                        if fast_exits >= MONITOR_MAX_FAST_EXITS:
                            log.warning("nft monitor keeps exiting; checking the rules every %d s instead",
                                        fallback_seconds)
                        break
                    words = line.decode("utf-8", "replace").split()
                    if words and words[0] in ("delete", "flush") and firewall.TABLE in words:
                        # Kendi apply'ımız da delete+add üretir (tek işlem); kısa bekleyip içeriği denetle.
                        await asyncio.sleep(0.05)
                        await self._guard_safe()
            except asyncio.TimeoutError:
                continue
            finally:
                if proc is not None and proc.returncode is None:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    try:
                        await asyncio.wait_for(proc.wait(), 2)
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        pass
            if not self._stop.is_set():
                try:
                    await asyncio.wait_for(self._stop.wait(), 1.0)
                except asyncio.TimeoutError:
                    pass

    async def _status_loop(self) -> None:
        while not self._stop.is_set():
            self._publish()
            try:
                await asyncio.wait_for(self._stop.wait(), 1.0)
            except asyncio.TimeoutError:
                pass

    async def run(self) -> int:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.request_stop)
        loop.add_signal_handler(signal.SIGUSR1, self.request_check)
        try:
            await self.start()
        except (OSError, PermissionError, firewall.FirewallError, ConfigError) as e:
            log.error("failed to start: %s", e)
            self.stats.error(str(e))
            await self.stop()
            return 1
        status_task = asyncio.ensure_future(self._status_loop())
        net_task = asyncio.ensure_future(self._net_loop())
        fw_task = asyncio.ensure_future(self._fw_watch())
        try:
            await self._stop.wait()
        finally:
            net_task.cancel()
            status_task.cancel()
            fw_task.cancel()  # kural koruyucusu stop() ile yarışmasın (apply eşzamanlı, iş parçacığı yok)
            if self._check_task is not None:
                self._check_task.cancel()
            for t in (status_task, fw_task):
                try:
                    await t
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            await self.stop()
        return 0


def run_engine(cfg: Dict[str, Any], console: bool = True, logfile: Optional[str] = LOG_FILE,
               status_path: str = STATUS_FILE, service: bool = False) -> int:
    setup_logging(cfg["log_level"], logfile, console)
    if cfg["log_domains"] and cfg["log_level"] == "debug":
        log.warning("log_domains is on: visited domain names will be written to the log")
    if status_path == STATUS_FILE:
        os.makedirs(RUN_DIR, exist_ok=True)
    engine = Engine(cfg, status_path=status_path, service=service)
    return asyncio.run(engine.run())
