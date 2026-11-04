"""Page footer: status + primary action when idle; step, progress and Cancel while working."""

import math

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFontMetricsF, QResizeEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from cipherstitch.gui import theme
from cipherstitch.gui.widgets.common import SHOW_IN_FOLDER, icon_label, label, set_icon

_MIN_HEIGHT = 68
_PROGRESS_SCALE = 1000
_IDLE = 0
_WORKING = 1
_STATUS_ICONS = {
    "warning": ("warning-circle", theme.ACCENT_300),
    "error": ("x-circle", theme.ACCENT_300),
    "done": ("check-circle", theme.ACCENT),
}


class _StatusLabel(QLabel):
    """One line while it fits, wrapped when it does not (a long path in an error). Qt would
    size a wrapping label narrower than its text, wrapping even short statuses."""

    def __init__(self) -> None:
        super().__init__(wordWrap=True)
        self.setTextFormat(Qt.TextFormat.PlainText)  # an extension's error, say: never markup

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        margins = self.contentsMargins()
        text = math.ceil(QFontMetricsF(self.font()).horizontalAdvance(self.text()))  # whole pixels
        width = text + margins.left() + margins.right()

        return QSize(width, self.heightForWidth(width))


class Footer(QWidget):
    cancel_requested = Signal()
    show_in_folder_requested = Signal()

    def __init__(self, action_text: str, action_icon: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("footer")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        idle = self._build_idle(action_text, action_icon)
        working = self._build_working()
        self._pages = QStackedWidget()
        self._pages.addWidget(idle)
        self._pages.addWidget(working)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(28, 14, 28, 14)
        self._layout.addWidget(self._pages)
        self.clear_status()

    def _build_idle(self, action_text: str, action_icon: str) -> QWidget:
        """The status line and the primary action."""
        self.primary = QPushButton(action_text)
        self.primary.setProperty("variant", "primary")
        self.primary.setIcon(theme.icon(action_icon, theme.ACCENT))

        self.status_icon = icon_label("check-circle", theme.ACCENT, 15)
        self.status_text = _StatusLabel()
        self.status_text.setProperty("role", "status")
        self.show_button = QPushButton(SHOW_IN_FOLDER)
        self.show_button.setProperty("variant", "ghost")
        self.show_button.clicked.connect(self.show_in_folder_requested)

        idle = QWidget()
        idle_row = QHBoxLayout(idle)
        idle_row.setContentsMargins(0, 0, 0, 0)
        idle_row.setSpacing(6)
        idle_row.addWidget(self.status_icon)
        idle_row.addWidget(self.status_text)
        idle_row.addWidget(self.show_button)
        idle_row.addStretch(1)
        idle_row.addWidget(self.primary)
        self._idle_row = idle_row

        return idle

    def _build_working(self) -> QWidget:
        """The current step, its progress and Cancel."""
        self.step_label = label(role="status")
        self.step_label.setTextFormat(Qt.TextFormat.PlainText)  # an extension's method name
        self.step_count = label(role="muted")
        self.progress = QProgressBar(textVisible=False, maximum=_PROGRESS_SCALE)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.cancel_requested)

        working = QWidget()
        working_row = QHBoxLayout(working)
        working_row.setContentsMargins(0, 0, 0, 0)
        working_row.setSpacing(16)
        step_column = QVBoxLayout()
        step_column.setSpacing(6)
        step_header = QHBoxLayout()
        step_header.addWidget(self.step_label, 1)
        step_header.addWidget(self.step_count)
        step_column.addLayout(step_header)
        step_column.addWidget(self.progress)
        working_row.addLayout(step_column, 1)
        working_row.addWidget(cancel)

        return working

    def clear_status(self) -> None:
        self.status_icon.hide()
        self.status_text.clear()
        self.show_button.hide()
        self._fit_status()

    def show_busy(self, text: str) -> None:
        """A plain status for work under way, such as compressing a carrier."""
        self.status_icon.hide()
        self.status_text.setText(text)
        theme.set_property(self.status_text, "role", "status")
        self.show_button.hide()
        self._fit_status()

    def set_status(self, kind: str, text: str, *, can_show_in_folder: bool = False) -> None:
        name, color = _STATUS_ICONS[kind]
        set_icon(self.status_icon, name, color, 15)
        self.status_icon.show()
        self.status_text.setText(text)
        theme.set_property(self.status_text, "role", "warning" if kind != "done" else "status")
        self.show_button.setVisible(can_show_in_folder)
        self._fit_status()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self._fit_status()

    def _fit_status(self) -> None:
        """Make room for a wrapped status in the minimum height, which the window grows with:
        layouts leave a wrapped label's height out of it, so the form above would be
        squeezed."""
        margins = self._layout.contentsMargins()
        width = self.width() - margins.left() - margins.right()
        height = self._idle_row.heightForWidth(width) + margins.top() + margins.bottom()
        self.setMinimumHeight(max(_MIN_HEIGHT, height))

    def start_working(self) -> None:
        self.progress.setValue(0)
        self.step_label.clear()
        self.step_count.clear()
        self._pages.setCurrentIndex(_WORKING)

    def show_step(self, index: int, count: int, text: str) -> None:
        self.step_label.setText(text)
        self.step_count.setText(f"Step {index + 1} of {count}")
        self.progress.setValue(round(index / count * _PROGRESS_SCALE))

    def stop_working(self) -> None:
        self.progress.setValue(_PROGRESS_SCALE)
        self._pages.setCurrentIndex(_IDLE)

    @property
    def is_working(self) -> bool:
        return self._pages.currentIndex() == _WORKING
