"""Capacity meter: a 4 px bar, the space the payload needs and the space available."""

from PySide6.QtCore import QEvent, QObject, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPaintEvent
from PySide6.QtWidgets import QHBoxLayout, QProgressBar, QVBoxLayout, QWidget

from cipherstitch.gui import theme
from cipherstitch.gui.widgets.common import label
from cipherstitch.sizes import format_size

_SCALE = 1000
_MIN_VISIBLE_PERCENT = 0.6
_MARKER_RISE = 4  # the marker reaches this far above and below the bar
_MARKER_WIDTH = 2


class _LimitMarks(QWidget):
    """Over the bar: a marker at the advised size and, where that is Standard mode's limit,
    hatching from it to the end."""

    def __init__(self, bar: QProgressBar, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._bar = bar
        self._limit = 0.0
        self._fill = 0.0
        self._is_hatched = False
        bar.installEventFilter(self)

    def show_limit(self, limit: float, fill: float, *, is_hatched: bool) -> None:
        """``limit`` and ``fill`` are shares of the bar's width."""
        self._limit, self._fill, self._is_hatched = limit, fill, is_hatched
        self.update()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt override)
        if event.type() in (QEvent.Type.Move, QEvent.Type.Resize):
            self.setGeometry(self._bar.geometry().adjusted(0, -_MARKER_RISE, 0, _MARKER_RISE))

        return False

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt override)
        bar = QRectF(0, _MARKER_RISE, self.width(), self._bar.height())
        limit = bar.width() * self._limit
        # The fill covers the hatching, as it does in the design.
        hatch_start = max(limit, bar.width() * self._fill)
        radius = bar.height() / 2
        track = QPainterPath()
        track.addRoundedRect(bar, radius, radius)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self._is_hatched:
            painter.setClipPath(track)
            hatch = bar.adjusted(hatch_start, 0, 0, 0)
            painter.fillRect(hatch, QColor(theme.NEUTRAL_900))
            painter.fillRect(hatch, QBrush(QColor(theme.BORDER_STRONG), Qt.BrushStyle.BDiagPattern))
            painter.setClipping(False)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.ACCENT_300))
        marker = QRectF(limit - _MARKER_WIDTH / 2, 0, _MARKER_WIDTH, self.height())
        painter.drawRoundedRect(marker, 1, 1)


class CapacityMeter(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.bar = QProgressBar(textVisible=False, maximum=_SCALE)
        self.used = label(role="muted")
        self.percent = label(role="muted")
        caption = QHBoxLayout()
        caption.addWidget(self.used, 1)
        caption.addWidget(self.percent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)
        self._layout.addWidget(self.bar)
        self._layout.addLayout(caption)
        self.limit_marks = _LimitMarks(self.bar, self)
        self.limit_marks.hide()

    def show_estimate(
        self,
        used: int,
        total: int | None,
        *,
        is_encrypted: bool,
        is_conspicuous: bool = False,
        limit: int | None = None,
        advised: int | None = None,
    ) -> None:
        """``is_conspicuous``: the payload is larger than ``advised``, the most it should take,
        which the bar marks. ``limit``: the most Standard mode allows (the advised size). The
        caption and warning follow it, and the bar is hatched beyond it. The percent is always
        the share of the whole capacity."""
        self.used.setText(_caption(used, total, limit, is_encrypted=is_encrypted))
        theme.set_property(self.bar, "conspicuous", is_conspicuous)
        self._show_limit(
            total, advised if limit is None else limit, used, is_limit=limit is not None
        )

        if total is None:
            self.bar.setValue(0)
            self.percent.clear()
            theme.set_property(self.bar, "over", False)

            return

        allowed = total if limit is None else limit
        fill = min(100.0, used / total * 100) if total else 100.0
        self.bar.setValue(round(max(fill, _MIN_VISIBLE_PERCENT if used else 0.0) / 100 * _SCALE))
        self.percent.setText("<1%" if 0 < fill < 1 else f"{round(fill)}%")
        theme.set_property(self.bar, "over", used > allowed)

    def _show_limit(
        self, total: int | None, limit: int | None, used: int, *, is_limit: bool
    ) -> None:
        """The marks, with room above the bar for the marker, only when the capacity is known
        and the limit is below it."""
        share = limit / total if total and limit is not None and limit < total else None
        self._layout.setContentsMargins(0, _MARKER_RISE if share is not None else 0, 0, 0)
        self.limit_marks.setVisible(share is not None)

        if total and share is not None:
            self.limit_marks.show_limit(share, min(used / total, 1.0), is_hatched=is_limit)
            self.limit_marks.raise_()


def _caption(used: int, total: int | None, limit: int | None, *, is_encrypted: bool) -> str:
    """E.g. ``1.2 KiB with encryption overhead · of 900 B``, ``1.2 KiB of 900 B``, or
    ``… · of 45 B allowed (capacity 900 B)`` in Standard mode."""
    needed = f"{format_size(used)} with encryption overhead" if is_encrypted else format_size(used)

    if total is None:
        return f"{needed} · capacity unknown"

    separator = " · of " if is_encrypted else " of "

    if limit is None:
        return f"{needed}{separator}{format_size(total)}"

    return f"{needed}{separator}{format_size(limit)} allowed (capacity {format_size(total)})"
