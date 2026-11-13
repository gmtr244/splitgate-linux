# Copyright (C) 2026 Project Gamers
# SPDX-License-Identifier: GPL-3.0-or-later
"""systemd-resolved ile uyum: servis çalışırken resolved'un DNS-over-TLS'ini kapatır.

Sorun: resolved DNSOverTLS=yes (katı) ise sorgular 853/TLS ile gider; nftables 53
yönlendirmesine hiç girmez. Ağın DNS sunucusu (çoğu ev router'ı) DoT desteklemiyorsa
resolved hiçbir adı çözemez ve servis "çalışıyor" görünse de hiçbir uygulama bağlanamaz.

Çözüm: servis çalışırken /run altına bir resolved drop-in'i konur (DNSOverTLS=no).
resolved'un 53'e giden düz sorguları nftables ile yerel DNS vekiline yönlendirilir ve
oradan DoH (HTTPS) ile çıkar; yani DNS şifreli kalır. Drop-in /run altında olduğu için
yeniden başlatmada kendiliğinden silinir.

Sıra (sızıntı penceresi olmaması için):
  açma  : yalnızca çalışan kurallarda DNS yönlendirmesi görüldükten SONRA (servis ExecStartPost=+)
  kapama: nftables kaldırılmadan ÖNCE (servis ExecStop=+ ve ExecStopPost=+ cleanup; servis modunda
          motor kuralları kendisi kaldırmaz, bkz. engine.Engine.stop)
Bu işlevler root olarak ve servis sandbox'ı dışında çalışır: sabit bir yola sabit içerik yazar,
systemctl/resolvectl çalıştırır; kullanıcı girdisi almaz. Kapama hiçbir zaman istisna fırlatmaz
(çağıranın ardından kuralları kaldırması engellenmemeli).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional

from . import firewall

log = logging.getLogger("dpigec.sysdns")

DROPIN_DIR = "/run/systemd/resolved.conf.d"
# "zz-" öneki: resolved drop-in'leri dosya adına göre sıralar, sonraki kazanır
# (kullanıcının /etc altındaki dot.conf gibi dosyalarından sonra okunur).
DROPIN_NAME = "zz-dpigec.conf"
# Drop-in silindi ama resolved yenilenemedi: bir sonraki disable() yeniden dener.
# ".conf" ile bitmediği için resolved bu dosyayı okumaz.
PENDING_NAME = ".dpigec-reload-pending"
RESOLVED_RUNDIR = "/run/systemd/resolve"  # resolved çalışıyorsa vardır
RESOLVED_UNIT = "systemd-resolved.service"

CONTENT = (
    "# SplitGate (dpigec) - managed automatically, removed when the service stops.\n"
    "# DNS-over-TLS is turned off only while the service runs: plain DNS on port 53\n"
    "# is redirected by nftables to the local proxy and leaves the machine as DoH.\n"
    "[Resolve]\n"
    "DNSOverTLS=no\n"
)

Runner = Callable[[List[str]], subprocess.CompletedProcess]


def _run(cmd: List[str]) -> subprocess.CompletedProcess:
    try:
        # 20 sn: disable() en kötü durumda 2 systemctl çağrısı = 40 sn (flush yalnızca enable'da);
        # TimeoutStopSec (90 sn) içinde kalır, ardından cleanup'ın kuralları kaldırması için zaman olur.
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as e:
        return subprocess.CompletedProcess(cmd, 255, "", f"{type(e).__name__}: {e}")


def wanted(cfg: Dict[str, Any]) -> bool:
    """Drop-in yalnızca DNS gerçekten yerel vekile yönlendiriliyorsa güvenlidir."""
    return cfg.get("mode") == "transparent" and bool(cfg.get("dns", {}).get("enabled"))


def resolved_active(rundir: str = RESOLVED_RUNDIR) -> bool:
    return os.path.isdir(rundir)


def _reload(run: Runner, hard: bool = False, flush: bool = False) -> bool:
    exe = shutil.which("systemctl") or "/usr/bin/systemctl"
    # try-*: resolved çalışmıyorsa başlatmaz. hard: yenileme olmadıysa yeniden başlat.
    res = run([exe, "try-restart" if hard else "try-reload-or-restart", RESOLVED_UNIT])
    if res.returncode != 0:
        log.warning("could not reload systemd-resolved: %s", (res.stderr or res.stdout).strip()[:200])
        print(f"warning: could not reload systemd-resolved: {(res.stderr or res.stdout).strip()[:200]}")
        return False
    rc = shutil.which("resolvectl")
    if flush and rc:
        run([rc, "flush-caches"])  # önceki (DoT ile başarısız/eksik) cevaplar atılsın
    return True


def _write_atomic(path: str, content: str) -> None:
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".dpigec-", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def dns_redirect_active(cfg: Dict[str, Any], run: Runner = _run) -> bool:
    """ÇALIŞAN kurallarda (diskteki ayar değil) 53 -> yerel DNS vekili yönlendirmesi var mı?"""
    try:
        nft = firewall._nft()
    except firewall.FirewallError:
        return False
    res = run([nft, "list", "table", "inet", firewall.TABLE])
    if res.returncode != 0:
        return False
    port = int(cfg["dns_port"])
    lines = {ln.strip() for ln in res.stdout.splitlines()}  # tam satır: ":535" ":5353" ile eşleşmesin
    return {f"udp dport 53 redirect to :{port}", f"tcp dport 53 redirect to :{port}"} <= lines


def enable(cfg: Dict[str, Any], dropin_dir: str = DROPIN_DIR, rundir: str = RESOLVED_RUNDIR,
           run: Runner = _run, fw_ready: Optional[Callable[[], bool]] = None,
           wait_seconds: float = 15.0, sleep: Callable[[float], None] = time.sleep) -> str:
    """Drop-in'i kurar. Dönüş: 'enabled' | 'unchanged' | 'skipped:<neden>' | 'error:<neden>'."""
    if not wanted(cfg):
        return "skipped:dns-not-redirected"
    if not resolved_active(rundir):
        return "skipped:resolved-not-running"
    if fw_ready is None:
        def fw_ready() -> bool:
            return dns_redirect_active(cfg, run)
    # DNS yönlendirmesi çalışmadan DoT kapatılırsa sorgular düz DNS ile dışarı çıkar.
    deadline = time.monotonic() + wait_seconds
    while not fw_ready():
        if time.monotonic() >= deadline:
            return "error:firewall-not-ready"
        sleep(0.25)
    path = os.path.join(dropin_dir, DROPIN_NAME)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            if fh.read() == CONTENT:
                return "unchanged"
    except OSError:
        pass
    try:
        os.makedirs(dropin_dir, mode=0o755, exist_ok=True)
        if os.path.islink(path):  # sabit yolumuz bir bağlantı olmamalı
            os.unlink(path)
        _write_atomic(path, CONTENT)
    except OSError as e:
        return f"error:{e.strerror or e}"
    _reload(run, flush=True)
    log.info("systemd-resolved: DNS-over-TLS paused while the service runs (DNS goes via DoH)")
    return "enabled"


def disable(dropin_dir: str = DROPIN_DIR, run: Runner = _run) -> bool:
    """Drop-in'i kaldırır ve resolved'u yeniler. Kaldırıldıysa (ya da bekleyen yenileme yapıldıysa) True.

    Asla istisna fırlatmaz. Yenileme başarısızsa bir işaret dosyası bırakılır; sonraki çağrı
    (ör. ExecStop başarısız olduysa ExecStopPost) yenilemeyi yeniden ve gerekirse restart ile dener.
    """
    path = os.path.join(dropin_dir, DROPIN_NAME)
    pending = os.path.join(dropin_dir, PENDING_NAME)
    try:
        present = os.path.lexists(path)
        if not present and not os.path.exists(pending):
            return False
        if present:
            # İşaret silmeden ÖNCE: arada çökülürse sonraki çağrı yenilemeyi yine yapar.
            try:
                with open(pending, "w", encoding="utf-8"):
                    pass
            except OSError:
                pass
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        ok = _reload(run) or _reload(run, hard=True)
        if ok:
            try:
                os.unlink(pending)
            except OSError:
                pass
            log.info("systemd-resolved: DNS-over-TLS setting restored")
        else:
            log.error("systemd-resolved still has DNS-over-TLS paused; run: systemctl restart systemd-resolved")
            print("error: systemd-resolved could not be reloaded; run: systemctl restart systemd-resolved")
        return True
    except Exception as e:  # noqa: BLE001  (çağıran kuralları yine de kaldırabilmeli)
        log.error("could not restore systemd-resolved: %s", e)
        return False


def is_enabled(dropin_dir: str = DROPIN_DIR) -> bool:
    return os.path.exists(os.path.join(dropin_dir, DROPIN_NAME))


def main_on(cfg: Dict[str, Any]) -> int:
    r = enable(cfg)
    print(r)
    return 1 if r.startswith("error:") else 0


def main_off() -> int:
    print("removed" if disable() else "nothing to remove")
    return 0
