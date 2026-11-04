"""Profiles in the app: choosing one, exporting one, and the ones used lately, which the
editor offers by name. Those are kept for the session only: the app stores nothing."""

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QWidget

from cipherstitch import __version__
from cipherstitch.errors import InvalidProfileError
from cipherstitch.gui.file_dialogs import leaving_no_trace, open_file_name
from cipherstitch.params import profile
from cipherstitch.params.profile import Profile

MAX_RECENT = 8
_FILTER = f"CipherStitch profiles (*{profile.SUFFIX})"


_recent: tuple[str, ...] = ()  # absolute paths, most recent first


def choose(parent: QWidget) -> Path | None:
    chosen = open_file_name(parent, "Load profile", "", _FILTER)

    return Path(chosen) if chosen else None


def choose_export(parent: QWidget, name: str) -> Path | None:
    """Where to save a profile. The dialog adds the suffix to a name typed without one
    before it asks whether to replace a file: a profile may be the only record of the
    values a payload needs."""
    with leaving_no_trace():
        dialog = QFileDialog(parent, "Export profile", "", _FILTER)
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setDefaultSuffix(profile.SUFFIX.removeprefix("."))
        dialog.selectFile(f"{name}{profile.SUFFIX}")
        chosen = Path(dialog.selectedFiles()[0]) if dialog.exec() else None
        # Qt records the folders when the dialog is destroyed: now, not with its parent.
        dialog.setParent(None)
        del dialog

    return chosen


def load(path: Path) -> Profile:
    """Read ``path`` and remember it among the profiles used lately."""
    loaded = profile.read(path)
    _remember(path)

    return loaded


def export(path: Path, exported: Profile) -> None:
    profile.write(path, exported, app=f"CipherStitch {__version__}")
    _remember(path)


def recent() -> list[tuple[Path, Profile]]:
    """The profiles used lately that still read, most recent first."""
    found = []

    for name in _recent:
        path = Path(name)

        try:
            found.append((path, profile.read(path)))
        except (InvalidProfileError, OSError):
            continue  # moved, deleted or changed since

    return found


def title(path: Path, loaded: Profile) -> str:
    return loaded.name or path.stem


def _remember(path: Path) -> None:
    global _recent
    name = str(path.absolute())
    _recent = (name, *(other for other in _recent if other != name))[:MAX_RECENT]
