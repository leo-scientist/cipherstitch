"""A field that looks like an input and opens a popover: the options on the left, the
hovered option's pros and cons on the right (the encryption and method pickers). An
extension's texts come from third-party code, so every option text is plain, never markup."""

from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QHideEvent,
    QKeyEvent,
    QPainter,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cipherstitch import standard
from cipherstitch.catalog import Tier
from cipherstitch.gui import theme
from cipherstitch.gui.widgets.common import (
    clear_layout,
    icon_label,
    plain_label,
    plain_tooltip,
    tier_tag,
)

_POPOVER_GAP = 6
_DETAIL_WIDTH = 230
_ROW_HEIGHT = 48
_PICK_KEYS = (Qt.Key.Key_Return, Qt.Key.Key_Enter)
_UNAVAILABLE_OPACITY = 0.4


class Option(Protocol):
    """A catalog entry the picker can show (a cipher or a method)."""

    @property
    def id(self) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def tier(self) -> Tier: ...

    @property
    def summary(self) -> str: ...

    @property
    def pros(self) -> tuple[str, ...]: ...

    @property
    def cons(self) -> tuple[str, ...]: ...

    @property
    def extension(self) -> str | None: ...


def _transparent(widget: QWidget) -> QWidget:
    widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    return widget


def popover_position(field: QRect, size: QSize, screen: QRect) -> QPoint:
    """Where a popover of ``size`` goes for ``field`` (both in global coordinates): under it,
    or above it when the screen has no room below, and never past the screen's edges. A
    ``Qt.Popup`` is shown where it is put."""
    x = max(screen.left(), min(field.left(), screen.left() + screen.width() - size.width()))
    bottom = screen.top() + screen.height()
    y = field.top() + field.height() + _POPOVER_GAP

    if y + size.height() > bottom:
        above = field.top() - _POPOVER_GAP - size.height()
        y = above if above >= screen.top() else max(screen.top(), bottom - size.height())

    return QPoint(x, y)


class PickerField[T: Option](QPushButton):
    """Looks like an input: icon · option name · tier tag · caret. Opens :class:`PickerPopover`.

    ``footnote`` gives the detail panel's line under the pros and cons, e.g. ``Size overhead
    +16 B``. An extension's option adds the extension's name after it.
    """

    def __init__(
        self,
        icon_name: str,
        footnote: Callable[[T], str],
        current: T,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("pickerField")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._name = plain_label("")
        self._tag_slot = QHBoxLayout()
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(10)
        row.addWidget(_transparent(icon_label(icon_name, theme.ACCENT)))
        row.addWidget(_transparent(self._name), 1)
        row.addLayout(self._tag_slot)
        row.addWidget(_transparent(icon_label("caret-up-down", theme.MUTED, 14)))
        self.popover: PickerPopover[T] = PickerPopover(footnote, self)
        self.clicked.connect(self._open_popover)
        self._show(current)

    def current(self) -> T:
        return self._current

    def set_unavailable[K: str](self, reasons: Mapping[K, str]) -> None:
        """Grey out the options whose ids are in ``reasons``; hovering one shows its reason."""
        self.popover.set_unavailable(reasons)

    def _show(self, option: T) -> None:
        self._current = option
        self._name.setText(option.name)
        self.setAccessibleName(option.name)  # the button's own text is empty
        clear_layout(self._tag_slot)
        self._tag_slot.addWidget(_transparent(tier_tag(option.tier)))

    def _open_popover(self) -> None:
        self.popover.setFixedWidth(self.width())
        self.popover.open_for(self._current, QRect(self.mapToGlobal(QPoint(0, 0)), self.size()))


class _OptionRow(QWidget):
    def __init__(self, option: Option) -> None:
        super().__init__()
        text = QVBoxLayout()
        text.setSpacing(0)
        text.addWidget(plain_label(option.name, "itemName"))
        text.addWidget(plain_label(option.summary, "caption"))
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 1, 2, 1)
        row.setSpacing(10)
        row.addLayout(text, 1)
        row.addWidget(tier_tag(option.tier))
        self._opacity = QGraphicsOpacityEffect(self, opacity=1.0)
        self.setGraphicsEffect(self._opacity)

    def show_reason(self, reason: str | None) -> None:
        """Dim the row while ``reason`` says why its option is unavailable."""
        self._opacity.setOpacity(1.0 if reason is None else _UNAVAILABLE_OPACITY)
        self.setToolTip(plain_tooltip(reason or ""))  # a file or extension name, say

        if reason is None:
            self.unsetCursor()
        else:
            self.setCursor(Qt.CursorShape.ForbiddenCursor)


class PickerPopover[T: Option](QFrame):
    """Popup listing the options; the detail panel follows the hovered row.

    Emits ``chosen(option id)`` when a row is clicked or activated with Return.
    """

    chosen = Signal(object)

    def __init__(self, footnote: Callable[[T], str], parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("pickerPopover")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._footnote = footnote
        self._options: tuple[T, ...] = ()
        self._rows: tuple[_OptionRow, ...] = ()
        self._reasons: dict[str, str] = {}
        self._selected: T | None = None

        self.list = QListWidget()
        self.list.setObjectName("pickerList")
        self.list.setMouseTracking(True)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.itemEntered.connect(lambda item: self._show_detail(self._option_of(item)))
        self.list.itemClicked.connect(self._choose)
        # Return does not activate list items on macOS, so the popover picks on it itself.
        self.list.installEventFilter(self)

        self.detail = QFrame()
        self.detail.setObjectName("pickerDetail")
        self.detail.setFixedWidth(_DETAIL_WIDTH)
        self._detail_layout = QVBoxLayout(self.detail)
        self._detail_layout.setContentsMargins(12, 12, 12, 12)
        self._detail_layout.setSpacing(8)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.addWidget(self.list, 1, Qt.AlignmentFlag.AlignTop)  # short lists stay on top
        layout.addWidget(self.detail)

    def set_options(self, options: Sequence[T]) -> None:
        self._options = tuple(options)
        self.list.clear()
        rows: list[_OptionRow] = []

        for index, option in enumerate(self._options):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setSizeHint(QSize(0, _ROW_HEIGHT))
            self.list.addItem(item)
            rows.append(_OptionRow(option))
            self.list.setItemWidget(item, rows[-1])

        self._rows = tuple(rows)
        self.list.setFixedHeight(_ROW_HEIGHT * len(self._options))
        self._show_availability()

    def set_unavailable[K: str](self, reasons: Mapping[K, str]) -> None:
        self._reasons = {str(option_id): reason for option_id, reason in reasons.items()}
        self._show_availability()

    def open_for(self, selected: T, field: QRect) -> None:
        """Open under ``field`` (its global rectangle), or above it near the screen's bottom."""
        self._selected = selected
        self.list.setCurrentRow(self._options.index(selected))
        self._show_detail(selected)
        self.adjustSize()
        screen = QGuiApplication.screenAt(field.center()) or self.screen()
        self.move(popover_position(field, self.size(), screen.availableGeometry()))
        self.show()
        self.list.setFocus()  # arrow keys move through the options, Return picks one

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt override)
        # Translucent top-level windows ignore stylesheet backgrounds, so paint the rounded card.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(theme.BORDER_STRONG), 1))
        painter.setBrush(QColor(theme.SURFACE))
        radius = theme.POPOVER_RADIUS
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt override)
        item = self.list.currentItem()
        is_pick = (
            event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
            and event.key() in _PICK_KEYS
        )

        if watched is self.list and is_pick and item is not None:
            self._choose(item)

            return True

        return super().eventFilter(watched, event)

    def leaveEvent(self, event: object) -> None:  # noqa: N802 (Qt override)
        self._show_selected()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802 (Qt override)
        self._show_selected()
        super().hideEvent(event)

    def _option_of(self, item: QListWidgetItem) -> T:
        index: int = item.data(Qt.ItemDataRole.UserRole)

        return self._options[index]

    def _choose(self, item: QListWidgetItem) -> None:
        option = self._option_of(item)

        if option.id in self._reasons:
            return

        self._selected = option
        self.hide()
        self.chosen.emit(option.id)

    def _show_availability(self) -> None:
        """Unavailable options are dimmed and cannot be picked."""
        for index, (option, row) in enumerate(zip(self._options, self._rows, strict=True)):
            reason = self._reasons.get(option.id)
            item = self.list.item(index)
            flags = item.flags() | Qt.ItemFlag.ItemIsEnabled
            item.setFlags(flags if reason is None else flags & ~Qt.ItemFlag.ItemIsEnabled)
            item.setToolTip(plain_tooltip(reason or ""))  # the row's padding, outside its widget
            row.show_reason(reason)

    def _show_selected(self) -> None:
        if self._selected is not None:
            self._show_detail(self._selected)

    def _show_detail(self, option: T) -> None:
        clear_layout(self._detail_layout)
        self._detail_layout.addWidget(plain_label(option.name, "title"))

        for text in option.pros:
            self._detail_layout.addWidget(_bullet("plus", theme.ACCENT, text, role="body"))

        for text in option.cons:
            self._detail_layout.addWidget(_bullet("minus", theme.MUTED, text, role="muted"))

        if self._reasons.get(option.id, "").startswith(standard.OFF):
            shield = _bullet("shield-check", theme.ACCENT_300, standard.OFF, role="warning")
            self._detail_layout.addWidget(shield)

        self._detail_layout.addStretch(1)
        self._detail_layout.addWidget(plain_label(self._footnote(option), "caption", wrap=True))

        if option.extension is not None:
            source = f"From the extension {option.extension}"
            self._detail_layout.addWidget(plain_label(source, "caption", wrap=True))


def _bullet(icon_name: str, color: str, text: str, *, role: str) -> QWidget:
    widget = QWidget()
    row = QHBoxLayout(widget)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    row.addWidget(icon_label(icon_name, color, 12), 0, Qt.AlignmentFlag.AlignTop)
    row.addWidget(plain_label(text, role, wrap=True), 1)

    return widget
