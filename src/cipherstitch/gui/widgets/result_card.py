"""Result card shown after a successful reveal."""

from pathlib import Path

from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from cipherstitch.gui import theme
from cipherstitch.gui.widgets.common import (
    SHOW_IN_FOLDER,
    display_path,
    icon_label,
    plain_label,
    set_icon,
)
from cipherstitch.sizes import format_size

_ICON_SIZE = 22
_ICONS_BY_SUFFIX = {
    ".pdf": "file-pdf",
    ".txt": "file-text",
    ".md": "file-text",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".bmp": "image",
}


class ResultCard(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("resultCard")
        self.icon = icon_label("file", theme.ACCENT_300, _ICON_SIZE)
        self.title = plain_label()  # the revealed file's name
        self.details = plain_label(role="muted")  # the folder it was saved to
        self.show_button = QPushButton(SHOW_IN_FOLDER)
        self.show_button.setProperty("variant", "ghost")

        text = QVBoxLayout()
        text.setSpacing(0)
        text.addWidget(self.title)
        text.addWidget(self.details)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(12)
        row.addWidget(self.icon)
        row.addLayout(text, 1)
        row.addWidget(self.show_button)

    def show_result(self, path: Path, size: int, *, is_verified: bool) -> None:
        icon_name = _ICONS_BY_SUFFIX.get(path.suffix.lower(), "file")
        set_icon(self.icon, icon_name, theme.ACCENT_300, _ICON_SIZE)
        self.title.setText(path.name)
        parts = [format_size(size)]

        if is_verified:
            parts.append("integrity verified")

        parts.append(f"saved to {display_path(path.parent)}")
        self.details.setText(" · ".join(parts))
