"""What the GUI test modules share. Import it only after ``pytest.importorskip("PySide6")``."""

from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPoint, QSettings, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from cipherstitch.gui import file_dialogs, profiles, theme
from cipherstitch.gui.main_window import MainWindow
from cipherstitch.gui.widgets.drop_overlay import DropZone


def isolated_session(monkeypatch: pytest.MonkeyPatch, directory: Path) -> None:
    """A new session: no profiles used yet, and Qt's dialog settings in ``directory``,
    never the user's."""
    monkeypatch.setattr(profiles, "_recent", ())
    monkeypatch.setattr(file_dialogs, "_store", lambda: dialog_store(directory))


def dialog_store(directory: Path) -> QSettings:
    """Stands in for the user's QtProject settings."""
    return QSettings(str(directory / "QtProject.ini"), QSettings.Format.IniFormat)


def new_window(qtbot: QtBot, directory: Path) -> MainWindow:
    """The app's window, styled, hiding to ``directory/embedded.png`` and revealing into
    ``directory/out``."""
    app = QApplication.instance()
    assert isinstance(app, QApplication)
    theme.apply(app)
    window = MainWindow()
    qtbot.addWidget(window)
    window.hide_page._custom_output = directory / "embedded.png"
    window.hide_page._set_output(directory / "embedded.png")
    window.reveal_page._output_dir = directory / "out"
    (directory / "out").mkdir(exist_ok=True)

    return window


def drag_enter(window: MainWindow, path: Path) -> str:
    """Drag ``path`` over the window and away: the formats the carrier zone listed."""
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    event = QDragEnterEvent(
        QPoint(10, 10),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    window.dragEnterEvent(event)
    caption: str = window.drop_overlay._zones[DropZone.CARRIER].caption.text()
    window._hide_overlay()

    return caption
