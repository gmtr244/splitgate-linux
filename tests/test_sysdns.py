"""systemd-resolved DoT uyumu (sahte dizin + sahte systemctl; sisteme dokunmaz)."""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dpigec import config, sysdns  # noqa: E402


class FakeRun:
    def __init__(self, rc=0):
        self.rc = rc
        self.calls = []

    def __call__(self, cmd):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, self.rc, "", "boom" if self.rc else "")


class SysDnsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dropin = os.path.join(self.tmp.name, "resolved.conf.d")
        self.rundir = os.path.join(self.tmp.name, "resolve")
        os.mkdir(self.rundir)
        self.cfg = config.default_config()
        self.run = FakeRun()
        self.path = os.path.join(self.dropin, sysdns.DROPIN_NAME)

    def tearDown(self):
        self.tmp.cleanup()

    def enable(self, **kw):
        kw.setdefault("fw_ready", lambda: True)
        return sysdns.enable(self.cfg, self.dropin, self.rundir, self.run, sleep=lambda s: None, **kw)

    def test_content_only_turns_dot_off(self):
        lines = [ln for ln in sysdns.CONTENT.splitlines() if ln and not ln.startswith("#")]
        self.assertEqual(lines, ["[Resolve]", "DNSOverTLS=no"])

    def test_dropin_name_sorts_after_user_dropins(self):
        # resolved drop-in'leri ada göre sıralar; sonraki kazanır
        for user in ("dot.conf", "dns.conf", "99-local.conf", "privacy.conf"):
            self.assertGreater(sysdns.DROPIN_NAME, user)

    def test_enable_writes_and_reloads(self):
        self.assertEqual(self.enable(), "enabled")
        with open(self.path) as fh:
            self.assertEqual(fh.read(), sysdns.CONTENT)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o644)
        self.assertTrue(any("try-reload-or-restart" in c for c in self.run.calls))
        self.assertTrue(sysdns.is_enabled(self.dropin))

    def test_enable_twice_does_not_reload_again(self):
        self.enable()
        n = len(self.run.calls)
        self.assertEqual(self.enable(), "unchanged")
        self.assertEqual(len(self.run.calls), n)

    def test_waits_for_firewall_and_gives_up(self):
        seen = []

        def ready():
            seen.append(1)
            return len(seen) >= 3

        self.assertEqual(self.enable(fw_ready=ready), "enabled")
        self.assertEqual(len(seen), 3)
        # tablo hiç kurulmazsa DoT kapatılmaz (düz DNS dışarı sızmasın)
        sysdns.disable(self.dropin, self.run)
        r = self.enable(fw_ready=lambda: False, wait_seconds=0)
        self.assertEqual(r, "error:firewall-not-ready")
        self.assertFalse(os.path.exists(self.path))

    def test_skipped_when_dns_is_not_redirected(self):
        self.cfg["dns"]["enabled"] = False
        self.assertEqual(self.enable(), "skipped:dns-not-redirected")
        self.cfg["dns"]["enabled"] = True
        self.cfg["mode"] = "socks"
        self.assertEqual(self.enable(), "skipped:dns-not-redirected")
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(self.run.calls, [])

    def test_skipped_without_resolved(self):
        os.rmdir(self.rundir)
        self.assertEqual(self.enable(), "skipped:resolved-not-running")
        self.assertFalse(os.path.exists(self.dropin))

    def test_symlink_at_path_is_replaced_not_followed(self):
        os.makedirs(self.dropin)
        victim = os.path.join(self.tmp.name, "victim")
        with open(victim, "w") as fh:
            fh.write("keep")
        os.symlink(victim, self.path)
        self.assertEqual(self.enable(), "enabled")
        self.assertFalse(os.path.islink(self.path))
        with open(victim) as fh:
            self.assertEqual(fh.read(), "keep")

    def test_disable_removes_and_reloads_once(self):
        self.enable()
        self.run.calls.clear()
        self.assertTrue(sysdns.disable(self.dropin, self.run))
        self.assertFalse(os.path.exists(self.path))
        self.assertTrue(any("try-reload-or-restart" in c for c in self.run.calls))
        self.run.calls.clear()
        self.assertFalse(sysdns.disable(self.dropin, self.run))
        self.assertEqual(self.run.calls, [])  # yoksa resolved boşuna yenilenmez

    def test_reload_failure_is_retried_by_next_disable(self):
        # ExecStop'ta resolved yenilenemedi: işaret kalır, ExecStopPost cleanup yeniden dener (restart ile).
        self.enable()
        self.run.rc = 1
        self.run.calls.clear()
        self.assertTrue(sysdns.disable(self.dropin, self.run))
        self.assertFalse(os.path.exists(self.path))
        verbs = [c[1] for c in self.run.calls]
        self.assertEqual(verbs, ["try-reload-or-restart", "try-restart"])
        self.assertTrue(os.path.exists(os.path.join(self.dropin, sysdns.PENDING_NAME)))
        self.run.rc = 0
        self.run.calls.clear()
        self.assertTrue(sysdns.disable(self.dropin, self.run))
        self.assertTrue(self.run.calls)
        self.assertFalse(os.path.exists(os.path.join(self.dropin, sysdns.PENDING_NAME)))
        self.run.calls.clear()
        self.assertFalse(sysdns.disable(self.dropin, self.run))
        self.assertEqual(self.run.calls, [])

    def test_pending_marker_is_not_a_resolved_dropin(self):
        self.assertFalse(sysdns.PENDING_NAME.endswith(".conf"))

    def test_disable_never_raises(self):
        self.enable()

        def boom(cmd):
            raise subprocess.TimeoutExpired(cmd, 30)

        self.assertIsInstance(sysdns.disable(self.dropin, boom), bool)

    def test_default_runner_turns_exec_errors_into_failures(self):
        r = sysdns._run(["/nonexistent/definitely-not-here"])
        self.assertNotEqual(r.returncode, 0)

    def test_readiness_requires_live_dns_redirect_rules(self):
        live = ("table inet dpigec {\n chain nat_output {\n"
                "  udp dport 53 redirect to :15353\n  tcp dport 53 redirect to :15353\n }\n}\n")

        def lister(out, rc=0):
            def run(cmd):
                return subprocess.CompletedProcess(cmd, rc, out, "")
            return run

        self.assertTrue(sysdns.dns_redirect_active(self.cfg, lister(live)))
        # tablo var ama DNS yönlendirmesi yok (dns.enabled=false ile çalışan servis): DoT kapatılmaz
        no_dns = live.replace("udp dport 53 redirect to :15353\n", "")
        self.assertFalse(sysdns.dns_redirect_active(self.cfg, lister(no_dns)))
        self.assertFalse(sysdns.dns_redirect_active(self.cfg, lister("", rc=1)))
        other = dict(self.cfg, dns_port=15354)
        self.assertFalse(sysdns.dns_redirect_active(other, lister(live)))
        prefix = dict(self.cfg, dns_port=1535)  # ":1535" canlı ":15353"in öneki: eşleşmemeli
        self.assertFalse(sysdns.dns_redirect_active(prefix, lister(live)))


if __name__ == "__main__":
    unittest.main()
