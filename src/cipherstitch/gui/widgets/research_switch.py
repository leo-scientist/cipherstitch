"""The Research mode switch: a flask icon, "Research mode" and a painted track with a knob."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPaintEvent
from PySide6.QtWidgets import QPushButton, QWidget

from cipherstitch.gui import theme
from cipherstitch.gui.widgets.toggle import TRACK, paint_track

_ICON_SIZE = 17
_RIGHT_PADDING = 8  # matches the right padding beyond the track in cipherstitch.qss


class ResearchSwitch(QPushButton):
    """Checkable; the track and knob sit in the right padding the stylesheet leaves for them."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Research mode", parent)
        self.setObjectName("researchSwitch")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIcon(theme.icon("flask", theme.NEUTRAL_400, theme.ACCENT))
        self.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt override)
        super().paintEvent(event)
        paint_track(self, self.width() - _RIGHT_PADDING - TRACK.width(), is_on=self.isChecked())
