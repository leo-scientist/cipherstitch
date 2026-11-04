"""Notes stacked top to bottom, each with an icon: the Hide page's detection risks, the
Reveal page's error and its hint."""

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from cipherstitch.gui import theme
from cipherstitch.gui.widgets.common import clear_layout, icon_label, label

_ICON_SIZE = 15
_TOP_MARGIN = 8  # lines the first warning up with the row label's text
_SPACING = 6


class WarningList(QWidget):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        icon: str = "warning-circle",
        color: str = theme.ACCENT_300,
        role: str = "warning",
        top_margin: int = _TOP_MARGIN,
    ) -> None:
        super().__init__(parent)
        self._messages: tuple[str, ...] = ()
        self._look = (icon, color, role)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, top_margin, 0, 0)
        self._layout.setSpacing(_SPACING)

    def messages(self) -> tuple[str, ...]:
        return self._messages

    def set_messages(self, messages: Sequence[str]) -> None:
        """Show ``messages``. The rows are rebuilt only when they change: the Hide page
        refreshes on every keystroke."""
        if tuple(messages) == self._messages:
            return

        self._messages = tuple(messages)
        clear_layout(self._layout)

        for message in self._messages:
            self._layout.addWidget(_warning_row(message, *self._look))

        self._fit_height(self.width())

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self._fit_height(event.size().width())

    def _fit_height(self, width: int) -> None:
        """A wrapped label's minimum is one line, which would let the window squeeze the
        form; ask for the height the lines take at ``width`` instead. Without lines,
        ``heightForWidth`` is -1."""
        self.setMinimumHeight(max(self.heightForWidth(width), 0))


def _warning_row(text: str, icon: str, color: str, role: str) -> QWidget:
    widget = QWidget()
    row = QHBoxLayout(widget)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    row.addWidget(icon_label(icon, color, _ICON_SIZE), 0, Qt.AlignmentFlag.AlignTop)
    line = label(text, role, wrap=True)
    line.setTextFormat(Qt.TextFormat.PlainText)  # a profile's text, say: never markup
    row.addWidget(line, 1)

    return widget
