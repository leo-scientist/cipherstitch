"""Long names in the parameter editor's combo boxes: a wider list, then scrolling."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QSpinBox
from pytestqt.qtbot import QtBot

from cipherstitch.gui import theme
from cipherstitch.gui.widgets import marquee_combo
from cipherstitch.gui.widgets.marquee_combo import MarqueeComboBox
from cipherstitch.gui.widgets.parameter_rows import ParamRow
from cipherstitch.params import registry

LONG = "Uniform (LSB matching) · experimental, and then some more words"
_WIDTH = 160


@pytest.fixture(autouse=True)
def fast_scrolling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(marquee_combo, "PAUSE_MS", 10)
    monkeypatch.setattr(marquee_combo, "_MS_PER_PIXEL", 1)


def _combo(qtbot: QtBot, *names: str) -> MarqueeComboBox:
    app = QApplication.instance()
    assert isinstance(app, QApplication)
    theme.apply(app)
    combo = MarqueeComboBox()
    qtbot.addWidget(combo)
    combo.addItems(names)
    combo.setFixedWidth(_WIDTH)
    combo.show()

    return combo


def _hover(combo: MarqueeComboBox, is_over: bool) -> None:
    kind = QEvent.Type.Enter if is_over else QEvent.Type.Leave
    QApplication.sendEvent(combo, QEvent(kind))


def test_a_name_that_fits_never_moves(qtbot: QtBot) -> None:
    combo = _combo(qtbot, "HILL")
    _hover(combo, True)
    qtbot.wait(50)

    assert combo.overflow() == 0
    assert combo.scroller.offset == 0


def test_a_cut_name_scrolls_to_its_end_while_hovered(qtbot: QtBot) -> None:
    combo = _combo(qtbot, LONG)
    overflow = combo.overflow()
    assert overflow > 0

    _hover(combo, True)
    qtbot.waitUntil(lambda: combo.scroller.offset == overflow)
    _hover(combo, False)

    assert combo.scroller.offset == 0


def test_the_list_fits_the_names_and_ends_where_the_box_ends(qtbot: QtBot) -> None:
    combo = _combo(qtbot, "HILL", "WOW · experimental, a little longer than the box")
    combo.showPopup()
    popup = combo.view().window()

    assert popup.width() > _WIDTH
    assert popup.width() <= marquee_combo._MAX_POPUP_WIDTH
    assert popup.geometry().right() == combo.mapToGlobal(combo.rect().topRight()).x()

    combo.hidePopup()


def test_a_listed_name_wider_than_the_list_scrolls_while_highlighted(qtbot: QtBot) -> None:
    combo = _combo(qtbot, "HILL", LONG * 2)
    combo.setCurrentIndex(1)
    combo.showPopup()
    scroller = combo.delegate.scroller

    qtbot.waitUntil(lambda: scroller.offset > 0)

    combo.view().setCurrentIndex(combo.model().index(0, 0))
    assert scroller.offset == 0

    combo.hidePopup()


def test_number_fields_keep_their_text_off_the_edge() -> None:
    _, param = registry.lookup("stc.h")
    row = ParamRow(param)
    control = row.control
    assert isinstance(control, QSpinBox)

    assert control.lineEdit().textMargins().left() > 0
