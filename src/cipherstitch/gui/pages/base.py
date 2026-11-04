"""A page = a form body that scrolls plus a footer; runs one background operation at a time."""

import functools
import itertools
import stat
import subprocess
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QScrollArea, QVBoxLayout, QWidget

from cipherstitch.errors import CipherStitchError, ExtensionError
from cipherstitch.gui.widgets.common import FormGrid
from cipherstitch.gui.widgets.footer import Footer
from cipherstitch.gui.worker import BackgroundTask, Job

_BUSY_OPACITY = 0.45


class _FormScroll(QScrollArea):
    """Scrolls the form on a short window and asks for its whole height otherwise, so the
    window can grow with the form while the screen has room (``QScrollArea`` would ask for
    a few lines)."""

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        widget = self.widget()

        return super().sizeHint() if widget is None else widget.sizeHint()

    def viewportEvent(self, event: QEvent) -> bool:  # noqa: N802 (Qt override)
        # The form's new height reaches the viewport, which has no layout to pass it on.
        if event.type() == QEvent.Type.LayoutRequest:
            self.updateGeometry()

        return super().viewportEvent(event)


class OperationPage(QWidget):
    idle = Signal()  # the page's last task has ended: a window closed meanwhile can quit

    def __init__(self, action_text: str, action_icon: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.body = QWidget()
        self.form = FormGrid()
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(28, 20, 28, 20)
        body_layout.addLayout(self.form)
        body_layout.addStretch(1)
        self._opacity = QGraphicsOpacityEffect(self.body, opacity=1.0)
        self.body.setGraphicsEffect(self._opacity)
        self.scroll_area = _FormScroll()
        self.scroll_area.setObjectName("pageScroll")
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # Tab goes from field to field
        self.scroll_area.setWidget(self.body)

        self.footer = Footer(action_text, action_icon)
        self.footer.primary.clicked.connect(self._on_action)
        self.footer.cancel_requested.connect(self._cancel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.scroll_area, 1)
        layout.addWidget(self.footer)
        # Cancel returns to the form at once; the abandoned task stays referenced until it
        # finishes, and only the current task may update the page.
        self._tasks: dict[int, BackgroundTask] = {}
        self._task_ids = itertools.count()
        self._current: int | None = None

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt override)
        """A wheel over a control without focus scrolls the page, never steps the value."""
        if (
            event.type() == QEvent.Type.Wheel
            and isinstance(watched, QWidget)
            and not watched.hasFocus()
        ):
            event.ignore()

            return True

        return super().eventFilter(watched, event)

    def guard_wheel(self, control: QWidget) -> None:
        """Let the wheel over ``control`` scroll the page unless it has the focus, which the
        wheel never gives it."""
        control.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        control.installEventFilter(self)

    def trigger_action(self) -> None:
        """Return in a field runs the primary action, when it can run."""
        if self.footer.primary.isEnabled():
            self.footer.primary.click()

    def has_tasks(self) -> bool:
        return bool(self._tasks)

    def _on_action(self) -> None:
        raise NotImplementedError

    def _on_succeeded(self, result: object) -> None:
        raise NotImplementedError

    def _on_failed(self, error: Exception) -> None:
        raise NotImplementedError

    def _run(self, job: Job) -> None:
        self.body.setEnabled(False)
        self._opacity.setOpacity(_BUSY_OPACITY)
        self.footer.clear_status()
        self.footer.start_working()
        task_id = next(self._task_ids)
        task = BackgroundTask(job)
        task.signals.progressed.connect(functools.partial(self._handle_progress, task_id))
        task.signals.succeeded.connect(functools.partial(self._handle_succeeded, task_id))
        task.signals.failed.connect(functools.partial(self._handle_failed, task_id))
        task.signals.finished.connect(functools.partial(self._release, task_id))
        self._tasks[task_id] = task
        self._current = task_id
        task.start()

    def _handle_progress(self, task_id: int, index: int, count: int, text: str) -> None:
        if task_id == self._current:
            self.footer.show_step(index, count, text)

    def _handle_succeeded(self, task_id: int, result: object) -> None:
        if task_id == self._current:
            self._finish()
            self._on_succeeded(result)

    def _handle_failed(self, task_id: int, error: Exception) -> None:
        if task_id == self._current:
            self._finish()
            print_cause(error)
            self._on_failed(error)

    def _release(self, task_id: int) -> None:
        del self._tasks[task_id]
        self._settle()

    def _settle(self) -> None:
        if not self.has_tasks():
            self.idle.emit()

    def cancel_tasks(self) -> None:
        """Stop every job at its next step and drop its outcome: the window is closing, so
        the form stays as it is."""
        for task in self._tasks.values():
            task.cancel()

    def _cancel(self) -> None:
        if self._current is not None:
            self._tasks[self._current].cancel()
            self._finish()

    def _finish(self) -> None:
        self._current = None
        self.footer.stop_working()
        self._opacity.setOpacity(1.0)
        self.body.setEnabled(True)


def show_in_folder(path: Path) -> None:
    """Reveal ``path`` selected in Finder; elsewhere, open the folder that holds it."""
    if sys.platform == "darwin":
        subprocess.run(["/usr/bin/open", "-R", str(path)], check=False)  # noqa: S603
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))


def unreadable(path: Path, error: Exception) -> str:
    """The footer text for a file that can't be read, with the system's reason for an
    ``OSError`` (its ``str`` names the whole path)."""
    reason = error.strerror if isinstance(error, OSError) and error.strerror else str(error)

    return f"Can't read {path.name}: {reason}"


def unreadable_file(path: Path | None) -> str | None:
    """Why ``path`` can't be read as a file, if it has gone or changed since it was chosen;
    ``None`` while it can be, or without one."""
    if path is None:
        return None

    try:
        mode = path.stat().st_mode
    except OSError as exc:
        return unreadable(path, exc)

    if stat.S_ISREG(mode):
        return None

    return f"Can't read {path.name}: {'a folder' if stat.S_ISDIR(mode) else 'not a regular file'}"


def error_message(error: Exception) -> str:
    """The footer text for a failed operation."""
    if isinstance(error, OSError) and error.filename is not None and error.strerror:
        return unreadable(Path(error.filename), error)

    if isinstance(error, CipherStitchError | OSError):
        return str(error)

    if isinstance(error, MemoryError):
        return "Out of memory"

    return f"Unexpected error: {str(error) or type(error).__name__}"


def print_cause(error: Exception) -> None:
    """The traceback of the exception an extension raised, on stderr for the extension's
    author: the window shows the error's one line."""
    if isinstance(error, ExtensionError) and error.__cause__ is not None:
        traceback.print_exception(error.__cause__, file=sys.stderr)
