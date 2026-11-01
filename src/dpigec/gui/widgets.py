# Copyright (C) 2026 Project Gamers
# SPDX-License-Identifier: GPL-3.0-or-later
"""SplitGate arayüzünün animasyonlu bileşenleri (PyQt6)."""
from __future__ import annotations

import time
from collections import deque
from typing import Deque, Dict, List, Optional

from PyQt6 import sip
from PyQt6.QtCore import (QEasingCurve, QPointF, QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer,
                          QVariantAnimation, pyqtSignal)
from PyQt6.QtGui import (QBrush, QColor, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPalette,
                         QPen)
from PyQt6.QtWidgets import (QAbstractButton, QButtonGroup, QCheckBox, QFrame, QGraphicsOpacityEffect,
                             QHBoxLayout, QLabel, QPushButton, QSizePolicy, QStackedWidget, QToolButton,
                             QVBoxLayout, QWidget)

ACCENT = "#2563eb"
QWIDGETSIZE_MAX = 16777215

_motion = True


def set_motion(on: bool) -> None:
    """Animasyonları açar/kapatır; kapalıyken her şey son değerine anında geçer."""
    global _motion
    _motion = bool(on)


def motion() -> bool:
    return _motion


def ms(n: int) -> int:
    return n if _motion else 0


def mix(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(round(a.red() + (b.red() - a.red()) * t), round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t), round(a.alpha() + (b.alpha() - a.alpha()) * t))


def halt(anim: Optional[QVariantAnimation]) -> None:
    """Animasyonu durdurur; bitip silinmiş (DeleteWhenStopped) bir animasyonda hata vermez."""
    if anim is not None and not sip.isdeleted(anim):
        anim.stop()


def tween(parent: QWidget, start, end, duration: int, on_value, on_done=None,
          easing: QEasingCurve.Type = QEasingCurve.Type.OutCubic) -> QVariantAnimation:
    """Tek seferlik değer animasyonu. Animasyon kapalıysa son değer hemen uygulanır."""
    anim = QVariantAnimation(parent)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setDuration(max(1, ms(duration)))
    anim.setEasingCurve(easing)
    anim.valueChanged.connect(on_value)
    if on_done is not None:
        anim.finished.connect(on_done)
    if _motion:
        anim.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)
    else:
        on_value(end)
        if on_done is not None:
            on_done()
        anim.deleteLater()
    return anim


def fade_in(widget: QWidget, duration: int = 200, delay: int = 0) -> None:
    """Parçayı saydamlıktan çıkarır; bitince efekti kaldırır (çocukların çizimi yavaşlamasın)."""
    if not _motion:
        return
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)

    def clear() -> None:
        if widget.graphicsEffect() is effect:
            widget.setGraphicsEffect(None)

    def start() -> None:
        if sip.isdeleted(widget) or sip.isdeleted(effect):  # gecikme sırasında pencere yeniden kurulmuş olabilir
            return
        anim = QPropertyAnimation(effect, b"opacity", widget)
        anim.setDuration(duration)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(clear)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    if delay > 0:
        QTimer.singleShot(delay, start)
    else:
        start()


class AnimButton(QPushButton):
    """Üzerine gelince yumuşakça parlayan düğme."""

    def __init__(self, text: str = "", primary: bool = False) -> None:
        super().__init__(text)
        if primary:
            self.setObjectName("primary")
        self._hover = 0.0
        self._anim: Optional[QVariantAnimation] = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _go(self, target: float) -> None:
        halt(self._anim)
        self._anim = tween(self, self._hover, target, 160, self._set_hover)

    def _set_hover(self, v) -> None:
        self._hover = float(v)
        self.update()

    def enterEvent(self, e) -> None:  # noqa: N802
        super().enterEvent(e)
        self._go(1.0)

    def leaveEvent(self, e) -> None:  # noqa: N802
        super().leaveEvent(e)
        self._go(0.0)

    def paintEvent(self, e) -> None:  # noqa: N802
        super().paintEvent(e)
        if self._hover <= 0.01 or not self.isEnabled():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(ACCENT)
        c.setAlphaF(0.16 * self._hover)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)


class Switch(QCheckBox):
    """Kaydıraklı anahtar. QCheckBox gibi davranır (isChecked, toggled, clicked...); uzun metin sarılır."""
    W, H, GAP = 40, 22, 10

    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self._pos = 0.0
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        sp = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)
        self.toggled.connect(self._on_toggled)

    def _on_toggled(self, on: bool) -> None:
        target = 1.0 if on else 0.0
        if self.isVisible():
            tween(self, self._pos, target, 170, self._set_pos)
        else:
            self._pos = target
            self.update()

    def setChecked(self, on: bool) -> None:  # noqa: N802
        super().setChecked(on)
        self._pos = 1.0 if on else 0.0  # kod ile yüklemede animasyon olmasın
        self.update()

    def _set_pos(self, v) -> None:
        self._pos = float(v)
        self.update()

    def hitButton(self, _pos) -> bool:  # noqa: N802
        return True

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def _text_rect(self, width: int) -> QRect:
        tw = max(40, width - self.W - self.GAP)
        return QFontMetrics(self.font()).boundingRect(QRect(0, 0, tw, 10000),
                                                      Qt.TextFlag.TextWordWrap.value, self.text())

    def heightForWidth(self, w: int) -> int:  # noqa: N802
        return max(self.H, self._text_rect(w).height()) + 6

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = QFontMetrics(self.font())
        return QSize(self.W + self.GAP + fm.horizontalAdvance(self.text()), self.H + 6)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.W + self.GAP + 120, self.H + 6)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        enabled = self.isEnabled()
        if not enabled:
            p.setOpacity(0.5)
        top = (self.height() - self.H) / 2
        track = QRectF(0.5, top + 0.5, self.W - 1, self.H - 1)
        p.setPen(Qt.PenStyle.NoPen)
        off = mix(pal.color(QPalette.ColorRole.WindowText), pal.color(QPalette.ColorRole.Window), 0.62)
        p.setBrush(mix(off, QColor(ACCENT), self._pos))
        p.drawRoundedRect(track, self.H / 2, self.H / 2)
        d = self.H - 6
        x = 3 + (self.W - 6 - d) * self._pos
        p.setBrush(QColor("white"))
        p.drawEllipse(QRectF(x, top + 3, d, d))
        if self.hasFocus():
            p.setPen(QPen(QColor(ACCENT), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(track.adjusted(-2, -2, 2, 2), self.H / 2 + 2, self.H / 2 + 2)
        p.setPen(pal.color(QPalette.ColorRole.WindowText))
        rect = QRectF(self.W + self.GAP, 0, self.width() - self.W - self.GAP, self.height())
        p.drawText(rect, int(Qt.AlignmentFlag.AlignVCenter.value) | int(Qt.TextFlag.TextWordWrap.value),
                   self.text())


class PowerButton(QAbstractButton):
    """Büyük güç düğmesi: durağan, dönen (bekleme) ya da nabız atan (çalışıyor) görünüm."""

    def __init__(self) -> None:
        super().__init__()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(200, 214)
        self._color = QColor("#6e7681")
        self._mode = "idle"
        self._neutral = True
        self._phase = 0.0
        self._last_paint = 0.0
        self._hover = 0.0
        self._loop = QVariantAnimation(self)
        self._loop.setStartValue(0.0)
        self._loop.setEndValue(1.0)
        self._loop.setLoopCount(-1)
        self._loop.setEasingCurve(QEasingCurve.Type.Linear)
        self._loop.valueChanged.connect(self._on_phase)
        self._color_anim: Optional[QVariantAnimation] = None
        self._hover_anim: Optional[QVariantAnimation] = None

    @property
    def mode(self) -> str:
        return self._mode

    def set_look(self, color: str, mode: str, neutral: bool = False) -> None:
        """mode: "idle" | "spin" | "pulse". neutral: gri (kapalı) görünüm."""
        self._neutral = neutral
        target = QColor(color)
        halt(self._color_anim)
        if target != self._color:
            self._color_anim = tween(self, self._color, target, 350, self._on_color)
        if mode != self._mode:
            self._mode = mode
            self._loop.stop()
            self._phase = 0.0
        self._sync_loop()
        self.update()

    def refresh(self) -> None:
        """Animasyon ayarı değişince döngüyü yeniden değerlendirir."""
        self._sync_loop()
        self.update()

    def _sync_loop(self) -> None:
        wanted = _motion and self.isVisible() and self._mode in ("spin", "pulse")
        if not wanted:
            self._loop.stop()
        elif self._loop.state() != QVariantAnimation.State.Running:
            self._loop.setDuration(1100 if self._mode == "spin" else 2400)
            self._loop.start()

    def _on_phase(self, v) -> None:
        self._phase = float(v)
        now = time.monotonic()
        if now - self._last_paint >= 0.030:  # ~30 kare/sn yeter; işlemciyi gereksiz yormasın
            self._last_paint = now
            self.update()

    def _on_color(self, v) -> None:
        self._color = QColor(v)
        self.update()

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self._sync_loop()

    def hideEvent(self, e) -> None:  # noqa: N802
        super().hideEvent(e)
        self._loop.stop()  # tepside gizliyken işlemciyi yormasın

    def _go_hover(self, target: float) -> None:
        halt(self._hover_anim)
        self._hover_anim = tween(self, self._hover, target, 160, self._on_hover)

    def _on_hover(self, v) -> None:
        self._hover = float(v)
        self.update()

    def enterEvent(self, e) -> None:  # noqa: N802
        super().enterEvent(e)
        self._go_hover(1.0)

    def leaveEvent(self, e) -> None:  # noqa: N802
        super().leaveEvent(e)
        self._go_hover(0.0)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(210, 224)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        w, h, cap = self.width(), self.height(), 34
        cx, cy = w / 2, (h - cap) / 2
        r = min(w, h - cap) / 2 - 26 + 4 * self._hover - (3 if self.isDown() else 0)
        col = QColor(self._color)
        if not self.isEnabled():
            p.setOpacity(0.55)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if self._mode == "pulse" and _motion:
            for k in (0.0, 0.5):
                t = (self._phase + k) % 1.0
                ring = QColor(col)
                ring.setAlphaF(0.34 * (1 - t))
                p.setPen(QPen(ring, 2.5))
                rr = r + 8 + t * 24
                p.drawEllipse(QPointF(cx, cy), rr, rr)
        track_r = r + 8
        p.setPen(QPen(pal.color(QPalette.ColorRole.Mid), 3))
        p.drawEllipse(QPointF(cx, cy), track_r, track_r)
        if self._mode == "spin":
            arc = QPen(col, 5)
            arc.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(arc)
            start = int(90 * 16 - self._phase * 360 * 16)
            p.drawArc(QRectF(cx - track_r, cy - track_r, 2 * track_r, 2 * track_r), start, -110 * 16)
        elif not self._neutral:
            ring = QColor(col)
            ring.setAlphaF(0.55)
            p.setPen(QPen(ring, 3))
            p.drawEllipse(QPointF(cx, cy), track_r, track_r)
        p.setPen(Qt.PenStyle.NoPen)
        if self._neutral:
            fill = pal.color(QPalette.ColorRole.Button)
            icon_col = pal.color(QPalette.ColorRole.WindowText)
            p.setBrush(fill)
            p.setPen(QPen(pal.color(QPalette.ColorRole.Mid), 2))
        else:
            grad = QLinearGradient(cx, cy - r, cx, cy + r)
            grad.setColorAt(0, mix(col, QColor("white"), 0.18))
            grad.setColorAt(1, mix(col, QColor("black"), 0.12))
            p.setBrush(QBrush(grad))
            icon_col = QColor("white")
        p.drawEllipse(QPointF(cx, cy), r, r)
        s = r * 0.42
        icon = QPen(icon_col, max(3.5, r * 0.08))
        icon.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(icon)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(cx - s, cy - s, 2 * s, 2 * s), 110 * 16, 320 * 16)
        p.drawLine(QPointF(cx, cy - s * 1.1), QPointF(cx, cy - s * 0.1))
        if self.hasFocus():
            p.setPen(QPen(QColor(ACCENT), 2, Qt.PenStyle.DashLine))
            p.drawEllipse(QPointF(cx, cy), track_r + 6, track_r + 6)
        f = self.font()
        f.setBold(True)
        f.setPointSizeF(max(11.0, f.pointSizeF() + 1))
        p.setFont(f)
        p.setPen(pal.color(QPalette.ColorRole.WindowText))
        p.drawText(QRectF(0, h - cap, w, cap), int(Qt.AlignmentFlag.AlignCenter.value), self.text())


class StatCard(QFrame):
    """Başlıklı sayı kartı; değer değişince isteğe bağlı renkli bir parlama gösterir."""

    def __init__(self, title: str, flash_color: Optional[str] = None) -> None:
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)
        self.value = QLabel("–")
        self.value.setObjectName("cardValue")
        self.value.setTextFormat(Qt.TextFormat.PlainText)
        t = QLabel(title)
        t.setObjectName("cardTitle")
        t.setWordWrap(True)
        lay.addWidget(self.value)
        lay.addWidget(t)
        self._flash_color = flash_color
        self._flash = 0.0

    def set(self, text: str) -> None:
        old = self.value.text()
        if text == old:
            return
        self.value.setText(text)
        if self._flash_color and old != "–" and text != "–" and self.isVisible():
            tween(self, 1.0, 0.0, 900, self._on_flash)

    def _on_flash(self, v) -> None:
        self._flash = float(v)
        self.update()

    def paintEvent(self, e) -> None:  # noqa: N802
        super().paintEvent(e)
        if self._flash <= 0.01 or not self._flash_color:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(self._flash_color)
        c.setAlphaF(0.9 * self._flash)
        p.setPen(QPen(c, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 12, 12)


class Sparkline(QWidget):
    """Son ~60 saniyenin indirme/yükleme hızı; yeni değer sağdan kayarak girer."""
    COLORS = ("#2563eb", "#2ea043")

    def __init__(self, size: int = 60) -> None:
        super().__init__()
        self.setMinimumHeight(86)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._n = size
        self._down: Deque[float] = deque([0.0] * size, maxlen=size)
        self._up: Deque[float] = deque([0.0] * size, maxlen=size)
        self._shift = 0.0

    def clear(self) -> None:
        self._down.extend([0.0] * self._n)
        self._up.extend([0.0] * self._n)
        self._shift = 0.0
        self.update()

    def push(self, down: float, up: float) -> None:
        self._down.append(max(0.0, down))
        self._up.append(max(0.0, up))
        if self.isVisible():
            tween(self, 1.0, 0.0, 900, self._on_shift, easing=QEasingCurve.Type.Linear)
        else:
            self._shift = 0.0

    def _on_shift(self, v) -> None:
        self._shift = float(v)
        self.update()

    def _path(self, data: List[float], peak: float, w: float, h: float) -> QPainterPath:
        step = w / (self._n - 2)
        pts = [QPointF((i - self._shift) * step, h - 4 - (v / peak) * (h - 10)) for i, v in enumerate(data)]
        path = QPainterPath(pts[0])
        for a, b in zip(pts, pts[1:]):
            mid = QPointF((a.x() + b.x()) / 2, (a.y() + b.y()) / 2)
            path.quadTo(a, mid)
        path.lineTo(pts[-1])
        return path

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = float(self.width()), float(self.height())
        pal = self.palette()
        grid = pal.color(QPalette.ColorRole.Mid)
        grid.setAlphaF(0.35)
        p.setPen(QPen(grid, 1, Qt.PenStyle.DotLine))
        for k in (0.25, 0.5, 0.75):
            p.drawLine(QPointF(0, h * k), QPointF(w, h * k))
        peak = max(max(self._down), max(self._up), 10 * 1024.0)
        p.setClipRect(QRectF(0, 0, w, h))
        for data, color in ((self._down, self.COLORS[0]), (self._up, self.COLORS[1])):
            line = self._path(list(data), peak, w, h)
            area = QPainterPath(line)
            area.lineTo(QPointF((self._n - 1 - self._shift) * (w / (self._n - 2)), h))
            area.lineTo(QPointF(-self._shift * (w / (self._n - 2)), h))
            area.closeSubpath()
            grad = QLinearGradient(0, 0, 0, h)
            top = QColor(color)
            top.setAlphaF(0.30)
            bottom = QColor(color)
            bottom.setAlphaF(0.0)
            grad.setColorAt(0, top)
            grad.setColorAt(1, bottom)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(grad))
            p.drawPath(area)
            p.setPen(QPen(QColor(color), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(line)


class BusyBar(QWidget):
    """İlerleme süresi bilinmeyen işler için kayan çubuk; gizliyken çalışmaz."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(5)
        self._phase = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(1300)
        self._anim.setLoopCount(-1)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._anim.valueChanged.connect(self._on_phase)
        self._running = False
        self.hide()

    def start(self) -> None:
        self._running = True
        self.show()
        if _motion and self.isVisible():
            self._anim.start()

    def stop(self) -> None:
        self._running = False
        self._anim.stop()
        self.hide()

    def running(self) -> bool:
        return self._running

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        if self._running and _motion and self._anim.state() != QVariantAnimation.State.Running:
            self._anim.start()

    def hideEvent(self, e) -> None:  # noqa: N802
        super().hideEvent(e)
        self._anim.stop()  # pencere tepside gizliyken de çalışmasın

    def _on_phase(self, v) -> None:
        self._phase = float(v)
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = float(self.width()), float(self.height())
        track = self.palette().color(QPalette.ColorRole.Mid)
        track.setAlphaF(0.35)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        chunk = w * 0.32
        x = -chunk + (w + chunk) * self._phase
        p.setClipRect(QRectF(0, 0, w, h))
        p.setBrush(QColor(ACCENT))
        p.drawRoundedRect(QRectF(x, 0, chunk, h), h / 2, h / 2)


class CollapsibleSection(QFrame):
    """Başlığa tıklayınca yumuşakça açılıp kapanan bölüm. İçerik `body` parçasına eklenir."""

    def __init__(self, title: str, expanded: bool = True) -> None:
        super().__init__()
        self.setObjectName("section")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.header = QToolButton()
        self.header.setObjectName("sectionHeader")
        self.header.setText(title)
        self.header.setCheckable(True)
        self.header.setChecked(expanded)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.toggled.connect(self._toggled)
        lay.addWidget(self.header)
        self.body = QWidget()
        self.body.setObjectName("sectionBody")
        lay.addWidget(self.body)
        self._anim: Optional[QPropertyAnimation] = None
        self.body.setVisible(expanded)

    def expanded(self) -> bool:
        return self.header.isChecked()

    def set_expanded(self, on: bool) -> None:
        self.header.setChecked(on)

    def _target_height(self) -> int:
        lay = self.body.layout()
        if lay is None:
            return self.body.sizeHint().height()
        w = self.body.width() if self.body.width() > 50 else self.width()
        if lay.hasHeightForWidth() and w > 50:
            return lay.totalHeightForWidth(w)
        return lay.sizeHint().height()

    def _toggled(self, on: bool) -> None:
        self.header.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)
        if self._anim is not None:
            self._anim.stop()
        if not (_motion and self.isVisible()):
            self.body.setMaximumHeight(QWIDGETSIZE_MAX)
            self.body.setVisible(on)
            return
        start = self.body.height() if self.body.isVisible() else 0
        end = self._target_height() if on else 0
        self.body.setMaximumHeight(start)
        self.body.show()
        anim = QPropertyAnimation(self.body, b"maximumHeight", self)
        anim.setDuration(220)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

        def done() -> None:
            self.body.setMaximumHeight(QWIDGETSIZE_MAX)
            self.body.setVisible(on)

        anim.finished.connect(done)
        self._anim = anim
        anim.start()


class Toast(QFrame):
    """Pencerenin altında kısa süre görünen, tıklanmayı beklemeyen bildirim."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("toast")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        self.label = QLabel("")
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        lay.addWidget(self.label)
        self._t = 0.0
        self._anim: Optional[QVariantAnimation] = None
        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._leave)
        self.hide()
        parent.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.parent() and event.type() == event.Type.Resize and self.isVisible():
            self._place()
        return False

    def _place(self) -> None:
        par = self.parentWidget()
        self.adjustSize()
        x = (par.width() - self.width()) // 2
        y = par.height() - self.height() - 28 + round(16 * (1 - self._t))
        self.move(x, y)

    def show_message(self, text: str, kind: str = "ok") -> None:
        color = {"ok": "#2ea043", "error": "#f85149"}.get(kind, ACCENT)
        self.setStyleSheet(f"QFrame#toast {{ border-left: 4px solid {color}; }}")
        self.label.setText(text)
        self._timer.stop()
        halt(self._anim)
        self._t = 0.0
        self.adjustSize()
        self._place()
        self.show()
        self.raise_()
        self._anim = tween(self, self._t, 1.0, 240, self._on_t)
        self._timer.start(2800)

    def _on_t(self, v) -> None:
        self._t = float(v)
        self._effect.setOpacity(self._t)
        self._place()

    def _leave(self) -> None:
        halt(self._anim)
        self._anim = tween(self, self._t, 0.0, 200, self._on_t, self.hide, QEasingCurve.Type.InCubic)


class FadeStack(QStackedWidget):
    """Sayfa değişince yeni sayfayı yumuşakça belirten yığın."""

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        changed = index != self.currentIndex()
        super().setCurrentIndex(index)
        page = self.currentWidget()
        if changed and page is not None and self.isVisible():
            fade_in(page, 180)


class NavButton(QAbstractButton):
    """Yan menü düğmesi: simge + yazı; seçilince renk dolgusu yumuşakça belirir."""

    def __init__(self, text: str, kind: str) -> None:
        super().__init__()
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(42)
        self.setAccessibleName(text)
        self._kind = kind
        self._sel = 0.0
        self._hover = 0.0
        self._anims: Dict[str, QVariantAnimation] = {}
        self.toggled.connect(lambda on: self._go("_sel", 1.0 if on else 0.0))

    def _go(self, attr: str, target: float) -> None:
        start = getattr(self, attr)

        def on_value(v) -> None:
            setattr(self, attr, float(v))
            self.update()

        halt(self._anims.get(attr))
        self._anims[attr] = tween(self, start, target, 200, on_value)

    def enterEvent(self, e) -> None:  # noqa: N802
        super().enterEvent(e)
        self._go("_hover", 1.0)

    def leaveEvent(self, e) -> None:  # noqa: N802
        super().leaveEvent(e)
        self._go("_hover", 0.0)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(176, 42)

    def _draw_icon(self, p: QPainter, box: QRectF, color: QColor) -> None:
        pen = QPen(color, 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        x, y, s = box.x(), box.y(), box.width()
        if self._kind == "home":
            path = QPainterPath(QPointF(x + s * 0.1, y + s * 0.48))
            path.lineTo(x + s * 0.5, y + s * 0.12)
            path.lineTo(x + s * 0.9, y + s * 0.48)
            path.moveTo(x + s * 0.22, y + s * 0.42)
            path.lineTo(x + s * 0.22, y + s * 0.88)
            path.lineTo(x + s * 0.78, y + s * 0.88)
            path.lineTo(x + s * 0.78, y + s * 0.42)
            p.drawPath(path)
        elif self._kind == "settings":
            for i, (fy, kx) in enumerate(((0.2, 0.68), (0.5, 0.32), (0.8, 0.6))):
                p.drawLine(QPointF(x + s * 0.1, y + s * fy), QPointF(x + s * 0.9, y + s * fy))
                p.setBrush(QColor(color))
                p.drawEllipse(QPointF(x + s * kx, y + s * fy), s * 0.1, s * 0.1)
                p.setBrush(Qt.BrushStyle.NoBrush)
        elif self._kind == "test":
            path = QPainterPath(QPointF(x + s * 0.38, y + s * 0.1))
            path.lineTo(x + s * 0.38, y + s * 0.42)
            path.lineTo(x + s * 0.12, y + s * 0.84)
            path.lineTo(x + s * 0.88, y + s * 0.84)
            path.lineTo(x + s * 0.62, y + s * 0.42)
            path.lineTo(x + s * 0.62, y + s * 0.1)
            path.moveTo(x + s * 0.3, y + s * 0.1)
            path.lineTo(x + s * 0.7, y + s * 0.1)
            p.drawPath(path)
        elif self._kind == "log":
            for fy in (0.25, 0.5, 0.75):
                p.drawPoint(QPointF(x + s * 0.12, y + s * fy))
                p.drawLine(QPointF(x + s * 0.3, y + s * fy), QPointF(x + s * 0.9, y + s * fy))
        else:  # about
            p.drawEllipse(QRectF(x + s * 0.08, y + s * 0.08, s * 0.84, s * 0.84))
            p.drawPoint(QPointF(x + s * 0.5, y + s * 0.3))
            p.drawLine(QPointF(x + s * 0.5, y + s * 0.46), QPointF(x + s * 0.5, y + s * 0.72))

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        text_col = mix(pal.color(QPalette.ColorRole.PlaceholderText), pal.color(QPalette.ColorRole.WindowText),
                       max(self._sel, self._hover * 0.7))
        p.setPen(Qt.PenStyle.NoPen)
        if self._hover > 0.01:
            c = QColor(pal.color(QPalette.ColorRole.WindowText))
            c.setAlphaF(0.07 * self._hover)
            p.setBrush(c)
            p.drawRoundedRect(rect, 10, 10)
        if self._sel > 0.01:
            c = QColor(ACCENT)
            c.setAlphaF(0.16 * self._sel)
            p.setBrush(c)
            p.drawRoundedRect(rect, 10, 10)
            bar = QColor(ACCENT)
            bar.setAlphaF(self._sel)
            p.setBrush(bar)
            hh = rect.height() * 0.5 * self._sel
            p.drawRoundedRect(QRectF(rect.x(), rect.center().y() - hh / 2, 3.5, hh), 1.75, 1.75)
        icon_col = mix(text_col, QColor(ACCENT), self._sel)
        self._draw_icon(p, QRectF(rect.x() + 14, rect.center().y() - 10, 20, 20), icon_col)
        f = self.font()
        f.setBold(self.isChecked())
        p.setFont(f)
        p.setPen(text_col)
        p.drawText(QRectF(rect.x() + 46, rect.y(), rect.width() - 50, rect.height()),
                   int(Qt.AlignmentFlag.AlignVCenter.value), self.text())
        if self.hasFocus():
            p.setPen(QPen(QColor(ACCENT), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect, 10, 10)


class Pages(QWidget):
    """Sol yan menü + sayfa yığını. QTabWidget'in kullanılan arayüzünü (addTab, tabText...) taklit eder."""
    currentChanged = pyqtSignal(int)

    def __init__(self) -> None:
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(204)
        sl = QVBoxLayout(self.sidebar)
        sl.setContentsMargins(14, 18, 14, 14)
        sl.setSpacing(4)
        self.top = QVBoxLayout()
        self.top.setSpacing(2)
        sl.addLayout(self.top)
        sl.addSpacing(14)
        self.nav = QVBoxLayout()
        self.nav.setSpacing(4)
        sl.addLayout(self.nav)
        sl.addStretch(1)
        self.bottom = QVBoxLayout()
        self.bottom.setSpacing(6)
        sl.addLayout(self.bottom)
        self.stack = FadeStack()
        lay.addWidget(self.sidebar)
        lay.addWidget(self.stack, 1)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: List[NavButton] = []
        self._group.idClicked.connect(self.setCurrentIndex)

    def addTab(self, widget: QWidget, text: str, kind: str = "about") -> int:  # noqa: N802
        index = self.stack.addWidget(widget)
        btn = NavButton(text, kind)
        btn.setToolTip(f"{text} (Ctrl+{index + 1})")
        self._group.addButton(btn, index)
        self.nav.addWidget(btn)
        self._buttons.append(btn)
        if index == 0:
            btn.setChecked(True)
        return index

    def count(self) -> int:
        return self.stack.count()

    def widget(self, index: int) -> QWidget:
        return self.stack.widget(index)

    def tabText(self, index: int) -> str:  # noqa: N802
        return self._buttons[index].text()

    def currentIndex(self) -> int:  # noqa: N802
        return self.stack.currentIndex()

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        if not 0 <= index < self.stack.count():
            return
        changed = index != self.stack.currentIndex()
        self._buttons[index].setChecked(True)
        self.stack.setCurrentIndex(index)
        if changed:
            self.currentChanged.emit(index)
