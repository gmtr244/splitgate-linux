"""Motor: ağ değişimi, gerçek kontrol sonucu ve canlı yöntem değişimi (ağa çıkmaz)."""
import asyncio
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dpigec import checker, config, engine, netwatch  # noqa: E402

A = netwatch.NetId("wifi:wlan0:10.0.0.1", "Wi-Fi (wlan0)")
B = netwatch.NetId("eth:eth0:192.168.1.1", "Ethernet (eth0)")


class FakeChecker:
    def __init__(self, result, delay=0.0, switch_to=None):
        self.result, self.delay, self.switch_to = result, delay, switch_to
        self.calls = []
        self.nets = []

    async def verify(self, net, active, apply, still=lambda: True):
        self.calls.append((net.key, active))
        self.nets.append(net)
        await asyncio.sleep(self.delay)
        if self.switch_to:
            apply(self.switch_to)
        return dict(self.result, preset=self.switch_to or active)


def make_engine(fake):
    tmp = tempfile.mkdtemp()
    e = engine.Engine(config.default_config(), status_path=os.path.join(tmp, "status.json"),
                      manage_firewall=False)
    e.state = "running"
    e.relay = mock.Mock()
    e.relay.reset_connections.return_value = 3
    e.doh = mock.Mock()
    e.doh.health.return_value = []
    e.resolver = mock.Mock()
    e.dns = mock.Mock()
    e.checker = fake
    return e


class DaemonFlagTests(unittest.TestCase):
    def test_service_mode_only_with_explicit_flag(self):
        from dpigec import cli
        seen = []
        with mock.patch.object(cli, "run_engine", lambda cfg, **kw: seen.append(kw["service"]) or 0), \
                mock.patch.object(cli, "load_config", lambda p: config.default_config()):
            cli.main(["daemon"])
            cli.main(["daemon", "--service"])
        self.assertEqual(seen, [False, True])


class ServiceFirewallTests(unittest.IsolatedAsyncioTestCase):
    def mk(self, service):
        e = make_engine(FakeChecker({}))
        e.dns = None  # stop() sahte sunucuları await etmesin
        e.manage_firewall = True
        e.service = service
        e._fw_active = True
        return e

    async def test_service_leaves_rule_removal_to_execstoppost(self):
        e = self.mk(True)
        with mock.patch.object(engine.firewall, "remove") as rm:
            await e.stop()
        rm.assert_not_called()

    async def test_foreground_run_removes_rules_itself(self):
        e = self.mk(False)
        with mock.patch.object(engine.firewall, "remove") as rm:
            await e.stop()
        rm.assert_called_once()

    async def test_flushed_rules_are_reapplied(self):
        e = self.mk(True)
        with mock.patch.object(engine.firewall, "is_intact", return_value=False), \
                mock.patch.object(engine.firewall, "apply") as ap:
            await e._guard_firewall()
        ap.assert_called_once_with(e.cfg)
        with mock.patch.object(engine.firewall, "is_intact", return_value=True), \
                mock.patch.object(engine.firewall, "apply") as ap:
            await e._guard_firewall()
        ap.assert_not_called()

    async def test_guard_does_not_reapply_once_stopping(self):
        e = self.mk(False)

        def installed(cfg):
            e.state = "stopping"  # stop() iş parçacığı beklerken başladı
            return False

        with mock.patch.object(engine.firewall, "is_intact", side_effect=installed), \
                mock.patch.object(engine.firewall, "apply") as ap:
            await e._guard_firewall()
        ap.assert_not_called()

    async def test_watch_reacts_only_to_events_on_our_table(self):
        e = self.mk(True)
        lines = [b"add table inet dpigec\n", b"delete table inet other\n", b"add rule ip nat x\n",
                 b"delete table inet dpigec\n", b""]

        class Out:
            async def readline(self_inner):
                return lines.pop(0) if lines else b""

        class Proc:
            stdout = Out()
            stderr = None
            returncode = None

            def kill(self_inner):
                self_inner.returncode = -9

            async def wait(self_inner):
                return -9

        spawns = []

        async def spawn(*a, **kw):
            spawns.append(a)
            if len(spawns) == 2:  # monitor kapandıktan sonra yeniden başlatıldı: testi bitir
                e._stop.set()
            return Proc()

        guards = []

        async def guard():
            guards.append(1)

        e._guard_firewall = guard
        with mock.patch.object(engine.asyncio, "create_subprocess_exec", spawn), \
                mock.patch.object(engine.firewall, "_nft", return_value="/usr/bin/nft"):
            await asyncio.wait_for(e._fw_watch(), 5)
        # 1. monitor: başlangıç kontrolü + yalnızca "delete table inet dpigec" olayı (diğer satırlar yok sayılır);
        # monitor kapanınca yeniden başlatılır (2. spawn, "monitor" argümanıyla) ve yine başlangıç kontrolü yapılır.
        self.assertEqual(len(guards), 3)
        self.assertEqual(spawns[0], ("/usr/bin/nft", "monitor"))

    async def test_watch_falls_back_to_polling_without_nft_monitor(self):
        e = self.mk(True)
        guards = []

        async def guard():
            guards.append(1)
            if len(guards) >= 2:
                e._stop.set()

        async def spawn(*a, **kw):
            raise FileNotFoundError("nft")

        e._guard_firewall = guard
        with mock.patch.object(engine.asyncio, "create_subprocess_exec", spawn), \
                mock.patch.object(engine.firewall, "_nft", return_value="/usr/bin/nft"):
            await asyncio.wait_for(e._fw_watch(fallback_seconds=0.01), 5)
        self.assertEqual(len(guards), 2)

    async def test_reapply_is_rate_limited(self):
        e = self.mk(True)
        with mock.patch.object(engine.firewall, "is_intact", return_value=False), \
                mock.patch.object(engine.firewall, "apply") as ap:
            for _ in range(engine.REAPPLY_MAX + 10):
                await e._guard_firewall()
        self.assertEqual(ap.call_count, engine.REAPPLY_MAX)
        self.assertGreater(e._guard_paused_until, 0)

    async def test_monitor_that_exits_at_once_falls_back_to_polling(self):
        e = self.mk(True)
        spawns, guards = [], []

        class Proc:
            returncode = None

            def __init__(self_inner):
                async def eof():
                    return b""

                async def err(n):
                    return b"Operation not permitted"
                self_inner.stdout = type("O", (), {"readline": staticmethod(eof)})()
                self_inner.stderr = type("E", (), {"read": staticmethod(err)})()

            def kill(self_inner):
                self_inner.returncode = -9

            async def wait(self_inner):
                return -9

        async def spawn(*a, **kw):
            spawns.append(1)
            return Proc()

        async def guard():
            guards.append(1)
            if len(guards) > engine.MONITOR_MAX_FAST_EXITS + 1:
                e._stop.set()

        e._guard_firewall = guard
        with mock.patch.object(engine.asyncio, "create_subprocess_exec", spawn), \
                mock.patch.object(engine.firewall, "_nft", return_value="/usr/bin/nft"), \
                mock.patch.object(engine.asyncio, "sleep", side_effect=lambda t: asyncio.sleep(0)):
            await asyncio.wait_for(e._fw_watch(fallback_seconds=0.01), 10)
        self.assertEqual(len(spawns), engine.MONITOR_MAX_FAST_EXITS)  # sonra yeniden başlatılmaz

    async def test_guard_idle_when_not_managing_firewall(self):
        e = self.mk(True)
        e.manage_firewall = False
        with mock.patch.object(engine.firewall, "is_intact") as inst:
            await e._guard_firewall()
        inst.assert_not_called()


WORK = {"check": checker.WORKING, "detail": "2/2", "verdict": "works", "results": []}
FAIL = {"check": checker.FAILED, "detail": "", "verdict": "dpi_unbeaten", "results": []}


class EngineCheckTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sleep = mock.patch.object(engine.asyncio, "sleep", side_effect=self._fast_sleep)
        self._real_sleep = asyncio.sleep
        self.sleep.start()

    async def asyncTearDown(self):
        self.sleep.stop()

    async def _fast_sleep(self, t):
        await self._real_sleep(0)

    async def test_first_network_checks_without_reset(self):
        e = make_engine(FakeChecker(WORK))
        e._on_network(A)
        self.assertEqual(e.check, checker.CHECKING)
        await e._check_task
        e.relay.reset_connections.assert_not_called()
        self.assertEqual((e.check, e.check_detail), (checker.WORKING, "2/2"))

    async def test_switch_resets_connections_and_caches_then_rechecks(self):
        e = make_engine(FakeChecker(WORK))
        e._on_network(A)
        await e._check_task
        e._on_network(B)
        e.relay.reset_connections.assert_called_once()
        e.doh.close.assert_called()
        e.resolver.flush.assert_called()
        e.dns.flush.assert_called()
        await e._check_task
        self.assertEqual(e.checker.calls[-1][0], B.key)

    async def test_stale_result_is_dropped(self):
        slow = FakeChecker(WORK, delay=0.05, switch_to="oob")
        e = make_engine(slow)
        e._on_network(A)
        task = e._check_task
        await self._real_sleep(0)
        e.checker = FakeChecker(FAIL)
        e._on_network(B)
        with self.assertRaises(asyncio.CancelledError):
            await task
        await e._check_task
        self.assertEqual(e.check, checker.FAILED)
        self.assertNotEqual(e.active_preset, "oob")

    async def test_working_method_is_applied_live(self):
        e = make_engine(FakeChecker(WORK, switch_to="tlsdisorder"))
        e.network = None
        e._on_network(A)
        await e._check_task
        self.assertEqual(e.active_preset, "tlsdisorder")
        st = e.relay.set_strategy.call_args[0][0]
        self.assertTrue(st["tlsrec"] and st["disorder"])

    async def test_back_from_no_network_resets_connections(self):
        e = make_engine(FakeChecker(WORK))
        e._on_network(A)
        await e._check_task
        e._on_network(None)
        e.relay.reset_connections.reset_mock()
        e._on_network(A)
        e.relay.reset_connections.assert_called_once()
        await e._check_task

    async def test_old_results_cleared_on_change(self):
        e = make_engine(FakeChecker(dict(WORK, results=[{"preset": "x"}])))
        e._on_network(A)
        await e._check_task
        self.assertTrue(e.check_results)
        e._on_network(B)
        self.assertEqual(e.check_results, [])
        await e._check_task

    async def test_same_gateway_other_mac_is_other_network(self):
        e = make_engine(FakeChecker(WORK))
        home = netwatch.NetId("wifi:wlan0:192.168.1.1", "Wi-Fi (wlan0)", "aa:aa:aa:aa:aa:aa")
        cafe = netwatch.NetId("wifi:wlan0:192.168.1.1", "Wi-Fi (wlan0)", "bb:bb:bb:bb:bb:bb")
        e._on_network(home)
        await e._check_task
        e._on_network(cafe)
        e.relay.reset_connections.assert_called_once()
        await e._check_task

    async def test_mac_learned_later_is_not_a_change(self):
        e = make_engine(FakeChecker(WORK))
        e._on_network(netwatch.NetId("wifi:wlan0:10.0.0.1", "Wi-Fi (wlan0)", ""))
        await e._check_task
        e._on_network(netwatch.NetId("wifi:wlan0:10.0.0.1", "Wi-Fi (wlan0)", "aa:aa:aa:aa:aa:aa"))
        e.relay.reset_connections.assert_not_called()
        self.assertEqual(e.network.mac, "aa:aa:aa:aa:aa:aa")

    async def test_late_arp_entry_is_used_for_checking_and_saving(self):
        fake = FakeChecker(WORK)
        e = make_engine(fake)
        e._on_network(netwatch.NetId("wifi:wlan0:10.0.0.1", "Wi-Fi (wlan0)", ""))
        learned = netwatch.NetId("wifi:wlan0:10.0.0.1", "Wi-Fi (wlan0)", "aa:aa:aa:aa:aa:aa")
        with mock.patch.object(netwatch, "current", return_value=learned):
            await e._check_task
        self.assertEqual(fake.nets[-1].mac, "aa:aa:aa:aa:aa:aa")
        self.assertEqual(e.network.mac, "aa:aa:aa:aa:aa:aa")

    async def test_watch_loop_survives_errors(self):
        e = make_engine(FakeChecker(WORK))
        calls = []

        def boom():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("x")
            e._stop.set()
            return A
        with mock.patch.object(netwatch, "current", boom):
            await asyncio.wait_for(e._net_loop(), 5)
        self.assertGreaterEqual(len(calls), 2)

    async def test_network_lost_clears_status(self):
        e = make_engine(FakeChecker(WORK))
        e._on_network(A)
        await e._check_task
        e._on_network(None)
        self.assertEqual((e.check, e.check_detail, e.verdict), (checker.IDLE, "", ""))

    async def test_status_contains_check(self):
        e = make_engine(FakeChecker(WORK))
        e._on_network(A)
        await e._check_task
        e._publish()
        from dpigec.status import read_status
        st = read_status(e.status_path)
        self.assertEqual((st["check"], st["network"], st["check_detail"]), ("working", "Wi-Fi (wlan0)", "2/2"))


if __name__ == "__main__":
    unittest.main()
