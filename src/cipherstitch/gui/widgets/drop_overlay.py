"""Overlay shown while a file is dragged over the window: two drop zones."""

from collections.abc import Iterable
from enum import StrEnum

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from cipherstitch.gui import theme
from cipherstitch.gui.widgets.common import format_name, icon_label, label, set_icon, usual_suffix

_ICON_SIZE = 40
_ENCRYPTED = "Encrypted, then embedded in the carrier"
_PLAIN = "Embedded in the carrier without encryption"


def format_names(suffixes: Iterable[str]) -> str:
    """E.g. ``PNG · TIFF · JPG · Camera RAW``: each format once, by its usual suffix."""
    return " · ".join(dict.fromkeys(format_name(usual_suffix(s)) for s in suffixes))


class DropZone(StrEnum):
    CARRIER = "carrier"
    PAYLOAD = "payload"


class _ZoneFrame(QFrame):
    def __init__(self, icon_name: str, title: str, caption: str) -> None:
        super().__init__()
        self.setObjectName("dropZone")
        self.icon = icon_label(icon_name, theme.ACCENT, _ICON_SIZE)
        heading = label(title, "dropTitle")
        self.caption = label(caption, "muted", wrap=True)
        self.caption.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addStretch(1)

        for widget in (self.icon, heading):
            layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignHCenter)

        layout.addWidget(self.caption)
        layout.addStretch(1)


class DropOverlay(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("dropOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._zones = {
            DropZone.CARRIER: _ZoneFrame("image", "Use as carrier", ""),  # set on each drag
            DropZone.PAYLOAD: _ZoneFrame("lock-key", "Hide this file", _ENCRYPTED),
        }
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        for zone in self._zones.values():
            layout.addWidget(zone, 1)

        self.hide()

    def set_carrier_icon(self, icon_name: str) -> None:
        set_icon(self._zones[DropZone.CARRIER].icon, icon_name, theme.ACCENT, _ICON_SIZE)

    def set_carrier_formats(self, text: str) -> None:
        self._zones[DropZone.CARRIER].caption.setText(text)

    def set_payload_encrypted(self, is_encrypted: bool) -> None:
        self._zones[DropZone.PAYLOAD].caption.setText(_ENCRYPTED if is_encrypted else _PLAIN)

    def zone_at(self, position: QPoint) -> DropZone | None:
        return next(
            (zone for zone, frame in self._zones.items() if frame.geometry().contains(position)),
            None,
        )

    def set_active(self, active: DropZone | None) -> None:
        for zone, frame in self._zones.items():
            if bool(frame.property("active")) != (zone == active):
                theme.set_property(frame, "active", zone == active)
