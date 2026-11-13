# Copyright (C) 2026 Project Gamers
# SPDX-License-Identifier: GPL-3.0-or-later
"""Gerçek kontrol: seçili yöntem bu ağda engelli siteleri gerçekten açıyor mu?

Sonuç "working" yalnızca yöntem, bölmesiz bağlantının açamadığı en az bir siteyi açtıysa olur.
Açmıyorsa (ve otomatik seçim açıksa) önce bu ağ için kayıtlı yöntem, sonra tüm yöntemler denenir;
çalışan uygulanır ve ağ için kaydedilir.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import tempfile
import threading
from typing import Any, Awaitable, Callable, Dict, List, Optional

from . import probe
from .config import PRESETS, effective_strategy
from .netwatch import NetId

log = logging.getLogger("dpigec.check")

STATE_DIR = "/var/lib/dpigec"
NETWORKS_FILE = STATE_DIR + "/networks.json"
MAX_NETWORKS = 256

CHECKING = "checking"
WORKING = "working"
NOT_BLOCKED = "not_blocked"
FAILED = "failed"
IDLE = "idle"

ProbeFn = Callable[..., Awaitable[List[Dict[str, Any]]]]
SysDnsFn = Callable[[List[str]], Awaitable[bool]]

SYSTEM_DNS_TIMEOUT = 8.0
# Askıda kalmış (çözücü yanıt vermiyor) getaddrinfo iş parçacıkları üst üste binmesin.
_SYSDNS_SLOTS = threading.BoundedSemaphore(3)


def _getaddrinfo_daemon(loop: asyncio.AbstractEventLoop, host: str) -> "asyncio.Future":
    """getaddrinfo'yu bir daemon iş parçacığında çalıştırır.

    loop.getaddrinfo varsayılan executor'ı kullanır; asyncio.run çıkarken oradaki iş parçacıklarını
    bekler. Sistem DNS'i yanıt vermiyorsa (tam da bu testin aradığı durum) servis SIGTERM'den sonra
    takılı kalır ve systemd onu SIGKILL ile öldürür. Daemon iş parçacığı çıkışı engellemez.
    """
    fut = loop.create_future()
    if not _SYSDNS_SLOTS.acquire(blocking=False):
        fut.set_exception(OSError("previous system DNS lookups are still hanging"))
        return fut

    def work() -> None:
        try:
            res, err = socket.getaddrinfo(host, 443, socket.AF_UNSPEC, socket.SOCK_STREAM), None
        except BaseException as e:  # noqa: BLE001
            res, err = None, e
        finally:
            _SYSDNS_SLOTS.release()

        def deliver() -> None:
            if fut.done():  # zaman aşımında iptal edildi
                return
            if err is not None:
                fut.set_exception(err)
            else:
                fut.set_result(res)

        try:
            loop.call_soon_threadsafe(deliver)
        except RuntimeError:  # döngü kapanmış
            pass

    threading.Thread(target=work, name="dpigec-sysdns", daemon=True).start()
    return fut


async def system_dns_ok(domains: List[str], timeout: float = SYSTEM_DNS_TIMEOUT) -> bool:
    """Uygulamaların kullandığı sistem çözücüsü (getaddrinfo -> resolved) en az bir adı çözüyor mu?

    Test, motorun kendi DoH'unu değil uygulamaların gerçek yolunu dener: yöntem çalışsa da
    sistem DNS'i ölü ise (ör. katı DoT + DoT bilmeyen router) hiçbir uygulama bağlanamaz.
    """
    loop = asyncio.get_running_loop()
    for d in domains[:3]:
        try:
            infos = await asyncio.wait_for(_getaddrinfo_daemon(loop, d), timeout)
        except (OSError, asyncio.TimeoutError, UnicodeError):
            continue
        if infos:
            return True
    return False


def load_networks(path: str = NETWORKS_FILE) -> Dict[str, Dict[str, str]]:
    # Bozuk/kötü niyetli dosya servisi çökertmemeli: her hata "kayıt yok" sayılır.
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:  # noqa: BLE001  (OSError, ValueError, RecursionError, ...)
        return {}
    out: Dict[str, Dict[str, str]] = {}
    if isinstance(data, dict):
        for k, v in list(data.items())[:MAX_NETWORKS]:
            if not (isinstance(k, str) and len(k) <= 160 and isinstance(v, dict)):
                continue
            preset = v.get("preset")
            if isinstance(preset, str) and preset in PRESETS:
                out[k] = {"preset": preset, "label": str(v.get("label", ""))[:60]}
    return out


def save_networks(nets: Dict[str, Dict[str, str]], path: str = NETWORKS_FILE) -> None:
    d = os.path.dirname(path)
    try:
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".net-", dir=d)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(nets, fh)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except OSError as e:
        log.warning("could not save networks: %s", e)


class Checker:
    def __init__(self, cfg: Dict[str, Any], run_probe: ProbeFn = probe.run_probe,
                 networks_path: str = NETWORKS_FILE, sys_dns: SysDnsFn = system_dns_ok) -> None:
        self.cfg = cfg
        self.run_probe = run_probe
        self.sys_dns = sys_dns
        self.networks_path = networks_path
        self.networks = load_networks(networks_path)

    def saved_for(self, net: Optional[NetId]) -> Optional[str]:
        if net is None:
            return None
        e = self.networks.get(net.save_key)
        return e["preset"] if e else None

    def _save(self, net: Optional[NetId], preset: str) -> None:
        if net is None:
            return
        self.networks[net.save_key] = {"preset": preset, "label": net.label}
        while len(self.networks) > MAX_NETWORKS:  # dosya sınırsız büyümesin (yüklerken de bu sınır uygulanır)
            self.networks.pop(next(iter(self.networks)))
        save_networks(self.networks, self.networks_path)

    async def _try(self, keys: Optional[List[str]], custom: bool = False) -> List[Dict[str, Any]]:
        plans = probe.plans_for(keys)
        if custom:
            plans.append(("custom", "Custom", effective_strategy(self.cfg)))
        return await self.run_probe(self.cfg, None, None, plans)

    async def verify(self, net: Optional[NetId], active: str, apply: Callable[[str], None],
                     still: Callable[[], bool] = lambda: True) -> Dict[str, Any]:
        res = await self._verify_method(net, active, apply, still)
        # Yöntem siteleri açsa bile uygulamalar sistem DNS'ini kullanır (şeffaf mod): o ölü ise
        # "çalışıyor" demek yanlış olur. SOCKS modunda adları vekil çözer, bu test gerekmez.
        if res["check"] in (WORKING, NOT_BLOCKED) and self.cfg.get("mode") == "transparent":
            # Engelli bir test sitesi + genel adlar: tek bir sitenin NXDOMAIN'i "DNS ölü" sanılmasın.
            first = list(self.cfg.get("probe_domains") or [])[:1]
            domains = first + ["example.com", "cloudflare.com"]
            if not await self.sys_dns(domains):
                log.warning("system DNS (getaddrinfo) cannot resolve names on %s", net.label if net else "?")
                res = dict(res, check=FAILED, verdict=probe.SYSTEM_DNS)
        return res

    async def _verify_method(self, net: Optional[NetId], active: str, apply: Callable[[str], None],
                             still: Callable[[], bool] = lambda: True) -> Dict[str, Any]:
        """active: şu an kullanılan ön ayar (custom olabilir). apply(preset): yöntemi canlı uygular.

        still(): sonuç uygulanmadan/kaydedilmeden hemen önce hâlâ aynı ağda mıyız. Ağ yoklaması birkaç saniyede
        bir olduğu için, test sürerken ağ değişmiş olabilir; o zaman eski ağa ait olmayan sonuç kaydedilmez.
        """
        custom = active == "custom"
        # Kullanıcının kendi (custom) stratejisi çalışmıyorsa sessizce ön ayara geçilmez; durum bildirilir.
        auto = self.cfg.get("auto_method", True) and not custom
        label = net.label if net else "?"
        keys = [active] if active in PRESETS else []
        results = await self._try(keys, custom)
        cur = probe.find(results, active)
        if probe.bypasses(results, cur):
            return self._result(WORKING, results, active, cur)
        if probe.not_blocked(results):
            return self._result(NOT_BLOCKED, results, active, probe.find(results, "none"))
        if probe.diagnose(results) == probe.DNS_FAILED or not auto:
            return self._result(FAILED, results, active, None)

        saved = self.saved_for(net)
        if saved and saved != active:
            log.info("%s does not open the test sites on %s, trying the saved method", active, label)
            tried = await self._try([saved])
            r = probe.find(tried, saved)
            if probe.bypasses(tried, r) and still():
                apply(saved)
                return self._result(WORKING, tried, saved, r)

        log.info("trying all methods on %s", label)
        full = await self._try(None)
        best = probe.best_preset(full)
        if best is None or not still():
            return self._result(FAILED, full, active, None)
        apply(best)
        self._save(net, best)
        return self._result(WORKING, full, best, probe.find(full, best))

    @staticmethod
    def _result(state: str, results, preset: str, r) -> Dict[str, Any]:
        return {
            "check": state,
            "preset": preset,
            "detail": f"{r['ok']}/{probe.reachable(r)}" if r else "",
            "verdict": probe.diagnose(results),
            "results": results,
        }
