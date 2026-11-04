"""The Parameters row of Research mode's pages: which values differ from Standard, loading
a profile, and on Hide a warning when a Format one does, since nothing in the file records it."""

from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from cipherstitch.errors import InvalidProfileError
from cipherstitch.gui import profiles, theme
from cipherstitch.gui.widgets.common import icon_label, label, plain_tooltip
from cipherstitch.gui.widgets.warning_list import WarningList
from cipherstitch.params import profile
from cipherstitch.params.model import Group, Kind, Param, ParamType, Value, key
from cipherstitch.params.profile import Profile
from cipherstitch.params.settings import Settings

LOAD = "Load profile…"
CLEAR = "Clear"
_SHOWN_CHANGES = 3
_ICON_SIZE = 22


def change_label(param: Param, value: Value) -> str:
    """E.g. ``Uniform costs on``, ``Cost model HILL``, ``CONSTRAINT_HEIGHT 4``."""
    if param.type is ParamType.BOOL:
        return f"{param.label} {'on' if value else 'off'}"

    if param.type is ParamType.ENUM:
        option = next(option for option in param.options if option.value == value)

        return f"{param.label} {option.label}"

    shown = f"{value:g}" if param.type is ParamType.FLOAT else str(value)
    unit = f" {param.unit}" if param.unit else ""

    return f"{param.code.split(' ')[0]} {shown}{unit}"


def format_warning(count: int) -> str:
    differ = "parameters differ" if count > 1 else "parameter differs"

    return (
        f"{count} format {differ} from Standard. Nothing is stored in the file. Export this "
        "profile, or Reveal won’t find the payload."
    )


def _counted(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


class _ElidedLabel(QLabel):
    """One line, cut with an ellipsis where it does not fit. ``text()`` stays whole."""

    def __init__(self, role: str) -> None:
        super().__init__()
        self.setProperty("role", role)
        self.setTextFormat(Qt.TextFormat.PlainText)  # sized as it is drawn: a profile's name
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return QSize(0, super().minimumSizeHint().height())

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt override)
        rect = self.contentsRect()
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, rect.width())
        self.style().drawItemText(
            QPainter(self),
            rect,
            self.alignment().value,
            self.palette(),
            self.isEnabled(),
            text,
            self.foregroundRole(),
        )


class ParametersRow(QWidget):
    """A chip that sums up the settings, with "Load profile…" (then "Clear") and "Edit…",
    and the Format warning when hiding. Emits ``profile_loaded(Profile)`` and ``cleared()``
    for the page to apply.

    Revealing (``is_reveal``) needs the values the hiding used, whatever they are, so it has
    no Format warning, and refuses a profile with parameters this version doesn't know,
    which hiding leaves out. Hiding refuses one with a cost function no loaded extension
    has, which revealing doesn't need."""

    profile_loaded = Signal(object)
    cleared = Signal()

    def __init__(self, parent: QWidget | None = None, *, is_reveal: bool = False) -> None:
        super().__init__(parent)
        self._is_reveal = is_reveal
        self._loaded: tuple[Path, Profile] | None = None
        self.title = label()
        self.title.setTextFormat(Qt.TextFormat.PlainText)  # a profile's name: never markup
        self.meta = _ElidedLabel("muted")
        self.load = QPushButton(LOAD)
        self.load.setProperty("variant", "ghost")
        self.load.clicked.connect(self._on_load)
        self.edit = QPushButton("Edit…")
        chip = QFrame()
        chip.setObjectName("fileChip")
        text = QVBoxLayout()
        text.setSpacing(0)
        text.addWidget(self.title)
        text.addWidget(self.meta)
        row = QHBoxLayout(chip)
        row.setContentsMargins(12, 8, 8, 8)
        row.setSpacing(12)
        row.addWidget(icon_label("sliders-horizontal", theme.ACCENT, _ICON_SIZE))
        row.addLayout(text, 1)
        row.addWidget(self.load)
        row.addWidget(self.edit)
        self.notice = WarningList()  # why a profile can't be used, or what it left out
        self.format_warning = WarningList()  # its top margin spaces it from the chip

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(chip)
        layout.addWidget(self.notice)
        layout.addWidget(self.format_warning)
        self._show_notice(None)

    def load_file(self, path: Path) -> None:
        """Load the profile at ``path``, or say why it can't be used."""
        try:
            loaded = profiles.load(path)

            if self._is_reveal:
                profile.refuse_unknown(loaded, path.name)
            else:
                profile.refuse_missing_costs(loaded, path.name)
        except InvalidProfileError as exc:
            self._show_notice(f"{exc}.")

            return
        except OSError as exc:
            self._show_notice(f"Can't read {path.name}. {exc.strerror}.")

            return

        self._loaded = (path, loaded)
        self._show_notice(None if self._is_reveal else profile.unknown_warning(loaded))
        self.profile_loaded.emit(loaded)

    def show_settings(self, settings: Settings, groups: Sequence[Group]) -> None:
        """Sum up the changes among the parameters of ``groups`` (the ones hiding or revealing
        reads), in the groups' order, as the editor lists them."""
        params = {key(group, param): param for group in groups for param in group.params}
        values = settings.as_dict()
        changes = [(param, values[name]) for name, param in params.items() if name in values]

        if changes:
            labels = [change_label(param, value) for param, value in changes[:_SHOWN_CHANGES]]
            more = len(changes) - _SHOWN_CHANGES
            title = f"{len(changes)} changed from Standard"
            meta = " · ".join(labels) + (f" · +{more}" if more > 0 else "")
        else:
            title = "Standard values"
            meta = (
                f"{_counted(len(params), 'parameter')} in {_counted(len(groups), 'group')} "
                "for this method"
            )

        loaded = self._loaded

        if loaded is not None and loaded[1].settings == settings:  # not edited since
            title = profiles.title(*loaded)
            meta = f"{loaded[0].name} · {meta}"

        self.title.setText(title)
        self.meta.setText(meta)
        self.meta.setToolTip(plain_tooltip(meta))  # a profile's file name, say
        self.load.setText(LOAD if loaded is None else CLEAR)
        is_format = [param.kind is Kind.FORMAT for param, _value in changes]
        formats = 0 if self._is_reveal else sum(is_format)
        self.format_warning.set_messages([format_warning(formats)] if formats else [])
        self.format_warning.setVisible(formats > 0)

    def _on_load(self) -> None:
        """Load a profile, or clear the one loaded: back to Standard's values."""
        if self._loaded is None:
            path = profiles.choose(self)

            if path is not None:
                self.load_file(path)

            return

        self._loaded = None
        self._show_notice(None)
        self.cleared.emit()

    def _show_notice(self, message: str | None) -> None:
        self.notice.set_messages([] if message is None else [message])
        self.notice.setVisible(message is not None)
