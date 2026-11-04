"""File chip: an icon, a file name, a muted meta line and an optional "Change…" button."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from cipherstitch.gui import theme
from cipherstitch.gui.widgets.common import icon_label, plain_label, set_icon

_LARGE_ICON = 22
_SMALL_ICON = 18
_COMPACT_HEIGHT = 36
_ACTIVATE_KEYS = (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter)


class FileChip(QFrame):
    """Two-line chip with a button, or a compact one-line chip that is itself clickable."""

    change_requested = Signal()

    def __init__(self, *, compact: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("fileChip")
        self._compact = compact
        self._icon_size = _SMALL_ICON if compact else _LARGE_ICON
        self.icon = icon_label("file", theme.ACCENT, self._icon_size)
        self.title = plain_label()  # a file name
        self.meta = plain_label(role="muted")  # its description, or why it can't be read
        self.button: QPushButton | None = None

        row = QHBoxLayout(self)
        row.setSpacing(10 if compact else 12)
        row.addWidget(self.icon)

        if compact:
            self.setMinimumHeight(_COMPACT_HEIGHT)
            self.setProperty("clickable", True)
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # a button, for the keyboard too
            row.setContentsMargins(10, 0, 10, 0)
            row.addWidget(self.title, 1)
            row.addWidget(self.meta)

            return

        row.setContentsMargins(12, 8, 8, 8)
        text = QVBoxLayout()
        text.setSpacing(0)
        text.addWidget(self.title)
        text.addWidget(self.meta)
        row.addLayout(text, 1)
        self.button = QPushButton("Change…")
        self.button.clicked.connect(self.change_requested)
        row.addWidget(self.button)

    def set_file(self, icon_name: str, title: str, meta: str) -> None:
        set_icon(self.icon, icon_name, theme.ACCENT, self._icon_size)
        self.title.setText(title)
        self.meta.setText(meta)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt override)
        if self._compact and event.button() == Qt.MouseButton.LeftButton:
            self.change_requested.emit()

            return

        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt override)
        if self._compact and event.key() in _ACTIVATE_KEYS:
            self.change_requested.emit()

            return

        super().keyPressEvent(event)
