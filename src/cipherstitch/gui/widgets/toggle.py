"""Switches drawn as a track with a knob: the painting, and the On/Off switch of a parameter."""

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QPushButton, QWidget

from cipherstitch.gui import theme

TRACK = QSize(32, 18)
_KNOB = 12
_KNOB_INSET = 2


def paint_track(widget: QWidget, left: float, *, is_on: bool) -> None:
    """Paint the track ``left`` pixels from ``widget``'s left edge, centred vertically."""
    top = (widget.height() - TRACK.height()) / 2
    track = QRectF(left, top, TRACK.width(), TRACK.height())
    knob_left = track.right() - _KNOB_INSET - _KNOB - 1 if is_on else left + _KNOB_INSET + 1

    painter = QPainter(widget)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(theme.ACCENT if is_on else theme.BORDER_STRONG), 1))
    painter.setBrush(QColor(theme.ACCENT_900 if is_on else theme.NEUTRAL_900))
    radius = TRACK.height() / 2
    painter.drawRoundedRect(track.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(theme.ACCENT if is_on else theme.NEUTRAL_500))
    painter.drawEllipse(QRectF(knob_left, top + (TRACK.height() - _KNOB) / 2, _KNOB, _KNOB))


class ToggleSwitch(QPushButton):
    """Checkable: the track, then "On" or "Off" in the left padding the stylesheet leaves."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("toggleSwitch")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._show_state()

    def checkStateSet(self) -> None:  # noqa: N802 (Qt override: setChecked, signals or not)
        super().checkStateSet()
        self._show_state()

    def nextCheckState(self) -> None:  # noqa: N802 (Qt override: a click)
        super().nextCheckState()
        self._show_state()

    def _show_state(self) -> None:
        self.setText("On" if self.isChecked() else "Off")

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt override)
        super().paintEvent(event)
        paint_track(self, 0, is_on=self.isChecked())
