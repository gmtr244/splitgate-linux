"""Animasyonlu arayüz bileşenleri ve yeni pencere davranışları (başsız / offscreen). PyQt6 yoksa atlanır."""
import json
import os
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

try:
    from PyQt6.QtWidgets import QApplication, QWidget
    HAVE_QT = True
except ImportError:  # pragma: no cover
    HAVE_QT = False

from test_gui import FAKE_STATUS  # noqa: E402


@unittest.skipUnless(HAVE_QT, "PyQt6 kurulu değil")
class WidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from dpigec.gui import widgets
        widgets.set_motion(True)

    def tearDown(self):
        from dpigec.gui import widgets
        widgets.set_motion(True)

    def wait(self, cond, seconds=3.0):
        end = time.monotonic() + seconds
        while not cond() and time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()
        return cond()

    # ---- güç düğmesi -------------------------------------------------------
    def test_power_button_loop_runs_only_while_visible_and_animated(self):
        from PyQt6.QtCore import QAbstractAnimation
        from dpigec.gui import widgets
        b = widgets.PowerButton()
        b.set_look("#2ea043", "pulse")
        self.assertNotEqual(b._loop.state(), QAbstractAnimation.State.Running)  # gizliyken çalışmaz
        b.show()
        self.app.processEvents()
        self.assertEqual(b._loop.state(), QAbstractAnimation.State.Running)
        b.hide()
        self.assertNotEqual(b._loop.state(), QAbstractAnimation.State.Running)  # tepside CPU yemesin
        b.show()
        b.set_look("#6e7681", "idle", neutral=True)
        self.assertNotEqual(b._loop.state(), QAbstractAnimation.State.Running)
        b.set_look("#d29922", "spin")
        self.assertEqual(b._loop.state(), QAbstractAnimation.State.Running)
        widgets.set_motion(False)
        b.refresh()
        self.assertNotEqual(b._loop.state(), QAbstractAnimation.State.Running)
        b.close()

    def test_power_button_color_reaches_target_and_paints(self):
        from PyQt6.QtGui import QColor
        from dpigec.gui import widgets
        b = widgets.PowerButton()
        b.setText("Stop")
        b.resize(210, 224)
        b.show()
        b.set_look("#f85149", "idle")
        self.assertTrue(self.wait(lambda: b._color == QColor("#f85149")))
        for mode in ("idle", "spin", "pulse"):
            b.set_look("#2ea043", mode)
            self.assertFalse(b.grab().isNull())
        b.close()

    def test_power_button_without_motion_switches_color_instantly(self):
        from PyQt6.QtGui import QColor
        from dpigec.gui import widgets
        widgets.set_motion(False)
        b = widgets.PowerButton()
        b.set_look("#2ea043", "pulse")
        self.assertEqual(b._color, QColor("#2ea043"))
        b.close()

    # ---- anahtar -----------------------------------------------------------
    def test_switch_behaves_like_a_checkbox(self):
        from dpigec.gui import widgets
        s = widgets.Switch("Option")
        seen = []
        s.toggled.connect(seen.append)
        self.assertFalse(s.isChecked())
        s.setChecked(True)
        self.assertTrue(s.isChecked())
        self.assertEqual(s._pos, 1.0)  # kod ile yüklemede animasyon yok
        s.setChecked(False)
        self.assertEqual(seen, [True, False])
        s.blockSignals(True)
        s.setChecked(True)
        s.blockSignals(False)
        self.assertEqual(s._pos, 1.0)  # sinyaller bloklu olsa da görünüm eşleşir

    def test_switch_click_animates_to_new_position(self):
        from dpigec.gui import widgets
        s = widgets.Switch("Option")
        s.show()
        s.click()
        self.assertTrue(s.isChecked())
        self.assertTrue(self.wait(lambda: s._pos == 1.0))
        s.click()
        self.assertTrue(self.wait(lambda: s._pos == 0.0))
        s.close()

    def test_switch_wraps_long_text_instead_of_growing_wider(self):
        from dpigec.gui import widgets
        s = widgets.Switch("A very long description " * 8)
        self.assertTrue(s.hasHeightForWidth())
        self.assertGreater(s.heightForWidth(200), s.heightForWidth(900))
        self.assertLess(s.minimumSizeHint().width(), s.sizeHint().width())

    # ---- bölüm -------------------------------------------------------------
    def test_collapsible_section_opens_and_closes(self):
        from PyQt6.QtWidgets import QLabel, QVBoxLayout
        from dpigec.gui import widgets
        host = QWidget()
        lay = QVBoxLayout(host)
        sec = widgets.CollapsibleSection("Title", expanded=False)
        QVBoxLayout(sec.body).addWidget(QLabel("content"))
        lay.addWidget(sec)
        host.resize(400, 300)
        host.show()
        self.app.processEvents()
        self.assertFalse(sec.expanded())
        self.assertFalse(sec.body.isVisible())
        sec.set_expanded(True)
        self.assertTrue(self.wait(lambda: sec.body.isVisible() and sec.body.maximumHeight() == widgets.QWIDGETSIZE_MAX))
        self.assertGreater(sec.body.height(), 5)
        sec.set_expanded(False)
        self.assertTrue(self.wait(lambda: not sec.body.isVisible()))
        widgets.set_motion(False)
        sec.set_expanded(True)
        self.assertTrue(sec.body.isVisible())
        host.close()

    # ---- bildirim ----------------------------------------------------------
    def test_toast_appears_and_hides_itself(self):
        from dpigec.gui import widgets
        host = QWidget()
        host.resize(500, 300)
        host.show()
        t = widgets.Toast(host)
        t.show_message("Saved")
        self.assertTrue(t.isVisible())
        self.assertEqual(t.label.text(), "Saved")
        self.assertLess(t.geometry().right(), host.width())
        self.assertGreater(t.geometry().top(), 100)
        t._timer.stop()
        t._leave()
        self.assertTrue(self.wait(lambda: not t.isVisible()))
        host.close()

    def test_toast_text_is_never_rendered_as_html(self):
        from PyQt6.QtCore import Qt
        from dpigec.gui import widgets
        host = QWidget()
        t = widgets.Toast(host)
        self.assertEqual(t.label.textFormat(), Qt.TextFormat.PlainText)

    # ---- grafik / çubuk / kart ---------------------------------------------
    def test_sparkline_keeps_a_fixed_window_and_clears(self):
        from dpigec.gui import widgets
        sp = widgets.Sparkline(size=10)
        sp.resize(300, 90)
        sp.show()
        for i in range(25):
            sp.push(i * 1000.0, 10.0)
        self.assertEqual(len(sp._down), 10)
        self.assertEqual(max(sp._down), 24000.0)
        self.assertFalse(sp.grab().isNull())
        sp.push(-5.0, -5.0)  # sayaç sıfırlanırsa negatif hız çizilmez
        self.assertEqual(sp._down[-1], 0.0)
        sp.clear()
        self.assertEqual(max(sp._down), 0.0)
        sp.close()

    def test_busy_bar_is_hidden_until_started(self):
        from PyQt6.QtCore import QAbstractAnimation
        from dpigec.gui import widgets
        host = QWidget()
        bar = widgets.BusyBar()
        bar.setParent(host)
        host.show()
        self.assertFalse(bar.isVisible())
        bar.start()
        self.assertTrue(bar.running() and bar.isVisible())
        self.assertEqual(bar._anim.state(), QAbstractAnimation.State.Running)
        host.hide()  # üst pencere gizlenince (tepsi) animasyon durur, açılınca kaldığı yerden sürer
        self.assertNotEqual(bar._anim.state(), QAbstractAnimation.State.Running)
        host.show()
        self.assertEqual(bar._anim.state(), QAbstractAnimation.State.Running)
        bar.stop()
        self.assertFalse(bar.isVisible())
        self.assertNotEqual(bar._anim.state(), QAbstractAnimation.State.Running)
        host.close()

    def test_stat_card_sets_text_immediately_and_flashes_only_when_asked(self):
        from dpigec.gui import widgets
        plain = widgets.StatCard("T")
        flashing = widgets.StatCard("T", flash_color="#f85149")
        for c in (plain, flashing):
            c.show()
            c.set("1")
            c.set("2")
            self.assertEqual(c.value.text(), "2")
        self.assertEqual(plain._flash, 0.0)
        self.assertTrue(self.wait(lambda: flashing._flash > 0.0, 1.0))  # ilk karede parlama başlar
        self.assertTrue(self.wait(lambda: flashing._flash == 0.0))  # sonra söner
        self.assertEqual(plain._flash, 0.0)
        plain.close()
        flashing.close()

    # ---- sayfa yığını ------------------------------------------------------
    def test_pages_navigation_and_tab_like_api(self):
        from PyQt6.QtWidgets import QLabel
        from dpigec.gui import widgets
        pages = widgets.Pages()
        for i, kind in enumerate(("home", "settings", "test", "log", "about")):
            pages.addTab(QLabel(str(i)), f"Page {i}", kind)
        pages.resize(700, 400)
        pages.show()
        changes = []
        pages.currentChanged.connect(changes.append)
        self.assertEqual(pages.count(), 5)
        self.assertEqual(pages.tabText(2), "Page 2")
        pages.setCurrentIndex(3)
        self.assertEqual(pages.currentIndex(), 3)
        self.assertTrue(pages._buttons[3].isChecked())
        pages.setCurrentIndex(3)  # aynı sayfa: sinyal yok
        pages.setCurrentIndex(9)  # geçersiz: yok sayılır
        pages.setCurrentIndex(-1)
        self.assertEqual(pages.currentIndex(), 3)
        pages._buttons[1].click()
        self.assertEqual(pages.currentIndex(), 1)
        self.assertEqual(changes, [3, 1])
        for b in pages._buttons:
            self.assertFalse(b.grab().isNull())
        pages.close()

    def test_page_fade_leaves_no_graphics_effect_behind(self):
        from PyQt6.QtWidgets import QLabel
        from dpigec.gui import widgets
        pages = widgets.Pages()
        for i in range(2):
            pages.addTab(QLabel(str(i)), f"P{i}", "about")
        pages.show()
        pages.setCurrentIndex(1)
        page = pages.widget(1)
        self.assertTrue(self.wait(lambda: page.graphicsEffect() is None))
        pages.close()

    def test_delayed_fade_in_survives_the_widget_being_deleted(self):
        from PyQt6 import sip
        from dpigec.gui import widgets
        w = QWidget()
        widgets.fade_in(w, 100, delay=30)
        sip.delete(w)
        end = time.monotonic() + 0.3
        while time.monotonic() < end:  # çökmemeli (eskiden RuntimeError ile abort ediyordu)
            self.app.processEvents()
            time.sleep(0.01)

    def test_busy_bar_does_not_animate_while_hidden(self):
        from PyQt6.QtCore import QAbstractAnimation
        from dpigec.gui import widgets
        host = QWidget()
        bar = widgets.BusyBar()
        bar.setParent(host)  # host gösterilmedi: görünmez
        bar.start()
        self.assertNotEqual(bar._anim.state(), QAbstractAnimation.State.Running)
        bar.stop()

    def test_stat_card_value_is_plain_text(self):
        from PyQt6.QtCore import Qt
        from dpigec.gui import widgets
        card = widgets.StatCard("T")
        self.assertEqual(card.value.textFormat(), Qt.TextFormat.PlainText)

    def test_motion_off_applies_final_values_synchronously(self):
        from dpigec.gui import widgets
        widgets.set_motion(False)
        host = QWidget()
        got = []
        widgets.tween(host, 0.0, 5.0, 500, got.append, lambda: got.append("done"))
        self.assertEqual(got, [5.0, "done"])
        widgets.fade_in(host)  # animasyon kapalıyken efekt eklenmez
        self.assertIsNone(host.graphicsEffect())
        self.assertEqual(widgets.ms(300), 0)


@unittest.skipUnless(HAVE_QT, "PyQt6 kurulu değil")
class WindowBehaviourTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from dpigec import i18n
        from dpigec.gui import widgets
        i18n.set_language("en")
        widgets.set_motion(False)
        self._old = os.environ.get("XDG_CONFIG_HOME")
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["XDG_CONFIG_HOME"] = self._tmp.name

    def tearDown(self):
        from dpigec import i18n
        from dpigec.gui import widgets
        i18n.set_language("en")
        widgets.set_motion(True)
        if self._old is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._old
        self._tmp.cleanup()

    def make_window(self):
        from dpigec.gui import app as gui
        self.app.setStyleSheet(gui.QSS)
        win = gui.MainWindow()
        win._timer.stop()
        return gui, win

    def test_unsaved_changes_indicator_follows_the_form(self):
        gui, win = self.make_window()
        win.show()
        self.assertFalse(win._dirty)
        self.assertTrue(win.dirty_label.isHidden())
        win.f_ports.setText("80, 443, 8443")
        self.assertTrue(win._dirty)
        self.assertFalse(win.dirty_label.isHidden())  # Ayarlar sayfası açık olmasa da etiket gösterilmiş sayılır
        win.f_ports.setText("80, 443")
        self.assertFalse(win._dirty)
        self.assertTrue(win.dirty_label.isHidden())
        win.f_ports.setText("80, abc")  # geçersiz değer de kaydedilmemiş sayılır
        self.assertTrue(win._dirty)
        win.close()

    def test_loading_a_config_does_not_mark_the_form_dirty_but_reset_does(self):
        gui, win = self.make_window()
        from dpigec import config
        win._load_form(config.normalize({"socks_port": 2080}))
        self.assertFalse(win._dirty)
        win._load_form(config.default_config(), keep_base=True)
        self.assertTrue(win._dirty)
        self.assertEqual(win._base_cfg["socks_port"], 2080)  # temel yapılandırma korunur
        win.close()

    def test_saving_clears_the_indicator_and_notifies(self):
        gui, win = self.make_window()
        win.show()
        calls = []
        old = gui.run_ctl
        gui.run_ctl = lambda args, stdin=None: calls.append(list(args)) or {"ok": True}
        win.f_quic.setChecked(not win.f_quic.isChecked())
        self.assertTrue(win._dirty)
        try:
            win._save()
            self.assertTrue(self._wait(lambda: not win._dirty))
        finally:
            gui.run_ctl = old
        self.assertEqual(calls[0], ["apply-config"])
        self.assertEqual(win.toast.label.text(), "Settings saved")
        win.close()

    def _wait(self, cond, seconds=3.0):
        end = time.monotonic() + seconds
        while not cond() and time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()
        return cond()

    def test_language_switch_keeps_unsaved_changes_flagged(self):
        gui, win = self.make_window()
        win.f_ports.setText("80, 443, 9000")
        self.assertTrue(win._dirty)
        old_status = gui.read_status
        gui.read_status = lambda: None
        try:
            win._change_language("tr")
        finally:
            gui.read_status = old_status
        self.assertTrue(win._dirty)
        self.assertEqual(win.f_ports.text(), "80, 443, 9000")
        self.assertEqual(win.dirty_label.text(), "● Kaydedilmemiş değişiklikler")
        win.close()

    def test_probe_highlight_survives_rows_being_cleared(self):
        from test_gui import FAKE_PROBE
        gui, win = self.make_window()
        from dpigec.gui import widgets
        widgets.set_motion(True)
        win._fill_probe_table(FAKE_PROBE)
        win.table.setRowCount(0)  # "Stratejileri dene" tekrar basılınca olan
        self._wait(lambda: False, 0.9)  # animasyon bitene kadar işle: çökmemeli
        win.close()

    def test_language_switch_with_invalid_form_keeps_the_text(self):
        from dpigec import i18n
        gui, win = self.make_window()
        win.f_ports.setText("80, abc")
        win._change_language("tr")
        self.assertEqual(i18n.language(), "en")
        self.assertEqual(win.f_ports.text(), "80, abc")
        self.assertIn("Fix the invalid setting", win.statusBar().currentMessage())
        win.close()

    def test_loading_flag_is_reset_even_if_loading_fails(self):
        gui, win = self.make_window()
        from dpigec import config
        good = config.default_config()
        win.f_mode.setEnabled(True)
        old = win.f_socks_port.setValue
        win.f_socks_port.setValue = lambda v: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            with self.assertRaises(RuntimeError):
                win._load_form(good)
        finally:
            win.f_socks_port.setValue = old
        self.assertFalse(win._loading)
        win.close()

    def test_power_button_look_follows_the_state(self):
        gui, win = self.make_window()
        win.render_status(None)
        self.assertEqual((win.power.mode, win.power._neutral), ("idle", True))
        win._pending_target = True
        win._pending_since = time.monotonic()
        win.render_status(None)
        self.assertEqual((win.power.mode, win.power._neutral), ("spin", False))
        win._pending_target = None
        win.render_status(FAKE_STATUS)
        self.assertEqual(win.power.mode, "pulse")
        win.render_status(dict(FAKE_STATUS, check="checking"))
        self.assertEqual(win.power.mode, "spin")
        win.render_status(dict(FAKE_STATUS, state="stopping"))
        self.assertEqual(win.power.mode, "spin")
        win.render_status(dict(FAKE_STATUS, check="failed", verdict="ip_block"))
        self.assertEqual(win.power.mode, "idle")
        win.close()

    def test_find_a_working_method_hint_only_when_the_check_failed(self):
        gui, win = self.make_window()
        win.show()
        win.render_status(FAKE_STATUS)
        self.assertFalse(win.hint_btn.isVisible())
        win.render_status(dict(FAKE_STATUS, check="failed", verdict="dpi"))
        self.assertTrue(win.hint_btn.isVisible())
        win.hint_btn.click()
        self.assertEqual(win.tabs.currentIndex(), gui.PAGE_TEST)
        win.render_status(None)
        self.assertFalse(win.hint_btn.isVisible())
        win.close()

    def test_empty_status_lines_take_no_space(self):
        gui, win = self.make_window()
        win.show()
        win.render_status(dict(FAKE_STATUS, doh=[], stats=dict(FAKE_STATUS["stats"], last_error="")))
        self.assertFalse(win.doh_label.isVisible())
        self.assertFalse(win.error_label.isVisible())
        win.render_status(FAKE_STATUS)
        self.assertTrue(win.doh_label.isVisible())
        self.assertTrue(win.error_label.isVisible())
        win.close()

    def test_traffic_rate_is_derived_from_byte_counters(self):
        gui, win = self.make_window()
        win.render_status(None)
        win.render_status(FAKE_STATUS)  # ilk örnek yalnızca başlangıç noktasıdır
        self.assertEqual(win.rate_label.text(), "")
        t0, up, down = win._last_bytes
        win._last_bytes = (t0 - 2.0, up, down)
        more = dict(FAKE_STATUS, stats=dict(FAKE_STATUS["stats"], bytes_down=FAKE_STATUS["stats"]["bytes_down"] + 40960,
                                            bytes_up=FAKE_STATUS["stats"]["bytes_up"] + 81920))
        win.render_status(more)
        self.assertIn("↓ 20.0 KB/s", win.rate_label.text())
        self.assertIn("↑ 40.0 KB/s", win.rate_label.text())
        self.assertAlmostEqual(win.spark._down[-1], 20480.0, delta=600)
        # sayaç geriye giderse (servis yeniden başladı) negatif hız çıkmaz
        t0, up, down = win._last_bytes
        win._last_bytes = (t0 - 2.0, up, down)
        win.render_status(FAKE_STATUS)
        self.assertEqual(win.spark._down[-1], 0.0)
        win.render_status(None)  # durunca grafik sıfırlanır
        self.assertEqual(win.rate_label.text(), "")
        self.assertEqual(max(win.spark._down), 0.0)
        win.close()

    def test_log_filter_and_follow(self):
        gui, win = self.make_window()
        log = os.path.join(self._tmp.name, "dpigec.log")
        with open(log, "w", encoding="utf-8") as fh:
            fh.write("INFO started\nWARNING network changed\nINFO split ok\n")
        old = gui.LOG_FILE
        gui.LOG_FILE = log
        try:
            win._refresh_log()
            self.assertEqual(win.log_view.toPlainText().count("\n"), 3)
            win.log_filter.setText("warning")
            self.assertEqual(win.log_view.toPlainText().strip(), "WARNING network changed")
            win.log_filter.setText("nomatch")
            self.assertIn("No log line matches", win.log_view.toPlainText())
            win.log_filter.setText("")
            self.assertIn("INFO started", win.log_view.toPlainText())
        finally:
            gui.LOG_FILE = old
        win.close()

    def test_welcome_banner_is_dismissed_once_and_remembered(self):
        from dpigec import i18n
        gui, win = self.make_window()
        win.show()
        self.assertTrue(win.welcome.isVisible())
        win._dismiss_welcome()
        self.assertFalse(win.welcome.isVisible())
        self.assertIs(i18n.load_prefs().get("welcome_dismissed"), True)
        win.close()
        gui, win2 = self.make_window()
        win2.show()
        self.assertFalse(win2.welcome.isVisible())
        win2.close()

    def test_animation_switch_is_saved_and_applied(self):
        from dpigec import i18n
        from dpigec.gui import widgets
        gui, win = self.make_window()
        win.anim_switch.setChecked(True)
        win._set_animations(False)
        self.assertFalse(widgets.motion())
        self.assertIs(i18n.load_prefs().get("animations"), False)
        win._set_animations(True)
        self.assertTrue(widgets.motion())
        self.assertIs(i18n.load_prefs().get("animations"), True)
        win.close()

    def test_animation_switch_is_not_part_of_the_config_form(self):
        gui, win = self.make_window()
        self.assertNotIn(win.anim_switch, win._form_widgets())
        self.assertNotIn(win.log_follow, win._form_widgets())
        win.close()

    def test_keyboard_shortcuts_switch_pages(self):
        gui, win = self.make_window()
        win.show()
        from PyQt6.QtGui import QShortcut
        by_key = {s.key().toString(): s for s in win.findChildren(QShortcut)}
        for i in range(5):
            by_key[f"Ctrl+{i + 1}"].activated.emit()
            self.assertEqual(win.tabs.currentIndex(), i)
        self.assertIn("Ctrl+S", by_key)
        self.assertIn("Ctrl+Q", by_key)
        win.close()

    def test_ctrl_s_only_saves_on_the_settings_page(self):
        gui, win = self.make_window()
        saved = []
        win._save = lambda *a, **k: saved.append(1)
        win.tabs.setCurrentIndex(gui.PAGE_LOG)
        win._save_shortcut()
        self.assertEqual(saved, [])
        win.tabs.setCurrentIndex(gui.PAGE_SETTINGS)
        win._save_shortcut()
        self.assertEqual(saved, [1])
        win._busy = True
        win._save_shortcut()
        self.assertEqual(saved, [1])
        win._busy = False
        win.close()

    def test_probe_shows_busy_bar_and_hides_it_when_done(self):
        gui, win = self.make_window()
        win.show()
        win.tabs.setCurrentIndex(gui.PAGE_TEST)
        old = gui.run_ctl
        gui.run_ctl = lambda args, stdin=None: {"ok": True, "best": None, "results": [
            {"preset": "none", "label": "No splitting (baseline)", "ok": 0, "total": 1, "avg_ms": None,
             "domains": {"x.org": {"ok": False, "err": "closed"}}}]}
        try:
            win._probe()
            self.assertTrue(win.probe_busy.running())
            self.assertTrue(self._wait(lambda: not win.probe_busy.running()))
        finally:
            gui.run_ctl = old
        self.assertEqual(win.table.rowCount(), 1)
        win.close()

    def test_collapsed_advanced_settings_still_feed_the_config(self):
        gui, win = self.make_window()
        from dpigec.gui.widgets import CollapsibleSection
        sections = win.findChildren(CollapsibleSection)
        self.assertEqual([s.expanded() for s in sections], [True, True, False, False, False])
        win.f_socks_port.setValue(2222)  # kapalı bölümdeki alan da değerini korur
        self.assertEqual(win._form_to_config()["socks_port"], 2222)
        win.close()


class PrefsTests(unittest.TestCase):
    def test_saving_language_keeps_other_preferences(self):
        from dpigec import i18n
        old = os.environ.get("XDG_CONFIG_HOME")
        with tempfile.TemporaryDirectory() as d:
            os.environ["XDG_CONFIG_HOME"] = d
            try:
                self.assertTrue(i18n.save_prefs(animations=False, welcome_dismissed=True))
                self.assertTrue(i18n.save_language("tr"))
                with open(i18n.pref_path(), encoding="utf-8") as fh:
                    self.assertEqual(json.load(fh), {"animations": False, "welcome_dismissed": True, "lang": "tr"})
                self.assertEqual(i18n.saved_language(), "tr")
                self.assertFalse(i18n.save_language("../x"))
                self.assertEqual(i18n.load_prefs()["lang"], "tr")
            finally:
                if old is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = old

    def test_corrupt_or_non_object_prefs_file_is_ignored(self):
        from dpigec import i18n
        old = os.environ.get("XDG_CONFIG_HOME")
        with tempfile.TemporaryDirectory() as d:
            os.environ["XDG_CONFIG_HOME"] = d
            try:
                os.makedirs(os.path.dirname(i18n.pref_path()))
                for junk in ("not json", "[1, 2]", "null"):
                    with open(i18n.pref_path(), "w", encoding="utf-8") as fh:
                        fh.write(junk)
                    self.assertEqual(i18n.load_prefs(), {})
                    self.assertIsNone(i18n.saved_language())
                self.assertTrue(i18n.save_prefs(animations=True))
                self.assertEqual(i18n.load_prefs(), {"animations": True})
            finally:
                if old is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = old


if __name__ == "__main__":
    unittest.main()
