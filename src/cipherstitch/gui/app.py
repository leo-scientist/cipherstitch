"""Application entry point: ``ciphst-gui [FILE.csprofile]``."""

import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QFileOpenEvent
from PySide6.QtWidgets import QApplication

from cipherstitch.gui import theme
from cipherstitch.gui.main_window import APP_NAME, MainWindow
from cipherstitch.params import profile


class _ProfileOpener(QObject):
    """macOS opens files with an event, not arguments: a profile goes to the window."""

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self._window = window

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt override)
        if isinstance(event, QFileOpenEvent) and event.file().endswith(profile.SUFFIX):
            self._window.open_profile(Path(event.file()))

            return True

        return super().eventFilter(watched, event)


def run() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    theme.apply(app)
    window = MainWindow()
    app.installEventFilter(_ProfileOpener(window))
    window.show()

    for argument in app.arguments()[1:]:  # what Qt left of the command line
        if argument.endswith(profile.SUFFIX):
            window.open_profile(Path(argument))

    return app.exec()
