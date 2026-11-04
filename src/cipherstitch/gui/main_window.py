"""CipherStitch main window: Hide/Reveal switch, Research mode switch, the pages and the
drag-and-drop overlay."""

import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QEvent, QSize
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QKeySequence,
    QMoveEvent,
    QResizeEvent,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from cipherstitch import service, standard
from cipherstitch.extension_loader import Failure
from cipherstitch.gui import theme
from cipherstitch.gui.pages.hide_page import HidePage
from cipherstitch.gui.pages.reveal_page import RevealPage
from cipherstitch.gui.widgets.common import icon_label, label
from cipherstitch.gui.widgets.drop_overlay import DropOverlay, DropZone, format_names
from cipherstitch.gui.widgets.parameter_editor import ParameterEditor
from cipherstitch.gui.widgets.research_switch import ResearchSwitch
from cipherstitch.gui.widgets.segmented import SegmentedControl
from cipherstitch.gui.widgets.warning_list import WarningList
from cipherstitch.params.settings import Settings
from cipherstitch.password import STRENGTH_LABELS

APP_NAME = "CipherStitch"
RESEARCH_TITLE = f"{APP_NAME} — Research"
WINDOW_WIDTH = 800
WINDOW_MIN_HEIGHT = 640
_FRAME_MARGIN = 48  # the title bar, and a little room, above the screen's available area
_HIDE = "hide"
_REVEAL = "reveal"
STANDARD_NOTE = (
    "Recommended carriers and algorithms only · payloads up to the advised size · "
    f"{STRENGTH_LABELS[standard.MIN_PASSWORD_STRENGTH].lower()} password required"
)
RESEARCH_NOTES = {
    _HIDE: "Research mode · every algorithm and parameter unlocked · output may be easier to "
    "detect, not for real secrets",
    _REVEAL: "Research mode · set the method, encryption and parameters used for hiding, or "
    "load a .csprofile",
}
_printed_failures: tuple[Failure, ...] = ()  # already on stderr: the loader keeps one tuple


def failure_lines() -> tuple[str, ...]:
    """One line per extension that couldn't load, with its first problem, for Research mode.
    Every problem and the traceback go to stderr once per process, for the author."""
    global _printed_failures

    failures = service.extension_failures()

    if failures is not _printed_failures:
        _printed_failures = failures

        for failure in failures:
            _print_failure(failure)

    return tuple(f"Extension {f.extension} couldn't load: {f.brief}" for f in failures)


def _print_failure(failure: Failure) -> None:
    problems = (f"  {problem}" for problem in failure.problems)
    print(f"Extension {failure.extension} couldn't load:", *problems, sep="\n", file=sys.stderr)

    if failure.traceback is not None:
        print(failure.traceback, end="", file=sys.stderr)


def _dropped_file(event: QDropEvent | QDragEnterEvent | QDragMoveEvent) -> Path | None:
    urls = event.mimeData().urls()

    if len(urls) != 1 or not urls[0].isLocalFile():
        return None

    path = Path(urls[0].toLocalFile())

    return path if path.is_file() else None


class MainWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(APP_NAME)
        self.setFixedWidth(WINDOW_WIDTH)
        self._fit_screen()
        self._wanted_height = 0  # the last height the form asked for
        self._quits_when_idle = False  # closed during work: the app quits once the jobs end
        self.setAcceptDrops(True)

        root = QWidget()
        self._root = root
        root.setObjectName("root")
        self.mode = SegmentedControl([(_HIDE, "Hide", "eye-slash"), (_REVEAL, "Reveal", "eye")])
        self.research_switch = ResearchSwitch()
        self.standard_note, _text = _note("shield-check", STANDARD_NOTE)
        self.research_note, self.research_note_text = _note("flask", "")
        self.extension_failures = WarningList(top_margin=0)  # Research mode's, under its note
        header = QHBoxLayout()
        header.setSpacing(16)
        header.addWidget(self.mode)
        header.addStretch(1)
        header.addWidget(self.research_switch)
        mode_bar = QWidget()
        mode_layout = QVBoxLayout(mode_bar)
        mode_layout.setContentsMargins(28, 20, 28, 4)
        mode_layout.setSpacing(10)
        mode_layout.addLayout(header)
        mode_layout.addWidget(self.standard_note)
        mode_layout.addWidget(self.research_note)
        mode_layout.addWidget(self.extension_failures)

        self.hide_page = HidePage()
        self.reveal_page = RevealPage()
        self.hide_page.idle.connect(self._quit_once_idle)
        self.reveal_page.idle.connect(self._quit_once_idle)
        self.pages = QStackedWidget()
        self.pages.addWidget(self.hide_page)
        self.pages.addWidget(self.reveal_page)
        self.mode.changed.connect(self._switch_mode)

        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(mode_bar)
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(root)
        self.drop_overlay = DropOverlay(root)
        self.parameter_editor = ParameterEditor(root)
        self._pending_profile: Path | None = None  # opened while the editor was open
        self.parameter_editor.closed.connect(self._on_parameters_edited)
        self.hide_page.parameters.edit.clicked.connect(self._edit_parameters)
        self.reveal_page.parameters.edit.clicked.connect(self._edit_parameters)
        # Every launch starts in Standard mode: the app stores no settings.
        self.research_switch.toggled.connect(self._show_research)
        self._show_research(False)

        quit_action = QAction(self, shortcut=QKeySequence(QKeySequence.StandardKey.Quit))
        quit_action.triggered.connect(self.close)
        self.addAction(quit_action)

    def open_profile(self, path: Path) -> None:
        """A profile opened with the app: Reveal in Research mode, with the profile loaded.
        While the parameter editor covers the window, it waits for the editor to close."""
        if self.parameter_editor.isVisible():
            self._pending_profile = path

            return

        self.mode.set_value(_REVEAL)
        self.research_switch.setChecked(True)
        self.reveal_page.parameters.load_file(path)

    def _switch_mode(self, mode: str) -> None:
        self.pages.setCurrentWidget(self.hide_page if mode == _HIDE else self.reveal_page)
        self._show_notes()

    def _show_research(self, is_research: bool) -> None:
        """Research mode loads the extensions the first time it is on (in the pages' lists),
        on this thread: a slow import holds the window that long (docs/extensions.md)."""
        self.setWindowTitle(RESEARCH_TITLE if is_research else APP_NAME)
        self.hide_page.set_research(is_research)
        self.reveal_page.set_research(is_research)

        if is_research:
            self.extension_failures.set_messages(failure_lines())

        self._show_notes()

    def _form_page(self) -> HidePage | RevealPage:
        return self.hide_page if self.mode.value() == _HIDE else self.reveal_page

    def _form_pages(self) -> tuple[HidePage, RevealPage]:
        return self.hide_page, self.reveal_page

    def _edit_parameters(self) -> None:
        """The editor over the whole window, for what the shown page's choices read. Each
        page keeps its own values: revealing needs the ones some file was hidden with."""
        self._set_covered(is_covered=True)
        self.parameter_editor.setGeometry(self._root.rect())
        self.parameter_editor.show_for(self._form_page().parameter_scope())

    def _show_notes(self) -> None:
        """Standard mode's note on Hide only; Research mode's on both pages, each its own."""
        mode = self.mode.value()
        is_research = self.research_switch.isChecked()
        self.standard_note.setVisible(mode == _HIDE and not is_research)
        self.research_note.setVisible(is_research)
        self.research_note_text.setText(RESEARCH_NOTES[mode])
        self.extension_failures.setVisible(is_research and bool(self.extension_failures.messages()))

    def _on_parameters_edited(self, settings: Settings) -> None:
        page = self._form_page()  # the sheet covered the mode control: still the same page
        self._set_covered(is_covered=False)
        # Hiding the sheet found nothing enabled to take the focus: it stayed in a hidden row.
        page.parameters.edit.setFocus()

        if settings != page.parameter_scope().settings:
            page.set_settings(settings)

        pending, self._pending_profile = self._pending_profile, None

        if pending is not None:
            self.open_profile(pending)

    def _set_covered(self, *, is_covered: bool) -> None:
        """What the sheet covers leaves the Tab chain and the keyboard while it is open."""
        for widget in (self.mode, self.research_switch, self.pages):
            widget.setEnabled(not is_covered)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt override)
        """Every job is cancelled: one left running would write its output after the window
        has gone. A job stops only at its next step, seconds away on a large picture, so
        while one runs the window hides and the app quits once the last one has ended."""
        for page in self._form_pages():
            page.cancel_tasks()

        if not any(page.has_tasks() for page in self._form_pages()):
            super().closeEvent(event)

            return

        event.ignore()
        self.hide()
        self._quits_when_idle = True

    def _quit_once_idle(self) -> None:
        if self._quits_when_idle and not any(page.has_tasks() for page in self._form_pages()):
            QApplication.quit()

    # — size —

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        """Tall enough for the whole form while the screen has room; past that the body
        scrolls."""
        return QSize(WINDOW_WIDTH, min(super().sizeHint().height(), self._room()))

    def event(self, event: QEvent) -> bool:
        handled = super().event(event)

        if event.type() == QEvent.Type.LayoutRequest:
            self._grow_with_the_form()

        return handled

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        self._fit_screen()

    def moveEvent(self, event: QMoveEvent) -> None:  # noqa: N802 (Qt override)
        super().moveEvent(event)
        self._fit_screen()  # another screen may be shorter

    def _room(self) -> int:
        """The tallest the window can be on its screen."""
        return self.screen().availableGeometry().height() - _FRAME_MARGIN

    def _fit_screen(self) -> None:
        self.setMinimumHeight(min(WINDOW_MIN_HEIGHT, self._room()))

    def _grow_with_the_form(self) -> None:
        """A form that got taller (the text payload, a wrapped status) shows whole while the
        screen has room, instead of behind a scroll bar. The window can still be made shorter
        by hand: only a form that grows again grows it."""
        wanted = self.sizeHint().height()

        if wanted > self._wanted_height and self.height() < wanted:
            self.resize(WINDOW_WIDTH, wanted)

        self._wanted_height = wanted

    # — drag and drop —

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self.drop_overlay.setGeometry(self._root.rect())
        self.parameter_editor.setGeometry(self._root.rect())

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 (Qt override)
        if _dropped_file(event) is None or self._is_busy():
            return

        event.acceptProposedAction()
        self.drop_overlay.set_carrier_icon(self.hide_page.carrier_icon())
        self.drop_overlay.set_carrier_formats(format_names(self._carrier_suffixes()))
        self.drop_overlay.set_payload_encrypted(self.hide_page.options().is_encrypted)
        self.drop_overlay.setGeometry(self._root.rect())
        self.drop_overlay.raise_()
        self.drop_overlay.show()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802 (Qt override)
        position = self.drop_overlay.mapFrom(self, event.position().toPoint())
        zone = self.drop_overlay.zone_at(position)
        self.drop_overlay.set_active(zone)

        if zone is None:
            event.ignore()
        else:
            event.acceptProposedAction()

    def dragLeaveEvent(self, event: object) -> None:  # noqa: N802 (Qt override)
        self._hide_overlay()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 (Qt override)
        position = self.drop_overlay.mapFrom(self, event.position().toPoint())
        zone = self.drop_overlay.zone_at(position)
        path = _dropped_file(event)
        self._hide_overlay()

        if path is None or zone is None:
            return

        event.acceptProposedAction()

        if zone == DropZone.PAYLOAD:
            self.mode.set_value(_HIDE)
            self.hide_page.set_payload_file(path)
        elif self.mode.value() == _REVEAL:
            self.reveal_page.set_carrier_file(path)
        else:
            self.hide_page.set_carrier_file(path)

    def _carrier_suffixes(self) -> Sequence[str]:
        """What the shown page takes in the mode: a carrier on Hide, a file Hide wrote on
        Reveal, as its file dialog does."""
        is_research = self.research_switch.isChecked()

        if self.mode.value() == _REVEAL:
            return service.reveal_suffixes(is_research=is_research)

        kinds = service.mode_carriers(is_research=is_research)

        return [
            s for kind in kinds for s in service.carrier_suffixes(kind, is_research=is_research)
        ]

    def _hide_overlay(self) -> None:
        self.drop_overlay.set_active(None)
        self.drop_overlay.hide()

    def _is_busy(self) -> bool:
        """Working, or editing the parameters: dropped files wait."""
        return (
            self.hide_page.footer.is_working
            or self.reveal_page.footer.is_working
            or self.parameter_editor.isVisible()
        )


def _note(icon_name: str, text: str) -> tuple[QWidget, QLabel]:
    """A line under the header: an icon and a note, hidden until a mode shows it."""
    note = QWidget()
    row = QHBoxLayout(note)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    row.addWidget(icon_label(icon_name, theme.ACCENT_300, 14))
    note_text = label(text, "warning")
    row.addWidget(note_text, 1)
    note.hide()

    return note, note_text
