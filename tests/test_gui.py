import json
import re
import threading
import wave
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("PySide6")

import shiboken6
from PySide6.QtCore import QMimeData, QPoint, QPointF, QRect, QSize, Qt, QUrl
from PySide6.QtGui import QDropEvent, QFileOpenEvent, QScreen, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QWidget,
)
from pytestqt.qtbot import QtBot

from cipherstitch import catalog, kdf, payload, service, standard
from cipherstitch.catalog import CarrierKind, CipherId, MethodId, MethodKey
from cipherstitch.errors import (
    CipherStitchError,
    NoHiddenDataError,
    OperationCancelledError,
    UnsupportedFormatError,
)
from cipherstitch.gui import app, file_dialogs, profiles
from cipherstitch.gui.main_window import (
    RESEARCH_NOTES,
    WINDOW_MIN_HEIGHT,
    WINDOW_WIDTH,
    MainWindow,
)
from cipherstitch.gui.pages import base, reveal_page
from cipherstitch.gui.pages.hide_page import HidePage
from cipherstitch.gui.pages.reveal_page import (
    ENCRYPTED_HINT,
    NO_SETTINGS_NOTE,
    RESEARCH_HINT,
    STANDARD_HINT,
    UNENCRYPTED_WARNING,
    RevealPage,
)
from cipherstitch.gui.widgets.common import free_path
from cipherstitch.gui.widgets.drop_overlay import DropZone
from cipherstitch.gui.widgets.parameter_editor import CUSTOM
from cipherstitch.gui.widgets.parameter_rows import EXPERIMENTAL, ParamRow
from cipherstitch.gui.widgets.parameters import CLEAR, LOAD, format_warning
from cipherstitch.gui.widgets.picker import popover_position
from cipherstitch.gui.widgets.toggle import ToggleSwitch
from cipherstitch.gui.widgets.warning_list import WarningList
from cipherstitch.gui.worker import BackgroundTask
from cipherstitch.media import precover
from cipherstitch.params import profile, registry
from cipherstitch.params.profile import Profile
from cipherstitch.params.settings import Settings
from cipherstitch.service import ProgressCallback
from cipherstitch.sizes import format_size

from .conftest import ImageFactory, skip_unless_method_installed
from .gui_support import drag_enter, isolated_session, new_window

STRONG = "Correct-Horse-42!"


@pytest.fixture
def shown_in_folder(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    shown: list[Path] = []

    for page in ("hide_page", "reveal_page"):
        monkeypatch.setattr(f"cipherstitch.gui.pages.{page}.show_in_folder", shown.append)

    return shown


@pytest.fixture(autouse=True)
def session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    isolated_session(monkeypatch, tmp_path)


@pytest.fixture(autouse=True)
def quits(monkeypatch: pytest.MonkeyPatch) -> list[None]:
    """The app's quit requests, recorded instead of ending the test's event loop: a window
    closed during work asks to quit once its jobs end."""
    requests: list[None] = []
    monkeypatch.setattr(QApplication, "quit", lambda: requests.append(None))

    return requests


@pytest.fixture
def window(qtbot: QtBot, tmp_path: Path, shown_in_folder: list[Path]) -> MainWindow:
    return new_window(qtbot, tmp_path)


@pytest.fixture
def research(window: MainWindow) -> None:
    """Research mode, which most of these tests need: every carrier, method and cipher."""
    window.research_switch.click()


def _type_password(page: HidePage, password: str) -> None:
    page.password.line_edit.setText(password)
    page.password.repeat_edit.setText(password)


def _fill_hide(window: MainWindow, cover: Path, secret: Path, password: str | None) -> None:
    page = window.hide_page
    page.set_carrier_file(cover)
    page.set_payload_file(secret)

    if password is None:
        page.cipher.set_cipher(CipherId.NONE)
    else:
        _type_password(page, password)


def _choose(
    page: RevealPage, method: MethodId | None = None, cipher: CipherId | None = None
) -> None:
    """Pick Research reveal's method and cipher as a click would."""
    choices = [
        (page.method, None if method is None else catalog.method(method).name),
        (page.cipher, None if cipher is None else catalog.cipher(cipher).name),
    ]

    for combo, name in choices:
        if name is not None:
            index = combo.findText(f"{name} · ", Qt.MatchFlag.MatchStartsWith)
            assert index >= 0, name
            combo.setCurrentIndex(index)
            combo.activated.emit(index)


def _wait_idle(qtbot: QtBot, window: MainWindow) -> None:
    qtbot.waitUntil(lambda: not window.hide_page.footer.is_working)
    qtbot.waitUntil(lambda: not window.reveal_page.footer.is_working)


@pytest.mark.usefixtures("research")
def test_hide_then_reveal_encrypted(
    window: MainWindow,
    qtbot: QtBot,
    make_image: ImageFactory,
    secret_file: Path,
    tmp_path: Path,
    shown_in_folder: list[Path],
) -> None:
    hide = window.hide_page
    assert not hide.footer.primary.isEnabled()
    _fill_hide(window, make_image(), secret_file, STRONG)
    assert "KiB" in hide.carrier_chip.meta.text()
    assert hide.footer.primary.isEnabled()
    hide.footer.primary.click()
    qtbot.waitUntil(lambda: hide.footer.status_text.text().startswith("Saved to"))
    assert hide.footer.show_button.isVisibleTo(hide)
    hide.footer.show_button.click()
    assert shown_in_folder == [tmp_path / "embedded.png"]

    window.mode.set_value("reveal")
    reveal = window.reveal_page
    reveal.set_carrier_file(tmp_path / "embedded.png")
    reveal.password.setText("wrong")
    reveal.footer.primary.click()
    qtbot.waitUntil(lambda: reveal.error.isVisibleTo(reveal))
    assert reveal.error.messages() == (f"{payload.NOT_FOUND_MESSAGE}.",)
    assert reveal.hint.messages() == (RESEARCH_HINT,)
    assert reveal.password.property("error") is True

    reveal.password.setText(STRONG)
    assert not reveal.error.isVisibleTo(reveal)
    assert not reveal.hint.isVisibleTo(reveal)
    reveal.footer.primary.click()
    qtbot.waitUntil(lambda: reveal.result_card.isVisibleTo(reveal))
    assert "integrity verified" in reveal.result_card.details.text()
    assert (tmp_path / "out" / secret_file.name).read_bytes() == secret_file.read_bytes()
    reveal.result_card.show_button.click()
    assert shown_in_folder[-1] == tmp_path / "out" / secret_file.name

    reveal.footer.primary.click()  # the revealed file is there now
    qtbot.waitUntil(lambda: "already exists" in reveal.footer.status_text.text())
    assert reveal.footer.status_text.text().startswith(f"{secret_file.name} already exists in")


@pytest.mark.parametrize(
    ("text", "is_one_line"),
    [
        ("Saved to ~/Desktop/embedded.png", True),
        ("note.txt already exists in " + "/a long folder name" * 16 + ". Move that file", False),
    ],
    ids=["short", "long"],
)
def test_the_footer_status_is_shown_whole(
    window: MainWindow, qtbot: QtBot, text: str, is_one_line: bool
) -> None:
    """One line while it fits, wrapped beside the action when it does not. A wrapped status
    takes its room from the form, which scrolls rather than squeezing a row, and the footer's
    minimum drops back after."""
    hide = window.hide_page
    footer = hide.footer
    status = footer.status_text
    window.show()
    hide.payload_kind.set_value("text")  # the tallest form: text box and two passwords
    qtbot.waitExposed(window)
    one_line = footer.minimumHeight()
    footer.set_status("error", text)
    window.resize(WINDOW_WIDTH, 0)  # as short as it goes

    def is_laid_out() -> bool:
        return status.width() >= min(status.sizeHint().width(), footer.width() // 2)

    qtbot.waitUntil(is_laid_out)
    text_height = status.heightForWidth(status.width())

    assert status.geometry().right() < footer.primary.geometry().left()
    assert (text_height < 2 * status.fontMetrics().lineSpacing()) is is_one_line
    assert footer.height() >= footer.minimumHeight() >= text_height
    assert hide.payload_kind.height() == hide.payload_kind.sizeHint().height()

    footer.set_status("warning", "Short")
    assert footer.minimumHeight() == one_line
    footer.set_status("error", text)
    footer.clear_status()
    assert footer.minimumHeight() == one_line


@pytest.mark.usefixtures("research")
def test_unencrypted_payload_is_warned_about_and_revealed_without_a_password(
    window: MainWindow, qtbot: QtBot, make_image: ImageFactory, secret_file: Path, tmp_path: Path
) -> None:
    hide = window.hide_page
    _fill_hide(window, make_image(), secret_file, None)
    assert hide.no_encryption_warning.isVisibleTo(hide)
    assert not hide.password.isVisibleTo(hide)
    hide.footer.primary.click()
    qtbot.waitUntil(lambda: hide.footer.status_text.text().startswith("Saved to"))

    reveal = window.reveal_page
    reveal.set_carrier_file(tmp_path / "embedded.png")
    _choose(reveal, cipher=CipherId.NONE)
    assert not reveal.password.isEnabled()  # a plain frame takes none
    reveal.footer.primary.click()
    qtbot.waitUntil(lambda: reveal.result_card.isVisibleTo(reveal))
    assert "integrity verified" not in reveal.result_card.details.text()


@pytest.mark.usefixtures("research")
def test_a_password_typed_for_a_plain_payload_is_warned_about(
    window: MainWindow, qtbot: QtBot, make_image: ImageFactory, secret_file: Path, tmp_path: Path
) -> None:
    """Standard reveals a plain frame too, and a success card alone would read as if the
    password had checked the file (the CLI prints the same warning)."""
    hide = window.hide_page
    _fill_hide(window, make_image(), secret_file, None)
    hide.footer.primary.click()
    qtbot.waitUntil(lambda: hide.footer.status_text.text().startswith("Saved to"))
    window.research_switch.click()
    reveal = window.reveal_page
    reveal.set_carrier_file(tmp_path / "embedded.png")
    reveal.password.setText(STRONG)
    reveal.footer.primary.click()
    qtbot.waitUntil(lambda: reveal.result_card.isVisibleTo(reveal))

    assert reveal.result_warning.isVisibleTo(reveal)
    assert reveal.result_warning.messages() == (UNENCRYPTED_WARNING,)
    assert "integrity verified" not in reveal.result_card.details.text()

    (tmp_path / "out" / secret_file.name).unlink()
    reveal.password.clear()
    reveal.footer.primary.click()
    qtbot.waitUntil(lambda: reveal.result_card.isVisibleTo(reveal))

    assert not reveal.result_warning.isVisibleTo(reveal)


@pytest.mark.usefixtures("research")
def test_hide_then_reveal_in_text(
    window: MainWindow, qtbot: QtBot, secret_file: Path, tmp_path: Path
) -> None:
    cover = tmp_path / "cover.txt"
    cover.write_text("Every word here can carry two bits. " * 400, encoding="utf-8")
    hide = window.hide_page
    assert hide.set_carrier_file(cover)
    assert hide.carrier_kind.value() == CarrierKind.TEXT
    assert hide._output == tmp_path / "embedded.txt"  # the custom .png follows the carrier
    assert "characters · TXT" in hide.carrier_chip.meta.text()
    hide.set_payload_file(secret_file)
    _type_password(hide, STRONG)
    assert hide.footer.primary.isEnabled()
    hide.footer.primary.click()
    qtbot.waitUntil(lambda: hide.footer.status_text.text().startswith("Saved to"))

    reveal = window.reveal_page
    reveal.set_carrier_file(tmp_path / "embedded.txt")
    reveal.password.setText(STRONG)
    assert reveal.research_reveal().method == MethodId.ZERO_WIDTH  # the only one for text
    reveal.footer.primary.click()
    qtbot.waitUntil(lambda: reveal.result_card.isVisibleTo(reveal))
    assert (tmp_path / "out" / secret_file.name).read_bytes() == secret_file.read_bytes()


@pytest.mark.usefixtures("research")
def test_suggested_output_follows_the_carrier(
    window: MainWindow, make_image: ImageFactory, tmp_path: Path
) -> None:
    hide = window.hide_page
    hide._custom_output = None
    cover = tmp_path / "cover.md"
    cover.write_text("Some words. " * 50, encoding="utf-8")
    hide.set_carrier_file(cover)
    assert hide._output is not None
    assert hide._output.name.endswith(".md")
    hide.set_carrier_file(make_image("photo.bmp"))
    assert hide._output.suffix == ".bmp"


def test_the_suggested_output_takes_the_covers_name(
    window: MainWindow, make_image: ImageFactory
) -> None:
    hide = window.hide_page
    hide._custom_output = None
    hide.set_carrier_file(make_image("harbour.png"))

    assert hide._output is not None
    assert re.fullmatch(r"harbour(-\d+)?\.png", hide._output.name)

    hide._set_carrier(None)

    assert hide._output is None
    assert not hide.output.text()


def test_the_output_keeps_the_covers_format_or_the_one_picked(
    window: MainWindow, make_image: ImageFactory
) -> None:
    hide = window.hide_page
    hide._custom_output = None
    formats = hide.output_format
    hide.set_carrier_file(make_image("scan.tif"))

    assert hide._output is not None
    assert hide._output.suffix == ".tif"
    assert [formats.itemText(i) for i in range(formats.count())] == ["PNG", "BMP", "TIFF"]
    assert (formats.currentText(), formats.isEnabled()) == ("TIFF", True)

    formats.activated.emit(formats.findText("BMP"))
    hide.set_research(True)  # the carrier and the output are shown again

    assert hide._output.suffix == ".bmp"

    hide.set_carrier_file(make_image("photo.png"))  # a new cover: its own format, if LSB writes it

    assert hide._output.suffix == ".png"

    skip_unless_method_installed(MethodId.DCT)
    hide.method.set_method(MethodId.DCT)

    assert hide._output.suffix == ".jpg"
    assert (formats.currentText(), formats.isEnabled()) == ("JPG", False)


@pytest.mark.parametrize(
    ("typed", "picked_filter", "saved"),
    [
        ("photo.xyz", "BMP (*.bmp)", "photo.bmp"),
        ("photo", "TIFF (*.tif *.tiff)", "photo.tif"),
        ("photo.tiff", "PNG (*.png)", "photo.tiff"),
    ],
    ids=["other-suffix", "no-suffix", "own-suffix-wins"],
)
def test_the_save_dialog_keeps_to_the_formats_the_method_writes(
    window: MainWindow,
    make_image: ImageFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    typed: str,
    picked_filter: str,
    saved: str,
) -> None:
    hide = window.hide_page
    hide.set_carrier_file(make_image())
    offered: list[tuple[str, str]] = []

    def save_as(*args: object) -> tuple[str, str]:
        offered.append((str(args[3]), str(args[4])))

        return str(tmp_path / typed), picked_filter

    dialog = SimpleNamespace(getSaveFileName=save_as)
    monkeypatch.setattr(file_dialogs, "QFileDialog", dialog)
    hide._choose_output()

    assert offered == [("PNG (*.png);;BMP (*.bmp);;TIFF (*.tif *.tiff)", "PNG (*.png)")]
    assert hide._output == tmp_path / saved
    assert hide.output_format.currentText() == {".bmp": "BMP", ".png": "PNG"}.get(
        Path(saved).suffix, "TIFF"
    )


@pytest.mark.usefixtures("research")
def test_unreadable_carrier_disables_hide(
    window: MainWindow, secret_file: Path, tmp_path: Path
) -> None:
    hide = window.hide_page
    good = tmp_path / "good.txt"
    good.write_text("Every word here can carry two bits. " * 400, encoding="utf-8")
    hide.set_carrier_file(good)
    hide.set_payload_file(secret_file)
    _type_password(hide, STRONG)
    assert hide.footer.primary.isEnabled()
    latin1 = tmp_path / "latin1.txt"
    latin1.write_bytes("café ".encode("latin-1") * 100)
    hide.set_carrier_file(latin1)
    assert not hide.footer.primary.isEnabled()
    assert "not UTF-8" in hide.footer.status_text.text()


@pytest.mark.usefixtures("research")
def test_a_carrier_deleted_after_being_chosen_is_reported_and_hide_is_off(
    window: MainWindow, make_image: ImageFactory, secret_file: Path, tmp_path: Path
) -> None:
    hide = window.hide_page
    cover = make_image()
    _fill_hide(window, cover, secret_file, STRONG)

    assert hide.footer.primary.isEnabled()

    cover.unlink()
    hide.text.setPlainText("x")  # any change, as typing does: the form is checked again

    assert not hide.footer.primary.isEnabled()
    assert hide.footer.status_text.text().startswith("Can't read cover.png: ")
    assert "Errno" not in hide.footer.status_text.text()
    assert hide.carrier_chip.meta.text() == "Unreadable file"
    assert hide.carrier_path is None

    cover = make_image()  # chosen again, deleted again: Hide checks before it starts
    _fill_hide(window, cover, secret_file, STRONG)
    cover.unlink()
    hide.footer.primary.click()

    assert not hide.footer.is_working
    assert hide.footer.status_text.text().startswith("Can't read cover.png: ")
    assert not (tmp_path / "embedded.png").exists()


@pytest.mark.usefixtures("research")
def test_a_payload_file_deleted_after_being_chosen_is_reported(
    window: MainWindow, make_image: ImageFactory, secret_file: Path, tmp_path: Path
) -> None:
    hide = window.hide_page
    _fill_hide(window, make_image(), secret_file, STRONG)
    secret_file.unlink()
    hide.footer.primary.click()

    assert not hide.footer.is_working
    assert not hide.footer.primary.isEnabled()
    assert hide.footer.status_text.text().startswith("Can't read secret.txt: ")
    assert "Errno" not in hide.footer.status_text.text()
    assert hide.payload_chip.title.text() == "Drop a file here or click to choose"

    secret_file.mkdir()
    hide.text.setPlainText("x")  # any change, as typing does: the form is checked again

    assert hide.footer.status_text.text() == "Can't read secret.txt: a folder"


@pytest.mark.parametrize(
    ("error", "text"),
    [
        (
            FileNotFoundError(2, "No such file or directory", "/pictures/cover.png"),
            "Can't read cover.png: No such file or directory",
        ),
        (OSError("plain"), "plain"),
        (CipherStitchError("Payload is 3 B too large"), "Payload is 3 B too large"),
        (MemoryError(), "Out of memory"),
        (KeyError(), "Unexpected error: KeyError"),
        (ValueError("boom"), "Unexpected error: boom"),
    ],
    ids=["file", "os", "own", "memory", "empty", "other"],
)
def test_error_message_names_the_file_and_never_shows_nothing(error: Exception, text: str) -> None:
    assert base.error_message(error) == text


@pytest.mark.usefixtures("research")
def test_corrected_custom_output_never_overwrites_a_file(
    window: MainWindow, tmp_path: Path
) -> None:
    hide = window.hide_page
    hide._set_output(tmp_path / "notes.png")
    cover = tmp_path / "notes.txt"
    cover.write_text("Some words. " * 50, encoding="utf-8")
    hide.set_carrier_file(cover)
    assert hide._output == tmp_path / "notes-2.txt"


@pytest.mark.usefixtures("research")
def test_a_custom_output_moves_on_after_each_hide(
    window: MainWindow,
    qtbot: QtBot,
    make_image: ImageFactory,
    secret_file: Path,
    tmp_path: Path,
) -> None:
    hide = window.hide_page
    _fill_hide(window, make_image(), secret_file, STRONG)

    for name, following in (
        ("embedded.png", "embedded-2.png"),
        ("embedded-2.png", "embedded-3.png"),
    ):
        hide.footer.primary.click()
        qtbot.waitUntil(lambda: not hide.footer.is_working)

        assert hide.footer.status_text.text().startswith("Saved to")
        assert hide._last_output == tmp_path / name
        assert hide._output == tmp_path / following
        assert hide.output.text().endswith(following)


@pytest.mark.usefixtures("research")
def test_audio_carrier_keeps_its_format(
    window: MainWindow, qtbot: QtBot, secret_file: Path, tmp_path: Path
) -> None:
    cover = tmp_path / "voice.wav"

    with wave.open(str(cover), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(bytes(range(256)) * 250)

    hide = window.hide_page
    assert hide.set_carrier_file(cover)
    assert hide.carrier_kind.value() == CarrierKind.AUDIO
    assert "16-bit mono · WAV" in hide.carrier_chip.meta.text()
    assert hide._output == tmp_path / "embedded.wav"
    hide.set_payload_file(secret_file)
    _type_password(hide, STRONG)
    hide.footer.primary.click()
    qtbot.waitUntil(lambda: hide.footer.status_text.text().startswith("Saved to"))

    reveal = window.reveal_page
    reveal.set_carrier_file(tmp_path / "embedded.wav")
    reveal.password.setText(STRONG)
    reveal.footer.primary.click()
    qtbot.waitUntil(lambda: reveal.result_card.isVisibleTo(reveal))
    assert (tmp_path / "out" / secret_file.name).read_bytes() == secret_file.read_bytes()


@pytest.mark.usefixtures("research")
def test_document_carrier_with_whitespace_padding(
    window: MainWindow, qtbot: QtBot, tmp_path: Path
) -> None:
    docx = pytest.importorskip("docx")
    cover = tmp_path / "letter.docx"
    document = docx.Document()

    for index in range(60):
        document.add_paragraph(f"Line {index} of a letter.")

    document.save(str(cover))
    hide = window.hide_page
    assert hide.set_carrier_file(cover)
    assert hide.carrier_kind.value() == CarrierKind.DOCUMENT
    assert hide._output == tmp_path / "embedded.docx"
    assert "holds up to 64.0 KiB" in hide.carrier_chip.meta.text()  # object stream
    hide.method.set_method(MethodId.WHITESPACE)
    assert "holds up to 480 B" in hide.carrier_chip.meta.text()
    hide.payload_kind.set_value("text")
    hide.text.setPlainText("Meet at nine.")
    _type_password(hide, STRONG)
    assert "of 480 B" in hide.capacity.used.text()
    hide.footer.primary.click()
    qtbot.waitUntil(lambda: hide.footer.status_text.text().startswith("Saved to"))

    reveal = window.reveal_page
    reveal.set_carrier_file(tmp_path / "embedded.docx")
    _choose(reveal, MethodId.WHITESPACE)
    reveal.password.setText(STRONG)
    reveal.footer.primary.click()
    qtbot.waitUntil(lambda: reveal.result_card.isVisibleTo(reveal))
    assert (tmp_path / "out" / "message.txt").read_text(encoding="utf-8") == "Meet at nine."


@pytest.mark.usefixtures("research")
def test_method_needing_a_missing_extra_keeps_the_carrier(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docx = pytest.importorskip("docx")
    cover = tmp_path / "letter.docx"
    document = docx.Document()
    document.add_paragraph("Hello.")
    document.save(str(cover))
    hide = window.hide_page
    hide.set_carrier_file(cover)
    monkeypatch.setattr(
        catalog.Requirement, "is_installed", property(lambda self: self.module != "docx")
    )
    hide.method.set_method(MethodId.WHITESPACE)  # needs python-docx
    assert hide.carrier_path == cover
    assert "holds up to 64.0 KiB" in hide.carrier_chip.meta.text()
    assert "Whitespace padding needs the documents extra" in hide.footer.status_text.text()


@pytest.mark.usefixtures("research")
def test_method_that_cannot_read_the_file_keeps_the_carrier(
    window: MainWindow, tmp_path: Path
) -> None:
    docx = pytest.importorskip("docx")
    good = tmp_path / "good.docx"
    docx.Document().save(str(good))
    cover = tmp_path / "odd.docx"

    with zipfile.ZipFile(good) as source, zipfile.ZipFile(cover, "w") as target:
        for info in source.infolist():
            broken = info.filename == "word/document.xml"
            target.writestr(info, b"<w:document" if broken else source.read(info))

    hide = window.hide_page
    hide.set_carrier_file(cover)
    hide.method.set_method(MethodId.WHITESPACE)  # python-docx rejects this file
    assert hide.carrier_path == cover
    hide.method.set_method(MethodId.OBJECT_STREAM)
    assert hide.carrier_path == cover
    assert "holds up to 64.0 KiB" in hide.carrier_chip.meta.text()


def test_app_icon_is_set(window: MainWindow) -> None:
    app = QApplication.instance()
    assert isinstance(app, QApplication)
    sizes = app.windowIcon().availableSizes()
    assert min(size.width() for size in sizes) == 16
    assert max(size.width() for size in sizes) == 512


def test_reveal_without_password_on_clean_image(
    window: MainWindow, qtbot: QtBot, make_image: ImageFactory
) -> None:
    reveal = window.reveal_page
    reveal.set_carrier_file(make_image())
    reveal.footer.primary.click()
    qtbot.waitUntil(lambda: reveal.error.isVisibleTo(reveal))
    assert reveal.error.messages() == (f"{payload.NOT_FOUND_MESSAGE}.",)
    assert reveal.password_hint.messages() == (ENCRYPTED_HINT,)  # Standard always encrypts
    assert reveal.hint.messages() == (STANDARD_HINT,)


@pytest.mark.parametrize(
    ("name", "method", "is_compressed"),
    [
        ("IMG_0001.CR2", MethodId.LSB, True),
        ("cover.png", MethodId.DCT, True),
        ("cover.png", MethodId.LSB, False),
    ],
    ids=["raw", "png-for-dct", "png-for-lsb"],
)
def test_a_carrier_is_compressed_in_the_background(
    window: MainWindow,
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    method: MethodId,
    is_compressed: bool,
) -> None:
    """Compressing a RAW, PNG or TIFF for DCT takes seconds: the window stays responsive,
    says what it is doing and keeps Hide off until the capacity is known."""
    hide = window.hide_page
    carrier = tmp_path / name
    carrier.write_bytes(b"picture")
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"secret")
    hide.set_payload_file(secret)
    _type_password(hide, STRONG)
    release = threading.Event()
    threads: list[str] = []

    def describe(*_args: object, **_kwargs: object) -> service.CarrierDetails:
        threads.append(threading.current_thread().name)
        release.wait(timeout=5)

        return service.CarrierDetails("192 × 128 · CR2", 2547)

    def estimate(*_args: object) -> service.Estimate:
        return service.Estimate(required=60, available=2547)

    monkeypatch.setattr(service, "describe_carrier", describe)
    monkeypatch.setattr(service, "estimate", estimate)
    hide.method.set_method(method)

    if not is_compressed:
        release.set()

    hide.set_carrier_file(carrier)

    if is_compressed:
        assert hide.carrier_chip.meta.text() == "Compressing to JPEG…"
        assert hide.footer.status_text.text() == (
            f"Compressing {name} to JPEG to measure its capacity…"
        )
        assert not hide.footer.primary.isEnabled()

        release.set()
        qtbot.waitUntil(lambda: not hide._compressions.is_running)

    assert hide.carrier_chip.meta.text() == "192 × 128 · CR2 · holds up to 2.5 KiB"
    assert hide.footer.primary.isEnabled()
    assert (threads[0] != threading.main_thread().name) is is_compressed


def test_new_research_settings_compress_the_carrier_again_in_the_background(
    window: MainWindow, qtbot: QtBot, make_image: ImageFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The capacity is cached per setting, so a precover is compressed again, never on the
    window's thread."""
    skip_unless_method_installed(MethodId.DCT)
    hide = window.hide_page
    threads: list[str] = []
    compress = precover.compress

    def recording(*args: Any) -> precover.Precover:
        threads.append(threading.current_thread().name)

        return compress(*args)

    monkeypatch.setattr(precover, "compress", recording)
    window.research_switch.click()
    hide.method.set_method(MethodId.DCT)
    hide.set_carrier_file(make_image("scan.png", size=(320, 240)))
    qtbot.waitUntil(lambda: not hide._compressions.is_running)
    hide.set_settings(Settings.of({"pre.q": 80}))

    assert hide.carrier_chip.meta.text() == "Compressing to JPEG…"

    qtbot.waitUntil(lambda: not hide._compressions.is_running)

    assert "holds up to" in hide.carrier_chip.meta.text()
    assert len(threads) == 2
    assert threading.main_thread().name not in threads


@pytest.mark.usefixtures("research")
def test_the_carrier_capacity_follows_the_research_settings(
    window: MainWindow, make_image: ImageFactory
) -> None:
    hide = window.hide_page
    cover = make_image(size=(320, 240))
    hide.set_carrier_file(cover)
    fill = Settings.of({"stc.fill": 40})
    hide.set_settings(fill)
    details = service.describe_carrier(cover, CarrierKind.IMAGE, MethodId.LSB, fill)

    assert details.capacity is not None
    assert hide.carrier_chip.meta.text().endswith(f"holds up to {format_size(details.capacity)}")
    assert f"of {format_size(details.capacity)}" in hide.capacity.used.text()


def test_choosing_a_raw_shows_dct(
    window: MainWindow,
    qtbot: QtBot,
    make_image: ImageFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A carrier the shown method cannot use brings up one that can; one it can use keeps it."""
    skip_unless_method_installed(MethodId.DCT)
    pytest.importorskip("rawpy")
    hide = window.hide_page
    raw = tmp_path / "IMG_0001.CR2"
    raw.write_bytes(b"picture")
    details = service.CarrierDetails("192 × 128 · CR2", 2547)
    monkeypatch.setattr(service, "describe_carrier", lambda *_args, **_kwargs: details)

    assert hide.options().method == MethodId.LSB

    hide.set_carrier_file(raw)
    qtbot.waitUntil(lambda: not hide._compressions.is_running)

    assert hide.options().method == MethodId.DCT

    hide.set_carrier_file(make_image("cover.jpg"))

    assert hide.options().method == MethodId.DCT


@pytest.mark.parametrize("is_written_while_compressing", [False, True], ids=["after", "during"])
def test_a_precover_written_again_is_compressed_again(
    window: MainWindow,
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    is_written_while_compressing: bool,
) -> None:
    """A new version of the chosen PNG is compressed in turn, once, so the page never waits
    on a compression that is not running."""
    skip_unless_method_installed(MethodId.DCT)
    hide = window.hide_page
    carrier = tmp_path / "cover.png"
    carrier.write_bytes(b"picture")
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"secret")
    hide.set_payload_file(secret)
    _type_password(hide, STRONG)
    release = threading.Event()
    compressions: list[str] = []

    def describe(path: Path, *_args: object, **_kwargs: object) -> service.CarrierDetails:
        if threading.current_thread() is not threading.main_thread():
            release.wait(timeout=5)
            compressions.append(path.name)

        return service.CarrierDetails(f"{path.stat().st_size} bytes", 2547)

    def estimate(*_args: object) -> service.Estimate:
        return service.Estimate(required=60, available=2547)

    monkeypatch.setattr(service, "describe_carrier", describe)
    monkeypatch.setattr(service, "estimate", estimate)
    hide.method.set_method(MethodId.DCT)
    hide.set_carrier_file(carrier)

    if not is_written_while_compressing:
        release.set()
        qtbot.waitUntil(lambda: hide.carrier_chip.meta.text() == "7 bytes · holds up to 2.5 KiB")
        release.clear()

    carrier.write_bytes(b"edited picture")

    if not is_written_while_compressing:
        hide.text.setPlainText("x")  # any refresh, as typing does

        assert hide.carrier_chip.meta.text() == "Compressing to JPEG…"
        assert hide.footer.status_text.text() == (
            "Compressing cover.png to JPEG to measure its capacity…"
        )
        assert not hide.footer.primary.isEnabled()
        assert hide._compressions.is_running

    release.set()
    qtbot.waitUntil(lambda: hide.carrier_chip.meta.text() == "14 bytes · holds up to 2.5 KiB")

    assert not hide._compressions.is_running
    assert hide.footer.status_text.text() == ""
    assert hide.footer.primary.isEnabled()
    assert compressions == ["cover.png", "cover.png"]


@pytest.mark.usefixtures("research")
def test_compressions_run_one_at_a_time_and_only_the_latest_request_waits(
    window: MainWindow, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Quick settings changes on a large RAW would each decode and compress it in parallel,
    each holding the decoded picture: the newest request waits for the running one, and the
    requests between never run."""
    skip_unless_method_installed(MethodId.DCT)
    hide = window.hide_page
    carrier = tmp_path / "cover.png"
    carrier.write_bytes(b"picture")
    release = threading.Event()
    compressed: list[Settings] = []

    def describe(*args: object, **_kwargs: object) -> service.CarrierDetails:
        if threading.current_thread() is not threading.main_thread():
            release.wait(timeout=5)
            compressed.append(args[3])  # type: ignore[arg-type]

        return service.CarrierDetails("192 × 128 · PNG", 2547)

    monkeypatch.setattr(service, "describe_carrier", describe)
    monkeypatch.setattr(service, "estimate", lambda *_args: service.Estimate(60, 2547))
    hide.method.set_method(MethodId.DCT)
    hide.set_carrier_file(carrier)
    hide.set_settings(Settings.of({"pre.q": 80}))
    hide.set_settings(Settings.of({"pre.q": 70}))

    assert hide._compressions.is_running
    assert compressed == []
    assert hide.carrier_chip.meta.text() == "Compressing to JPEG…"

    release.set()
    qtbot.waitUntil(lambda: not hide._compressions.is_running)

    assert compressed == [Settings(), Settings.of({"pre.q": 70})]
    assert hide._compressed is not None
    assert hide._compressed[1] == Settings.of({"pre.q": 70})
    assert hide.carrier_chip.meta.text() == "192 × 128 · PNG · holds up to 2.5 KiB"
    assert hide.footer.primary.isEnabled() is False  # no payload yet
    assert hide.footer.status_text.text() == ""


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("cover.png", "JPEG can't keep cover.png's transparency"),
        ("cover.jpg", "cover.jpg: CMYK JPEG is not supported"),
    ],
    ids=["precover", "jpeg"],
)
def test_a_method_that_cannot_use_the_carrier_says_why_once(
    window: MainWindow,
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    reason: str,
) -> None:
    """The footer gives the method's error and Hide stays off. Typing does not run the
    failing work (a compression, say) again."""
    skip_unless_method_installed(MethodId.DCT)
    hide = window.hide_page
    carrier = tmp_path / name
    carrier.write_bytes(b"picture")
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"secret")
    hide.set_payload_file(secret)
    _type_password(hide, STRONG)
    described: list[MethodId | None] = []
    estimated: list[MethodKey] = []

    def describe(
        _path: Path,
        _kind: CarrierKind,
        method: MethodId | None,
        _settings: Settings,
        *,
        is_research: bool = False,
    ) -> object:
        described.append(method)

        return service.CarrierDetails("96 × 72 · PNG", 2547)

    def estimate(*args: object) -> service.Estimate:
        options = args[-1]
        assert isinstance(options, service.HideOptions)
        estimated.append(options.method)

        if options.method == MethodId.DCT:
            raise UnsupportedFormatError(reason)

        return service.Estimate(required=60, available=2547)

    monkeypatch.setattr(service, "describe_carrier", describe)
    monkeypatch.setattr(service, "estimate", estimate)
    hide.method.set_method(MethodId.DCT)
    hide.set_carrier_file(carrier)
    qtbot.waitUntil(lambda: not hide._compressions.is_running)

    for text in ("p", "pa", "pas"):
        hide.text.setPlainText(text)

    assert hide.footer.status_text.text() == reason
    assert not hide.footer.primary.isEnabled()
    assert estimated == [MethodId.DCT]
    assert described.count(MethodId.DCT) == 1

    hide.method.set_method(MethodId.LSB)

    assert hide.footer.status_text.text() == ""
    assert hide.footer.primary.isEnabled()


def test_a_method_for_another_format_is_shown_but_blocked(
    window: MainWindow, make_image: ImageFactory, secret_file: Path
) -> None:
    _fill_hide(window, make_image("cover.bmp"), secret_file, STRONG)
    window.hide_page.method.set_method(MethodId.DCT)
    skip_unless_method_installed(MethodId.DCT)
    reason = "DCT coefficients (JPEG) can't use .bmp"
    assert reason in window.hide_page.footer.status_text.text()
    assert not window.hide_page.footer.primary.isEnabled()


@pytest.mark.usefixtures("research")
def test_payload_too_large(window: MainWindow, make_image: ImageFactory) -> None:
    hide = window.hide_page
    hide.set_carrier_file(make_image(size=(8, 8)))
    hide.payload_kind.set_value("text")
    hide.text.setPlainText("x" * 500)
    _type_password(hide, STRONG)
    assert "too large for this carrier" in hide.footer.status_text.text()
    assert hide.capacity.bar.property("over") is True
    assert not hide.footer.primary.isEnabled()


def test_unsupported_carrier_file(window: MainWindow, tmp_path: Path) -> None:
    archive = tmp_path / "a.zip"
    archive.write_bytes(b"zip")
    assert not window.hide_page.set_carrier_file(archive)
    assert "not a supported carrier" in window.hide_page.footer.status_text.text()


def test_dropping_a_file_on_the_payload_zone(
    window: MainWindow, qtbot: QtBot, secret_file: Path
) -> None:
    window.show()
    qtbot.waitExposed(window)
    window.mode.set_value("reveal")
    overlay = window.drop_overlay
    overlay.setGeometry(window._root.rect())
    overlay.show()
    zone = overlay._zones[DropZone.PAYLOAD]
    position = overlay.mapTo(window, zone.geometry().center())
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(secret_file))])
    event = QDropEvent(
        QPointF(position),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    window.dropEvent(event)
    assert window.mode.value() == "hide"
    assert window.hide_page.payload_path == secret_file
    assert not overlay.isVisible()
    assert overlay.zone_at(QPoint(-5, -5)) is None


@pytest.mark.usefixtures("research")
def test_cipher_popover_lists_every_cipher_and_picks_one(window: MainWindow) -> None:
    field = window.hide_page.cipher
    field.click()
    popover = field.popover
    assert popover.list.count() == len(CipherId)
    popover.list.itemClicked.emit(popover.list.item(1))
    assert field.cipher().id == CipherId.CHACHA20
    assert not popover.isVisible()
    captions = [label.text() for label in popover.detail.findChildren(QLabel)]
    assert "Size overhead +16 B" in captions


@pytest.mark.usefixtures("research")
def test_method_popover_lists_the_carrier_methods_and_picks_one(window: MainWindow) -> None:
    hide = window.hide_page
    field = hide.method
    field.set_method(MethodId.DCT)
    field.click()
    popover = field.popover
    assert popover.list.count() == len(catalog.carrier(CarrierKind.IMAGE).methods)
    popover.list.itemClicked.emit(popover.list.item(0))
    assert hide.options().method == MethodId.LSB
    assert not popover.isVisible()
    captions = [label.text() for label in popover.detail.findChildren(QLabel)]
    assert {"Secret order with a password", "Capacity up to 2.4 bits per pixel"} <= set(captions)

    hide.carrier_kind.set_value(CarrierKind.TEXT)
    assert popover.list.count() == 1
    assert hide.options().method == MethodId.ZERO_WIDTH


def test_options_that_cannot_work_are_greyed_out_with_the_reason(
    window: MainWindow, make_image: ImageFactory
) -> None:
    hide = window.hide_page
    hide.set_carrier_file(make_image("cover.bmp"))
    methods = hide.method.popover.list
    dct = methods.item(1)

    assert not dct.flags() & Qt.ItemFlag.ItemIsEnabled
    assert methods.itemWidget(dct).toolTip().startswith("DCT coefficients (JPEG)")
    assert dct.toolTip() == methods.itemWidget(dct).toolTip()

    methods.itemClicked.emit(dct)

    assert hide.options().method == MethodId.LSB


def test_capacity_mentions_the_encryption_overhead_only_when_encrypting(
    window: MainWindow,
) -> None:
    hide = window.hide_page

    assert "with encryption overhead" in hide.capacity.used.text()

    hide.cipher.set_cipher(CipherId.NONE)

    assert "encryption" not in hide.capacity.used.text()


def test_detection_row_lists_the_risks_and_tints_the_meter(
    window: MainWindow, make_image: ImageFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hide = window.hide_page
    large = tmp_path / "large.bin"
    large.write_bytes(bytes(9000))  # the cover is advised 1.5 KiB

    assert not hide.detection.isVisibleTo(hide)

    hide.set_carrier_file(make_image("cover.png", size=(256, 192)))
    hide.set_payload_file(large)
    found = hide.detection.messages()

    assert hide.detection.isVisibleTo(hide)
    assert hide.detection_label.isVisibleTo(hide)
    assert found[0].startswith("The payload takes 8.9 KiB of the carrier")  # with the overhead
    assert found[0].endswith("Keep it to 1.5 KiB or less.")
    assert hide.capacity.bar.property("conspicuous") is True
    assert hide.capacity.limit_marks.isVisibleTo(hide)

    hide.text.setPlainText("hi")
    hide.payload_kind.set_value("text")

    assert not hide.detection.isVisibleTo(hide)
    assert hide.capacity.bar.property("conspicuous") is False

    hide.payload_kind.set_value("file")

    assert hide.detection.isVisibleTo(hide)

    monkeypatch.setattr(catalog.Requirement, "is_installed", property(lambda _self: False))
    hide.cipher.set_cipher(CipherId.XCHACHA20)  # its extra is missing: nothing to advise on

    assert not hide.detection.isVisibleTo(hide)


@pytest.mark.qt_log_level_fail("WARNING")
@pytest.mark.qt_log_ignore("Populating font family aliases")
def test_clearing_the_warnings_keeps_a_valid_minimum_height(qtbot: QtBot) -> None:
    warnings = WarningList()
    qtbot.addWidget(warnings)
    warnings.set_messages(["risky"])
    warnings.set_messages([])
    warnings.resize(300, 40)

    assert warnings.minimumHeight() == 0


def test_pickers_work_by_keyboard(window: MainWindow) -> None:
    hide = window.hide_page

    for field in (hide.cipher, hide.method):
        field.click()
        QTest.keyClick(field.popover.list, Qt.Key.Key_Down)
        QTest.keyClick(field.popover.list, Qt.Key.Key_Return)
        assert not field.popover.isVisible()

    assert hide.options().cipher == CipherId.CHACHA20
    skip_unless_method_installed(MethodId.DCT)  # the next method: its row is off without jpeglib
    assert hide.options().method == MethodId.DCT


@pytest.mark.parametrize(
    ("field", "height", "expected"),
    [
        (QRect(100, 100, 500, 36), 300, QPoint(100, 142)),  # under the field
        (QRect(100, 700, 500, 36), 300, QPoint(100, 394)),  # no room below: above it
        (QRect(100, 400, 500, 36), 500, QPoint(100, 300)),  # neither: as low as the screen allows
        (QRect(600, 100, 500, 36), 300, QPoint(300, 142)),  # past the right edge: moved in
    ],
    ids=["below", "above", "clamped", "right-edge"],
)
def test_the_popover_stays_on_the_screen(field: QRect, height: int, expected: QPoint) -> None:
    assert popover_position(field, QSize(500, height), QRect(0, 0, 800, 800)) == expected


def test_the_popover_opens_upwards_near_the_screens_bottom(window: MainWindow) -> None:
    popover = window.hide_page.cipher.popover
    screen_bottom = window.screen().availableGeometry().bottom()
    popover.open_for(window.hide_page.cipher.current(), QRect(100, screen_bottom - 60, 500, 36))

    assert popover.isVisible()
    assert popover.geometry().bottom() < screen_bottom - 60

    popover.hide()


def test_picking_the_shown_method_again_changes_nothing(window: MainWindow, qtbot: QtBot) -> None:
    hide = window.hide_page
    hide.footer.set_status("done", "Saved to x", can_show_in_folder=True)

    with qtbot.assertNotEmitted(hide.method.method_changed):
        hide.method.set_method(MethodId.LSB)

    assert hide.footer.status_text.text() == "Saved to x"


def test_background_task_cancels_at_next_step(qtbot: QtBot) -> None:
    def job(report: ProgressCallback) -> str:
        report(0, 2, "first")
        report(1, 2, "second")

        return "done"

    task = BackgroundTask(job)
    succeeded: list[object] = []
    task.signals.succeeded.connect(succeeded.append)
    task.cancel()

    with qtbot.waitSignal(task.signals.finished):
        task.start()

    assert succeeded == []

    failing = BackgroundTask(lambda _report: (_ for _ in ()).throw(ValueError("boom")))

    with qtbot.waitSignal(failing.signals.failed) as blocker:
        failing.start()

    assert isinstance(blocker.args[0], ValueError)
    assert not isinstance(blocker.args[0], OperationCancelledError)


class _Stop(BaseException):
    pass


@pytest.mark.parametrize(
    ("error", "text"),
    [
        (SystemExit(3), "SystemExit: 3"),
        (SystemExit(), "SystemExit"),
        (_Stop("stop"), "_Stop: stop"),
    ],
    ids=["exit-code", "exit", "base"],
)
def test_background_task_reports_what_no_except_would_catch(
    qtbot: QtBot, error: BaseException, text: str
) -> None:
    """A SystemExit from an extension's code must not end the process from a worker thread,
    and the page must hear of every failure."""

    def job(_report: ProgressCallback) -> None:
        raise error

    task = BackgroundTask(job)
    failures: list[object] = []
    task.signals.failed.connect(failures.append)

    with qtbot.waitSignal(task.signals.finished):
        task.start()

    (failure,) = failures

    assert type(failure) is RuntimeError
    assert str(failure) == text


@pytest.mark.usefixtures("research")
def test_cancel_returns_to_the_form_at_once_and_saves_nothing(
    window: MainWindow,
    qtbot: QtBot,
    make_image: ImageFactory,
    secret_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resume = threading.Event()

    def slow_hide(*args: object) -> None:
        report = args[-1]
        assert callable(report)
        report(0, 2, "Embedding")
        resume.wait(timeout=5)
        report(1, 2, "Writing PNG")  # raises once cancelled
        (tmp_path / "embedded.png").write_bytes(b"never")

    monkeypatch.setattr("cipherstitch.service.hide", slow_hide)
    hide = window.hide_page
    _fill_hide(window, make_image(), secret_file, STRONG)
    hide.footer.primary.click()
    qtbot.waitUntil(lambda: hide.footer.step_label.text() == "Embedding")
    hide.footer.cancel_requested.emit()
    assert not hide.footer.is_working
    assert hide.body.isEnabled()

    resume.set()
    qtbot.waitUntil(lambda: not hide._tasks)
    assert not (tmp_path / "embedded.png").exists()
    assert hide.footer.status_text.text() == ""


@pytest.mark.usefixtures("research")
def test_closing_the_window_during_a_hide_quits_once_the_job_has_stopped(
    window: MainWindow,
    qtbot: QtBot,
    make_image: ImageFactory,
    secret_file: Path,
    tmp_path: Path,
    quits: list[None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A step can't be interrupted: the window goes at once, the job stops at its next step
    without writing, and only then does the app quit. A second close changes nothing."""
    resume = threading.Event()

    def slow_hide(*args: object) -> None:
        report = args[-1]
        assert callable(report)
        report(0, 2, "Embedding")
        resume.wait(timeout=5)
        report(1, 2, "Writing PNG")  # raises once cancelled
        (tmp_path / "embedded.png").write_bytes(b"never")

    monkeypatch.setattr("cipherstitch.service.hide", slow_hide)
    hide = window.hide_page
    window.show()
    _fill_hide(window, make_image(), secret_file, STRONG)
    hide.footer.primary.click()
    qtbot.waitUntil(lambda: hide.footer.step_label.text() == "Embedding")
    window.close()

    assert window.isHidden()
    assert hide.has_tasks()
    assert quits == []

    window.close()
    resume.set()
    qtbot.waitUntil(lambda: quits == [None])
    qtbot.wait(50)

    assert not hide._tasks
    assert not (tmp_path / "embedded.png").exists()
    assert quits == [None]


def test_closing_the_window_drops_a_compressions_outcome(
    window: MainWindow,
    qtbot: QtBot,
    tmp_path: Path,
    quits: list[None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hide = window.hide_page
    carrier = tmp_path / "scan.cr2"
    carrier.write_bytes(b"picture")
    release = threading.Event()

    def describe(*_args: object, **_kwargs: object) -> service.CarrierDetails:
        release.wait(timeout=5)

        raise UnsupportedFormatError("scan.cr2 is not a readable RAW")

    monkeypatch.setattr(service, "describe_carrier", describe)
    hide.set_carrier_file(carrier)

    assert hide._compressions.is_running

    window.close()
    release.set()
    qtbot.waitUntil(lambda: not hide._compressions.is_running)

    assert hide._unusable is None
    assert quits == [None]  # the compression was the last task


def test_background_task_stops_quietly_once_its_signals_are_gone() -> None:
    """After the app has quit, PySide deletes the signals' object under a running job."""
    steps: list[str] = []

    def job(report: ProgressCallback) -> None:
        report(0, 2, "first")
        steps.append("went on")

    task = BackgroundTask(job)
    shiboken6.delete(task.signals)
    task.run()

    assert steps == []


def test_show_in_folder_selects_the_file_in_finder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[list[str]] = []
    opened: list[QUrl] = []
    monkeypatch.setattr("subprocess.run", lambda args, check: commands.append(args))
    monkeypatch.setattr(base, "QDesktopServices", SimpleNamespace(openUrl=opened.append))
    monkeypatch.setattr("sys.platform", "darwin")
    base.show_in_folder(tmp_path / "embedded.png")
    assert commands == [["/usr/bin/open", "-R", str(tmp_path / "embedded.png")]]
    monkeypatch.setattr("sys.platform", "linux")
    base.show_in_folder(tmp_path / "embedded.png")
    assert opened == [QUrl.fromLocalFile(str(tmp_path))]


@pytest.mark.parametrize("raises", [False, True], ids=["returns", "raises"])
def test_background_task_drops_the_outcome_once_cancelled(qtbot: QtBot, raises: bool) -> None:
    def job(_report: ProgressCallback) -> str:
        task.cancel()  # no report follows: the outcome itself must be dropped

        if raises:
            raise ValueError("boom")

        return "done"

    task = BackgroundTask(job)
    outcomes: list[object] = []
    task.signals.succeeded.connect(outcomes.append)
    task.signals.failed.connect(outcomes.append)

    with qtbot.waitSignal(task.signals.finished):
        task.start()

    assert outcomes == []


def test_password_field_names_the_key_derivation(window: MainWindow) -> None:
    label = window.hide_page.password.strength_label
    window.hide_page.set_settings(Settings.of({"crypto.kdf": "scrypt"}))

    assert label.text() == "No password · Argon2id"  # Standard ignores the settings

    window.research_switch.click()

    assert label.text() == "No password · scrypt"

    window.hide_page.set_settings(Settings())

    assert label.text() == "No password · Argon2id"


@pytest.mark.usefixtures("research")
@pytest.mark.parametrize("function", list(kdf.Function))
def test_every_key_derivation_fits_the_meter(window: MainWindow, function: kdf.Function) -> None:
    field = window.hide_page.password
    window.hide_page.set_settings(Settings.of({"crypto.kdf": function.value}))

    for password in ("", "quiet harbour 47 lanterns"):
        field.line_edit.setText(password)
        label = field.strength_label

        assert label.fontMetrics().horizontalAdvance(label.text()) <= label.width()


@pytest.mark.usefixtures("research")
def test_hide_needs_the_password_repeated(
    window: MainWindow, make_image: ImageFactory, secret_file: Path
) -> None:
    hide = window.hide_page
    hide.set_carrier_file(make_image())
    hide.set_payload_file(secret_file)
    hide.password.line_edit.setText(STRONG)
    assert not hide.footer.primary.isEnabled()
    hide.password.repeat_edit.setText("Correct-Horse-43!")
    assert hide.footer.status_text.text() == "Passwords do not match"
    assert hide.password.repeat_edit.property("error") is True
    assert not hide.footer.primary.isEnabled()
    hide.password.repeat_edit.setText(STRONG)
    assert hide.password.repeat_edit.property("error") is False
    assert hide.footer.primary.isEnabled()


def _screen_height(monkeypatch: pytest.MonkeyPatch, height: int) -> None:
    """Every screen is ``height`` pixels tall, with nothing taken by a dock or a task bar."""
    monkeypatch.setattr(QScreen, "availableGeometry", lambda _self: QRect(0, 0, 800, height))


@pytest.mark.usefixtures("research")
def test_the_window_grows_with_the_form_while_the_screen_has_room(
    window: MainWindow, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    _screen_height(monkeypatch, 2000)
    window.show()
    hide = window.hide_page
    bar = hide.scroll_area.verticalScrollBar()
    qtbot.waitUntil(lambda: window.height() > WINDOW_MIN_HEIGHT and bar.maximum() == 0)
    before = window.height()
    hide.payload_kind.set_value("text")  # taller: the text box and two passwords
    qtbot.waitUntil(lambda: window.height() > before)

    assert bar.maximum() == 0
    assert window.minimumHeight() == WINDOW_MIN_HEIGHT
    assert hide.payload_kind.height() == hide.payload_kind.sizeHint().height()

    window.resize(WINDOW_WIDTH, WINDOW_MIN_HEIGHT)  # by hand: it stays
    qtbot.waitUntil(lambda: bar.maximum() > 0)
    hide.text.setPlainText("x")

    assert window.height() == WINDOW_MIN_HEIGHT


@pytest.mark.usefixtures("research")
def test_the_wheel_over_a_combo_box_scrolls_the_form_unless_it_has_the_focus(
    window: MainWindow, make_image: ImageFactory
) -> None:
    hide = window.hide_page
    window.show()
    hide.set_carrier_file(make_image("scan.tif"))  # three output formats to pick from
    formats = hide.output_format
    wheel = QWheelEvent(
        QPointF(5, 5),
        formats.mapToGlobal(QPointF(5, 5)),
        QPoint(0, 0),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(formats, wheel)

    assert formats.currentText() == "TIFF"
    assert formats.focusPolicy() == Qt.FocusPolicy.StrongFocus  # the wheel never gives it
    assert not wheel.isAccepted()  # a real (spontaneous) one goes on to the form's scroll area


@pytest.mark.usefixtures("research")
def test_on_a_short_screen_the_form_scrolls_and_the_footer_stays(
    window: MainWindow, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 600 px screen (a small laptop) gets a window it can hold: the rows keep their
    heights inside the scroll area, and the action stays in view."""
    _screen_height(monkeypatch, 600)
    window.show()
    hide = window.hide_page
    hide.payload_kind.set_value("text")
    window.resize(WINDOW_WIDTH, 600)
    bar = hide.scroll_area.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 0)
    footer_bottom = hide.footer.mapTo(window, QPoint(0, hide.footer.height())).y()

    assert window.height() == 600
    assert window.minimumHeight() < 600
    assert hide.footer.isVisible()
    assert footer_bottom <= window.height()
    assert hide.payload_kind.height() == hide.payload_kind.sizeHint().height()
    assert hide.body.height() > hide.scroll_area.viewport().height()


def test_the_payload_chip_works_by_keyboard(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    hide = window.hide_page
    asked: list[str] = []

    def open_file(_parent: QWidget, title: str, *_rest: str) -> tuple[str, str]:
        asked.append(title)

        return "", ""

    dialog = SimpleNamespace(getOpenFileName=open_file)
    monkeypatch.setattr(file_dialogs, "QFileDialog", dialog)

    assert hide.payload_chip.focusPolicy() == Qt.FocusPolicy.StrongFocus

    QTest.keyClick(hide.payload_chip, Qt.Key.Key_Space)
    QTest.keyClick(hide.payload_chip, Qt.Key.Key_Return)
    QTest.keyClick(hide.payload_chip, Qt.Key.Key_Tab)

    assert asked == ["Choose file to hide", "Choose file to hide"]
    assert hide.carrier_chip.focusPolicy() == Qt.FocusPolicy.NoFocus  # its button has the focus


def test_icon_only_buttons_say_what_they_do(window: MainWindow) -> None:
    buttons = [b for b in window.findChildren(QPushButton) if b.property("variant") == "icon"]

    assert len(buttons) == 2
    assert all(b.toolTip() and b.accessibleName() == b.toolTip() for b in buttons)

    for edit in (window.hide_page.password.line_edit, window.reveal_page.password):
        assert edit.visibility_toggle.text() == edit.visibility_toggle.toolTip() == "Show password"

    assert window.hide_page.cipher.accessibleName() == "AES-256-GCM"


@pytest.mark.usefixtures("research")
def test_return_in_a_password_field_runs_the_action_when_it_can(
    window: MainWindow, qtbot: QtBot, make_image: ImageFactory, secret_file: Path, tmp_path: Path
) -> None:
    hide, reveal = window.hide_page, window.reveal_page
    hide.set_carrier_file(make_image())
    _type_password(hide, STRONG)
    QTest.keyClick(hide.password.repeat_edit, Qt.Key.Key_Return)  # no payload yet

    assert not hide.footer.is_working

    hide.set_payload_file(secret_file)
    QTest.keyClick(hide.password.repeat_edit, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: hide.footer.status_text.text().startswith("Saved to"))

    reveal.set_carrier_file(tmp_path / "embedded.png")
    reveal.password.setText(STRONG)
    QTest.keyClick(reveal.password, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: reveal.result_card.isVisibleTo(reveal))

    assert (tmp_path / "out" / secret_file.name).read_bytes() == secret_file.read_bytes()


def test_password_inputs_can_show_their_text(window: MainWindow) -> None:
    hide_field = window.hide_page.password

    for edit in (hide_field.line_edit, hide_field.repeat_edit, window.reveal_page.password):
        assert edit.echoMode() == QLineEdit.EchoMode.Password
        edit.visibility_toggle.trigger()
        assert edit.echoMode() == QLineEdit.EchoMode.Normal
        assert edit.visibility_toggle.toolTip() == "Hide password"
        edit.visibility_toggle.trigger()
        assert edit.echoMode() == QLineEdit.EchoMode.Password


def test_failed_reveal_shows_the_install_hint_of_the_error(window: MainWindow) -> None:
    reveal = window.reveal_page
    reveal._on_failed(NoHiddenDataError(payload.NOT_FOUND_MESSAGE, "Install the extra"))

    assert reveal.error.messages() == (f"{payload.NOT_FOUND_MESSAGE}. Install the extra.",)
    assert reveal.password_hint.messages() == (ENCRYPTED_HINT,)
    assert reveal.hint.messages() == (STANDARD_HINT,)

    reveal._on_failed(NoHiddenDataError("Hidden data is truncated"))  # found, but damaged

    assert reveal.error.messages() == ("Hidden data is truncated.",)
    assert not reveal.password_hint.messages()
    assert not reveal.hint.messages()


@pytest.mark.parametrize(
    ("password", "cipher", "hints"),
    [
        (None, None, ((ENCRYPTED_HINT,), (STANDARD_HINT,))),
        (STRONG, None, ((), (STANDARD_HINT,))),
        (None, catalog.DEFAULT_CIPHER, ((ENCRYPTED_HINT,), (RESEARCH_HINT,))),
        (None, CipherId.NONE, ((), (RESEARCH_HINT,))),  # a plain frame takes none
        (STRONG, catalog.DEFAULT_CIPHER, ((), (RESEARCH_HINT,))),
    ],
    ids=["standard", "standard-password", "research", "research-plain", "research-password"],
)
def test_a_failed_reveal_hints_at_what_it_lacked(
    password: str | None, cipher: CipherId | None, hints: tuple[tuple[str, ...], ...]
) -> None:
    research = None if cipher is None else service.ResearchReveal(MethodId.LSB, cipher)

    assert reveal_page._hints(password, research) == hints


def _choices(combo: QComboBox) -> list[str]:
    return [combo.itemText(index) for index in range(combo.count())]


def test_reveal_asks_for_the_method_encryption_and_parameters_in_research(
    window: MainWindow, tmp_path: Path
) -> None:
    reveal = window.reveal_page
    window.mode.set_value("reveal")
    fields = (reveal.method, reveal.cipher, reveal.parameters, reveal.no_settings_note)

    assert not any(field.isVisibleTo(reveal) for field in fields)
    assert reveal.password.placeholderText() == "Leave empty if it was hidden without encryption"

    window.research_switch.click()

    assert all(field.isVisibleTo(reveal) for field in fields)
    assert reveal.password.placeholderText() == "The password used for hiding"
    assert reveal.no_settings_note.messages() == (NO_SETTINGS_NOTE,)
    assert reveal.cipher.currentText() == "AES-256-GCM · Recommended"
    assert reveal.method.count() == len(service.reveal_methods())

    audio = tmp_path / "voice.wav"
    audio.touch()
    reveal.set_carrier_file(audio)
    expected = [catalog.method(m) for m in service.reveal_methods(audio)]

    assert _choices(reveal.method) == [f"{m.name} · {m.tier}" for m in expected]

    _choose(reveal, MethodId.ECHO_HIDING)
    reveal.set_carrier_file(audio)

    assert reveal.research_reveal().method == MethodId.ECHO_HIDING  # it can still have

    image = tmp_path / "photo.png"
    image.touch()
    reveal.set_carrier_file(image)
    reveal.set_settings(Settings.of({"stc.h": 4}))

    assert reveal.research_reveal() == service.ResearchReveal(
        MethodId.LSB, catalog.DEFAULT_CIPHER, Settings.of({"stc.h": 4})
    )
    assert reveal.parameters.title.text() == "1 changed from Standard"
    assert not reveal.parameters.format_warning.isVisibleTo(reveal)  # the note says it all


@pytest.mark.usefixtures("research")
def test_research_reveal_needs_the_settings_the_hiding_used(
    window: MainWindow, qtbot: QtBot, make_image: ImageFactory, secret_file: Path, tmp_path: Path
) -> None:
    hide, reveal, editor = window.hide_page, window.reveal_page, window.parameter_editor
    hidden_with = Settings.of({"stc.h": 4, "stc.kept": 6})  # stc.h is Format, stc.kept not
    hide.set_settings(hidden_with)
    _fill_hide(window, make_image(), secret_file, STRONG)
    hide.footer.primary.click()
    qtbot.waitUntil(lambda: hide.footer.status_text.text().startswith("Saved to"))
    window.mode.set_value("reveal")
    reveal.set_carrier_file(tmp_path / "embedded.png")
    reveal.password.setText(STRONG)
    reveal.footer.primary.click()  # each page keeps its own values: Reveal's are Standard's
    qtbot.waitUntil(lambda: reveal.error.isVisibleTo(reveal))

    assert reveal.hint.messages() == (RESEARCH_HINT,)

    window.show()
    reveal.parameters.edit.click()

    assert editor.subtitle.text() == "LSB · AES-256-GCM · all Standard"

    editor.group_list.setCurrentRow(2)  # STC core
    height = _editor_rows(window)["stc.h"].control
    assert isinstance(height, QSpinBox)
    height.setValue(4)
    editor.done.click()

    assert not editor.format_warning.messages()  # revealing needs them, whatever they are
    assert reveal.research_reveal().settings == Settings.of({"stc.h": 4})
    assert hide.options().settings == hidden_with  # each page keeps its own

    reveal.footer.primary.click()
    qtbot.waitUntil(lambda: reveal.result_card.isVisibleTo(reveal))
    revealed = tmp_path / "out" / secret_file.name

    assert revealed.read_bytes() == secret_file.read_bytes()

    revealed.unlink()
    window.research_switch.click()  # Standard tries everything, at Standard's values
    reveal.footer.primary.click()
    qtbot.waitUntil(lambda: reveal.error.isVisibleTo(reveal))

    assert reveal.hint.messages() == (STANDARD_HINT,)


def test_research_mode_is_off_at_every_launch(window: MainWindow, qtbot: QtBot) -> None:
    assert not window.research_switch.isChecked()
    assert window.windowTitle() == "CipherStitch"
    assert not window.hide_page.options().is_research

    window.research_switch.click()

    assert window.windowTitle() == "CipherStitch — Research"

    reopened = MainWindow()
    qtbot.addWidget(reopened)

    assert not reopened.research_switch.isChecked()
    assert not reopened.hide_page.options().is_research


def test_the_switch_shows_on_both_pages_with_each_modes_note(window: MainWindow) -> None:
    assert window.standard_note.isVisibleTo(window)
    assert not window.research_note.isVisibleTo(window)

    window.mode.set_value("reveal")

    assert window.research_switch.isVisibleTo(window)
    assert not window.standard_note.isVisibleTo(window)

    window.research_switch.click()

    assert window.research_note.isVisibleTo(window)
    assert window.research_note_text.text() == RESEARCH_NOTES["reveal"]

    window.mode.set_value("hide")

    assert window.research_note_text.text() == RESEARCH_NOTES["hide"]
    assert not window.standard_note.isVisibleTo(window)


def test_standard_mode_lists_only_what_it_offers(window: MainWindow) -> None:
    hide = window.hide_page
    shown = [kind for kind in CarrierKind if hide.carrier_kind.button(kind).isVisibleTo(hide)]
    methods, ciphers = hide.method.popover.list, hide.cipher.popover.list

    assert shown == [CarrierKind.IMAGE, CarrierKind.AUDIO]
    assert hide.carrier_kind.button(CarrierKind.AUDIO).property("segPos") == "last"
    assert [_texts(methods.itemWidget(methods.item(i)))[0] for i in range(methods.count())] == [
        "LSB",
        "DCT coefficients (JPEG)",
    ]
    assert ciphers.count() == 3

    window.research_switch.click()
    replacement = methods.item(2)

    assert all(hide.carrier_kind.button(kind).isVisibleTo(hide) for kind in CarrierKind)
    assert hide.carrier_kind.button(CarrierKind.AUDIO).property("segPos") == "mid"
    assert (methods.count(), ciphers.count()) == (3, len(CipherId))
    assert replacement.flags() & Qt.ItemFlag.ItemIsEnabled
    assert _texts(methods.itemWidget(replacement))[-1] == "Experimental"


def test_switching_to_standard_moves_to_what_it_offers(window: MainWindow, tmp_path: Path) -> None:
    hide = window.hide_page
    window.research_switch.click()
    hide.carrier_kind.set_value(CarrierKind.DOCUMENT)
    hide.cipher.set_cipher(CipherId.FERNET)
    window.research_switch.click()
    options = hide.options()
    document = hide.carrier_kind.button(CarrierKind.DOCUMENT)

    assert (options.carrier, options.method, options.cipher) == (
        CarrierKind.IMAGE,
        MethodId.LSB,
        CipherId.AES_GCM,
    )
    assert not options.is_research
    assert not document.isVisibleTo(hide)
    assert "PNG · BMP · TIF · TIFF · JPG · JPEG" in hide.carrier_chip.meta.text()

    docx = tmp_path / "a.docx"
    docx.write_bytes(b"docx")

    assert not hide.set_carrier_file(docx)
    assert hide.carrier_kind.value() == CarrierKind.IMAGE
    assert hide.footer.status_text.text() == standard.carrier_reason(CarrierKind.DOCUMENT)

    window.research_switch.click()

    assert document.isVisibleTo(hide)
    assert hide.options().cipher == CipherId.AES_GCM
    assert hide.options().is_research
    assert "WEBP" in hide.carrier_chip.meta.text()


def test_switching_to_standard_moves_to_a_method_that_can_run(
    window: MainWindow, make_image: ImageFactory
) -> None:
    hide = window.hide_page
    window.research_switch.click()
    hide.carrier_kind.set_value(CarrierKind.AUDIO)
    hide.method.set_method(MethodId.PHASE_CODING)
    hide.cipher.set_cipher(CipherId.NONE)
    window.research_switch.click()

    expected = (MethodId.AUDIO_LSB, CipherId.AES_GCM)
    assert (hide.options().method, hide.options().cipher) == expected

    window.research_switch.click()
    hide.set_settings(Settings.of({"limg.lossy": True}))  # LSB on a JPEG, in Research only
    hide.set_carrier_file(make_image("cover.jpg"))

    assert hide.options().method == MethodId.LSB

    skip_unless_method_installed(MethodId.DCT)
    window.research_switch.click()

    assert hide.options().method == MethodId.DCT


@pytest.mark.parametrize("outcome", ["succeeded", "failed", "cancelled"])
def test_switching_to_standard_while_hiding_shows_once_done(
    window: MainWindow,
    qtbot: QtBot,
    make_image: ImageFactory,
    secret_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    """The form ignores changes while working. Once the job ends it shows what Standard mode
    changed (a password to type), under the job's own status."""
    resume = threading.Event()

    def slow_hide(*_args: object) -> None:
        resume.wait(timeout=5)

        if outcome == "failed":
            raise CipherStitchError("Hiding failed")

    monkeypatch.setattr("cipherstitch.service.hide", slow_hide)
    hide = window.hide_page
    window.research_switch.click()
    _fill_hide(window, make_image(), secret_file, None)
    hide.footer.primary.click()
    window.research_switch.click()

    assert hide.footer.is_working
    assert hide.options().cipher == CipherId.AES_GCM

    if outcome == "cancelled":
        hide.footer.cancel_requested.emit()

    resume.set()
    qtbot.waitUntil(lambda: not hide._tasks)

    assert hide.password.isVisibleTo(hide)
    assert not hide.no_encryption_warning.isVisibleTo(hide)
    assert not hide.footer.primary.isEnabled()

    if outcome == "succeeded":
        assert hide.footer.status_text.text().startswith("Saved to")
    elif outcome == "failed":
        assert hide.footer.status_text.text() == "Hiding failed"


@pytest.mark.filterwarnings("ignore:Function.*QDragEnterEvent:DeprecationWarning")
def test_dragging_shows_the_formats_the_page_takes_in_the_mode(
    window: MainWindow, secret_file: Path
) -> None:
    written = "PNG · BMP · TIFF · JPG · WAV · FLAC · PDF · DOCX · TXT · MD"

    assert drag_enter(window, secret_file) == "PNG · BMP · TIFF · JPG · Camera RAW · WAV · FLAC"

    window.mode.set_value("reveal")

    assert drag_enter(window, secret_file) == written

    window.mode.set_value("hide")
    window.research_switch.click()

    assert drag_enter(window, secret_file) == (
        "PNG · BMP · TIFF · JPG · WEBP · Camera RAW · WAV · FLAC · PDF · DOCX · TXT · MD"
    )

    window.mode.set_value("reveal")

    assert drag_enter(window, secret_file) == written


@pytest.mark.filterwarnings("ignore:Function.*QDragEnterEvent:DeprecationWarning")
def test_dragging_says_whether_the_payload_is_encrypted(
    window: MainWindow, secret_file: Path
) -> None:
    def payload_caption() -> str:
        drag_enter(window, secret_file)
        caption: str = window.drop_overlay._zones[DropZone.PAYLOAD].caption.text()

        return caption

    assert payload_caption() == "Encrypted, then embedded in the carrier"

    window.hide_page.cipher.set_cipher(CipherId.NONE)

    assert payload_caption() == "Embedded in the carrier without encryption"


def test_standard_mode_greys_out_a_cover_it_refuses(
    window: MainWindow, make_image: ImageFactory
) -> None:
    hide = window.hide_page
    hide.set_carrier_file(make_image("cover.png", mode="L"))
    methods = hide.method.popover.list
    lsb = methods.item(0)

    assert not lsb.flags() & Qt.ItemFlag.ItemIsEnabled
    assert methods.itemWidget(lsb).toolTip().startswith(f"{standard.OFF}: LSB would save")
    assert methods.itemWidget(lsb).cursor().shape() == Qt.CursorShape.ForbiddenCursor


def test_standard_capacity_limit(window: MainWindow, make_image: ImageFactory) -> None:
    hide = window.hide_page
    hide.set_carrier_file(make_image(size=(320, 240)))  # 20 144 bytes, 1 933 advised
    hide.payload_kind.set_value("text")
    hide.text.setPlainText("x" * 500)
    _type_password(hide, STRONG)

    assert "of 2.4 KiB allowed (capacity 19.7 KiB)" in hide.capacity.used.text()
    assert hide.capacity.percent.text() == "3%"  # of the whole capacity: of the limit, about 23%
    assert hide.capacity.limit_marks.isVisibleTo(hide)
    assert hide.capacity.limit_marks._is_hatched
    assert not hide.capacity.limit_marks.grab().isNull()
    assert hide.footer.primary.isEnabled()

    hide.text.setPlainText("x" * 10_000)

    assert hide.footer.status_text.text().endswith("over the advised 2.4 KiB for this carrier")
    assert hide.capacity.bar.property("over") is True
    assert hide.capacity.percent.text() == "50%"
    assert not hide.footer.primary.isEnabled()

    window.research_switch.click()

    assert "allowed" not in hide.capacity.used.text()
    assert hide.capacity.limit_marks.isVisibleTo(hide)  # the advised size, no longer a limit
    assert not hide.capacity.limit_marks._is_hatched
    assert hide.capacity.bar.property("conspicuous") is True
    assert hide.footer.primary.isEnabled()


def test_standard_warns_of_a_weak_password_at_once_and_research_once_typed(
    window: MainWindow, make_image: ImageFactory, secret_file: Path
) -> None:
    """Research's minimum is its ``crypto.minpw`` parameter, Strong by default."""
    hide = window.hide_page
    message = hide.password.min_strength_message
    _fill_hide(window, make_image(size=(320, 240)), secret_file, "")

    assert message.isVisibleTo(hide)
    assert _texts(message) == [standard.weak_password(3, is_research=False)]

    _type_password(hide, STRONG)

    assert not message.isVisibleTo(hide)
    assert hide.footer.primary.isEnabled()

    window.research_switch.click()
    _type_password(hide, "")

    assert not message.isVisibleTo(hide)

    _type_password(hide, "weak")

    assert _texts(message) == [standard.weak_password(3, is_research=True)]
    assert not hide.footer.primary.isEnabled()

    hide.set_settings(Settings.of({"crypto.minpw": 0}))

    assert not message.isVisibleTo(hide)
    assert hide.footer.primary.isEnabled()


def _texts(widget: QWidget) -> list[str]:
    return [child.text() for child in widget.findChildren(QLabel) if child.text()]


def test_hide_then_reveal_in_standard_mode(
    window: MainWindow, qtbot: QtBot, make_image: ImageFactory, secret_file: Path, tmp_path: Path
) -> None:
    hide = window.hide_page
    _fill_hide(window, make_image(size=(320, 240)), secret_file, STRONG)
    hide.footer.primary.click()
    qtbot.waitUntil(lambda: hide.footer.status_text.text().startswith("Saved to"))

    window.mode.set_value("reveal")
    reveal = window.reveal_page
    reveal.set_carrier_file(tmp_path / "embedded.png")
    reveal.password.setText(STRONG)
    reveal.footer.primary.click()
    qtbot.waitUntil(lambda: reveal.result_card.isVisibleTo(reveal))

    assert (tmp_path / "out" / secret_file.name).read_bytes() == secret_file.read_bytes()


def test_the_parameters_row_sums_up_research_settings(window: MainWindow) -> None:
    hide = window.hide_page
    row = hide.parameters
    groups = registry.groups_for(MethodId.LSB, is_encrypted=True)
    count = sum(len(group.params) for group in groups)

    assert not row.isVisibleTo(hide)

    window.research_switch.click()

    assert row.isVisibleTo(hide)
    assert row.title.text() == "Standard values"
    assert row.meta.text() == f"{count} parameters in {len(groups)} groups for this method"
    assert not row.format_warning.isVisibleTo(hide)

    hide.set_settings(Settings.of({"stc.kept": 6, "echo.seg": 40}))  # echo hiding's: not LSB's

    assert row.title.text() == "1 changed from Standard"
    assert row.meta.text() == "KEPT_PER_BIT 6"
    assert not row.format_warning.isVisibleTo(hide)

    changes = {"limg.cost": "uniform", "limg.meta": False, "stc.head": 1_048_576, "crypto.iter": 3}
    hide.set_settings(Settings.of(changes))

    assert row.title.text() == "4 changed from Standard"
    assert row.meta.text() == (  # in the groups' order, as the editor lists them
        "ARGON2_ITERATIONS 3 · HEAD_UNITS 1048576 · Cost function Uniform (LSB matching) · +1"
    )
    assert not row.grab().isNull()  # paints the line cut to the chip's width
    assert row.format_warning.isVisibleTo(hide)
    assert row.format_warning.messages() == (format_warning(2),)
    assert format_warning(1).startswith("1 format parameter differs from Standard.")

    hide.cipher.set_cipher(CipherId.NONE)  # the key derivation's parameters no longer apply

    assert row.title.text() == "3 changed from Standard"
    assert row.meta.text() == (
        "HEAD_UNITS 1048576 · Cost function Uniform (LSB matching) · Keep metadata off"
    )

    window.research_switch.click()

    assert not row.isVisibleTo(hide)
    assert not hide.options().is_research


def test_the_parameters_row_follows_dcts_kind_of_cover(
    window: MainWindow, qtbot: QtBot, make_image: ImageFactory
) -> None:
    """A precover's compression parameters count only for a RAW, PNG or TIFF cover."""
    skip_unless_method_installed(MethodId.DCT)
    hide = window.hide_page
    window.research_switch.click()
    hide.method.set_method(MethodId.DCT)
    hide.set_settings(Settings.of({"pre.q": 80}))

    assert hide.parameters.title.text() == "1 changed from Standard"  # no cover yet: either kind

    hide.set_carrier_file(make_image("photo.jpg"))

    assert hide.parameters.title.text() == "Standard values"

    hide.set_carrier_file(make_image("scan.png"))
    qtbot.waitUntil(lambda: not hide._compressions.is_running)

    assert hide.options().method == MethodId.DCT
    assert hide.parameters.title.text() == "1 changed from Standard"


def _editor_rows(window: MainWindow) -> dict[str, ParamRow]:
    return dict(window.parameter_editor._rows)


@pytest.mark.usefixtures("research")
def test_the_editor_opens_on_the_groups_hiding_reads(window: MainWindow) -> None:
    editor = window.parameter_editor
    groups = registry.groups_for(MethodId.LSB, is_encrypted=True)

    assert not editor.isVisible()

    window.show()
    window.hide_page.parameters.edit.click()
    items = editor._group_items

    assert editor.isVisible()
    assert [item.group.id for item in items] == [group.id for group in groups]
    assert [item.count.text() for item in items] == [str(len(g.params)) for g in groups]
    assert editor.subtitle.text() == "LSB · AES-256-GCM · all Standard"
    assert editor.presets.currentText() == "Standard values"
    assert editor.group_title.text() == groups[0].label
    assert not editor.reset_group.isEnabled()

    editor.done.click()

    assert not editor.isVisible()


@pytest.mark.usefixtures("research")
def test_an_edit_reaches_the_hide_page_and_resets(window: MainWindow) -> None:
    hide, editor = window.hide_page, window.parameter_editor
    window.show()
    hide.parameters.edit.click()
    editor.group_list.setCurrentRow(2)  # STC core
    height = _editor_rows(window)["stc.h"]
    assert isinstance(height.control, QSpinBox)
    height.control.setValue(4)

    assert editor.settings() == Settings.of({"stc.h": 4})
    assert hide.options().settings == Settings()  # Hide reads them once the editor closes
    assert editor.subtitle.text() == "LSB · AES-256-GCM · 1 changed · 1 format"
    assert editor.format_warning.messages() == (format_warning(1),)
    assert editor._group_items[2].count.text() == "1 changed"
    assert not height.reset_button.isHidden()  # rows show on the next event loop pass
    assert editor.presets.currentText() == CUSTOM

    height.reset_button.click()

    assert editor.settings() == Settings()
    assert height.reset_button.isHidden()
    assert editor.presets.currentText() == "Standard values"

    height.control.setValue(5)
    editor.done.click()

    assert hide.options().settings == Settings.of({"stc.h": 5})
    assert hide.parameters.title.text() == "1 changed from Standard"


@pytest.mark.usefixtures("research")
def test_the_editor_offers_presets_and_resets_a_group(window: MainWindow) -> None:
    hide, editor = window.hide_page, window.parameter_editor
    window.show()
    hide.parameters.edit.click()
    fast = editor.presets.findText("Fast STC (h = 4)")
    editor.presets.activated.emit(fast)

    assert editor.settings() == Settings.of({"stc.h": 4, "stc.kept": 6})
    assert editor.presets.currentText() == "Fast STC (h = 4)"

    editor.group_list.setCurrentRow(2)  # STC core
    editor.reset_group.click()

    assert editor.settings() == Settings()


@pytest.mark.usefixtures("research")
def test_the_editor_shows_options_and_switches(window: MainWindow) -> None:
    hide, editor = window.hide_page, window.parameter_editor
    window.show()
    hide.parameters.edit.click()
    editor.group_list.setCurrentRow(3)  # LSB image
    rows = _editor_rows(window)
    costs, wet = rows["limg.cost"].control, rows["limg.wet"].control
    assert isinstance(costs, QComboBox)
    assert isinstance(wet, ToggleSwitch)

    assert [costs.itemText(index) for index in range(costs.count())] == [
        "HILL",
        f"Uniform (LSB matching){EXPERIMENTAL}",
        f"WOW{EXPERIMENTAL}",
        f"S-UNIWARD{EXPERIMENTAL}",
        f"MiPOD{EXPERIMENTAL}",
    ]
    assert (wet.isChecked(), wet.text()) == (True, "On")

    wet.click()
    costs.activated.emit(1)

    assert editor.settings() == Settings.of({"limg.wet": False, "limg.cost": "uniform"})
    assert wet.text() == "Off"


@pytest.mark.usefixtures("research")
def test_the_editor_refuses_values_that_cannot_work_together(window: MainWindow) -> None:
    hide, editor = window.hide_page, window.parameter_editor
    hide.carrier_kind.set_value(CarrierKind.AUDIO)
    hide.method.set_method(MethodId.PHASE_CODING)
    window.show()
    hide.parameters.edit.click()
    editor.group_list.setCurrentRow([item.group.id for item in editor._group_items].index("phase"))
    rows = _editor_rows(window)
    segment, bins = rows["phase.seg"].control, rows["phase.bins"].control
    assert isinstance(segment, QSpinBox)
    assert isinstance(bins, QSpinBox)
    segment.setValue(1024)
    bins.setValue(512)

    assert editor.settings() == Settings.of({"phase.seg": 1024})
    assert bins.value() == 64
    assert editor.error.messages() == (
        "Phase coding: The coded bins must end below half the segment length",
    )

    segment.setValue(4096)

    assert not editor.error.messages()

    first = rows["phase.first"]
    assert isinstance(first.control, QSpinBox)
    segment.setValue(1024)
    first.control.setValue(1)
    bins.setValue(496)  # 1 + 496 fits a 1024-sample segment, 32 + 496 does not
    first.reset_button.click()
    kept = Settings.of({"phase.seg": 1024, "phase.first": 1, "phase.bins": 496})

    assert editor.settings() == kept
    assert editor.error.messages()[0].startswith("Phase coding: ")


@pytest.mark.usefixtures("research")
def test_the_editor_edits_fractions_in_their_steps(window: MainWindow) -> None:
    skip_unless_method_installed(MethodId.ECHO_HIDING)
    hide, editor = window.hide_page, window.parameter_editor
    hide.carrier_kind.set_value(CarrierKind.AUDIO)
    hide.method.set_method(MethodId.ECHO_HIDING)
    window.show()
    hide.parameters.edit.click()
    editor.group_list.setCurrentRow([item.group.id for item in editor._group_items].index("echo"))
    decay = _editor_rows(window)["echo.decay"].control
    assert isinstance(decay, QDoubleSpinBox)

    assert (decay.decimals(), decay.singleStep()) == (2, 0.05)

    decay.setValue(0.55)

    assert editor.settings() == Settings.of({"echo.decay": 0.55})


@pytest.mark.usefixtures("research")
def test_a_value_off_its_step_shows_as_the_step_everywhere(window: MainWindow) -> None:
    """A profile's 83 for a parameter in steps of 5: the Parameters row and the editor show
    the 85 the settings hold, and a value typed in moves to its step too."""
    hide, editor = window.hide_page, window.parameter_editor
    hide.set_settings(Settings.of({"stc.fill": 83}))

    assert hide.parameters.meta.text() == "MAX_FILL_PERCENT 85 %"

    window.show()
    hide.parameters.edit.click()
    editor.group_list.setCurrentRow([item.group.id for item in editor._group_items].index("stc"))
    fill = _editor_rows(window)["stc.fill"].control
    assert isinstance(fill, QSpinBox)

    assert fill.value() == 85

    fill.setValue(72)

    assert (fill.value(), editor.settings()) == (70, Settings.of({"stc.fill": 70}))


@pytest.mark.usefixtures("research")
@pytest.mark.filterwarnings("ignore:Function.*QDragEnterEvent:DeprecationWarning")
def test_the_editor_covers_the_window_until_closed_and_research_keeps_its_settings(
    window: MainWindow, secret_file: Path
) -> None:
    hide, editor = window.hide_page, window.parameter_editor
    window.show()
    hide.parameters.edit.click()
    editor.presets.activated.emit(editor.presets.findText("Fast STC (h = 4)"))
    covered = (window.mode, window.research_switch, window.pages)

    assert not any(widget.isEnabled() for widget in covered)

    drag_enter(window, secret_file)

    assert not window.drop_overlay.isVisible()  # no drop while the editor is open

    QTest.keyClick(editor, Qt.Key.Key_Escape)

    assert not editor.isVisible()
    assert all(widget.isEnabled() for widget in covered)
    assert hide.options().settings == Settings.of({"stc.h": 4, "stc.kept": 6})

    window.research_switch.click()

    assert hide.options().mode_settings == Settings()  # Standard ignores them

    window.research_switch.click()

    assert hide.options().settings == Settings.of({"stc.h": 4, "stc.kept": 6})


@pytest.mark.usefixtures("research")
def test_the_editor_reopens_on_its_group_and_the_wheel_only_scrolls(window: MainWindow) -> None:
    hide, editor = window.hide_page, window.parameter_editor
    window.show()
    hide.parameters.edit.click()
    editor.group_list.setCurrentRow(2)  # STC core
    editor.done.click()
    hide.parameters.edit.click()
    height = _editor_rows(window)["stc.h"].control
    assert isinstance(height, QSpinBox)
    wheel = QWheelEvent(
        QPointF(5, 5),
        QPointF(5, 5),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(height, wheel)

    assert editor.group_title.text() == "STC core"
    assert height.value() == 6
    assert not wheel.isAccepted()  # it goes on to the scroll area
    assert editor.settings() == Settings()


@pytest.mark.usefixtures("research")
def test_closing_the_editor_gives_the_focus_back(qtbot: QtBot, window: MainWindow) -> None:
    hide, editor = window.hide_page, window.parameter_editor
    window.show()
    window.activateWindow()
    qtbot.waitActive(window)
    hide.parameters.edit.click()
    editor.group_list.setCurrentRow(2)  # STC core
    height = _editor_rows(window)["stc.h"].control
    height.setFocus()
    qtbot.waitUntil(height.hasFocus)
    editor.done.click()

    assert QApplication.focusWidget() is hide.parameters.edit

    closed: list[Settings] = []
    editor.closed.connect(closed.append)
    QTest.keyClick(editor, Qt.Key.Key_Escape)  # a closed sheet takes no keys

    assert not closed


@pytest.mark.usefixtures("research")
def test_the_presets_cover_only_the_parameters_shown(window: MainWindow) -> None:
    hide, editor = window.hide_page, window.parameter_editor
    hide.set_settings(Settings.of({"pre.q": 80}))  # a precover's, which LSB does not read
    window.show()
    hide.parameters.edit.click()

    assert editor.subtitle.text() == "LSB · AES-256-GCM · all Standard"
    assert editor.presets.currentText() == "Standard values"
    assert editor.presets.findText("P-0 · no side information") == -1

    editor.presets.activated.emit(editor.presets.findText("Fast STC (h = 4)"))

    assert editor.settings() == Settings.of({"pre.q": 80, "stc.h": 4, "stc.kept": 6})

    editor.presets.activated.emit(editor.presets.findText("Standard values"))

    assert editor.settings() == Settings.of({"pre.q": 80})


def _write_profile(path: Path, **fields: object) -> Path:
    document = {"format": "cipherstitch-profile", "version": 1, "defaults": 1, **fields}
    path.write_text(json.dumps(document), encoding="utf-8")

    return path


def _choose_profile(monkeypatch: pytest.MonkeyPatch, path: Path | None) -> None:
    monkeypatch.setattr(profiles, "choose", lambda _parent: path)


@pytest.mark.usefixtures("research")
def test_a_profile_fills_reveal_and_clears(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reveal = window.reveal_page
    row = reveal.parameters
    fast = _write_profile(
        tmp_path / "fast.csprofile",
        name="Fast STC",
        method="lsb",
        cipher="none",
        params={"stc.h": 4},
    )
    _choose_profile(monkeypatch, fast)
    row.load.click()

    assert reveal.research_reveal() == service.ResearchReveal(
        MethodId.LSB, CipherId.NONE, Settings.of({"stc.h": 4})
    )
    assert not reveal.password.isEnabled()
    assert row.title.text() == "Fast STC"
    assert row.meta.text() == "fast.csprofile · CONSTRAINT_HEIGHT 4"
    assert row.load.text() == CLEAR

    reveal.set_settings(Settings.of({"stc.h": 5}))  # edited since: no longer the profile

    assert row.title.text() == "1 changed from Standard"

    row.load.click()

    assert reveal.research_reveal().settings == Settings()
    assert row.load.text() == LOAD


@pytest.mark.usefixtures("research")
def test_reveal_refuses_unknown_profile_parameters_and_hide_leaves_them_out(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    newer = _write_profile(tmp_path / "newer.csprofile", params={"stc.h": 4, "zz.new": 1})
    _choose_profile(monkeypatch, newer)
    reveal, hide = window.reveal_page, window.hide_page
    reveal.parameters.load.click()

    assert reveal.parameters.notice.messages()[0].startswith("newer.csprofile sets parameters")
    assert reveal.research_reveal().settings == Settings()
    assert reveal.parameters.load.text() == LOAD

    hide.parameters.load.click()

    assert hide.parameters.notice.messages() == (
        "Left out the parameters this version of CipherStitch doesn't know (zz.new). "
        "An extension that isn't installed may add them",
    )
    assert hide.options().settings == Settings.of({"stc.h": 4})

    broken = tmp_path / "broken.csprofile"
    broken.write_text("{", encoding="utf-8")
    _choose_profile(monkeypatch, broken)
    hide.parameters.load.click()  # Clear first
    hide.parameters.load.click()

    assert hide.parameters.notice.messages() == ("broken.csprofile is not a CipherStitch profile.",)
    assert hide.options().settings == Settings()

    hide.parameters.load_file(tmp_path / "gone.csprofile")

    assert hide.parameters.notice.messages() == (
        "Can't read gone.csprofile. No such file or directory.",
    )


@pytest.mark.usefixtures("research")
def test_the_editor_exports_a_profile_and_offers_it_by_name(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hide, editor = window.hide_page, window.parameter_editor
    hide.set_settings(Settings.of({"stc.h": 4, "pre.q": 80}))  # pre.q: not LSB's
    target = tmp_path / "harbour"
    monkeypatch.setattr(profiles, "choose_export", lambda _parent, _name: target)
    window.show()
    hide.parameters.edit.click()
    editor.export.click()
    exported = profile.read(target)

    assert exported == Profile(
        Settings.of({"stc.h": 4}), "harbour", method=MethodId.LSB, cipher=catalog.DEFAULT_CIPHER
    )
    assert editor.presets.currentText() == "harbour"

    editor.presets.activated.emit(editor.presets.findText("Standard values"))
    editor.done.click()
    hide.parameters.edit.click()

    assert editor.presets.findText("harbour") >= 0  # used lately
    assert editor.settings() == Settings.of({"pre.q": 80})

    monkeypatch.setattr(profiles, "choose_export", lambda _parent, _name: tmp_path)
    editor.export.click()  # a folder: it can't be written

    assert editor.error.messages()[0].startswith(f"Can't save {tmp_path.name}. ")


@pytest.mark.usefixtures("research")
@pytest.mark.parametrize(
    ("stem", "name"),
    [("h" * 100, "h" * profile.MAX_NAME_LENGTH), (f"har{chr(0x200B)}bour", "harbour")],
    ids=["long", "hidden"],
)
def test_an_exported_profile_gets_a_name_its_reader_accepts(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stem: str, name: str
) -> None:
    hide, editor = window.hide_page, window.parameter_editor
    hide.set_settings(Settings.of({"stc.h": 4}))
    target = tmp_path / stem
    monkeypatch.setattr(profiles, "choose_export", lambda _parent, _name: target)
    window.show()
    hide.parameters.edit.click()
    editor.export.click()

    assert not editor.error.messages()
    assert profile.read(target).name == name


def test_the_export_dialog_adds_the_suffix_before_asking_to_replace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}

    class SaveDialog:
        """Records how the dialog is set up, and accepts it."""

        AcceptMode = QFileDialog.AcceptMode

        def __init__(self, *_args: object) -> None:
            pass

        def __getattr__(self, name: str) -> Any:
            return lambda value: calls.__setitem__(name, value)

        def exec(self) -> int:
            return 1

        def selectedFiles(self) -> list[str]:  # noqa: N802 (Qt's name)
            return ["/chosen/fast.csprofile"]

    monkeypatch.setattr(profiles, "QFileDialog", SaveDialog)

    assert profiles.choose_export(QWidget(), "Fast") == Path("/chosen/fast.csprofile")
    assert calls == {
        "setAcceptMode": QFileDialog.AcceptMode.AcceptSave,
        "setDefaultSuffix": "csprofile",
        "selectFile": "Fast.csprofile",
        "setParent": None,  # destroyed at once: it records its folders then
    }


def test_a_profile_opened_while_the_editor_is_open_waits_for_it(
    window: MainWindow, tmp_path: Path
) -> None:
    fast = _write_profile(tmp_path / "fast.csprofile", params={"stc.h": 4})
    window.research_switch.click()
    window.show()
    window.hide_page.parameters.edit.click()
    window.open_profile(fast)

    assert window.mode.value() == "hide"  # the sheet covers the mode control

    window.parameter_editor.done.click()

    assert window.mode.value() == "reveal"
    assert window.reveal_page.research_reveal().settings == Settings.of({"stc.h": 4})


def test_profiles_used_lately_are_kept_up_to_eight(tmp_path: Path) -> None:
    paths = [_write_profile(tmp_path / f"p{index}.csprofile") for index in range(10)]

    for path in paths:
        profiles.load(path)

    paths[9].unlink()  # gone since: skipped
    profiles.load(paths[3])  # again: first, once

    assert [path for path, _loaded in profiles.recent()] == [
        paths[3],
        paths[8],
        *paths[7:3:-1],
        paths[2],
    ]


def test_a_profile_opened_with_the_app_goes_to_reveal_in_research(
    window: MainWindow, tmp_path: Path
) -> None:
    fast = _write_profile(tmp_path / "fast.csprofile", method="lsb", params={"stc.h": 4})
    opener = app._ProfileOpener(window)

    assert opener.eventFilter(window, QFileOpenEvent(str(fast)))
    assert window.mode.value() == "reveal"
    assert window.research_switch.isChecked()
    assert window.reveal_page.research_reveal().settings == Settings.of({"stc.h": 4})
    assert not opener.eventFilter(window, QFileOpenEvent(str(tmp_path / "photo.png")))


def test_free_path(tmp_path: Path) -> None:
    assert free_path(tmp_path, "p", ".png") == tmp_path / "p.png"
    (tmp_path / "p.png").touch()
    (tmp_path / "p-2.png").touch()
    assert free_path(tmp_path, "p", ".png") == tmp_path / "p-3.png"
