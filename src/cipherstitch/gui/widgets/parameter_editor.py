"""The Research parameters sheet over the window body, built from the registry alone: the
groups hiding or revealing reads, one row per parameter, the presets and the kind legend."""

from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from cipherstitch import catalog
from cipherstitch.catalog import CipherKey, MethodId, MethodKey
from cipherstitch.errors import InvalidParameterError
from cipherstitch.gui import profiles, theme
from cipherstitch.gui.widgets.common import (
    clear_layout,
    icon_label,
    label,
    plain_label,
    row,
    set_icon,
)
from cipherstitch.gui.widgets.marquee_combo import MarqueeComboBox
from cipherstitch.gui.widgets.parameter_rows import ParamRow, kind_tag
from cipherstitch.gui.widgets.parameters import format_warning
from cipherstitch.gui.widgets.warning_list import WarningList
from cipherstitch.hidden_text import has_hidden
from cipherstitch.params.model import Group, Kind, Value, key
from cipherstitch.params.presets import PRESETS
from cipherstitch.params.profile import MAX_NAME_LENGTH, Profile
from cipherstitch.params.settings import Settings

CUSTOM = "Custom (unsaved)"
EXPORT_NAME = "Research parameters"  # an export's suggested name for custom values
_LEGEND = {
    Kind.FORMAT: "Reveal needs the same value · not stored in the file",
    Kind.EMBEDDER: "Hiding only · Reveal unaffected",
    Kind.GUIDANCE: "A rule or warning, not part of the file",
}
_LEGEND_COLUMNS = 2
_GROUP_LIST_WIDTH = 200
_GROUP_ROW_HEIGHT = 36
_PRESETS_WIDTH = 210
_ICON_SIZE = 16


@dataclass(frozen=True, slots=True)
class ParameterScope:
    """What the sheet edits: the groups hiding or revealing with ``method`` and ``cipher``
    reads, and the page's values. Revealing needs the values the hiding used, whatever they
    are: no Format warning there."""

    method: MethodKey
    cipher: CipherKey
    groups: tuple[Group, ...]
    settings: Settings
    is_reveal: bool = False


class _GroupItem(QWidget):
    """A group in the list: its icon, its name and how many parameters it has or changed."""

    def __init__(self, group: Group) -> None:
        super().__init__()
        self.group = group
        self.icon = icon_label(group.icon, theme.NEUTRAL_400, _ICON_SIZE)
        self.count = label(role="caption")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(10)
        layout.addWidget(self.icon)
        layout.addWidget(plain_label(group.label, "itemName"), 1)
        layout.addWidget(self.count)

    def show_state(self, changed: int, *, is_selected: bool) -> None:
        self.count.setText(f"{changed} changed" if changed else str(len(self.group.params)))
        theme.set_property(self.count, "role", "warning" if changed else "caption")
        color = theme.ACCENT if is_selected else theme.NEUTRAL_400
        set_icon(self.icon, self.group.icon, color, _ICON_SIZE)


class ParameterEditor(QFrame):
    """Emits ``closed(Settings)`` when Done or Esc closes it: hiding reads the settings once,
    not at every step. A value the parameters cannot work with never gets in: the row goes
    back and the group says why."""

    closed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("parameterSheet")
        self._settings = Settings()
        self._groups: tuple[Group, ...] = ()
        self._choices = ""  # "{method} · {cipher}", the subtitle's start
        self._rows: list[tuple[str, ParamRow]] = []
        self._group_items: list[_GroupItem] = []
        self._scope = ParameterScope(MethodId.LSB, catalog.DEFAULT_CIPHER, (), Settings())
        self._saved: list[tuple[str, Settings]] = []  # profiles used lately, by name
        self._presets: list[tuple[str, Settings]] = []  # what the menu offers, by name

        self.subtitle = plain_label(role="muted")  # an extension's method or cipher, say
        self.presets = MarqueeComboBox()
        self.presets.setFixedWidth(_PRESETS_WIDTH)
        self.presets.activated.connect(self._on_preset)
        self.export = QPushButton("Export…")
        self.export.clicked.connect(self._on_export)
        self.done = QPushButton("Done")
        self.done.setProperty("variant", "primary")
        self.done.clicked.connect(self._close)
        self.format_warning = WarningList()
        self.group_list = QListWidget()
        self.group_list.setObjectName("groupList")
        self.group_list.setFixedWidth(_GROUP_LIST_WIDTH)
        self.group_list.currentRowChanged.connect(self._show_group)
        self.group_title = plain_label(role="sheetTitle")  # an extension's group, say
        self.group_note = plain_label(role="muted", wrap=True)
        self.reset_group = QPushButton("Reset group")
        self.reset_group.setProperty("variant", "ghost")
        self.reset_group.setIcon(theme.icon("arrow-counter-clockwise", theme.ACCENT))
        self.reset_group.clicked.connect(self._on_reset_group)
        self.error = WarningList()
        self._rows_layout = QVBoxLayout()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._header())
        layout.addWidget(self._body(), 1)
        self.hide()

    def show_for(self, scope: ParameterScope) -> None:
        """Open on the scope's groups and values."""
        selected = self._selected_group()
        self._scope = scope
        self._choices = f"{catalog.method(scope.method).name} · {catalog.cipher(scope.cipher).name}"
        self._saved = _saved_profiles()
        self._groups = scope.groups
        self._settings = scope.settings
        self.group_list.blockSignals(True)
        self.group_list.clear()
        self._group_items = [_GroupItem(group) for group in self._groups]

        for group_item in self._group_items:
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, _GROUP_ROW_HEIGHT))
            self.group_list.addItem(item)
            self.group_list.setItemWidget(item, group_item)

        ids = [group.id for group in self._groups]
        self.group_list.setCurrentRow(ids.index(selected) if selected in ids else 0)
        self.group_list.blockSignals(False)
        self._show_group(self.group_list.currentRow())
        self.show()
        self.raise_()
        self.group_list.setFocus()

    def settings(self) -> Settings:
        return self._settings

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt override)
        """Esc closes the sheet: focus is inside it, so the key reaches it from any row."""
        if event.key() == Qt.Key.Key_Escape and self.isVisible():
            self._close()

            return

        super().keyPressEvent(event)

    # — layout —

    def _header(self) -> QWidget:
        title = QWidget()
        text = QVBoxLayout(title)
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(0)
        text.addWidget(label("Research parameters", "sheetTitle"))
        text.addWidget(self.subtitle)
        top = QHBoxLayout()
        top.setSpacing(12)
        top.addWidget(icon_label("sliders-horizontal", theme.ACCENT, 22))
        top.addWidget(title, 1)
        top.addWidget(self.presets)
        top.addWidget(self.export)
        top.addWidget(self.done)

        legend = QGridLayout()  # the design's legend wraps after two of the three kinds
        legend.setHorizontalSpacing(16)
        legend.setVerticalSpacing(6)
        legend.setColumnStretch(_LEGEND_COLUMNS, 1)

        for index, (kind, meaning) in enumerate(_LEGEND.items()):
            item = row(kind_tag(kind), label(meaning, "caption"), spacing=6)
            line, column = divmod(index, _LEGEND_COLUMNS)
            legend.addWidget(item, line, column)

        header = QWidget()
        layout = QVBoxLayout(header)
        layout.setContentsMargins(24, 18, 24, 14)
        layout.setSpacing(12)
        layout.addLayout(top)
        layout.addLayout(legend)
        layout.addWidget(self.format_warning)

        return header

    def _body(self) -> QWidget:
        heading = QWidget()
        text = QVBoxLayout(heading)
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(4)
        text.addWidget(self.group_title)
        text.addWidget(self.group_note)
        top = QHBoxLayout()
        top.setSpacing(12)
        top.addWidget(heading, 1)
        top.addWidget(self.reset_group, 0, Qt.AlignmentFlag.AlignTop)

        rows = QWidget()
        rows.setLayout(self._rows_layout)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(4)
        scroll = QScrollArea()
        scroll.setObjectName("parameterRows")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(rows)

        detail = QFrame()
        detail.setObjectName("groupDetail")
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(20, 16, 20, 0)
        detail_layout.setSpacing(6)
        detail_layout.addLayout(top)
        detail_layout.addWidget(self.error)
        detail_layout.addWidget(scroll, 1)

        groups = QWidget()
        groups.setObjectName("groupPanel")
        group_layout = QVBoxLayout(groups)
        group_layout.setContentsMargins(12, 12, 12, 12)
        group_layout.addWidget(self.group_list)

        body = QWidget()
        body.setObjectName("sheetBody")
        layout = QHBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(groups)
        layout.addWidget(detail, 1)

        return body

    # — state —

    def _selected_group(self) -> str | None:
        index = self.group_list.currentRow()

        return self._groups[index].id if 0 <= index < len(self._groups) else None

    def _show_group(self, index: int) -> None:
        """Rows for the group at ``index``, built from its parameters."""
        if not 0 <= index < len(self._groups):
            return

        group = self._groups[index]
        self.group_title.setText(group.label)
        self.group_note.setText(group.note)
        self.error.set_messages([])
        clear_layout(self._rows_layout)
        self._rows = []

        for param in group.params:
            name = key(group, param)
            param_row = ParamRow(param)
            param_row.changed.connect(lambda value, name=name: self._change(name, value))
            param_row.reset.connect(lambda name=name: self._reset([name]))
            self._rows.append((name, param_row))
            self._rows_layout.addWidget(param_row)

        self._rows_layout.addStretch(1)
        self._show_settings()

    def _shown_names(self) -> list[str]:
        return [key(group, param) for group in self._groups for param in group.params]

    def _show_settings(self) -> None:
        """Every value, count and note from the current settings."""
        values = self._settings.as_dict()
        shown = self._shown_names()
        changed = [name for name in shown if name in values]
        format_changes = set(self._settings.format_changes())
        formats = [name for name in changed if name in format_changes]
        state = f"{len(changed)} changed · {len(formats)} format" if changed else "all Standard"
        self.subtitle.setText(f"{self._choices} · {state}")
        warns = bool(formats) and not self._scope.is_reveal
        self.format_warning.set_messages([format_warning(len(formats))] if warns else [])
        self.format_warning.setVisible(warns)
        selected = self.group_list.currentRow()

        for index, item in enumerate(self._group_items):
            names = {key(item.group, param) for param in item.group.params}
            item.show_state(len(names & set(changed)), is_selected=index == selected)

        for name, param_row in self._rows:
            param_row.show_value(self._settings[name])

        group_names = {name for name, _row in self._rows}
        self.reset_group.setEnabled(bool(group_names & set(changed)))
        self._show_presets(set(shown), {name: values[name] for name in changed})

    def _show_presets(self, shown: set[str], shown_changes: dict[str, Value]) -> None:
        """The presets and the profiles used lately for the parameters shown, matched on
        those alone, as the subtitle counts them."""
        offered = [(preset.label, preset.settings) for preset in PRESETS] + self._saved
        self._presets = [entry for entry in offered if set(entry[1].as_dict()) <= shown]
        self.presets.blockSignals(True)
        self.presets.clear()

        for name, _settings in self._presets:
            self.presets.addItem(name)

        current = next(
            (
                index
                for index, (_name, settings) in enumerate(self._presets)
                if settings.as_dict() == shown_changes
            ),
            None,
        )

        if current is None:
            self.presets.addItem(CUSTOM)
            current = len(self._presets)

        self.presets.setCurrentIndex(current)
        self.presets.blockSignals(False)

    def _close(self) -> None:
        self.hide()
        self.closed.emit(self._settings)

    def _apply(self, settings: Settings) -> None:
        self._settings = settings
        self.error.set_messages([])
        self._show_settings()

    def _refuse(self, error: InvalidParameterError) -> None:
        """Keep the current settings, show them again and say why."""
        self._show_settings()
        self.error.set_messages([str(error)])

    def _change(self, name: str, value: Value) -> None:
        try:
            self._apply(self._settings.replaced(name, value))
        except InvalidParameterError as exc:
            self._refuse(exc)

    def _reset(self, names: list[str]) -> None:
        try:
            self._apply(self._settings.reset(names))
        except InvalidParameterError as exc:
            self._refuse(exc)

    def _on_reset_group(self) -> None:
        self._reset([name for name, _row in self._rows])

    def _on_preset(self, index: int) -> None:
        """Only the parameters shown take the preset's values: other methods' stay."""
        if index < len(self._presets):
            kept = self._settings.reset(self._shown_names()).as_dict()
            self._apply(Settings.of({**kept, **self._presets[index][1].as_dict()}))

    def _on_export(self) -> None:
        """Save the values shown, with the method and encryption, for revealing later."""
        current = self.presets.currentText()
        path = profiles.choose_export(self, EXPORT_NAME if current == CUSTOM else current)

        if path is None:
            return

        shown = set(self._shown_names())
        others = [name for name in self._settings.as_dict() if name not in shown]
        scope = self._scope
        settings = self._settings.reset(others)
        name = _profile_name(path.stem)
        exported = Profile(settings, name=name, method=scope.method, cipher=scope.cipher)

        try:
            profiles.export(path, exported)
        except OSError as exc:
            self.error.set_messages([f"Can't save {path.name}. {exc.strerror}."])

            return

        self._saved = _saved_profiles()  # offered by its name from now on
        self._show_settings()


def _profile_name(stem: str) -> str:
    """The file's name as the profile's, within what a profile may hold: a name the reader
    refuses would lose the file from the recent list."""
    shown = "".join(char for char in stem if not has_hidden(char))

    return shown[:MAX_NAME_LENGTH]


def _saved_profiles() -> list[tuple[str, Settings]]:
    """The profiles used lately that this version reads whole, by name."""
    return [
        (profiles.title(path, saved), saved.settings)
        for path, saved in profiles.recent()
        if not saved.unknown and not saved.missing_costs
    ]
