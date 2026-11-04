"""Small shared helpers: labels, tags, icons, paths and form rows."""

import sys
from collections.abc import Callable, Iterable
from pathlib import Path

from PySide6 import QtGui
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cipherstitch.catalog import RAW_EXTENSIONS, CarrierKind, Tier
from cipherstitch.gui import theme

ROW_LABEL_WIDTH = 96
FORM_COLUMN_SPACING = 16
FORM_ROW_SPACING = 18
SHOW_IN_FOLDER = "Show in Finder" if sys.platform == "darwin" else "Show in folder"

_TIER_TAGS = {
    Tier.RECOMMENDED: "recommended",
    Tier.ACCEPTABLE: "acceptable",
    Tier.NOT_RECOMMENDED: "notRecommended",
    Tier.EXPERIMENTAL: "experimental",
}
CARRIER_ICONS = {
    CarrierKind.IMAGE: "image",
    CarrierKind.AUDIO: "waveform",
    CarrierKind.DOCUMENT: "file-text",
    CarrierKind.TEXT: "text-aa",
}
_CAMERA_RAW = "Camera RAW"  # one name for every camera's own RAW suffix
_USUAL_SUFFIXES = {".tif": ".tiff", ".jpeg": ".jpg"}
OutputFormats = dict[str, tuple[str, ...]]  # each format's usual suffix: all its suffixes


def usual_suffix(suffix: str) -> str:
    """The suffix a format is usually named by: ``.tiff`` for ``.tif``."""
    return _USUAL_SUFFIXES.get(suffix, suffix)


def format_name(suffix: str) -> str:
    """E.g. ``PNG`` for ``.png``, and one name for all the camera RAW suffixes."""
    return _CAMERA_RAW if suffix in RAW_EXTENSIONS else suffix[1:].upper()


def output_formats(suffixes: Iterable[str]) -> OutputFormats:
    """``suffixes`` by format, in their order: ``{".tiff": (".tif", ".tiff")}``."""
    formats: OutputFormats = {}

    for suffix in suffixes:
        usual = usual_suffix(suffix)
        formats[usual] = (*formats.get(usual, ()), suffix)

    return formats


def free_path(directory: Path, stem: str, suffix: str) -> Path:
    """``directory/stem+suffix``, or ``stem-2``, ``stem-3``… if it already exists."""
    candidate = directory / f"{stem}{suffix}"
    number = 2

    while candidate.exists():
        candidate = directory / f"{stem}-{number}{suffix}"
        number += 1

    return candidate


def display_path(path: Path) -> str:
    """``path`` with the home directory shortened to ``~``."""
    try:
        return f"~/{path.relative_to(Path.home())}"
    except ValueError:
        return str(path)


def label(text: str = "", role: str | None = None, *, wrap: bool = False) -> QLabel:
    widget = QLabel(text, wordWrap=wrap)

    if role:
        widget.setProperty("role", role)

    return widget


def plain_label(text: str = "", role: str | None = None, *, wrap: bool = False) -> QLabel:
    """A label for text from outside the app (an extension's, a profile's): never markup."""
    widget = label(text, role, wrap=wrap)
    widget.setTextFormat(Qt.TextFormat.PlainText)

    return widget


def plain_tooltip(text: str) -> str:
    """``text`` for ``setToolTip``, shown as written: a tooltip that looks like markup (a file
    named ``<b>cover``, say) is otherwise rendered as rich text."""
    return QtGui.Qt.convertFromPlainText(text) if QtGui.Qt.mightBeRichText(text) else text


def tier_tag(tier: Tier) -> QLabel:
    widget = QLabel(tier.value)
    widget.setProperty("tag", _TIER_TAGS[tier])

    return widget


def tag(text: str, kind: str) -> QLabel:
    widget = QLabel(text)
    widget.setProperty("tag", kind)

    return widget


def icon_label(name: str, color: str = theme.TEXT, size: int = 16) -> QLabel:
    widget = QLabel()
    widget.setPixmap(theme.pixmap(name, color, size))
    widget.setFixedSize(size, size)

    return widget


def set_icon(widget: QLabel, name: str, color: str = theme.TEXT, size: int = 16) -> None:
    widget.setPixmap(theme.pixmap(name, color, size))


def icon_button(name: str, on_click: Callable[[], None], text: str) -> QPushButton:
    """An icon-only button; ``text`` says what it does, as its tooltip and to a screen
    reader."""
    button = QPushButton()
    button.setProperty("variant", "icon")
    button.setIcon(theme.icon(name))
    button.setToolTip(text)
    button.setAccessibleName(text)
    button.clicked.connect(on_click)

    return button


def column(*widgets: QWidget, spacing: int) -> QWidget:
    """``widgets`` stacked top to bottom, without margins."""
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)

    for widget in widgets:
        layout.addWidget(widget)

    return container


def row(*widgets: QWidget, spacing: int = 8) -> QWidget:
    """Widgets side by side, left-aligned; the last one takes the remaining width."""
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)

    for widget in widgets[:-1]:
        layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignLeft)

    layout.addWidget(widgets[-1], 1)

    return container


def clear_layout(layout: QLayout) -> None:
    """Remove and delete every widget in ``layout``."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget() if item is not None else None

        if widget is not None:
            widget.hide()
            widget.deleteLater()


class FormGrid(QGridLayout):
    """Two-column form: a fixed-width muted label column and a control column."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setContentsMargins(0, 0, 0, 0)
        self.setHorizontalSpacing(FORM_COLUMN_SPACING)
        self.setVerticalSpacing(FORM_ROW_SPACING)
        self.setColumnMinimumWidth(0, ROW_LABEL_WIDTH)
        self.setColumnStretch(1, 1)

    def add_row(self, text: str, control: QWidget) -> QLabel:
        row = self.rowCount()
        row_label = label(text, "rowLabel")
        row_label.setFixedWidth(ROW_LABEL_WIDTH)

        self.addWidget(row_label, row, 0, Qt.AlignmentFlag.AlignTop)
        self.addWidget(control, row, 1)

        return row_label
