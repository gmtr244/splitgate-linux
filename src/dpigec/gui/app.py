# Copyright (C) 2026 Project Gamers
# SPDX-License-Identifier: GPL-3.0-or-later
"""SplitGate ana penceresi (PyQt6)."""
from __future__ import annotations

import json
import os
import socket
import ssl
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFormLayout, QFrame,
                             QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu,
                             QMessageBox, QPlainTextEdit, QScrollArea, QSizePolicy, QSpinBox,
                             QSystemTrayIcon, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget, QHeaderView, QAbstractItemView)

from .. import __version__
from ..i18n import (DEFAULT_LANGUAGE, LANGUAGES, _, init as init_language, language, load_prefs,
                    normalize_language, save_language, save_prefs, saved_language, set_language)
from ..config import (LOG_FILE, PRESETS, SYSTEM_CONFIG, ConfigError, default_config, load_config,
                      normalize)
from ..ctlclient import run_ctl, unit_active, unit_enabled
from ..probe import diagnose, verdict_text
from . import single
from ..status import read_status
from .widgets import (ACCENT, AnimButton, BusyBar, CollapsibleSection, Pages, PowerButton, Sparkline,
                      StatCard, Switch, Toast, fade_in, set_motion, tween)

GREEN, GREY, AMBER, RED = "#2ea043", "#6e7681", "#d29922", "#f85149"
ICON_PATH = Path(__file__).parent / "resources" / "dpigec.svg"

MODE_ITEMS = [("transparent", "System-wide (transparent, recommended)"),
              ("socks", "Local SOCKS5/HTTP proxy (127.0.0.1)")]
FILTER_ITEMS = [("all", "All sites"), ("include", "Only listed sites"),
                ("exclude", "All except listed")]
DNS_ITEMS = [("cloudflare", "Cloudflare (1.1.1.1)"), ("google", "Google (8.8.8.8)"),
             ("quad9", "Quad9 (9.9.9.9)"), ("custom", "Custom provider")]
LOG_LEVELS = [("info", "Info"), ("debug", "Verbose (debug)"), ("warning", "Warning"),
              ("error", "Errors only")]

PAGE_STATUS, PAGE_SETTINGS, PAGE_TEST, PAGE_LOG, PAGE_ABOUT = range(5)

QSS = """
QMainWindow, QDialog { background: palette(window); }
QFrame#sidebar { background: palette(alternate-base); border: none; border-right: 1px solid rgba(128, 128, 128, 120); }
QFrame#footer { background: palette(alternate-base); border: none; border-top: 1px solid rgba(128, 128, 128, 120); }
QLabel#brand { font-size: 16px; font-weight: 800; }
QLabel#version { color: palette(placeholder-text); font-size: 11px; }
QLabel#pageTitle { font-size: 24px; font-weight: 800; }
QLabel#hero { font-size: 26px; font-weight: 800; }
QLabel#muted { color: palette(placeholder-text); }
QLabel#groupTitle { font-size: 11px; font-weight: 700; color: palette(placeholder-text); }
QLabel#dirty { color: #d29922; font-weight: 600; }
QLabel#chip { background: #2563eb; color: white; border-radius: 9px; padding: 2px 10px; font-weight: 600; }
QGroupBox { border: 1px solid rgba(128, 128, 128, 120); border-radius: 12px; margin-top: 14px; padding: 14px 12px 10px 12px;
            background: palette(alternate-base); }
QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 6px; font-weight: 700; }
QFrame#card, QFrame#panel { border: 1px solid rgba(128, 128, 128, 120); border-radius: 12px; background: palette(alternate-base); }
QFrame#card:hover { border-color: #2563eb; }
QLabel#cardValue { font-size: 20px; font-weight: 700; }
QLabel#cardTitle { color: palette(placeholder-text); font-size: 11px; }
QFrame#banner { border: 1px solid #2563eb; border-radius: 12px; background: palette(alternate-base); }
QFrame#section { border: 1px solid rgba(128, 128, 128, 120); border-radius: 12px; background: palette(alternate-base); }
QToolButton#sectionHeader { border: none; background: transparent; font-weight: 700; text-align: left;
                            padding: 11px 14px; }
QToolButton#sectionHeader:hover { color: #2563eb; }
QWidget#sectionBody { background: transparent; }
QFrame#toast { background: #1f2937; border-radius: 10px; border-left: 4px solid #2563eb; }
QFrame#toast QLabel { color: white; font-weight: 600; }
QPushButton { padding: 7px 16px; border-radius: 8px; border: 1px solid rgba(128, 128, 128, 120); background: palette(button); }
QPushButton:hover { border-color: #2563eb; }
QPushButton:pressed { background: palette(midlight); }
QPushButton:disabled { color: palette(placeholder-text); }
QPushButton#primary { font-weight: 600; padding: 8px 22px; color: white; background: #2563eb;
                      border: 1px solid #1d4ed8; }
QPushButton#primary:hover { background: #3b82f6; }
QPushButton#primary:pressed { background: #1d4ed8; }
QPushButton#primary:disabled { background: rgba(128, 128, 128, 120); border-color: rgba(128, 128, 128, 120); color: palette(placeholder-text); }
QLineEdit, QPlainTextEdit, QSpinBox, QComboBox { border: 1px solid rgba(128, 128, 128, 120); border-radius: 8px; padding: 5px 9px; }
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus { border: 1px solid #2563eb; }
QTableWidget { border: 1px solid rgba(128, 128, 128, 120); border-radius: 10px; gridline-color: rgba(128, 128, 128, 120);
               alternate-background-color: palette(alternate-base); }
QHeaderView::section { padding: 7px 9px; border: none; border-bottom: 1px solid rgba(128, 128, 128, 120);
                       background: palette(alternate-base); font-weight: 600; }
QScrollArea { background: transparent; }
QStatusBar { color: palette(placeholder-text); }
"""


def app_icon() -> QIcon:
    icon = QIcon(str(ICON_PATH)) if ICON_PATH.exists() else QIcon()
    if icon.isNull():  # SVG eklentisi yoksa basit bir simge çiz
        pm = QPixmap(128, 128)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(ACCENT))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(14, 8, 100, 112, 28, 28)
        p.setPen(QColor("white"))
        f = QFont()
        f.setBold(True)
        f.setPixelSize(44)
        p.setFont(f)
        p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "DPI")
        p.end()
        icon = QIcon(pm)
    return icon


def fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def check_style(check: str):
    """Ana durum rengi ve metni: yalnızca gerçek kontrol başarılıysa yeşil "Çalışıyor"."""
    if check == "working":
        return GREEN, _("Working")
    if check == "not_blocked":
        return GREEN, _("Connected, sites not blocked")
    if check == "failed":
        return RED, _("Not working on this network")
    if check == "checking":
        return AMBER, _("Checking…")
    return AMBER, _("Connected (not checked yet)")


def fmt_duration(sec: int) -> str:
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    return _("{h}h {m:02d}m", h=h, m=m) if h else _("{m}m {s:02d}s", m=m, s=s)


def combo_with(items) -> QComboBox:
    c = QComboBox()
    for data, label in items:
        c.addItem(_(label), data)
    return c


def set_combo(c: QComboBox, data) -> None:
    i = c.findData(data)
    if i >= 0:
        c.setCurrentIndex(i)


def muted(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("muted")
    lab.setWordWrap(True)
    return lab


# --------------------------------------------------------------------------
# Arka plan işçileri
# --------------------------------------------------------------------------
class CtlWorker(QThread):
    done = pyqtSignal(dict)

    def __init__(self, args: List[str], stdin_text: Optional[str] = None, parent=None) -> None:
        super().__init__(parent)
        self.args, self.stdin_text = args, stdin_text

    def run(self) -> None:
        self.done.emit(run_ctl(self.args, self.stdin_text))


class QuickTestWorker(QThread):
    done = pyqtSignal(dict)

    def __init__(self, host: str, parent=None) -> None:
        super().__init__(parent)
        self.host = host

    def run(self) -> None:
        t0 = time.monotonic()
        try:
            with socket.create_connection((self.host, 443), timeout=8) as raw:
                ctx = ssl.create_default_context()
                with ctx.wrap_socket(raw, server_hostname=self.host) as tls:
                    ver = tls.version()
            self.done.emit({"ok": True, "ms": int((time.monotonic() - t0) * 1000), "tls": ver})
        except Exception as e:  # noqa: BLE001
            self.done.emit({"ok": False, "err": f"{type(e).__name__}: {e}"})


# --------------------------------------------------------------------------
# Ana pencere
# --------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("SplitGate")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(900, 620)
        self.resize(1020, 740)
        self._workers: List[QThread] = []
        self._busy = False
        self._pending_since = 0.0
        self._pending_target: Optional[bool] = None
        self._status: Optional[Dict[str, Any]] = None
        self._last_probe: Optional[Dict[str, Any]] = None
        self._quitting = False
        self._loading = False
        self._dirty = False
        self._intro_played = False
        self._last_bytes: Optional[tuple] = None
        self._custom_cache = {"positions": "1, midsld", "delay": 1, "oob": False}

        self._build_ui()
        self._build_tray()
        self._build_shortcuts()
        self._load_form(self._read_config())
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._tick_count = 0
        self._tick()

    # ---- iskelet -----------------------------------------------------------
    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        self.tabs = Pages()
        lay.addWidget(self.tabs)
        self._build_sidebar()
        self.tabs.addTab(self._build_status_tab(), _("Status"), "home")
        self.tabs.addTab(self._build_settings_tab(), _("Settings"), "settings")
        self.tabs.addTab(self._build_test_tab(), _("Test"), "test")
        self.tabs.addTab(self._build_log_tab(), _("Log"), "log")
        self.tabs.addTab(self._build_about_tab(), _("About"), "about")
        self.tabs.currentChanged.connect(lambda _i: self._refresh_log())
        self._connect_dirty()
        self.toast = Toast(root)
        self.statusBar().showMessage(_("Ready"))
        self._intro_played = False

    def _build_shortcuts(self) -> None:
        for i in range(5):
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self, activated=lambda i=i: self.tabs.setCurrentIndex(i))
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self._save_shortcut)
        QShortcut(QKeySequence("Ctrl+Q"), self, activated=self._quit)

    def _save_shortcut(self) -> None:
        if self.tabs.currentIndex() == PAGE_SETTINGS and not self._busy:
            self._save()

    def _warn(self, text: str, title: str = "SplitGate") -> None:
        box = QMessageBox(QMessageBox.Icon.Warning, title, text, QMessageBox.StandardButton.Ok, self)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.exec()

    def _notify(self, text: str, kind: str = "ok") -> None:
        self.toast.show_message(text, kind)

    def _change_language(self, code: str) -> None:
        if code == language():
            return
        if self._busy or self._workers:
            self.statusBar().showMessage(_("Please wait for the current operation to finish."), 5000)
            return
        try:
            cfg = self._form_to_config()
        except (ConfigError, ValueError):
            # geçersiz bir değer varken yeniden kurmak yazılanı sessizce silerdi
            self.statusBar().showMessage(_("Fix the invalid setting before changing the language."), 6000)
            return
        set_language(code)
        saved = save_language(code)
        orig = self._base_cfg
        index = self.tabs.currentIndex()
        self._build_ui()
        self._load_form(cfg)
        self._base_cfg = orig  # kaydedilmemiş değişiklikler dil değişince "kaydedilmiş" sayılmasın
        self._mark_dirty()
        self.tabs.setCurrentIndex(index)
        self._retranslate_tray()
        self._tick()
        self._refresh_log()
        if not saved:
            self.statusBar().showMessage(_("could not save the language choice"), 8000)

    def _build_sidebar(self) -> None:
        icon = QLabel()
        icon.setPixmap(app_icon().pixmap(44, 44))
        brand = QLabel("SplitGate")
        brand.setObjectName("brand")
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(icon)
        row.addWidget(brand, 1)
        self.tabs.top.addLayout(row)
        self.lang_btn = AnimButton("🌐 " + LANGUAGES[language()])
        lang_menu = QMenu(self.lang_btn)
        for code, name in LANGUAGES.items():
            act = lang_menu.addAction(name)
            act.triggered.connect(lambda _checked=False, c=code: self._change_language(c))
        self.lang_btn.setMenu(lang_menu)
        self.tabs.bottom.addWidget(self.lang_btn)
        ver = QLabel(f"v{__version__}")
        ver.setObjectName("version")
        ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tabs.bottom.addWidget(ver)

    def _page(self, title: str, subtitle: str = "") -> tuple:
        """Kaydırılabilir bir sayfa kabuğu; (dış parça, içerik yerleşimi) döner."""
        outer = QWidget()
        ol = QVBoxLayout(outer)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(30, 24, 30, 20)
        lay.setSpacing(12)
        if title:
            head = QLabel(title)
            head.setObjectName("pageTitle")
            lay.addWidget(head)
            if subtitle:
                lay.addWidget(muted(subtitle))
            lay.addSpacing(4)
        scroll.setWidget(inner)
        ol.addWidget(scroll, 1)
        return outer, lay

    def _build_status_tab(self) -> QWidget:
        w, lay = self._page("")
        self.welcome = QFrame()
        self.welcome.setObjectName("banner")
        wl = QVBoxLayout(self.welcome)
        wl.setContentsMargins(16, 12, 16, 12)
        wt = QLabel(_("New here? Get connected in 3 steps"))
        wt.setStyleSheet("font-weight: 700;")
        wl.addWidget(wt)
        wl.addWidget(muted(_("1. Open the Test page and press “Try strategies”.\n"
                             "2. Press “Apply recommended”.\n"
                             "3. Come back here and press Start.")))
        wrow = QHBoxLayout()
        go = AnimButton(_("Open the Test page"))
        go.clicked.connect(lambda: self.tabs.setCurrentIndex(PAGE_TEST))
        dismiss = AnimButton(_("Got it"))
        dismiss.clicked.connect(self._dismiss_welcome)
        wrow.addWidget(go)
        wrow.addWidget(dismiss)
        wrow.addStretch(1)
        wl.addLayout(wrow)
        self.welcome.setVisible(not load_prefs().get("welcome_dismissed"))
        lay.addWidget(self.welcome)

        self.power = PowerButton()
        self.power.setText(_("Start"))
        self.power.setAccessibleName(_("Start / stop"))
        self.power.clicked.connect(self._toggle)
        lay.addWidget(self.power, 0, Qt.AlignmentFlag.AlignHCenter)
        self.state_label = QLabel(_("Stopped"))
        self.state_label.setObjectName("hero")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.state_label)
        self.state_detail = QLabel("")
        self.state_detail.setObjectName("muted")
        self.state_detail.setTextFormat(Qt.TextFormat.PlainText)
        self.state_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_detail.setWordWrap(True)
        lay.addWidget(self.state_detail)
        self.hint_btn = AnimButton(_("Find a working method"), primary=True)
        self.hint_btn.clicked.connect(lambda: self.tabs.setCurrentIndex(PAGE_TEST))
        self.hint_btn.hide()
        lay.addWidget(self.hint_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        self.doh_label = QLabel("")
        self.doh_label.setObjectName("muted")
        self.doh_label.setTextFormat(Qt.TextFormat.PlainText)
        self.doh_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.doh_label)
        self.error_label = QLabel("")
        self.error_label.setTextFormat(Qt.TextFormat.PlainText)
        self.error_label.setWordWrap(True)
        self.error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.error_label.setStyleSheet(f"color: {RED};")
        lay.addWidget(self.error_label)

        grid = QGridLayout()
        grid.setSpacing(10)
        self.cards = {
            "conn": StatCard(_("Active / total connections")), "split": StatCard(_("Split TLS / HTTP")),
            "pass": StatCard(_("Passed through")), "traffic": StatCard(_("Traffic ↑ / ↓")),
            "dns": StatCard(_("DNS queries (DoH)")), "cache": StatCard(_("DNS from cache")),
            "uptime": StatCard(_("Uptime")), "err": StatCard(_("Error count"), flash_color=RED),
        }
        for i, c in enumerate(self.cards.values()):
            grid.addWidget(c, i // 4, i % 4)
        lay.addLayout(grid)

        live = QFrame()
        live.setObjectName("panel")
        ll = QVBoxLayout(live)
        ll.setContentsMargins(14, 10, 14, 10)
        head = QHBoxLayout()
        title = QLabel(_("Live traffic"))
        title.setStyleSheet("font-weight: 700;")
        self.rate_label = QLabel("")
        self.rate_label.setTextFormat(Qt.TextFormat.RichText)
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.rate_label)
        ll.addLayout(head)
        self.spark = Sparkline()
        ll.addWidget(self.spark)
        lay.addWidget(live)

        box = QGroupBox(_("Startup"))
        bl = QVBoxLayout(box)
        self.autostart = Switch(_("Start automatically at boot"))
        self.autostart.clicked.connect(self._autostart_clicked)
        bl.addWidget(self.autostart)
        bl.addWidget(muted(_("The service runs at the system level. Closing this window does not stop it; "
                             "use the button above, or Quit in the tray menu, to stop it.")))
        lay.addWidget(box)
        lay.addStretch(1)
        return w

    def _dismiss_welcome(self) -> None:
        save_prefs(welcome_dismissed=True)
        self.welcome.hide()

    def _section(self, title: str, expanded: bool) -> tuple:
        sec = CollapsibleSection(title, expanded)
        f = QFormLayout(sec.body)
        f.setContentsMargins(16, 2, 16, 14)
        f.setHorizontalSpacing(14)
        f.setVerticalSpacing(10)
        f.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        f.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        return sec, f

    def _build_settings_tab(self) -> QWidget:
        outer, lay = self._page(_("Settings"), _("Changes are applied when you press “Save and apply”."))

        # Strateji
        sec, f = self._section(_("Splitting strategy"), True)
        self.f_preset = QComboBox()
        for key, p in PRESETS.items():
            self.f_preset.addItem(_(p["label"]), key)
        self.f_preset.addItem(_("Custom"), "custom")
        self.f_preset.currentIndexChanged.connect(self._preset_changed)
        self.f_positions = QLineEdit()
        self.f_positions.setPlaceholderText(_("e.g. 1,midsld  (number, sni, midsld, sniend, host, method)"))
        self.f_delay = QSpinBox()
        self.f_delay.setRange(0, 200)
        self.f_delay.setSuffix(" ms")
        self.f_oob = Switch(_("Send an OOB byte after the first piece (experimental)"))
        self.f_hostcase = Switch(_("Mix the letter case of the HTTP 'Host:' header"))
        self.f_httpsplit = Switch(_("Also split plain HTTP requests"))
        f.addRow(_("Preset"), self.f_preset)
        find = AnimButton(_("Find the best method for me"))
        find.clicked.connect(lambda: self.tabs.setCurrentIndex(PAGE_TEST))
        f.addRow(muted(_("Not sure which preset works on your network? SplitGate can test them for you.")))
        find_row = QHBoxLayout()
        find_row.addWidget(find)
        find_row.addStretch(1)
        f.addRow(find_row)
        f.addRow(_("Split positions"), self.f_positions)
        f.addRow(_("Delay between pieces"), self.f_delay)
        f.addRow(self.f_oob)
        f.addRow(self.f_hostcase)
        f.addRow(self.f_httpsplit)
        lay.addWidget(sec)

        # DNS
        sec, f = self._section(_("DNS (DoH against DNS poisoning)"), True)
        self.f_dns_on = Switch(_("Resolve DNS queries over encrypted DoH"))
        self.f_dns_on.clicked.connect(self._update_enabled)
        self.f_dns_primary = combo_with(DNS_ITEMS)
        self.f_dns_primary.currentIndexChanged.connect(self._update_enabled)
        self.f_dns_host = QLineEdit()
        self.f_dns_host.setPlaceholderText(_("dns.example.net"))
        self.f_dns_ips = QLineEdit()
        self.f_dns_ips.setPlaceholderText("9.9.9.10, 149.112.112.10")
        self.f_dns_path = QLineEdit("/dns-query")
        self.f_dns_cache = QSpinBox()
        self.f_dns_cache.setRange(0, 86400)
        self.f_dns_cache.setSuffix(_(" s"))
        f.addRow(self.f_dns_on)
        f.addRow(_("Primary provider"), self.f_dns_primary)
        f.addRow(_("Custom: host"), self.f_dns_host)
        f.addRow(_("Custom: IP addresses"), self.f_dns_ips)
        f.addRow(_("Custom: path"), self.f_dns_path)
        f.addRow(_("Cache duration (upper limit)"), self.f_dns_cache)
        lay.addWidget(sec)

        adv = QLabel(_("Advanced").upper())
        adv.setObjectName("groupTitle")
        lay.addSpacing(6)
        lay.addWidget(adv)

        # Çalışma modu
        sec, f = self._section(_("Operating mode"), False)
        self.f_mode = combo_with(MODE_ITEMS)
        self.f_mode.currentIndexChanged.connect(self._update_enabled)
        self.f_socks_port = QSpinBox()
        self.f_socks_port.setRange(1024, 65535)
        self.f_gateway = Switch(_("Also process traffic passing through this computer (shared)"))
        self.f_ipv6 = Switch(_("Use IPv6"))
        f.addRow(_("Mode"), self.f_mode)
        f.addRow(_("SOCKS5/HTTP port"), self.f_socks_port)
        f.addRow(self.f_gateway)
        f.addRow(self.f_ipv6)
        lay.addWidget(sec)

        # Trafik
        sec, f = self._section(_("Traffic"), False)
        self.f_ports = QLineEdit()
        self.f_ports.setPlaceholderText("80, 443")
        self.f_quic = Switch(_("Block QUIC/HTTP3 (browsers fall back to TCP; required for splitting)"))
        self.f_filter = combo_with(FILTER_ITEMS)
        self.f_filter.currentIndexChanged.connect(self._update_enabled)
        self.f_domains = QPlainTextEdit()
        self.f_domains.setPlaceholderText(_("One domain per line (e.g. discord.com)"))
        self.f_domains.setFixedHeight(80)
        f.addRow(_("Ports to redirect"), self.f_ports)
        f.addRow(self.f_quic)
        f.addRow(_("Sites to apply splitting to"), self.f_filter)
        f.addRow(_("Domain list"), self.f_domains)
        lay.addWidget(sec)

        # Günlük
        sec, f = self._section(_("Log"), False)
        self.f_loglevel = combo_with(LOG_LEVELS)
        self.f_logdomains = Switch(_("Write visited domain names to the log (privacy risk)"))
        f.addRow(_("Detail level"), self.f_loglevel)
        f.addRow(self.f_logdomains)
        lay.addWidget(sec)
        lay.addStretch(1)

        bar = QFrame()
        bar.setObjectName("footer")
        row = QHBoxLayout(bar)
        row.setContentsMargins(30, 10, 30, 10)
        reset = AnimButton(_("Reset to defaults"))
        reset.clicked.connect(lambda: self._load_form(default_config(), keep_base=True))
        self.dirty_label = QLabel("● " + _("Unsaved changes"))
        self.dirty_label.setObjectName("dirty")
        self.dirty_label.hide()
        self.save_btn = AnimButton(_("Save and apply"), primary=True)
        self.save_btn.setToolTip("Ctrl+S")
        self.save_btn.clicked.connect(self._save)
        row.addWidget(reset)
        row.addStretch(1)
        row.addWidget(self.dirty_label)
        row.addWidget(self.save_btn)
        outer.layout().addWidget(bar)
        return outer

    def _build_test_tab(self) -> QWidget:
        w, lay = self._page(_("Test"), _("Find out which method opens the sites that are blocked for you."))
        g = QGroupBox(_("Automatic strategy finder"))
        gl = QVBoxLayout(g)
        gl.addWidget(muted(_("Each strategy is tried on the sites below with a real TLS handshake. "
                             "The result does not depend on system DNS (DoH is used). "
                             "An administrator password is requested.")))
        self.t_domains = QLineEdit()
        self.t_domains.setPlaceholderText("discord.com, x.com, roblox.com")
        gl.addWidget(self.t_domains)
        row = QHBoxLayout()
        self.probe_btn = AnimButton(_("Try strategies"), primary=True)
        self.probe_btn.clicked.connect(self._probe)
        self.apply_best = AnimButton(_("Apply recommended"))
        self.apply_best.setEnabled(False)
        self.apply_best.clicked.connect(self._apply_best)
        row.addWidget(self.probe_btn)
        row.addWidget(self.apply_best)
        row.addStretch(1)
        gl.addLayout(row)
        self.probe_busy = BusyBar()
        gl.addWidget(self.probe_busy)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([_("Strategy"), _("Sites opened"), _("Avg. time")])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(220)
        gl.addWidget(self.table, 1)
        self.probe_msg = QLabel("")
        self.probe_msg.setTextFormat(Qt.TextFormat.PlainText)
        self.probe_msg.setWordWrap(True)
        gl.addWidget(self.probe_msg)
        lay.addWidget(g, 1)

        g = QGroupBox(_("Quick connection test (through the system)"))
        gl = QHBoxLayout(g)
        self.q_host = QLineEdit("discord.com")
        self.q_btn = AnimButton(_("Try"))
        self.q_btn.clicked.connect(self._quick_test)
        self.q_res = QLabel("")
        self.q_res.setTextFormat(Qt.TextFormat.PlainText)
        gl.addWidget(self.q_host, 1)
        gl.addWidget(self.q_btn)
        gl.addWidget(self.q_res, 2)
        lay.addWidget(g)
        return w

    def _build_log_tab(self) -> QWidget:
        w, lay = self._page(_("Log"), _("What the service has been doing (newest at the bottom)."))
        top = QHBoxLayout()
        self.log_filter = QLineEdit()
        self.log_filter.setPlaceholderText(_("Filter log lines…"))
        self.log_filter.setClearButtonEnabled(True)
        self.log_filter.textChanged.connect(lambda _t: self._refresh_log())
        self.log_follow = Switch(_("Follow new lines"))
        self.log_follow.setChecked(True)
        top.addWidget(self.log_filter, 1)
        top.addWidget(self.log_follow)
        lay.addLayout(top)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        mono = QFont("monospace")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.log_view.setFont(mono)
        self.log_view.setMinimumHeight(300)
        lay.addWidget(self.log_view, 1)
        row = QHBoxLayout()
        copy = AnimButton(_("Copy"))
        copy.clicked.connect(self._copy_log)
        row.addStretch(1)
        row.addWidget(copy)
        lay.addLayout(row)
        return w

    def _copy_log(self) -> None:
        QApplication.clipboard().setText(self.log_view.toPlainText())
        self._notify(_("Copied to clipboard"))

    def _build_about_tab(self) -> QWidget:
        w, lay = self._page("")
        head = QHBoxLayout()
        head.setSpacing(16)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(84, 84))
        head.addWidget(logo)
        col = QVBoxLayout()
        col.setSpacing(4)
        name = QLabel("SplitGate")
        name.setObjectName("hero")
        chip = QLabel(f"v{__version__}")
        chip.setObjectName("chip")
        chip.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        col.addStretch(1)
        col.addWidget(name)
        col.addWidget(chip, 0, Qt.AlignmentFlag.AlignLeft)
        col.addStretch(1)
        head.addLayout(col)
        head.addStretch(1)
        lay.addLayout(head)
        lay.addWidget(QLabel(_("A DPI bypass tool for Linux with a GUI, written from scratch.")))

        panel = QFrame()
        panel.setObjectName("panel")
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(16, 14, 16, 14)
        pl.setSpacing(10)
        for text in (_("Splits the TLS ClientHello at the SNI; splits the Host header in plain HTTP."),
                     _("Gets around DNS poisoning with DoH (Cloudflare / Google / Quad9)."),
                     _("Works on all Wi-Fi and wired connections through nftables; "
                       "it can also be used only as a local SOCKS5 proxy."),
                     _("Content is never decrypted; your traffic does not pass through any third-party server.")):
            row = QHBoxLayout()
            row.setSpacing(10)
            tick = QLabel("✔")
            tick.setStyleSheet(f"color: {ACCENT}; font-weight: 700;")
            tick.setAlignment(Qt.AlignmentFlag.AlignTop)
            row.addWidget(tick, 0, Qt.AlignmentFlag.AlignTop)
            lab = QLabel(text)
            lab.setWordWrap(True)
            row.addWidget(lab, 1)
            pl.addLayout(row)
        lay.addWidget(panel)
        lay.addWidget(muted(_("This tool is only meant to get around access blocks; it is not a VPN and does not "
                              "hide your IP address. You are responsible for how you use it.")))
        lic = muted(_("© 2026 Project Gamers — License: GPL-3.0-or-later"))
        lic.setObjectName("about_license")
        lic.setStyleSheet("color: palette(placeholder-text);")
        lay.addWidget(lic)

        box = QGroupBox(_("Interface"))
        bl = QVBoxLayout(box)
        bl.setSpacing(10)
        self.anim_switch = Switch(_("Enable animations"))
        self.anim_switch.setChecked(bool(load_prefs().get("animations", True)))
        self.anim_switch.clicked.connect(self._set_animations)
        bl.addWidget(self.anim_switch)
        bl.addWidget(muted(_("Ctrl+1 … Ctrl+5: switch page\nCtrl+S: save and apply the settings\n"
                             "Ctrl+Q: quit")))
        lay.addWidget(box)
        lay.addStretch(1)
        return w

    def _set_animations(self, on: bool) -> None:
        set_motion(on)
        save_prefs(animations=on)
        self.power.refresh()

    # ---- tepsi -------------------------------------------------------------
    def _build_tray(self) -> None:
        self.tray: Optional[QSystemTrayIcon] = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(app_icon(), self)
        menu = QMenu()
        self._tray_show = QAction(_("Show window"), self)
        self._tray_show.triggered.connect(self._show_window)
        self.tray_toggle = QAction(_("Start"), self)
        self.tray_toggle.triggered.connect(self._toggle)
        self._tray_quit = QAction(_("Quit"), self)
        self._tray_quit.triggered.connect(self._quit)
        menu.addAction(self._tray_show)
        menu.addAction(self.tray_toggle)
        menu.addSeparator()
        menu.addAction(self._tray_quit)
        self._tray_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self._show_window()
            if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()

    def _retranslate_tray(self) -> None:
        if self.tray is None:
            return
        self._tray_show.setText(_("Show window"))
        self._tray_quit.setText(_("Quit"))

    def _show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit(self) -> None:
        if self._busy or self._workers:
            self.statusBar().showMessage(_("Please wait for the current operation to finish."), 5000)
            return
        if read_status() is None and unit_active() is not True:
            self._finish_quit()
            return

        def done(res: Dict[str, Any]) -> None:
            if res.get("ok"):
                self._finish_quit()
            else:
                self._pending_target = None
                self._warn(res.get("msg", _("Operation failed")))

        self._pending_target = False
        self._pending_since = time.monotonic()
        self._run(["stop"], None, done, _("Stopping…"))

    def _finish_quit(self) -> None:
        self._quitting = True
        QApplication.quit()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.tray is not None and self.tray.isVisible() and not self._quitting:
            self.hide()
            event.ignore()
        else:
            event.accept()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._intro_played:
            self._intro_played = True
            for i, card in enumerate(self.cards.values()):
                fade_in(card, 260, delay=80 + i * 45)

    # ---- yapılandırma <-> form --------------------------------------------
    @staticmethod
    def _read_config() -> Dict[str, Any]:
        try:
            return load_config(SYSTEM_CONFIG)
        except ConfigError:
            return default_config()

    def _form_widgets(self) -> List[QWidget]:
        return [v for k, v in vars(self).items() if k.startswith("f_") or k == "t_domains"]

    def _connect_dirty(self) -> None:
        for w in self._form_widgets():
            if isinstance(w, QComboBox):
                w.currentIndexChanged.connect(self._mark_dirty)
            elif isinstance(w, QCheckBox):
                w.toggled.connect(self._mark_dirty)
            elif isinstance(w, QSpinBox):
                w.valueChanged.connect(self._mark_dirty)
            elif isinstance(w, (QLineEdit, QPlainTextEdit)):
                w.textChanged.connect(self._mark_dirty)

    def _mark_dirty(self, *_args) -> None:
        if self._loading:
            return
        try:
            dirty = self._form_to_config() != self._base_cfg
        except (ConfigError, ValueError):
            dirty = True  # geçersiz değer de "kaydedilmemiş değişiklik"tir
        if dirty and not self._dirty:
            self.dirty_label.show()
            fade_in(self.dirty_label, 180)
        elif not dirty:
            self.dirty_label.hide()
        self._dirty = dirty

    def _load_form(self, cfg: Dict[str, Any], keep_base: bool = False) -> None:
        cfg = normalize(cfg)
        st, dns = cfg["strategy"], cfg["dns"]
        self._loading = True
        try:
            set_combo(self.f_mode, cfg["mode"])
            self.f_socks_port.setValue(cfg["socks_port"])
            self.f_gateway.setChecked(cfg["gateway"])
            self.f_ipv6.setChecked(cfg["ipv6"])
            self._custom_cache = {"positions": ", ".join(st["positions"]), "delay": st["delay_ms"],
                                  "oob": st["oob"]}
            set_combo(self.f_preset, st["preset"])
            self._preset_changed()
            self.f_hostcase.setChecked(st["http_host_case"])
            self.f_httpsplit.setChecked(st["http_split"])
            self.f_dns_on.setChecked(dns["enabled"])
            set_combo(self.f_dns_primary, dns["providers"][0])
            self.f_dns_host.setText(dns["custom"]["host"])
            self.f_dns_ips.setText(", ".join(dns["custom"]["ips"]))
            self.f_dns_path.setText(dns["custom"]["path"])
            self.f_dns_cache.setValue(dns["cache_seconds"])
            self.f_ports.setText(", ".join(str(p) for p in cfg["ports"]))
            self.f_quic.setChecked(cfg["quic_block"])
            set_combo(self.f_filter, cfg["domain_filter"]["mode"])
            self.f_domains.setPlainText("\n".join(cfg["domain_filter"]["domains"]))
            set_combo(self.f_loglevel, cfg["log_level"])
            self.f_logdomains.setChecked(cfg["log_domains"])
            self.t_domains.setText(", ".join(cfg["probe_domains"]))
            if not keep_base:
                self._base_cfg = cfg
            self._update_enabled()
        finally:
            self._loading = False
        self._dirty = False
        self.dirty_label.hide()
        self._mark_dirty()

    def _preset_changed(self) -> None:
        key = self.f_preset.currentData()
        custom = key == "custom"
        if custom:
            c = self._custom_cache
            self.f_positions.setText(c["positions"])
            self.f_delay.setValue(c["delay"])
            self.f_oob.setChecked(c["oob"])
        else:
            p = PRESETS[key]
            self.f_positions.setText(", ".join(p["positions"]))
            self.f_delay.setValue(p["delay_ms"])
            self.f_oob.setChecked(p["oob"])
        for wdg in (self.f_positions, self.f_delay, self.f_oob):
            wdg.setEnabled(custom)

    def _update_enabled(self) -> None:
        socks = self.f_mode.currentData() == "socks"
        self.f_socks_port.setEnabled(socks)
        dns_on = self.f_dns_on.isChecked()
        self.f_dns_primary.setEnabled(True)
        custom_dns = self.f_dns_primary.currentData() == "custom"
        for wdg in (self.f_dns_host, self.f_dns_ips, self.f_dns_path):
            wdg.setEnabled(custom_dns)
        self.f_dns_cache.setEnabled(dns_on)
        self.f_domains.setEnabled(self.f_filter.currentData() != "all")

    @staticmethod
    def _split_list(text: str) -> List[str]:
        return [t for t in text.replace(",", " ").replace(";", " ").split() if t]

    def _form_to_config(self) -> Dict[str, Any]:
        base = json.loads(json.dumps(self._base_cfg))  # derin kopya
        primary = self.f_dns_primary.currentData()
        order = [primary] + [p for p in ("cloudflare", "google", "quad9") if p != primary]
        base.update({
            "mode": self.f_mode.currentData(),
            "socks_port": self.f_socks_port.value(),
            "gateway": self.f_gateway.isChecked(),
            "ipv6": self.f_ipv6.isChecked(),
            "ports": [int(p) for p in self._split_list(self.f_ports.text())],
            "quic_block": self.f_quic.isChecked(),
            "log_level": self.f_loglevel.currentData(),
            "log_domains": self.f_logdomains.isChecked(),
            "probe_domains": self._split_list(self.t_domains.text()) or base["probe_domains"],
        })
        base["strategy"].update({
            "preset": self.f_preset.currentData(),
            "http_host_case": self.f_hostcase.isChecked(),
            "http_split": self.f_httpsplit.isChecked(),
        })
        if self.f_preset.currentData() == "custom":
            base["strategy"].update({
                "positions": self._split_list(self.f_positions.text()),
                "delay_ms": self.f_delay.value(),
                "oob": self.f_oob.isChecked(),
            })
        base["dns"].update({
            "enabled": self.f_dns_on.isChecked(),
            "providers": order,
            "cache_seconds": self.f_dns_cache.value(),
            "custom": {"host": self.f_dns_host.text().strip(),
                       "ips": self._split_list(self.f_dns_ips.text()),
                       "path": self.f_dns_path.text().strip() or "/dns-query"},
        })
        base["domain_filter"] = {"mode": self.f_filter.currentData(),
                                 "domains": [d for d in self.f_domains.toPlainText().splitlines() if d.strip()]}
        return normalize(base)

    # ---- komutlar ----------------------------------------------------------
    def _run(self, args: List[str], stdin_text: Optional[str], done: Callable[[Dict[str, Any]], None],
             busy_msg: str) -> None:
        if self._busy:
            return
        self._busy = True
        self.statusBar().showMessage(busy_msg)
        self._update_buttons()
        worker = CtlWorker(args, stdin_text, self)
        self._workers.append(worker)

        def finished(res: Dict[str, Any]) -> None:
            self._busy = False
            if worker in self._workers:
                self._workers.remove(worker)
            self.statusBar().showMessage(res.get("msg", "") or _("Ready"), 8000)
            self._update_buttons()
            done(res)

        worker.done.connect(finished)
        worker.start()

    def _toggle(self) -> None:
        if self._busy:
            return
        running = self._status is not None
        action = "stop" if running else "start"
        self._pending_target = not running
        self._pending_since = time.monotonic()

        def done(res: Dict[str, Any]) -> None:
            if not res.get("ok"):
                self._pending_target = None
                self._warn(res.get("msg", _("Operation failed")))
            self._tick()

        self._run([action], None, done, _("Starting…") if action == "start" else _("Stopping…"))
        self._update_buttons()

    def _autostart_clicked(self, checked: bool) -> None:
        def done(res: Dict[str, Any]) -> None:
            if not res.get("ok"):
                self._warn(res.get("msg", _("Operation failed")))
            self._sync_autostart()

        self._run(["enable" if checked else "disable"], None, done, _("Applying setting…"))

    def _save(self, _checked: bool = False, then: Optional[Callable[[], None]] = None) -> None:
        try:
            cfg = self._form_to_config()
        except (ConfigError, ValueError) as e:
            self._warn(str(e), _("Invalid setting"))
            return
        was_running = self._status is not None

        def done(res: Dict[str, Any]) -> None:
            if not res.get("ok"):
                self._warn(res.get("msg", _("Could not save")))
                return
            self._base_cfg = cfg
            self._mark_dirty()
            self._notify(_("Settings saved"))
            if was_running:
                self._pending_target = True
                self._pending_since = time.monotonic()
                self._run(["restart"], None,
                          lambda r: self._warn(r.get("msg", ""))
                          if not r.get("ok") else None, _("Restarting the service…"))
            if then:
                then()

        self._run(["apply-config"], json.dumps(cfg), done, _("Saving settings…"))

    def _probe(self) -> None:
        domains = self._split_list(self.t_domains.text())
        if not domains:
            QMessageBox.information(self, "SplitGate", _("Enter at least one domain first."))
            return
        self.table.setRowCount(0)
        self.apply_best.setEnabled(False)
        self.probe_msg.setText(_("Trying… (this can take up to about a minute)"))
        self.probe_busy.start()

        def done(res: Dict[str, Any]) -> None:
            self.probe_busy.stop()
            if not res.get("ok"):
                self.probe_msg.setText(res.get("msg", _("Test failed")))
                return
            self._last_probe = res
            self._fill_probe_table(res)

        self._run(["probe"], json.dumps(domains), done, _("Trying strategies…"))

    def _fill_probe_table(self, res: Dict[str, Any]) -> None:
        rows = res["results"]
        best = res.get("best")
        self.table.setRowCount(len(rows))
        best_rows = []
        for i, r in enumerate(rows):
            avg = f"{r['avg_ms']} ms" if r["avg_ms"] is not None else "–"
            items = [QTableWidgetItem(_(r["label"])), QTableWidgetItem(f"{r['ok']}/{r['total']}"),
                     QTableWidgetItem(avg)]
            tip = "\n".join(f"{d}: " + (f"{v['ms']} ms" if v["ok"] else v.get("err", _("error")))
                            for d, v in r["domains"].items())
            for j, it in enumerate(items):
                it.setToolTip(tip)
                self.table.setItem(i, j, it)
            if r["preset"] == best:
                best_rows.append(i)
        if best_rows:
            def paint(v) -> None:
                # satırlar animasyon sürerken silinebilir (yeni test); öğeleri her karede yeniden bul
                for i in best_rows:
                    for j in range(3):
                        it = self.table.item(i, j)
                        if it is not None:
                            it.setBackground(QColor(46, 160, 67, int(v)))

            tween(self.table, 0, 70, 700, paint)
        if best:
            label = _(next(r["label"] for r in rows if r["preset"] == best))
            self.probe_msg.setText(_("Recommended: {label}.{extra}", label=label, extra=""))
            self.apply_best.setEnabled(True)
        else:
            self.probe_msg.setText(verdict_text(diagnose(rows)))

    def _apply_best(self) -> None:
        if not self._last_probe or not self._last_probe.get("best"):
            return
        set_combo(self.f_preset, self._last_probe["best"])
        self._save()

    def _quick_test(self) -> None:
        host = self.q_host.text().strip()
        if not host:
            return
        self.q_btn.setEnabled(False)
        self.q_res.setText(_("trying…"))
        worker = QuickTestWorker(host, self)
        self._workers.append(worker)

        def done(res: Dict[str, Any]) -> None:
            if worker in self._workers:
                self._workers.remove(worker)
            self.q_btn.setEnabled(True)
            if res["ok"]:
                self.q_res.setText(_("✔ opened ({ms} ms, {tls})", ms=res["ms"], tls=res["tls"]))
                self.q_res.setStyleSheet(f"color: {GREEN};")
            else:
                self.q_res.setText(f"✖ {res['err'][:70]}")
                self.q_res.setStyleSheet(f"color: {RED};")
            fade_in(self.q_res, 220)

        worker.done.connect(done)
        worker.start()

    # ---- durum yenileme ----------------------------------------------------
    def _sync_autostart(self) -> None:
        val = unit_enabled()
        self.autostart.blockSignals(True)
        self.autostart.setEnabled(val is not None)
        self.autostart.setChecked(bool(val))
        self.autostart.blockSignals(False)

    def _update_buttons(self) -> None:
        for b in (self.power, self.save_btn, self.probe_btn, self.autostart):
            b.setEnabled(not self._busy)

    def _tick(self) -> None:
        self._tick_count += 1
        self._status = read_status()
        self.render_status(self._status)
        if self._tick_count % 5 == 1:
            self._sync_autostart()
        if self.tabs.currentIndex() == PAGE_LOG and self._tick_count % 2 == 0:
            self._refresh_log()

    @staticmethod
    def _set_line(label: QLabel, text: str) -> None:
        label.setText(text)
        label.setVisible(bool(text))

    def _update_rate(self, st: Optional[Dict[str, Any]]) -> None:
        """Toplam bayt sayaçlarından anlık hız çıkarır ve grafiğe işler."""
        if not st:
            if self._last_bytes is not None:
                self._last_bytes = None
                self.spark.clear()
                self.rate_label.setText("")
            return
        s = st["stats"]
        now, up, down = time.monotonic(), s["bytes_up"], s["bytes_down"]
        if self._last_bytes is None:
            self._last_bytes = (now, up, down)
            return
        t0, up0, down0 = self._last_bytes
        if now - t0 < 0.5:  # aynı saniyede art arda çağrılırsa yanlış hız çıkmasın
            return
        dt = now - t0
        rate_up, rate_down = max(0.0, (up - up0) / dt), max(0.0, (down - down0) / dt)
        self._last_bytes = (now, up, down)
        self.spark.push(rate_down, rate_up)
        self.rate_label.setText(
            f'<span style="color:{Sparkline.COLORS[0]}">●</span> ↓ {fmt_bytes(rate_down)}/s &nbsp; '
            f'<span style="color:{Sparkline.COLORS[1]}">●</span> ↑ {fmt_bytes(rate_up)}/s')

    def render_status(self, st: Optional[Dict[str, Any]]) -> None:
        pending = (self._pending_target is not None
                   and time.monotonic() - self._pending_since < 10
                   and (self._pending_target == (st is None)))
        if self._pending_target is not None and not pending:
            self._pending_target = None
        if st:
            if st.get("state") == "stopping":
                color, text = AMBER, _("Stopping…")
            else:
                # Eski durum dosyalarında "check" yoktur: kontrol edilmemiş sayılır (yeşil olmaz).
                color, text = check_style(st.get("check", "idle") if st.get("state") == "running" else "idle")
            btn = _("Stop")
            mode = _("system-wide") if st["mode"] == "transparent" else _("local SOCKS5/HTTP proxy")
            preset = _(PRESETS[st["preset"]]["label"]) if st["preset"] in PRESETS else _("custom")
            net = st.get("network")
            detail = _("Mode: {mode} · Strategy: {preset}", mode=mode, preset=preset)
            if net:
                detail += " · " + net
            if st.get("check") == "failed":
                detail += "\n" + verdict_text(st.get("verdict", ""))
            self.state_detail.setText(detail)
            s = st["stats"]
            self.cards["conn"].set(f"{s['conn_active']} / {s['conn_total']}")
            self.cards["split"].set(f"{s['tls_split']} / {s['http_split']}")
            self.cards["pass"].set(str(s["passthrough"]))
            self.cards["traffic"].set(f"{fmt_bytes(s['bytes_up'])} / {fmt_bytes(s['bytes_down'])}")
            self.cards["dns"].set(str(s["dns_queries"]))
            self.cards["cache"].set(str(s["dns_cached"]))
            self.cards["uptime"].set(fmt_duration(s["uptime"]))
            self.cards["err"].set(str(s["errors"]))
            doh = st.get("doh", [])
            self._set_line(self.doh_label, _("DoH providers: ") + ", ".join(
                f"{d['name']} {'✔' if d['ok'] else '✖'}" for d in doh) if doh else "")
            self._set_line(self.error_label,
                           _("Last error: {err}", err=s["last_error"]) if s.get("last_error") else "")
            if st["mode"] == "socks":
                self.state_detail.setText(self.state_detail.text() + _(" · Set it as the proxy in your browser"))
            running = st.get("state") == "running"
            if st.get("state") == "stopping" or (running and st.get("check") == "checking"):
                look = (AMBER, "spin")
            elif color == GREEN:
                look = (GREEN, "pulse")
            elif color == RED:
                look = (RED, "idle")
            else:
                look = (AMBER, "idle")
            self.hint_btn.setVisible(running and st.get("check") == "failed")
        else:
            for c in self.cards.values():
                c.set("–")
            self._set_line(self.doh_label, "")
            self._set_line(self.error_label, "")
            self.hint_btn.hide()
            if pending:
                color, text, btn = AMBER, _("Starting…"), _("Stop")
                look = (AMBER, "spin")
            else:
                color, text, btn = GREY, _("Stopped"), _("Start")
                look = (GREY, "idle")
            self.state_detail.setText("" if pending else _("Your connection flows directly"))
        if st is None and pending:
            btn = _("Please wait…")
        self._update_rate(st)
        self.state_label.setText(text)
        self.state_label.setStyleSheet(f"color: {color};")
        self.power.setText(btn)
        self.power.set_look(look[0], look[1], neutral=st is None and not pending)
        if self.tray is not None:
            self.tray.setToolTip(f"SplitGate — {text}")
            self.tray_toggle.setText(btn if btn != _("Please wait…") else _("Start"))

    def _refresh_log(self) -> None:
        try:
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()[-300:]
            needle = self.log_filter.text().strip().lower()
            if needle:
                lines = [ln for ln in lines if needle in ln.lower()]
            text = "".join(lines)
            if not text and needle:
                text = _("(No log line matches the filter.)")
        except OSError:
            text = _("(The log does not exist yet; it will appear here once the service starts.)")
        if text != self.log_view.toPlainText():
            bar = self.log_view.verticalScrollBar()
            keep = bar.value()
            self.log_view.setPlainText(text)
            bar.setValue(bar.maximum() if self.log_follow.isChecked() else keep)


def choose_language() -> Optional[str]:
    """First-run language picker; returns the chosen code, or None if the dialog was dismissed."""
    dlg = QDialog()
    dlg.setWindowTitle("SplitGate — Language / Dil")
    dlg.setWindowIcon(app_icon())
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(28, 24, 28, 24)
    lay.setSpacing(10)
    logo = QLabel()
    logo.setPixmap(app_icon().pixmap(64, 64))
    logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lay.addWidget(logo)
    title = QLabel("Choose your language")
    title.setObjectName("hero")
    title.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lay.addWidget(title)
    chosen: Dict[str, str] = {}

    def pick(code: str) -> None:
        chosen["code"] = code
        dlg.accept()

    for code, name in LANGUAGES.items():
        b = AnimButton(name)
        b.setMinimumHeight(44)
        b.setMinimumWidth(280)
        b.clicked.connect(lambda _checked=False, c=code: pick(c))
        lay.addWidget(b)
    dlg.exec()
    return chosen.get("code")


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    app.setApplicationName("dpigec")
    app.setApplicationDisplayName("SplitGate")
    app.setDesktopFileName("dpigec")
    app.setStyleSheet(QSS)
    app.setWindowIcon(app_icon())
    name = single.server_name()
    guard = single.Guard(name)
    lock = guard.start()
    if lock == single.RUNNING:
        # Zaten çalışan bir kopya var: o öne gelir (gizliyse açılır); bu kopya yeni pencere açmaz.
        # Otomatik başlatmada (--minimized) pencere açtırılmaz.
        for _i in range(10):  # kilidi yeni almış kopyanın soketi henüz hazır olmayabilir
            if single.notify_running(name, 200, show="--minimized" not in argv):
                print("SplitGate is already running.", file=sys.stderr)
                return 0
            time.sleep(0.1)
        print("SplitGate is already running, but its window did not answer. "
              "If it is stuck, end the 'dpigec-gui' process and start it again.", file=sys.stderr)
        return 0
    if lock == single.FAILED:
        print("SplitGate: could not create the single-instance lock; running without it", file=sys.stderr)
    init_language()
    set_motion(bool(load_prefs().get("animations", True)) and not os.environ.get("DPIGEC_NO_ANIM"))
    if normalize_language(os.environ.get("DPIGEC_LANG")) is None and saved_language() is None:
        picked = choose_language()
        if picked is not None:
            set_language(picked)
            save_language(picked)
    win = MainWindow()
    guard.on_show = win._show_window
    app.aboutToQuit.connect(guard.close)
    if win.tray is not None:
        app.setQuitOnLastWindowClosed(False)
    if "--minimized" not in argv or win.tray is None:
        win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
