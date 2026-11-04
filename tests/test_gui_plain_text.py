"""Text from outside the app shows as written: file names, folders, errors and profile names
that look like markup are never rendered as rich text, in labels or in tooltips."""

import json
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtGui
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget
from pytestqt.qtbot import QtBot

from cipherstitch import payload
from cipherstitch.catalog import MethodId
from cipherstitch.gui.main_window import MainWindow
from cipherstitch.gui.widgets.common import plain_tooltip
from cipherstitch.gui.widgets.result_card import ResultCard

from .conftest import ImageFactory
from .gui_support import isolated_session, new_window

MARKUP = "<b>"


@pytest.fixture(autouse=True)
def session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    isolated_session(monkeypatch, tmp_path)


@pytest.fixture
def window(qtbot: QtBot, tmp_path: Path) -> MainWindow:
    window = new_window(qtbot, tmp_path)
    window.research_switch.click()

    return window


def _rich_labels(widget: QWidget) -> list[str]:
    """The texts that labels in ``widget`` render as rich text, since Qt reads them as markup."""
    return [
        label.text()
        for label in widget.findChildren(QLabel)
        if label.textFormat() != Qt.TextFormat.PlainText and QtGui.Qt.mightBeRichText(label.text())
    ]


def test_file_names_show_as_written_on_both_pages(
    window: MainWindow, make_image: ImageFactory, tmp_path: Path
) -> None:
    cover = make_image(f"{MARKUP}cover.png")
    secret = tmp_path / f"{MARKUP}notes.txt"
    secret.write_text("meet at noon")
    unreadable = tmp_path / f"{MARKUP}broken.png"
    unreadable.write_bytes(b"not a picture")
    hide, reveal = window.hide_page, window.reveal_page
    hide.set_payload_file(secret)
    hide.set_carrier_file(cover)

    assert hide.carrier_chip.title.text() == cover.name
    assert hide.payload_chip.title.text() == secret.name
    assert _rich_labels(window) == []

    hide.set_carrier_file(unreadable)
    reveal.set_carrier_file(unreadable)

    assert reveal.carrier_chip.title.text() == unreadable.name
    assert hide.footer.status_text.text().startswith(f"Can't read {unreadable.name}")
    assert _rich_labels(window) == []


def test_the_result_card_shows_the_name_and_folder_as_written(qtbot: QtBot, tmp_path: Path) -> None:
    """A hidden file's name can't hold ``<``, but Qt reads an entity as markup too."""
    revealed = tmp_path / f"{MARKUP}out" / "&lt;b&gt;notes.txt"
    payload.validate_file_name(revealed.name)
    card = ResultCard()
    qtbot.addWidget(card)
    card.show_result(revealed, 12, is_verified=True)

    assert card.title.text() == revealed.name
    assert f"{MARKUP}out" in card.details.text()
    assert _rich_labels(card) == []


def test_tooltips_show_reasons_and_profile_names_as_written(
    window: MainWindow, tmp_path: Path
) -> None:
    reason = f"LSB can't use {MARKUP}cover files"
    popover = window.hide_page.method.popover
    window.hide_page.method.set_unavailable({MethodId.LSB: reason})
    tips = [popover.list.item(index).toolTip() for index in range(popover.list.count())]
    tips += [row.toolTip() for row in popover._rows]

    assert plain_tooltip(reason) in tips
    assert not any(MARKUP in tip for tip in tips)

    profile = tmp_path / f"{MARKUP}fast.csprofile"
    document = {"format": "cipherstitch-profile", "version": 1, "defaults": 1}
    profile.write_text(json.dumps({**document, "params": {"stc.h": 4}}), encoding="utf-8")
    row = window.hide_page.parameters
    row.load_file(profile)

    assert row.meta.text() == f"{profile.name} · CONSTRAINT_HEIGHT 4"
    assert row.meta.toolTip() == plain_tooltip(row.meta.text())
    assert _rich_labels(window) == []


@pytest.mark.parametrize(
    ("text", "tooltip"),
    [
        ("", ""),
        ("Standard values", "Standard values"),
        ("a < b & c", "a < b & c"),  # no tag: Qt shows it as written already
        (f"{MARKUP}cover", "<p>&lt;b&gt;cover</p>"),
    ],
)
def test_a_plain_tooltip_escapes_only_what_qt_would_read_as_markup(text: str, tooltip: str) -> None:
    assert plain_tooltip(text) == tooltip
