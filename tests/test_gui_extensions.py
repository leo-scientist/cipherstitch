"""The app with extensions: their entries, files and load failures in Research mode only, and
a Standard window that never loads them (docs/extensions.md)."""

import json
import os
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QFileDialog, QLabel, QSpinBox, QWidget
from pytestqt.qtbot import QtBot

from cipherstitch import catalog, extension_loader, service
from cipherstitch.catalog import CarrierKind, CipherId, CipherKey, MethodId
from cipherstitch.gui import profiles
from cipherstitch.gui.main_window import MainWindow
from cipherstitch.gui.widgets.parameter_editor import CUSTOM
from cipherstitch.gui.widgets.parameters import format_warning
from cipherstitch.gui.widgets.picker import PickerField
from cipherstitch.gui.widgets.toggle import ToggleSwitch
from cipherstitch.params import profile
from cipherstitch.params.profile import Profile
from cipherstitch.params.settings import Settings

from .conftest import EXTENSION_FIXTURES, ImageFactory, use_extensions
from .gui_support import drag_enter, isolated_session, new_window

STRONG = "Correct-Horse-42!"
SECRET = b"meet at noon"
BROKEN = (
    "Extension broken couldn't load: ModuleNotFoundError: "
    "No module named 'cipherstitch_missing_dependency'"
)
WRITTEN = "PNG · BMP · TIFF · JPG · WAV · FLAC · PDF · DOCX · TXT · MD"
dragging = pytest.mark.filterwarnings("ignore:Function.*QDragEnterEvent:DeprecationWarning")


@pytest.fixture(autouse=True)
def session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    isolated_session(monkeypatch, tmp_path)


@pytest.fixture
def window(qtbot: QtBot, tmp_path: Path) -> MainWindow:
    return new_window(qtbot, tmp_path)


@pytest.fixture
def dialogs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The filters the open dialogs offer. Each dialog is closed at once."""
    filters: list[str] = []

    def open_file(*args: Any) -> tuple[str, str]:
        filters.append(args[3])

        return "", ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", open_file)

    return filters


def _options(field: PickerField[Any]) -> list[str]:
    """The names a picker's popover lists."""
    rows = field.popover.list

    return [_texts(rows.itemWidget(rows.item(index)))[0] for index in range(rows.count())]


def _items(combo: QComboBox) -> list[str]:
    return [combo.itemText(index) for index in range(combo.count())]


def _texts(widget: QWidget) -> list[str]:
    """The texts of the labels shown in ``widget``, top to bottom."""
    labels = widget.findChildren(QLabel)

    return [label.text() for label in labels if label.text() and not label.isHidden()]


def _choose(combo: QComboBox, name: str) -> None:
    index = combo.findText(f"{name} · ", Qt.MatchFlag.MatchStartsWith)
    assert index >= 0, name
    combo.setCurrentIndex(index)
    combo.activated.emit(index)


def test_extension_entries_show_in_research_mode_only(
    monkeypatch: pytest.MonkeyPatch, window: MainWindow
) -> None:
    use_extensions(monkeypatch, "mixed")
    hide, reveal = window.hide_page, window.reveal_page

    assert _options(hide.cipher) == ["AES-256-GCM", "ChaCha20-Poly1305", "XChaCha20-Poly1305"]
    assert _items(reveal.cipher) == [f"{c.name} · {c.tier}" for c in catalog.CIPHERS]

    window.research_switch.click()
    hide.carrier_kind.set_value(CarrierKind.DOCUMENT)

    assert _options(hide.method)[-1] == "Byte LSB"
    assert _options(hide.cipher)[-1] == "ChaCha20 copy"

    hide.carrier_kind.set_value(CarrierKind.TEXT)

    assert _options(hide.method) == ["Zero-width characters", "Faulty"]
    assert _items(reveal.method)[-2:] == ["Faulty · Experimental", "Byte LSB · Experimental"]
    assert _items(reveal.cipher)[-1] == "ChaCha20 copy · Experimental"

    _choose(reveal.cipher, "ChaCha20 copy")
    hide.cipher.set_cipher("chacha-copy")
    window.research_switch.click()

    assert "ChaCha20 copy" not in _options(hide.cipher)
    assert "ChaCha20 copy · Experimental" not in _items(reveal.cipher)
    assert "Faulty · Experimental" not in _items(reveal.method)
    assert hide.options().cipher == catalog.DEFAULT_CIPHER

    window.research_switch.click()  # an extension's cipher isn't chosen again

    assert reveal.research_reveal().cipher == catalog.DEFAULT_CIPHER


def test_the_pickers_name_the_extension_an_option_comes_from(
    monkeypatch: pytest.MonkeyPatch, window: MainWindow
) -> None:
    use_extensions(monkeypatch, "working")
    window.research_switch.click()
    hide = window.hide_page
    hide.carrier_kind.set_value(CarrierKind.DOCUMENT)
    hide.method.set_method("byte-lsb")
    hide.method.click()
    detail = hide.method.popover.detail

    assert _texts(detail)[-2:] == ["Capacity 1 bit per byte", "From the extension byte_lsb"]
    # Third-party texts are never markup.
    labels = [label for label in detail.findChildren(QLabel) if label.text()]

    assert all(label.textFormat() == Qt.TextFormat.PlainText for label in labels)

    hide.cipher.set_cipher("chacha-copy")
    hide.cipher.click()

    assert _texts(hide.cipher.popover.detail)[-2:] == [
        "Size overhead +16 B",
        "From the extension chacha_copy",
    ]

    hide.cipher.popover.hide()  # rows added to an open popover show on the next event loop pass
    hide.cipher.set_cipher(CipherId.AES_GCM)
    hide.cipher.click()

    assert _texts(hide.cipher.popover.detail)[-1] == "Size overhead +16 B"


@pytest.mark.parametrize("cipher", [CipherId.NONE, "chacha-copy"])
def test_hide_and_reveal_with_an_extension_through_the_pages(
    monkeypatch: pytest.MonkeyPatch,
    window: MainWindow,
    qtbot: QtBot,
    tmp_path: Path,
    cipher: CipherKey,
) -> None:
    use_extensions(monkeypatch, "working")
    cover, secret = tmp_path / "cover.fxb", tmp_path / "note.txt"
    cover.write_bytes(os.urandom(4096))
    secret.write_bytes(SECRET)
    hide, reveal = window.hide_page, window.reveal_page

    assert not hide.set_carrier_file(cover)  # Standard mode doesn't know the suffix
    assert hide.footer.status_text.text() == ".fxb is not a supported carrier"

    window.research_switch.click()

    assert hide.set_carrier_file(cover)
    assert (hide.options().carrier, hide.options().method) == (CarrierKind.DOCUMENT, "byte-lsb")
    assert hide.carrier_chip.meta.text().startswith("4.0 KiB · FXB · holds up to")

    hide.set_payload_file(secret)
    hide.cipher.set_cipher(cipher)
    password = None if cipher == CipherId.NONE else STRONG

    if password is not None:
        hide.password.line_edit.setText(password)
        hide.password.repeat_edit.setText(password)

    hide.footer.primary.click()
    qtbot.waitUntil(lambda: hide.footer.status_text.text().startswith("Saved to"))
    window.mode.set_value("reveal")
    reveal.set_carrier_file(tmp_path / "embedded.fxb")

    assert _items(reveal.method) == ["Byte LSB · Experimental"]

    _choose(reveal.cipher, catalog.cipher(cipher).name)
    reveal.password.setText(password or "")
    reveal.footer.primary.click()
    qtbot.waitUntil(lambda: reveal.result_card.isVisibleTo(reveal))

    assert (tmp_path / "out" / secret.name).read_bytes() == SECRET


def test_standard_mode_drops_a_file_only_an_extension_takes(
    monkeypatch: pytest.MonkeyPatch, window: MainWindow, tmp_path: Path
) -> None:
    use_extensions(monkeypatch, "working")
    cover = tmp_path / "cover.fxb"
    cover.write_bytes(bytes(64))
    hide, reveal = window.hide_page, window.reveal_page
    window.research_switch.click()
    hide.set_carrier_file(cover)
    reveal.set_carrier_file(cover)

    assert (hide.carrier_path, reveal.carrier_path) == (cover, cover)

    window.research_switch.click()

    assert (hide.carrier_path, reveal.carrier_path) == (None, None)
    assert hide.options().carrier == CarrierKind.IMAGE
    assert reveal.footer.status_text.text() == ".fxb files can't carry a payload"

    window.research_switch.click()  # the reason Standard gave goes with it

    assert reveal.carrier_chip.title.text() == "No file selected"
    assert (hide.footer.status_text.text(), reveal.footer.status_text.text()) == ("", "")


def test_a_file_gone_before_a_mode_switch_is_reported(window: MainWindow, tmp_path: Path) -> None:
    gone = tmp_path / "gone.png"
    gone.touch()
    reveal = window.reveal_page
    reveal.set_carrier_file(gone)
    gone.unlink()
    window.research_switch.click()  # the mode's methods are checked against the file again

    assert reveal.carrier_path is None
    assert reveal.carrier_chip.meta.text() == "Unreadable file"
    assert reveal.footer.status_text.text().startswith("Can't read gone.png: ")


def test_compressing_a_carrier_describes_it_in_the_pages_mode(
    monkeypatch: pytest.MonkeyPatch, window: MainWindow, qtbot: QtBot, tmp_path: Path
) -> None:
    """In Research mode an extension's method may take the RAW itself."""
    modes: list[object] = []
    monkeypatch.setattr(service, "describe_carrier", lambda *_a, **kw: modes.append(kw))
    monkeypatch.setattr(service, "estimate", lambda *_args: None)
    raw = tmp_path / "photo.dng"
    raw.write_bytes(b"raw")
    window.research_switch.click()
    window.hide_page._compress(raw, CarrierKind.IMAGE)
    qtbot.waitUntil(lambda: not window.hide_page._compressions.is_running)

    assert modes == [{"is_research": True}]


def test_a_failing_extension_shows_its_error_and_prints_the_traceback(
    monkeypatch: pytest.MonkeyPatch,
    window: MainWindow,
    qtbot: QtBot,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    use_extensions(monkeypatch, "mixed")
    cover = tmp_path / "<b>cover.txt"  # the extension's message looks like markup
    cover.write_text("Lanterns over the harbour. " * 40)
    window.research_switch.click()
    capsys.readouterr()
    hide, reveal = window.hide_page, window.reveal_page
    hide.set_carrier_file(cover)
    hide.method.set_method("faulty")
    failed = "The extension faulty failed: ValueError: can't open <b>cover.txt"

    assert hide.footer.status_text.text() == f"Can't read <b>cover.txt: {failed}"
    assert hide.footer.status_text.textFormat() == Qt.TextFormat.PlainText
    assert reveal.footer.step_label.textFormat() == Qt.TextFormat.PlainText  # method names
    assert capsys.readouterr().err.endswith("ValueError: can't open <b>cover.txt\n")

    window.mode.set_value("reveal")
    reveal.set_carrier_file(cover)
    _choose(reveal.method, "Faulty")
    _choose(reveal.cipher, catalog.cipher(CipherId.NONE).name)
    reveal.footer.primary.click()
    qtbot.waitUntil(lambda: reveal.footer.status_text.text() == failed)

    assert capsys.readouterr().err.endswith("ValueError: can't open <b>cover.txt\n")


def test_research_mode_shows_a_line_for_each_extension_that_failed(
    monkeypatch: pytest.MonkeyPatch,
    window: MainWindow,
    qtbot: QtBot,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    use_extensions(monkeypatch, "mixed")
    failures = window.extension_failures

    assert not failures.isVisibleTo(window)

    window.research_switch.click()
    stderr = capsys.readouterr().err

    assert failures.messages() == (BROKEN,)
    assert failures.isVisibleTo(window)
    assert stderr.startswith(
        "Extension broken couldn't load:\n"
        "  ModuleNotFoundError: No module named 'cipherstitch_missing_dependency'\n"
        "Traceback (most recent call last):\n"
    )
    assert stderr.count("Traceback") == 1

    window.mode.set_value("reveal")

    assert failures.isVisibleTo(window)

    window.research_switch.click()

    assert not failures.isVisibleTo(window)

    window.research_switch.click()

    assert failures.messages() == (BROKEN,)
    assert failures.isVisibleTo(window)

    reopened = new_window(qtbot, tmp_path)
    reopened.research_switch.click()

    assert reopened.extension_failures.messages() == (BROKEN,)
    assert capsys.readouterr().err == ""  # once per process


def test_no_line_shows_when_every_extension_loads(
    monkeypatch: pytest.MonkeyPatch, window: MainWindow
) -> None:
    use_extensions(monkeypatch, "working")
    window.research_switch.click()

    assert window.extension_failures.messages() == ()
    assert not window.extension_failures.isVisibleTo(window)


def test_an_extension_that_exits_on_import_never_stops_the_app(
    monkeypatch: pytest.MonkeyPatch,
    qtbot: QtBot,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(extension_loader, "PACKAGE", f"{EXTENSION_FIXTURES}.exits")
    window = new_window(qtbot, tmp_path)
    window.research_switch.click()

    assert window.extension_failures.messages() == ("Extension quits couldn't load: SystemExit: 2",)
    assert "Extension quits couldn't load:\n  SystemExit: 2\n" in capsys.readouterr().err


@dragging
def test_dialogs_and_the_drop_overlay_take_the_modes_suffixes(
    monkeypatch: pytest.MonkeyPatch,
    window: MainWindow,
    dialogs: list[str],
    secret_file: Path,
) -> None:
    use_extensions(monkeypatch, "working")
    hide, reveal = window.hide_page, window.reveal_page
    reveal.carrier_chip.change_requested.emit()

    assert "*.fxb" not in dialogs[-1]

    window.research_switch.click()
    hide.carrier_kind.set_value(CarrierKind.DOCUMENT)
    hide.carrier_chip.change_requested.emit()
    reveal.carrier_chip.change_requested.emit()

    assert dialogs[-2:] == [
        "Document files (*.pdf *.docx *.fxb)",
        "Carrier files (*.png *.bmp *.tif *.tiff *.jpg *.jpeg *.wav *.flac *.pdf *.docx *.txt "
        "*.md *.fxb)",
    ]
    assert hide.carrier_chip.meta.text() == "PDF · DOCX · FXB"
    assert drag_enter(window, secret_file).endswith("PDF · DOCX · FXB · TXT · MD")

    window.mode.set_value("reveal")

    assert drag_enter(window, secret_file) == f"{WRITTEN} · FXB"


def _group_ids(window: MainWindow) -> list[str]:
    return [item.group.id for item in window.parameter_editor._group_items]


def test_the_editor_shows_an_extensions_parameters_for_its_method(
    monkeypatch: pytest.MonkeyPatch, window: MainWindow, tmp_path: Path
) -> None:
    use_extensions(monkeypatch, "tuned")
    hide, editor = window.hide_page, window.parameter_editor
    window.research_switch.click()
    window.show()
    hide.parameters.edit.click()

    assert "plane" not in _group_ids(window)  # LSB's groups

    editor.done.click()
    cover = tmp_path / "cover.fxb"
    cover.write_bytes(os.urandom(4096))
    hide.set_carrier_file(cover)
    hide.cipher.set_cipher(CipherId.NONE)

    assert hide.options().method == "bit-plane"
    assert hide.parameters.meta.text() == "3 parameters in 2 groups for this method"

    hide.parameters.edit.click()
    editor.group_list.setCurrentRow(1)
    rows = dict(editor._rows)
    bit, fill = rows["plane.bit"].control, rows["plane.fill"].control
    assert isinstance(bit, QSpinBox)
    assert isinstance(fill, ToggleSwitch)
    bit.setValue(3)
    fill.click()

    assert _group_ids(window) == ["frame", "plane"]
    assert [item.count.text() for item in editor._group_items] == ["1", "2 changed"]
    assert editor.subtitle.text() == "Bit plane · No encryption · 2 changed · 1 format"
    assert editor.format_warning.messages() == (format_warning(1),)
    assert editor.presets.currentText() == CUSTOM
    assert editor.presets.findText("Fast STC (h = 4)") == -1  # no STC parameter shown
    assert editor.group_title.text() == "Bit plane"
    assert editor.group_title.textFormat() == Qt.TextFormat.PlainText

    editor.done.click()

    assert hide.parameters.title.text() == "2 changed from Standard"
    assert hide.parameters.meta.text() == "PlaneChannel._bit 3 · Fill the rest on"
    assert hide.parameters.format_warning.messages() == (format_warning(1),)


def test_a_profile_carries_an_extensions_parameters_from_hide_to_reveal(
    monkeypatch: pytest.MonkeyPatch, window: MainWindow, tmp_path: Path
) -> None:
    use_extensions(monkeypatch, "tuned")
    hide, reveal, editor = window.hide_page, window.reveal_page, window.parameter_editor
    window.research_switch.click()
    cover = tmp_path / "cover.fxb"
    cover.write_bytes(os.urandom(4096))
    hide.set_carrier_file(cover)
    hide.cipher.set_cipher(CipherId.NONE)
    plane = Settings.of({"plane.bit": 3, "plane.fill": True})
    hide.set_settings(Settings.of({**plane.as_dict(), "stc.h": 4}))  # stc.h: not bit-plane's
    target = tmp_path / "plane.csprofile"
    monkeypatch.setattr(profiles, "choose_export", lambda _parent, _name: target)
    window.show()
    hide.parameters.edit.click()
    editor.export.click()
    editor.done.click()

    assert profile.read(target) == Profile(plane, "plane", method="bit-plane", cipher="none")

    window.mode.set_value("reveal")
    monkeypatch.setattr(profiles, "choose", lambda _parent: target)
    reveal.parameters.load.click()

    assert reveal.research_reveal() == service.ResearchReveal("bit-plane", CipherId.NONE, plane)
    assert reveal.parameters.title.text() == "plane"


def test_a_trellis_extension_gets_the_stc_parameters_and_presets(
    monkeypatch: pytest.MonkeyPatch, window: MainWindow, tmp_path: Path
) -> None:
    use_extensions(monkeypatch, "tuned")
    hide, editor = window.hide_page, window.parameter_editor
    window.research_switch.click()
    cover = tmp_path / "cover.fxt"
    cover.write_bytes(os.urandom(8192))
    hide.set_carrier_file(cover)
    window.show()
    hide.parameters.edit.click()

    assert hide.options().method == "trellis-bytes"
    assert _group_ids(window) == ["crypto", "frame", "stc"]

    editor.presets.activated.emit(editor.presets.findText("Fast STC (h = 4)"))
    editor.done.click()

    assert hide.options().settings == Settings.of({"stc.h": 4, "stc.kept": 6})
    assert hide.carrier_chip.meta.text().startswith("8.0 KiB · FXT · holds up to")


def test_the_editor_offers_the_extensions_cost_functions(
    monkeypatch: pytest.MonkeyPatch, window: MainWindow, make_image: ImageFactory
) -> None:
    use_extensions(monkeypatch, "costs")
    hide, editor = window.hide_page, window.parameter_editor
    window.research_switch.click()
    hide.set_carrier_file(make_image())
    window.show()
    hide.parameters.edit.click()
    editor.group_list.setCurrentRow(_group_ids(window).index("limg"))
    cost = dict(editor._rows)["limg.cost"].control
    assert isinstance(cost, QComboBox)

    assert _items(cost)[-2:] == ["MiPOD · experimental", "Ones · experimental"]

    cost.activated.emit(cost.count() - 1)
    editor.done.click()

    assert hide.options().settings == Settings.of({"limg.cost": "ones"})
    assert hide.parameters.meta.text() == "Cost function Ones"
    assert hide.parameters.format_warning.messages() == ()  # an Embedder value


def test_a_profile_with_a_missing_cost_function_reveals_but_doesnt_hide(
    monkeypatch: pytest.MonkeyPatch, window: MainWindow, tmp_path: Path
) -> None:
    """Revealing reads the same values whatever the costs were."""
    hide, reveal = window.hide_page, window.reveal_page
    window.research_switch.click()
    stored = profile.dumps(Profile(Settings.of({"stc.h": 4})), app="test")
    document = {**json.loads(stored), "params": {"limg.cost": "ones", "stc.h": 4}}
    target = tmp_path / "costly.csprofile"
    target.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(profiles, "choose", lambda _parent: target)
    hide.parameters.load.click()

    assert hide.parameters.notice.messages() == (
        "costly.csprofile sets limg.cost to a cost function this CipherStitch doesn't know, "
        "'ones' (an extension that isn't installed?).",
    )
    assert hide.options().settings == Settings()

    window.mode.set_value("reveal")
    reveal.parameters.load.click()

    assert reveal.parameters.notice.messages() == ()
    assert reveal.research_reveal().settings == Settings.of({"stc.h": 4})


@pytest.fixture
def refuse_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    def load() -> extension_loader.Loaded:
        raise AssertionError("Standard mode loaded the extensions")

    monkeypatch.setattr(extension_loader, "load", load)


@dragging
@pytest.mark.usefixtures("refuse_loading")
def test_a_standard_window_never_loads_the_extensions(
    window: MainWindow,
    qtbot: QtBot,
    make_image: ImageFactory,
    dialogs: list[str],
    tmp_path: Path,
) -> None:
    hide, reveal = window.hide_page, window.reveal_page
    secret = tmp_path / "note.txt"
    secret.write_bytes(SECRET)
    hide.carrier_chip.change_requested.emit()
    reveal.carrier_chip.change_requested.emit()

    assert "*.png" in dialogs[0]
    assert drag_enter(window, secret).startswith("PNG")

    hide.set_carrier_file(make_image(size=(320, 240)))
    hide.set_payload_file(secret)
    hide.password.line_edit.setText(STRONG)
    hide.password.repeat_edit.setText(STRONG)

    assert hide.options().method == MethodId.LSB

    hide.footer.primary.click()
    qtbot.waitUntil(lambda: hide.footer.status_text.text().startswith("Saved to"))
    window.mode.set_value("reveal")

    assert drag_enter(window, secret) == WRITTEN

    reveal.set_carrier_file(tmp_path / "embedded.png")
    reveal.password.setText(STRONG)
    reveal.footer.primary.click()
    qtbot.waitUntil(lambda: reveal.result_card.isVisibleTo(reveal))

    assert (tmp_path / "out" / secret.name).read_bytes() == SECRET
    assert not window.extension_failures.isVisibleTo(window)
