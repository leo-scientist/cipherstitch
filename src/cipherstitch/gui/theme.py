"""CipherStitch look: Nocturne colour tokens, the Inter font, the stylesheet, the app icon
and Phosphor icons."""

from functools import cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QFontDatabase, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QWidget

RESOURCES = Path(__file__).parent / "resources"
_ICONS = RESOURCES / "icons"
_APP_ICON = RESOURCES / "app-icon"
_ICON_RENDER_SCALE = 2  # render at 2x so icons stay sharp on HiDPI screens

SURFACE = "#232532"
BORDER_STRONG = "#595d6c"
POPOVER_RADIUS = 14
TEXT = "#e9e9ed"
ACCENT = "#9184d9"
ACCENT_300 = "#d2cefd"
ACCENT_900 = "#2b2741"
NEUTRAL_400 = "#b2b6ca"
NEUTRAL_500 = "#9397ab"
NEUTRAL_900 = "#292b31"
MUTED = "#8a8b93"
PLACEHOLDER = "#75798c"


def apply(app: QApplication) -> None:
    """Load the font, stylesheet and application icon into ``app``."""
    QFontDatabase.addApplicationFont(str(RESOURCES / "fonts" / "Inter.ttf"))
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(PLACEHOLDER))
    app.setPalette(palette)
    sheet = (RESOURCES / "cipherstitch.qss").read_text(encoding="utf-8")
    app.setStyleSheet(sheet.replace("@icons", _ICONS.as_posix()))
    app.setWindowIcon(app_icon())


def app_icon() -> QIcon:
    """The CipherStitch icon at every bundled size (window, task bar, macOS Dock)."""
    result = QIcon()

    for size_png in _APP_ICON.glob("cipherstitch_*.png"):
        result.addFile(str(size_png))

    return result


@cache
def pixmap(name: str, color: str = TEXT, size: int = 16) -> QPixmap:
    """Phosphor icon ``name`` tinted with ``color``, ``size`` logical pixels square."""
    svg = (_ICONS / f"{name}.svg").read_text(encoding="utf-8")
    renderer = QSvgRenderer(QByteArray(svg.replace("currentColor", color).encode()))
    image = QPixmap(size * _ICON_RENDER_SCALE, size * _ICON_RENDER_SCALE)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter, QRectF(image.rect()))
    painter.end()
    image.setDevicePixelRatio(_ICON_RENDER_SCALE)

    return image


def icon(name: str, color: str = TEXT, checked_color: str | None = None) -> QIcon:
    """Icon tinted ``color``; checkable buttons switch to ``checked_color`` when checked."""
    result = QIcon(pixmap(name, color))

    if checked_color is not None:
        result.addPixmap(pixmap(name, checked_color), QIcon.Mode.Normal, QIcon.State.On)

    return result


def set_property(widget: QWidget, name: str, value: object) -> None:
    """Set a dynamic property used by the stylesheet and re-apply the style."""
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
