"""Reveal page: carrier file, Research mode's method, encryption and parameters, password,
destination folder and the result card."""

from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QWidget

from cipherstitch import catalog, payload, service
from cipherstitch.catalog import CipherId, CipherKey, MethodKey
from cipherstitch.errors import NoHiddenDataError, UnsupportedFormatError
from cipherstitch.gui import theme
from cipherstitch.gui.file_dialogs import existing_folder, open_file_name
from cipherstitch.gui.pages.base import OperationPage, error_message, show_in_folder, unreadable
from cipherstitch.gui.widgets.common import CARRIER_ICONS, column, display_path, icon_button
from cipherstitch.gui.widgets.file_chip import FileChip
from cipherstitch.gui.widgets.parameter_editor import ParameterScope
from cipherstitch.gui.widgets.parameters import ParametersRow
from cipherstitch.gui.widgets.password import PasswordEdit
from cipherstitch.gui.widgets.result_card import ResultCard
from cipherstitch.gui.widgets.warning_list import WarningList
from cipherstitch.params import registry
from cipherstitch.params.profile import Profile
from cipherstitch.params.settings import STANDARD as STANDARD_SETTINGS
from cipherstitch.params.settings import Settings
from cipherstitch.sizes import format_size

NO_SETTINGS_NOTE = (
    "Files carry no settings. Method, encryption and parameters must match the ones used for "
    "hiding."
)
STANDARD_HINT = (
    "Hidden in Research mode? Turn it on, then load the profile or set the same method, "
    "encryption and parameters."
)
RESEARCH_HINT = (
    "Check the method, encryption and parameters. They must match the ones used for hiding exactly."
)
ENCRYPTED_HINT = "Encrypted? Enter the password used for hiding."
STANDARD_PLACEHOLDER = "Leave empty if it was hidden without encryption"
RESEARCH_PLACEHOLDER = "The password used for hiding"
UNENCRYPTED_WARNING = (
    "The file was hidden without encryption, so the password was not used and nothing proves "
    "who hid it or that it is unchanged."
)


def _fill(combo: QComboBox, entries: Sequence[catalog.Method | catalog.Cipher], index: int) -> None:
    """``entries`` as "name · tier", the one at ``index`` chosen."""
    combo.clear()

    for entry in entries:
        combo.addItem(f"{entry.name} · {entry.tier}")

    combo.setCurrentIndex(index)


Hints = tuple[tuple[str, ...], tuple[str, ...]]  # the password's and the mode's


def _hints(password: str | None, research: service.ResearchReveal | None) -> Hints:
    """What to check when a reveal with these inputs finds nothing: the password if none
    was typed and the payload may be encrypted (Standard mode always encrypts), and the
    mode's choices."""
    may_decrypt = research is None or research.cipher != CipherId.NONE
    mode = STANDARD_HINT if research is None else RESEARCH_HINT

    return (ENCRYPTED_HINT,) if password is None and may_decrypt else (), (mode,)


def _note(icon: str) -> WarningList:
    """A muted line with ``icon``, under a field."""
    return WarningList(icon=icon, color=theme.MUTED, role="muted", top_margin=0)


class RevealPage(OperationPage):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Reveal payload", "lock-key-open", parent)
        self.carrier_path: Path | None = None
        self._result: service.RevealResult | None = None
        self._is_research = False
        self._settings: Settings = STANDARD_SETTINGS  # Research's, kept while in Standard
        self._methods: tuple[MethodKey, ...] = ()
        self._ciphers: tuple[CipherKey, ...] = ()
        self._failure_hints = _hints(None, None)  # those of the last reveal
        self._was_password_typed = False  # for the last reveal
        self._build_form()
        self.set_research(False)

    def _build_form(self) -> None:
        self.carrier_chip = FileChip()
        self.carrier_chip.change_requested.connect(self._choose_carrier)
        self.form.add_row("File", self.carrier_chip)

        self.method = QComboBox()
        self.method.activated.connect(lambda _index: self._show_parameters())
        self.guard_wheel(self.method)
        method_label = self.form.add_row("Method", self.method)
        self.cipher = QComboBox()
        self.cipher.activated.connect(lambda _index: self._on_cipher())
        self.guard_wheel(self.cipher)
        cipher_label = self.form.add_row("Encryption", self.cipher)
        self.parameters = ParametersRow(is_reveal=True)
        self.parameters.profile_loaded.connect(self._use_profile)
        self.parameters.cleared.connect(lambda: self.set_settings(STANDARD_SETTINGS))
        self.no_settings_note = _note("info")
        self.no_settings_note.set_messages([NO_SETTINGS_NOTE])
        parameters = column(self.parameters, self.no_settings_note, spacing=8)
        parameters_label = self.form.add_row("Parameters", parameters)
        self._research_rows = (
            method_label,
            self.method,
            cipher_label,
            self.cipher,
            parameters_label,
            parameters,
        )

        self.password = PasswordEdit(STANDARD_PLACEHOLDER)
        self.password.textChanged.connect(self._clear_error)
        self.password.returnPressed.connect(self.trigger_action)
        self.error = WarningList(icon="x-circle", top_margin=0)
        self.password_hint = _note("key")
        self.hint = _note("flask")  # the flask means Research mode, as everywhere
        password = column(self.password, self.error, self.password_hint, self.hint, spacing=6)
        self.form.add_row("Password", password)

        downloads = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DownloadLocation
        )
        self._output_dir = Path(downloads)
        self.output_dir = QLineEdit(display_path(self._output_dir), readOnly=True)
        browse = icon_button("folder-open", self._choose_output_dir, "Choose folder")
        save_row = QWidget()
        row = QHBoxLayout(save_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(self.output_dir)
        row.addWidget(browse)
        self.form.add_row("Save to", save_row)

        self.result_card = ResultCard()
        self.result_card.show_button.clicked.connect(self._show_result)
        self.result_warning = WarningList(top_margin=0)  # a password typed for a plain payload
        self._result_row = column(self.result_card, self.result_warning, spacing=8)
        self.result_label = self.form.add_row("Result", self._result_row)
        self._show_result_card(visible=False)

    # — public: used by drag and drop —

    def set_carrier_file(self, path: Path | None) -> None:
        self._show_result_card(visible=False)
        self._clear_error()
        self._use_carrier(path)

    def _use_carrier(self, path: Path | None) -> None:
        """Show ``path`` and the methods that may have hidden a payload in it. A file the mode
        can't reveal from is dropped with the reason: one only an extension writes, say."""
        self.carrier_path = path

        if path is None:
            self.carrier_chip.set_file("file", "No file selected", "A file produced by Hide")
        else:
            error = self._show_carrier(path)

            if error is not None:
                self.carrier_path = None
                self.footer.set_status("error", error)

        self._show_methods()
        self._update_action()

    # — public: Research mode —

    def set_research(self, is_research: bool) -> None:
        """Research mode reveals with only the method, encryption and parameters set here,
        and offers the extensions' too."""
        self._is_research = is_research

        for widget in self._research_rows:
            widget.setVisible(is_research)

        self.password.setPlaceholderText(
            RESEARCH_PLACEHOLDER if is_research else STANDARD_PLACEHOLDER
        )
        self._clear_error()

        if not self.footer.is_working:
            self.footer.clear_status()  # a reason the other mode gave, say for a dropped file

        self._show_ciphers()
        self._use_carrier(self.carrier_path)
        self._on_cipher()

    def set_settings(self, settings: Settings) -> None:
        """Research mode's parameters; Standard mode ignores them."""
        self._settings = settings
        self._show_parameters()

    def _use_profile(self, loaded: Profile) -> None:
        """A profile fills the method (if the file type can hold it), the encryption and the
        parameters."""
        if loaded.method in self._methods:
            self.method.setCurrentIndex(self._methods.index(loaded.method))

        if loaded.cipher in self._ciphers:
            self.cipher.setCurrentIndex(self._ciphers.index(loaded.cipher))
            self._on_cipher()

        self.set_settings(loaded.settings)

    def research_reveal(self) -> service.ResearchReveal:
        """What a Research reveal tries: the method, encryption and parameters set here."""
        return service.ResearchReveal(self._method(), self._cipher(), self._settings)

    def parameter_scope(self) -> ParameterScope:
        """The parameters revealing with the method and encryption set here reads."""
        method, cipher = self._method(), self._cipher()
        groups = registry.groups_for(method, is_encrypted=cipher != CipherId.NONE)

        return ParameterScope(method, cipher, groups, self._settings, is_reveal=True)

    # — state —

    def _show_carrier(self, path: Path) -> str | None:
        """Show ``path`` in the chip; the error message if no method can have hidden a
        payload in a file like it."""
        is_research = self._is_research
        carrier = service.carrier_for_path(path, is_research=is_research)
        icon = "file" if carrier is None else CARRIER_ICONS[carrier.kind]

        try:
            size = path.stat().st_size
        except OSError as exc:
            self.carrier_chip.set_file(icon, path.name, "Unreadable file")

            return unreadable(path, exc)

        self.carrier_chip.set_file(
            icon, path.name, f"{display_path(path.parent)} · {format_size(size)}"
        )

        try:
            service.reveal_methods(path, is_research=is_research)
        except UnsupportedFormatError as exc:
            return str(exc)

        return None

    def _method(self) -> MethodKey:
        return self._methods[self.method.currentIndex()]

    def _cipher(self) -> CipherKey:
        return self._ciphers[self.cipher.currentIndex()]

    def _show_methods(self) -> None:
        """The methods that may have hidden a payload in the file, the extensions' too in
        Research mode. The chosen one stays if it can have, else the first is chosen."""
        chosen = self._method() if self._methods else None
        self._methods = service.reveal_methods(self.carrier_path, is_research=self._is_research)
        index = self._methods.index(chosen) if chosen in self._methods else 0
        _fill(self.method, [catalog.method(method) for method in self._methods], index)
        self._show_parameters()

    def _show_ciphers(self) -> None:
        """Every built-in cipher, then in Research mode the extensions'. The chosen one stays
        if it is still listed, else the default is chosen. Standard mode hides the row but
        keeps the built-ins, so Research mode finds its choice again."""
        chosen = self._cipher() if self._ciphers else catalog.DEFAULT_CIPHER
        entries: Sequence[catalog.Cipher] = catalog.CIPHERS

        if self._is_research:
            entries = [catalog.cipher(cipher) for cipher in service.mode_ciphers(is_research=True)]

        self._ciphers = tuple(entry.id for entry in entries)
        index = self._ciphers.index(chosen if chosen in self._ciphers else catalog.DEFAULT_CIPHER)
        _fill(self.cipher, entries, index)

    def _on_cipher(self) -> None:
        """A plain frame takes no password, and the cipher decides the parameter groups."""
        is_plain = self._is_research and self._cipher() == CipherId.NONE
        self.password.setEnabled(not is_plain)
        self._show_parameters()

    def _show_parameters(self) -> None:
        """Research mode only: Standard mode hides the row, and listing DCT's or LSB's cost
        functions loads the extensions. ``set_research`` shows them again."""
        if not self._is_research:
            return

        scope = self.parameter_scope()
        self.parameters.show_settings(scope.settings, scope.groups)

    def _show_result_card(self, *, visible: bool) -> None:
        self._result_row.setVisible(visible)
        self.result_label.setVisible(visible)

    def _clear_error(self) -> None:
        self._show_error(())

    def _show_error(self, error: tuple[str, ...], hints: Hints = ((), ())) -> None:
        lines = ((self.error, error), (self.password_hint, hints[0]), (self.hint, hints[1]))

        for widget, messages in lines:
            widget.set_messages(messages)
            widget.setVisible(bool(messages))

        theme.set_property(self.password, "error", bool(error))

    def _update_action(self) -> None:
        self.footer.primary.setEnabled(self.carrier_path is not None)

    # — dialogs —

    def _choose_carrier(self) -> None:
        suffixes = service.reveal_suffixes(is_research=self._is_research)
        patterns = " ".join(f"*{suffix}" for suffix in suffixes)
        chosen = open_file_name(self, "Choose file", "", f"Carrier files ({patterns})")

        if chosen:
            self.footer.clear_status()
            self.set_carrier_file(Path(chosen))

    def _choose_output_dir(self) -> None:
        chosen = existing_folder(self, "Save revealed file to", str(self._output_dir))

        if chosen:
            self._output_dir = Path(chosen)
            self.output_dir.setText(display_path(self._output_dir))

    # — run —

    def _on_action(self) -> None:
        carrier_path = self.carrier_path

        if carrier_path is None:
            return

        output_dir = self._output_dir
        password = self.password.text() or None
        research = self.research_reveal() if self._is_research else None
        self._failure_hints = _hints(password, research)
        self._was_password_typed = password is not None
        self._clear_error()
        self._show_result_card(visible=False)
        self._run(
            lambda report: service.reveal(carrier_path, output_dir, password, report, research)
        )

    def _on_succeeded(self, result: object) -> None:
        if not isinstance(result, service.RevealResult):
            return

        self._result = result
        self.result_card.show_result(
            result.path, result.size, is_verified=result.is_integrity_verified
        )
        # A success card alone would read as if the password had checked the file.
        is_unused = self._was_password_typed and result.cipher == CipherId.NONE
        self.result_warning.set_messages([UNENCRYPTED_WARNING] if is_unused else [])
        self.result_warning.setVisible(is_unused)
        self._show_result_card(visible=True)

    def _on_failed(self, error: Exception) -> None:
        """Nothing found is one error whatever the cause, with what to check for the reveal
        that ran. A payload found damaged says so, with nothing to check."""
        if not isinstance(error, NoHiddenDataError):
            self.footer.set_status("error", error_message(error))
        elif str(error).startswith(payload.NOT_FOUND_MESSAGE):
            self._show_error((f"{error}.",), self._failure_hints)
        else:
            self._show_error((f"{error}.",))

    def _show_result(self) -> None:
        if self._result is not None:
            show_in_folder(self._result.path)
