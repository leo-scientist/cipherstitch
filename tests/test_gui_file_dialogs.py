"""The app's file dialogs leave Qt's dialog settings as they found them."""

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QWidget

from cipherstitch.gui import file_dialogs

from .gui_support import dialog_store, isolated_session

VISITED = "file:///Users/someone/secret"
REAL_STORE = file_dialogs._store  # before the session points it at a test folder


@pytest.fixture(autouse=True)
def session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    isolated_session(monkeypatch, tmp_path)


def _record_folder(store: QSettings) -> None:
    """What a closing Qt dialog does."""
    store.setValue("FileDialog/lastVisited", VISITED)
    store.setValue("FileDialog/history", [VISITED])
    store.sync()


def _values(store: QSettings) -> dict[str, object]:
    store.sync()

    return {key: store.value(key) for key in store.allKeys()}


def test_the_store_is_the_one_qt_dialogs_write() -> None:
    """Read only: neither opens the user's store for writing."""
    qt = QSettings(QSettings.Scope.UserScope, "QtProject")  # as QFileDialog opens it

    assert REAL_STORE().fileName() == qt.fileName()
    assert REAL_STORE().format() == qt.format() == QSettings.Format.NativeFormat


def test_the_folders_a_dialog_records_are_put_back(tmp_path: Path) -> None:
    store = dialog_store(tmp_path)
    store.setValue("FileDialog/lastVisited", "file:///Users/someone/Pictures")
    store.setValue("FileDialog/viewMode", "Detail")
    store.setValue("Other/kept", "another app's")
    before = _values(store)

    with file_dialogs.leaving_no_trace():
        _record_folder(dialog_store(tmp_path))

    assert _values(dialog_store(tmp_path)) == before


def test_a_group_qt_adds_is_removed(tmp_path: Path) -> None:
    with file_dialogs.leaving_no_trace():
        _record_folder(dialog_store(tmp_path))

    assert _values(dialog_store(tmp_path)) == {}


def test_an_untouched_store_is_never_written(tmp_path: Path) -> None:
    with file_dialogs.leaving_no_trace():
        pass

    assert not (tmp_path / "QtProject.ini").exists()


@pytest.mark.usefixtures("qapp")
def test_the_wrappers_leave_no_trace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def dialog(result: object) -> object:
        def run(*_args: object) -> object:
            _record_folder(dialog_store(tmp_path))

            return result

        return run

    monkeypatch.setattr(
        file_dialogs,
        "QFileDialog",
        SimpleNamespace(
            getOpenFileName=dialog(("/a/cover.png", "")),
            getSaveFileName=dialog(("/a/out.png", "PNG (*.png)")),
            getExistingDirectory=dialog("/a"),
        ),
    )
    parent = QWidget()

    assert file_dialogs.open_file_name(parent, "Choose") == "/a/cover.png"
    assert file_dialogs.save_file_name(parent, "Save", "", "", "") == ("/a/out.png", "PNG (*.png)")
    assert file_dialogs.existing_folder(parent, "Folder", "") == "/a"
    assert _values(dialog_store(tmp_path)) == {}
