"""Hide page: carrier, method, payload, encryption, password, Research parameters and
destination."""

import functools
from pathlib import Path

from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QWidget,
)

from cipherstitch import catalog, ciphers, service, standard
from cipherstitch.catalog import CarrierKind, CipherId, MethodKey
from cipherstitch.errors import CipherStitchError, UnsupportedFormatError
from cipherstitch.gui import theme
from cipherstitch.gui.compressions import Compression, CompressionQueue, Version, version
from cipherstitch.gui.file_dialogs import open_file_name, save_file_name
from cipherstitch.gui.pages.base import (
    OperationPage,
    error_message,
    print_cause,
    show_in_folder,
    unreadable,
    unreadable_file,
)
from cipherstitch.gui.widgets.capacity import CapacityMeter
from cipherstitch.gui.widgets.cipher_picker import CipherField
from cipherstitch.gui.widgets.common import (
    CARRIER_ICONS,
    OutputFormats,
    column,
    display_path,
    format_name,
    free_path,
    icon_button,
    icon_label,
    label,
    output_formats,
    row,
    usual_suffix,
)
from cipherstitch.gui.widgets.file_chip import FileChip
from cipherstitch.gui.widgets.method_picker import MethodField
from cipherstitch.gui.widgets.parameter_editor import ParameterScope
from cipherstitch.gui.widgets.parameters import ParametersRow
from cipherstitch.gui.widgets.password import PasswordField
from cipherstitch.gui.widgets.segmented import SegmentedControl
from cipherstitch.gui.widgets.warning_list import WarningList
from cipherstitch.params import registry
from cipherstitch.params.model import Cover, Group
from cipherstitch.params.settings import STANDARD as STANDARD_SETTINGS
from cipherstitch.params.settings import Settings
from cipherstitch.payload import HiddenFile
from cipherstitch.sizes import format_size

TEXT_PAYLOAD_NAME = "message.txt"
_PAYLOAD_FILE = "file"
_PAYLOAD_TEXT = "text"
_TEXT_MIN_HEIGHT = 90
_SAVE_LOCATIONS = {
    CarrierKind.IMAGE: QStandardPaths.StandardLocation.PicturesLocation,
    CarrierKind.AUDIO: QStandardPaths.StandardLocation.MusicLocation,
    CarrierKind.DOCUMENT: QStandardPaths.StandardLocation.DocumentsLocation,
    CarrierKind.TEXT: QStandardPaths.StandardLocation.DocumentsLocation,
}


class HidePage(OperationPage):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Hide payload", "lock-key", parent)
        self.carrier_path: Path | None = None
        self.payload_path: Path | None = None
        self._output: Path | None = None
        self._custom_output: Path | None = None  # picked in the save dialog
        self._chosen_format: str | None = None  # an output format picked by hand
        self._last_output: Path | None = None
        self._is_research = False
        self._settings: Settings = STANDARD_SETTINGS  # Research's, kept while in Standard
        # A RAW, PNG or TIFF for DCT is compressed in the background first, and describing
        # and estimating it afterwards reuse the result.
        self._compressions = CompressionQueue(self)
        self._compressions.done.connect(self._on_compressed)
        self._compressions.idle.connect(self._settle)
        self._compressed: Compression | None = None
        # A method that cannot use a carrier version, and why: describing and estimating
        # would otherwise run the failing work (a compression, say) on every keystroke.
        self._unusable: tuple[Version, MethodKey, str] | None = None

        self._build_form()
        self._connect()
        self.set_research(False)

    # — layout —

    def _build_form(self) -> None:
        self.carrier_kind = SegmentedControl(
            [(c.kind, c.label, CARRIER_ICONS[c.kind]) for c in catalog.CARRIERS]
        )
        self.carrier_chip = FileChip()
        self.form.add_row("Carrier", column(self.carrier_kind, self.carrier_chip, spacing=8))

        self.method = MethodField(catalog.CARRIERS[0].methods)
        self.form.add_row("Method", self.method)

        self.payload_kind = SegmentedControl(
            [(_PAYLOAD_FILE, "File", "file"), (_PAYLOAD_TEXT, "Text", "chat-text")]
        )
        self.payload_chip = FileChip(compact=True)
        self.text = QPlainTextEdit(placeholderText="Type or paste the message to hide…")
        self.text.setMinimumHeight(_TEXT_MIN_HEIGHT)
        self.capacity = CapacityMeter()
        payload = column(
            row(self.payload_kind, self.payload_chip), self.text, self.capacity, spacing=8
        )
        self.form.add_row("Payload", payload)

        self.detection = WarningList()
        self.detection_label = self.form.add_row("Detection", self.detection)

        self.cipher = CipherField()
        self.form.add_row("Encryption", self.cipher)

        self.password = PasswordField()
        self.password_label = self.form.add_row("Password", self.password)
        self.no_encryption_warning = row(
            icon_label("warning-circle", theme.ACCENT_300, 15),
            label("Without encryption anyone who finds the payload can read it.", "warning"),
        )
        self.warning_label = self.form.add_row("", self.no_encryption_warning)

        self.parameters = ParametersRow()
        self.parameters_label = self.form.add_row("Parameters", self.parameters)

        self.output = QLineEdit(readOnly=True)
        self.output_format = QComboBox()
        self.output_format.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.guard_wheel(self.output_format)
        browse = icon_button("folder-open", self._choose_output, "Choose where to save")
        save_row = QWidget()
        save_layout = QHBoxLayout(save_row)
        save_layout.setContentsMargins(0, 0, 0, 0)
        save_layout.setSpacing(8)
        save_layout.addWidget(self.output, 1)
        save_layout.addWidget(self.output_format)
        save_layout.addWidget(browse)
        self.form.add_row("Save to", save_row)

    def _connect(self) -> None:
        self.carrier_kind.changed.connect(lambda kind: self._on_carrier_kind(CarrierKind(kind)))
        self.carrier_chip.change_requested.connect(self._choose_carrier)
        self.method.method_changed.connect(self._on_method)
        self.payload_kind.changed.connect(self._on_payload_kind)
        self.payload_chip.change_requested.connect(self._choose_payload)
        self.text.textChanged.connect(self._refresh)
        self.cipher.cipher_changed.connect(self._refresh)
        self.password.line_edit.textChanged.connect(self._refresh)
        self.password.repeat_edit.textChanged.connect(self._refresh)
        self.password.line_edit.returnPressed.connect(self.trigger_action)
        self.password.repeat_edit.returnPressed.connect(self.trigger_action)
        self.footer.show_in_folder_requested.connect(self._show_output)
        self.parameters.profile_loaded.connect(lambda loaded: self.set_settings(loaded.settings))
        self.parameters.cleared.connect(lambda: self.set_settings(STANDARD_SETTINGS))
        self.output_format.activated.connect(self._on_output_format)
        self._on_payload_kind(_PAYLOAD_FILE)

    # — public: used by drag and drop —

    def set_carrier_file(self, path: Path) -> bool:
        carrier = service.carrier_for_path(path, is_research=self._is_research)

        if carrier is None:
            self.footer.set_status(
                "warning", f"{path.suffix or path.name or path} is not a supported carrier"
            )

            return False

        reason = service.unavailable_carriers(is_research=self._is_research).get(carrier.kind)

        if reason is not None:
            self.footer.set_status("warning", reason)

            return False

        self.carrier_kind.set_value(carrier.kind)
        self._use_carrier_file(path)

        return True

    def set_payload_file(self, path: Path) -> None:
        self.payload_kind.set_value(_PAYLOAD_FILE)
        self.payload_path = path
        self._refresh()

    def carrier_icon(self) -> str:
        return CARRIER_ICONS[CarrierKind(self.carrier_kind.value())]

    # — public: Research mode —

    def set_research(self, is_research: bool) -> None:
        """Offer what the mode does. Switching to Standard moves every choice it does not
        offer to the first one it does: Image, the first method that can run, the default
        cipher. Switching to Research keeps every choice."""
        self._is_research = is_research
        self._require_password_strength()
        carriers = service.mode_carriers(is_research=is_research)

        if CarrierKind(self.carrier_kind.value()) not in carriers:
            self.carrier_kind.set_value(CarrierKind.IMAGE)

        self.carrier_kind.set_shown(carriers)
        self._forget_other_carrier()
        offered = service.mode_ciphers(is_research=is_research)

        if self.cipher.cipher().id not in offered:
            self.cipher.set_cipher(catalog.DEFAULT_CIPHER)

        self.cipher.set_ciphers([catalog.cipher(cipher) for cipher in offered])
        self._show_mode_methods(CarrierKind(self.carrier_kind.value()), self.carrier_path)
        self._set_carrier(self.carrier_path)  # the formats and the limit follow the mode

    def set_settings(self, settings: Settings) -> None:
        """Research mode's parameters; Standard mode ignores them."""
        self._settings = settings
        self._require_password_strength()
        self._set_carrier(self.carrier_path)  # the capacity follows the settings

    def _require_password_strength(self) -> None:
        """The strength the mode needs, as the service checks it: Research's default is
        Standard's too. The meter names the key derivation the mode's settings use."""
        options = self.options()
        minimum = options.min_password_strength
        message = standard.weak_password(minimum, is_research=options.is_research)
        self.password.set_min_strength(minimum, message, warns_when_empty=not options.is_research)
        self.password.set_kdf_name(service.kdf_name(options.mode_settings))

    # — state —

    def options(self) -> service.HideOptions:
        password = self.password.text() if self.cipher.cipher().id != CipherId.NONE else None

        return service.HideOptions(
            carrier=CarrierKind(self.carrier_kind.value()),
            method=self.method.method().id,
            cipher=self.cipher.cipher().id,
            password=password or None,
            is_research=self._is_research,
            settings=self._settings,
        )

    def _payload(self) -> tuple[str, int] | None:
        if self.payload_kind.value() == _PAYLOAD_TEXT:
            size = len(self.text.toPlainText().encode("utf-8"))

            return (TEXT_PAYLOAD_NAME, size) if size else None

        if self.payload_path is None or not self.payload_path.is_file():
            return None

        return self.payload_path.name, self.payload_path.stat().st_size

    def _on_carrier_kind(self, kind: CarrierKind) -> None:
        self._forget_other_carrier()
        self._show_mode_methods(kind, self.carrier_path)
        self._set_carrier(self.carrier_path)

    def _forget_other_carrier(self) -> None:
        """Forget a carrier file that the mode doesn't take as the shown carrier type: one of
        another type, or in Standard mode one whose suffix only an extension takes."""
        if self.carrier_path is None:
            return

        carrier = service.carrier_for_path(self.carrier_path, is_research=self._is_research)

        if carrier is None or carrier.kind != self.carrier_kind.value():
            self.carrier_path = None

    def _use_carrier_file(self, path: Path) -> None:
        """A newly chosen carrier file: a method that can run on it, and the output in its
        own format again (if the method can write it)."""
        self._chosen_format = None
        self._show_mode_methods(CarrierKind(self.carrier_kind.value()), path)
        self._set_carrier(path)

    def _show_mode_methods(self, kind: CarrierKind, path: Path | None) -> None:
        """Offer the mode's methods for ``kind``. Keep the shown one if it can run on
        ``path``, else show the first one that can: a camera RAW, for one, is only for DCT."""
        is_research = self._is_research
        methods = [catalog.method(m) for m in service.mode_methods(kind, is_research=is_research)]
        unavailable = service.unavailable_methods(
            kind, path, is_research=is_research, settings=self._settings
        )
        usable = [method for method in methods if method.id not in unavailable]
        shown = self.method.method()

        if shown not in usable:
            listed = shown if shown in methods else methods[0]
            shown = next(iter(usable), listed)

        self.method.set_methods(methods, shown=shown)

    def _on_method(self) -> None:
        self._set_carrier(self.carrier_path)  # capacity depends on the method

    def _output_suffixes(self) -> tuple[str, ...]:
        options = self.options()

        return service.output_suffixes(
            options.method, self.carrier_path, is_research=options.is_research
        )

    def _output_format(self, formats: OutputFormats) -> str:
        """The format picked by hand, else the carrier's own, else the method's first."""
        own = None if self.carrier_path is None else usual_suffix(self.carrier_path.suffix.lower())

        return next((f for f in (self._chosen_format, own) if f in formats), next(iter(formats)))

    def _suggest_output(self) -> None:
        """Default destination: the cover's name, made free (``photo-2.png``), in the user's
        media folder and the output format (FLAC in, FLAC out), and none without a cover. The
        tool's name would mark the file. A custom destination only gets its suffix corrected,
        and moves on to a free name after each hide (:meth:`_on_succeeded`). The format picker
        follows."""
        formats = output_formats(self._output_suffixes())
        chosen = self._output_format(formats)
        suffixes = formats[chosen]
        current = None if self._output is None else self._output.suffix.lower()
        own = None if self.carrier_path is None else self.carrier_path.suffix.lower()
        suffix = next((s for s in (current, own) if s in suffixes), suffixes[0])  # .tif or .tiff
        self._show_output_formats(formats, chosen)

        if self._custom_output is not None:
            if self._output is not None and current != suffix:
                self._set_output(free_path(self._output.parent, self._output.stem, suffix))

            return

        if self.carrier_path is None:
            self._output = None
            self.output.clear()

            return

        kind = CarrierKind(self.carrier_kind.value())
        folder = QStandardPaths.writableLocation(_SAVE_LOCATIONS[kind])
        self._set_output(free_path(Path(folder), self.carrier_path.stem, suffix))

    def _show_output_formats(self, formats: OutputFormats, chosen: str) -> None:
        """A choice when the method can write several formats, else the one it writes."""
        self.output_format.clear()

        for usual in formats:
            self.output_format.addItem(format_name(usual), usual)

        self.output_format.setCurrentIndex(list(formats).index(chosen))
        self.output_format.setEnabled(len(formats) > 1)

    def _on_output_format(self, index: int) -> None:
        self._chosen_format = self.output_format.itemData(index)
        self._suggest_output()

    def _set_output(self, path: Path) -> None:
        self._output = path
        self.output.setText(display_path(path))

    def _on_payload_kind(self, kind: str) -> None:
        self.payload_chip.setVisible(kind == _PAYLOAD_FILE)
        self.text.setVisible(kind == _PAYLOAD_TEXT)
        self._refresh()

    def _set_carrier(self, path: Path | None) -> None:
        self.carrier_path = path
        error = self._show_carrier(path)

        if error is not None:
            self.carrier_path = None

        self._suggest_output()
        self._refresh()

        if error is not None:
            self.footer.set_status("error", error)

    def _show_carrier(self, path: Path | None) -> str | None:
        """Show ``path`` in the carrier chip; the error message if it cannot be read."""
        kind = CarrierKind(self.carrier_kind.value())
        icon = CARRIER_ICONS[kind]

        if path is None:
            suffixes = service.carrier_suffixes(kind, is_research=self._is_research)
            names = dict.fromkeys(format_name(suffix) for suffix in suffixes)
            self.carrier_chip.set_file(icon, "No carrier selected", " · ".join(names))

            return None

        if self._is_compressing(path):
            self._show_compressing(path)

            return None

        options = self.options()
        # Straight to the default method, where describing falls back after a failure
        described = None if self._unusable_reason(path, options.method) else options.method

        try:
            details = service.describe_carrier(
                path, kind, described, options.mode_settings, is_research=options.is_research
            )
        except (CipherStitchError, OSError) as exc:
            print_cause(exc)
            self.carrier_chip.set_file(icon, path.name, "Unreadable file")

            return unreadable(path, exc)

        capacity = (
            f"holds up to {format_size(details.capacity)}"
            if details.capacity is not None
            else "capacity unknown"
        )
        self.carrier_chip.set_file(icon, path.name, f"{details.description} · {capacity}")

        return None

    def _is_compressing(self, path: Path | None) -> bool:
        """Whether ``path`` must be compressed before it can be described: a RAW, or a PNG or
        TIFF for DCT, not yet compressed in this version at these settings."""
        if path is None or not service.compresses_carrier(path, self.options().method):
            return False

        compression = self._compression(path)

        return compression is not None and compression != self._compressed

    def _compression(self, path: Path) -> Compression | None:
        """The version of ``path`` and the settings it would be compressed at: each setting
        that changes the compression or the capacity is cached apart."""
        current = version(path)

        return None if current is None else (current, self.options().mode_settings)

    def _show_compressing(self, path: Path) -> None:
        """Compress ``path`` on a worker thread unless that is under way, and say so."""
        kind = CarrierKind(self.carrier_kind.value())
        self._compress(path, kind)
        self.carrier_chip.set_file(CARRIER_ICONS[kind], path.name, "Compressing to JPEG…")

    def _compress(self, path: Path, kind: CarrierKind) -> None:
        """Compress ``path`` on a worker thread (seconds for a large RAW), then show it."""
        compression = self._compression(path)

        if compression is None:
            return

        options = self.options()

        def job(_report: service.ProgressCallback) -> None:
            service.describe_carrier(
                path, kind, options.method, options.mode_settings, is_research=options.is_research
            )
            service.estimate(path, "", 0, options)

        remember = functools.partial(self._remember_unusable, compression[0], options.method)
        self._compressions.request(compression, job, remember)

    def has_tasks(self) -> bool:
        return super().has_tasks() or self._compressions.is_running

    def cancel_tasks(self) -> None:
        """Compressing has no steps to stop at, so it runs on, but its outcome is dropped."""
        super().cancel_tasks()
        self._compressions.cancel()

    def _on_compressed(self, compression: Compression) -> None:
        compressed = compression[0]
        path = compressed[0]

        if path != self.carrier_path:
            return  # another carrier was chosen meanwhile

        # Also after a failure: describing the carrier again reports why. A version written
        # meanwhile gets compressed in turn, and so do other settings.
        if version(path) == compressed:
            self._compressed = compression

        self._set_carrier(path)

    def _remember_unusable(
        self, version: Version | None, method: MethodKey, error: Exception
    ) -> None:
        """Remember that ``method`` cannot use this carrier version. Other errors may not
        last (a file being written) or depend on more than the method."""
        if version is not None and isinstance(error, UnsupportedFormatError):
            self._unusable = (version, method, str(error))

    def _unusable_reason(self, path: Path | None, method: MethodKey) -> str | None:
        """Why ``method`` cannot use this version of ``path``, if it failed to."""
        if path is None or self._unusable is None:
            return None

        unusable, unusable_method, reason = self._unusable

        return reason if (unusable, unusable_method) == (version(path), method) else None

    def _refresh(self) -> None:
        if self.footer.is_working:
            return

        if self.carrier_path is not None and not self.carrier_path.is_file():
            self._set_carrier(self.carrier_path)  # gone since it was chosen: it says why

            return

        self.footer.clear_status()
        options = self.options()
        self._show_encryption_rows(is_encrypted=options.is_encrypted)
        self._show_unavailable_options(options)
        self._show_parameters(options)

        payload = self._payload()
        self._show_payload(payload)
        is_compressing = self._is_compressing(self.carrier_path)
        estimate = self._show_capacity(options, payload, is_compressing=is_compressing)
        excess = estimate.excess if estimate is not None else 0

        payload_path = self.payload_path if self.payload_kind.value() == _PAYLOAD_FILE else None
        reason = (
            unreadable_file(payload_path)  # gone since it was chosen
            or service.unavailable_reason(options, self.carrier_path)
            or self._unusable_reason(self.carrier_path, options.method)
        )
        self._show_detection_risks(options, estimate, can_hide=reason is None)
        warning = self._warning(reason, estimate, is_encrypted=options.is_encrypted)

        if is_compressing and self.carrier_path is not None:
            name = self.carrier_path.name
            self._show_compressing(self.carrier_path)  # starts it for a version written since
            self.footer.show_busy(f"Compressing {name} to JPEG to measure its capacity…")
        elif warning:
            self.footer.set_status("warning", warning)

        is_possible = reason is None and not excess and not is_compressing
        self.footer.primary.setEnabled(is_possible and self._is_ready(options, payload))

    def _show_encryption_rows(self, *, is_encrypted: bool) -> None:
        """The password rows with encryption, the warning row without it."""
        password_row: tuple[QWidget, ...] = (self.password_label, self.password)
        warning_row: tuple[QWidget, ...] = (self.warning_label, self.no_encryption_warning)

        for widget in password_row:
            widget.setVisible(is_encrypted)

        for widget in warning_row:
            widget.setVisible(not is_encrypted)

    def _show_unavailable_options(self, options: service.HideOptions) -> None:
        """Grey out the offered methods and ciphers that cannot run with the rest."""
        is_research = options.is_research
        self.method.set_unavailable(
            service.unavailable_methods(
                options.carrier,
                self.carrier_path,
                is_research=is_research,
                settings=options.settings,
            )
        )
        self.cipher.set_unavailable(service.unavailable_ciphers(is_research=is_research))

    def _show_parameters(self, options: service.HideOptions) -> None:
        """Research mode's Parameters row, for the parameters hiding with ``options`` reads."""
        for widget in (self.parameters_label, self.parameters):
            widget.setVisible(options.is_research)

        if options.is_research:
            self.parameters.show_settings(options.settings, self._parameter_groups(options))

    def parameter_scope(self) -> ParameterScope:
        """The parameters hiding with the current choices reads, and their values."""
        options = self.options()
        groups = self._parameter_groups(options)

        return ParameterScope(options.method, options.cipher, groups, options.settings)

    def _parameter_groups(self, options: service.HideOptions) -> tuple[Group, ...]:
        """The parameter groups hiding with ``options`` reads, for this carrier."""
        path = self.carrier_path
        is_precover = path is not None and service.compresses_carrier(path, options.method)
        cover = None if path is None else Cover.PRECOVER if is_precover else Cover.JPEG

        return registry.groups_for(options.method, is_encrypted=options.is_encrypted, cover=cover)

    def _show_payload(self, payload: tuple[str, int] | None) -> None:
        if payload is None:
            self.payload_chip.set_file("file", "Drop a file here or click to choose", "")
        elif self.payload_kind.value() == _PAYLOAD_FILE:
            self.payload_chip.set_file("file", payload[0], format_size(payload[1]))

    def _show_capacity(
        self,
        options: service.HideOptions,
        payload: tuple[str, int] | None,
        *,
        is_compressing: bool,
    ) -> service.Estimate | None:
        """Show the room the payload takes; its estimate if the carrier and payload are set
        and the carrier is not being compressed."""
        name, size = payload or ("", 0)

        if self.carrier_path is None or is_compressing:
            sealed = ciphers.get(options.cipher).sealed_size(size) if size else 0
            self.capacity.show_estimate(sealed, None, is_encrypted=options.is_encrypted)

            return None

        try:
            estimate = self._estimate(self.carrier_path, name, size, options)
        except (CipherStitchError, OSError):
            self.capacity.show_estimate(size, None, is_encrypted=options.is_encrypted)

            return None

        self.capacity.show_estimate(
            estimate.required if size else 0,
            estimate.available,
            is_encrypted=options.is_encrypted,
            is_conspicuous=bool(size) and service.is_conspicuous(estimate),
            limit=estimate.limit,
            advised=estimate.advised,
        )

        return estimate if size else None

    def _estimate(
        self, path: Path, name: str, size: int, options: service.HideOptions
    ) -> service.Estimate:
        """The service's estimate, or the error of a method that cannot use this version of
        the carrier, raised again without running the failing work again."""
        reason = self._unusable_reason(path, options.method)

        if reason is not None:
            raise UnsupportedFormatError(reason)

        try:
            return service.estimate(path, name, size, options)
        except UnsupportedFormatError as exc:
            self._remember_unusable(version(path), options.method, exc)

            raise

    def _show_detection_risks(
        self, options: service.HideOptions, estimate: service.Estimate | None, *, can_hide: bool
    ) -> None:
        """The Detection row, shown while the options can run and something is risky."""
        risks = self._detection_risks(options, estimate) if can_hide else ()
        self.detection.set_messages(risks)

        for widget in (self.detection_label, self.detection):
            widget.setVisible(bool(risks))

    def _detection_risks(
        self, options: service.HideOptions, estimate: service.Estimate | None
    ) -> tuple[str, ...]:
        try:
            return service.detection_risks(options, self.carrier_path, estimate)
        except (CipherStitchError, OSError):
            return ()  # the carrier has become unreadable; hiding reports why

    def _warning(
        self, reason: str | None, estimate: service.Estimate | None, *, is_encrypted: bool
    ) -> str | None:
        if reason:
            return reason

        if estimate is not None and estimate.excess:
            excess = format_size(estimate.excess)

            if not self._is_research:
                return standard.over_limit(excess, estimate.advised)

            return f"Payload is {excess} too large for this carrier"

        if is_encrypted and self.password.is_mismatched():
            return "Passwords do not match"

        return None

    def _is_ready(self, options: service.HideOptions, payload: tuple[str, int] | None) -> bool:
        """Every input is filled in, and the password is set and confirmed if needed."""
        has_files = (
            self.carrier_path is not None and payload is not None and self._output is not None
        )
        has_password = not options.is_encrypted or (
            bool(options.password)
            and self.password.is_confirmed()
            and self.password.is_strong_enough()
        )

        return has_files and has_password

    # — dialogs —

    def _choose_carrier(self) -> None:
        carrier = catalog.carrier(CarrierKind(self.carrier_kind.value()))
        suffixes = service.carrier_suffixes(carrier.kind, is_research=self._is_research)
        patterns = " ".join(f"*{suffix}" for suffix in suffixes)
        chosen = open_file_name(self, "Choose carrier", "", f"{carrier.label} files ({patterns})")

        if chosen:
            self._use_carrier_file(Path(chosen))

    def _choose_payload(self) -> None:
        chosen = open_file_name(self, "Choose file to hide")

        if chosen:
            self.set_payload_file(Path(chosen))

    def _choose_output(self) -> None:
        """One filter per format. A file name in another format, or in none, takes the
        format of the filter picked in the dialog."""
        formats = output_formats(self._output_suffixes())
        filters = {
            usual: f"{format_name(usual)} ({' '.join(f'*{suffix}' for suffix in suffixes)})"
            for usual, suffixes in formats.items()
        }
        current = self._output_format(formats)
        chosen, chosen_filter = save_file_name(
            self, "Save as", str(self._output or ""), ";;".join(filters.values()), filters[current]
        )

        if not chosen:
            return

        path = Path(chosen)
        typed = usual_suffix(path.suffix.lower())
        picked = next((usual for usual, name in filters.items() if name == chosen_filter), current)
        self._chosen_format = typed if typed in formats else picked
        self._custom_output = path
        self._set_output(path)
        self._suggest_output()
        self._refresh()

    # — run —

    def _on_action(self) -> None:
        self._refresh()  # a file deleted since it was chosen: the footer says so
        carrier_path, output = self.carrier_path, self._output

        if not self.footer.primary.isEnabled() or carrier_path is None or output is None:
            return

        options = self.options()
        text = self.text.toPlainText() if self.payload_kind.value() == _PAYLOAD_TEXT else None
        payload_path = self.payload_path

        def job(report: service.ProgressCallback) -> Path:
            if text is not None:
                hidden = HiddenFile(TEXT_PAYLOAD_NAME, text.encode("utf-8"))
            elif payload_path is not None:
                hidden = HiddenFile(payload_path.name, payload_path.read_bytes())
            else:
                raise CipherStitchError("Choose a file to hide")

            service.hide(carrier_path, hidden, output, options, report)

            return output

        self._run(job)

    def _finish(self) -> None:
        super()._finish()
        # The form ignores changes while working, such as switching Research mode
        self._refresh()

    def _on_succeeded(self, result: object) -> None:
        if not isinstance(result, Path):
            return

        self._last_output = result

        if self._custom_output is not None:
            # The save dialog asked before replacing that file, a second hide wouldn't ask.
            stem = self._custom_output.stem
            self._set_output(free_path(result.parent, stem, result.suffix))

        self._suggest_output()
        self.footer.set_status("done", f"Saved to {display_path(result)}", can_show_in_folder=True)

    def _on_failed(self, error: Exception) -> None:
        self.footer.set_status("error", error_message(error))

    def _show_output(self) -> None:
        if self._last_output is not None:
            show_in_folder(self._last_output)
