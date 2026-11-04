"""The app's file dialogs, which leave no trace. A closing Qt file dialog records the folder
it last showed and its history in the user's QtProject settings (shared by every Qt app),
and no option turns that off: these put that group back as it was. See
docs/security.md#what-the-app-keeps."""

from collections.abc import Iterator
from contextlib import contextmanager

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QFileDialog, QWidget

_ORGANIZATION = "QtProject"
_GROUP = "FileDialog"


def open_file_name(parent: QWidget, caption: str, folder: str = "", filters: str = "") -> str:
    """The chosen file's path, or "" when cancelled."""
    with leaving_no_trace():
        chosen, _ = QFileDialog.getOpenFileName(parent, caption, folder, filters)

    return chosen


def save_file_name(
    parent: QWidget, caption: str, path: str, filters: str, selected: str
) -> tuple[str, str]:
    """The path typed or chosen ("" when cancelled) and the filter picked."""
    with leaving_no_trace():
        return QFileDialog.getSaveFileName(parent, caption, path, filters, selected)


def existing_folder(parent: QWidget, caption: str, folder: str) -> str:
    """The chosen folder, or "" when cancelled."""
    with leaving_no_trace():
        return QFileDialog.getExistingDirectory(parent, caption, folder)


@contextmanager
def leaving_no_trace() -> Iterator[None]:
    """Run file dialogs, then put Qt's dialog settings back as they were. A dialog object
    records them when it is destroyed, so destroy it inside."""
    before = _dialog_settings()

    try:
        yield
    finally:
        _restore(before)


def _store() -> QSettings:
    """Where Qt's dialogs write: the native store, whatever the default format."""
    settings = QSettings(QSettings.Format.NativeFormat, QSettings.Scope.UserScope, _ORGANIZATION)
    settings.setFallbacksEnabled(False)

    return settings


def _dialog_settings() -> dict[str, object]:
    settings = _store()
    settings.beginGroup(_GROUP)

    return {key: settings.value(key) for key in settings.allKeys()}


def _restore(before: dict[str, object]) -> None:
    """Write only on a change: an untouched store stays as it was."""
    if _dialog_settings() == before:
        return

    settings = _store()
    settings.remove(_GROUP)
    settings.beginGroup(_GROUP)

    for key, value in before.items():
        settings.setValue(key, value)

    settings.endGroup()
    settings.sync()
