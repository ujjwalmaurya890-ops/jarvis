"""The circular audio visualiser (pure QPainter, no OpenGL, no images).

Kept deliberately cheap: one timer at 20 fps, a fixed-size ring buffer of levels,
and ~120 line segments per frame. On an integrated-GPU laptop this costs well
under 1% CPU, which is the point - a pretty widget that stalls the mic callback
would cause audio dropouts.
"""

from __future__ import annotations

import math
from collections import deque

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget

MAX_BARS = 56


class VoiceOrb(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._level = 0.0
        self._target = 0.0
        self._history: deque[float] = deque([0.0] * MAX_BARS, maxlen=MAX_BARS)
        self._phase = 0.0
        self._color = QColor("#38bdf8")
        self._active = False
        self._state = "offline"
        self.setMinimumSize(210, 210)
        self.setRenderRole()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)

    def setRenderRole(self) -> None:  # noqa: N802
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

    # ---- data ---------------------------------------------------------------
    def push_level(self, level: float) -> None:
        self._target = max(0.0, min(1.0, float(level)))

    def set_state(self, state: str, color: str) -> None:
        self._state = state
        self._color = QColor(color)
        self.update()

    def set_active(self, active: bool) -> None:
        self._active = bool(active)

    # ---- paint ---------------------------------------------------------------
    def _tick(self) -> None:
        # fast attack / slow release so speech looks alive but does not flicker
        if self._target > self._level:
            self._level += (self._target - self._level) * 0.55
        else:
            self._level += (self._target - self._level) * 0.14
        self._history.append(self._level)
        self._phase += 0.045 if self._active else 0.012
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        radius = min(w, h) / 2 - 34

        # backdrop glow
        glow = QRadialGradient(cx, cy, radius * 1.8)
        glow.setColorAt(0.0, QColor(self._color.red(), self._color.green(), self._color.blue(), 46 if self._active else 20))
        glow.setColorAt(1.0, QColor(7, 13, 22, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QT_point(cx, cy), radius * 1.8, radius * 1.8)

        # outer dashed ring, slowly rotating
        pen = QPen(QColor(self._color.red(), self._color.green(), self._color.blue(), 90), 1, Qt.DotLine)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.save()
        p.translate(cx, cy)
        p.rotate(math.degrees(self._phase) * 0.6)
        p.drawEllipse(QRectF(-radius - 16, -radius - 16, (radius + 16) * 2, (radius + 16) * 2))
        p.restore()

        # inner core
        core = QLinearGradient(cx - radius, cy - radius, cx + radius, cy + radius)
        core.setColorAt(0.0, QColor(13, 24, 40, 235))
        core.setColorAt(1.0, QColor(8, 15, 27, 235))
        p.setPen(QPen(QColor(self._color.red(), self._color.green(), self._color.blue(), 140), 1.4))
        p.setBrush(core)
        pulse = radius * (0.42 + 0.12 * self._level)
        p.drawEllipse(QT_point(cx, cy), pulse, pulse)

        # radial spectrum bars from the level history
        n = len(self._history)
        for i, value in enumerate(self._history):
            angle = (i / n) * math.tau - math.pi / 2 + self._phase * 0.25
            energy = 0.10 + value * 0.92
            inner = radius * 0.52
            outer = inner + radius * 0.52 * energy
            alpha = 90 + int(165 * min(1.0, value * 1.6))
            p.setPen(
                QPen(
                    QColor(self._color.red(), self._color.green(), self._color.blue(), alpha),
                    2.6,
                    Qt.SolidLine,
                    Qt.RoundCap,
                )
            )
            p.drawLine(
                QT_point(cx + math.cos(angle) * inner, cy + math.sin(angle) * inner),
                QT_point(cx + math.cos(angle) * outer, cy + math.sin(angle) * outer),
            )

        # waveform arc (bottom), mirrors the mic level over time
        path = QPainterPath()
        span = radius * 1.9
        for i, value in enumerate(self._history):
            x = cx - span / 2 + (i / (n - 1)) * span
            y = cy + radius * 1.02 + math.sin(i * 0.5 + self._phase * 3) * 7 * value + (1 - value) * 2
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        p.setPen(QPen(QColor(self._color.red(), self._color.green(), self._color.blue(), 120), 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

        # centred state text
        p.setPen(QPen(QColor("#dbeafe")))
        font = QFont("Segoe UI", 11, QFont.Bold)
        font.setLetterSpacing(QFont.AbsoluteSpacing, 4)
        p.setFont(font)
        p.drawText(QT_rect(cx - radius, cy - 8, radius * 2, 18), Qt.AlignCenter, self._state.upper())
        p.setPen(QPen(QColor(self._color.lighter(140))))
        small = QFont("Segoe UI", 8)
        small.setLetterSpacing(QFont.AbsoluteSpacing, 2)
        p.setFont(small)
        p.drawText(QT_rect(cx - radius, cy + 10, radius * 2, 14), Qt.AlignCenter, f"{int(self._level * 100):>3}%" if self._active else "MIC OFF")
        p.end()


# --- tiny helpers so this file works with either Qt binding's geometry types ---
def QT_point(x: float, y: float):  # noqa: N802, ANN201
    from PySide6.QtCore import QPointF

    return QPointF(x, y)


def QT_rect(x: float, y: float, w: float, h: float):  # noqa: N802, ANN201
    from PySide6.QtCore import QRectF

    return QRectF(x, y, w, h)
