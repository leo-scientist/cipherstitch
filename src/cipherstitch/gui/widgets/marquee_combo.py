"""A combo box whose names scroll into view instead of being cut off."""

from PySide6.QtCore import (
    QEvent,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QRect,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QPainter, QPaintEvent, QPalette
from PySide6.QtWidgets import (
    QComboBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionComboBox,
    QStyleOptionViewItem,
    QStylePainter,
    QWidget,
)

PAUSE_MS = 900  # at each end, so the start and the end can be read
_MS_PER_PIXEL = 25
_MAX_POPUP_WIDTH = 320  # wider names scroll in the list too


class _Scroller(QObject):
    """Moves a text that overflows its room to its end and back, pausing at each end."""

    moved = Signal()

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.offset = 0.0
        self._animation = QVariantAnimation(self)
        self._animation.setLoopCount(-1)
        self._animation.valueChanged.connect(self._move)

    def start(self, overflow: int) -> None:
        self.stop()

        if overflow <= 0:
            return

        travel = overflow * _MS_PER_PIXEL
        duration = 2 * (PAUSE_MS + travel)
        animation = self._animation
        animation.setDuration(duration)
        stops = [(0, 0), (PAUSE_MS, 0), (PAUSE_MS + travel, overflow)]
        stops += [(duration - travel, overflow), (duration, 0)]

        for time, offset in stops:
            animation.setKeyValueAt(time / duration, float(offset))

        animation.start()

    def stop(self) -> None:
        self._animation.stop()
        self._move(0.0)

    def _move(self, offset: float) -> None:
        if offset != self.offset:
            self.offset = offset
            self.moved.emit()


def _draw_shifted(
    painter: QPainter,
    style: QStyle,
    room: QRect,
    text: str,
    offset: float,
    palette: QPalette,
    is_enabled: bool,
    role: QPalette.ColorRole,
) -> None:
    """Draw ``text`` from ``offset`` pixels in, clipped to ``room``. The style's own drawing
    clips to the full text's rect, so the moving text is drawn here."""
    width = painter.fontMetrics().horizontalAdvance(text)
    shifted = QRect(room.left() - round(offset), room.top(), width + 1, room.height())
    painter.save()
    painter.setClipRect(room)
    align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    style.drawItemText(painter, shifted, align, palette, is_enabled, text, role)
    painter.restore()


class _ScrollingDelegate(QStyledItemDelegate):
    """Scrolls the highlighted name of the list when it overflows."""

    def __init__(self, combo: QComboBox) -> None:
        super().__init__(combo)
        self._view = combo.view()
        self._row = QPersistentModelIndex()
        self._is_measured = False  # the room is known only when the row is painted
        self.scroller = _Scroller(self)
        self.scroller.moved.connect(self._view.viewport().update)

    def follow(self, index: QModelIndex) -> None:
        self._row = QPersistentModelIndex(index)
        self._is_measured = False
        self.scroller.stop()
        self._view.viewport().update()

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        if QPersistentModelIndex(index) != self._row:
            super().paint(painter, option, index)

            return

        item = QStyleOptionViewItem(option)
        self.initStyleOption(item, index)
        style = self._view.style()
        # The style draws an item's text this far inside its text rect.
        margin = style.pixelMetric(QStyle.PixelMetric.PM_FocusFrameHMargin, None, self._view) + 1
        room = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, item, self._view)
        # The style sheet's item text rect can run past the item, which cuts the name there.
        room = room.intersected(item.rect).adjusted(margin, 0, -margin, 0)

        if not self._is_measured:
            self._is_measured = True
            self.scroller.start(item.fontMetrics.horizontalAdvance(item.text) - room.width())

        if not self.scroller.offset:
            super().paint(painter, option, index)

            return

        text, item.text = item.text, ""
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, item, painter, self._view)
        is_selected = bool(item.state & QStyle.StateFlag.State_Selected)
        role = QPalette.ColorRole.HighlightedText if is_selected else QPalette.ColorRole.Text
        is_enabled = bool(item.state & QStyle.StateFlag.State_Enabled)
        painter.setFont(item.font)
        _draw_shifted(
            painter, style, room, text, self.scroller.offset, item.palette, is_enabled, role
        )


class MarqueeComboBox(QComboBox):
    """Its list grows to fit the names (right-aligned, up to a limit), and a name that still
    overflows scrolls: the shown one while the pointer is over the box, a listed one while
    it is highlighted. At rest a cut name ends in an ellipsis."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.scroller = _Scroller(self)
        self.scroller.moved.connect(self.update)
        self.delegate = _ScrollingDelegate(self)
        self.view().setItemDelegate(self.delegate)
        self.view().selectionModel().currentChanged.connect(self.delegate.follow)
        self.currentIndexChanged.connect(self._restart)

    def overflow(self) -> int:
        option = self._option()

        return max(0, option.fontMetrics.horizontalAdvance(option.currentText) - self._room(option))

    def showPopup(self) -> None:  # noqa: N802 (Qt override)
        self.scroller.stop()
        super().showPopup()
        view = self.view()
        popup = view.window()
        # The list's frame and padding: what the popup adds around the names' column.
        frame = popup.width() - view.viewport().width()
        width = min(_MAX_POPUP_WIDTH, view.sizeHintForColumn(0) + frame)

        if width > popup.width():
            popup.resize(width, popup.height())

        popup.move(self.mapToGlobal(self.rect().topRight()).x() - popup.width() + 1, popup.y())
        self.delegate.follow(view.currentIndex())

    def hidePopup(self) -> None:  # noqa: N802 (Qt override)
        self.delegate.scroller.stop()
        super().hidePopup()

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Enter:
            self.scroller.start(self.overflow())
        elif event.type() == QEvent.Type.Leave:
            self.scroller.stop()

        return super().event(event)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt override)
        painter = QStylePainter(self)
        option = self._option()
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        room = self._field(option)
        text = option.currentText
        is_cut = option.fontMetrics.horizontalAdvance(text) > room.width()

        if is_cut and self.scroller.offset:
            option.currentText = ""
            painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)
            is_enabled = self.isEnabled()
            role = QPalette.ColorRole.Text
            offset = self.scroller.offset
            _draw_shifted(
                painter, self.style(), room, text, offset, option.palette, is_enabled, role
            )

            return

        if is_cut:
            elided = option.fontMetrics.elidedText(text, Qt.TextElideMode.ElideRight, room.width())
            option.currentText = elided

        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)

    def _option(self) -> QStyleOptionComboBox:
        option = QStyleOptionComboBox()
        self.initStyleOption(option)

        return option

    def _field(self, option: QStyleOptionComboBox) -> QRect:
        return self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, option, QStyle.SubControl.SC_ComboBoxEditField, self
        )

    def _room(self, option: QStyleOptionComboBox) -> int:
        return self._field(option).width()

    def _restart(self) -> None:
        if self.underMouse():
            self.scroller.start(self.overflow())
        else:
            self.scroller.stop()
