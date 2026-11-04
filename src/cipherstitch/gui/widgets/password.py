"""Password inputs: a line edit that can show its text, and the hide page's field with a
4-segment strength meter, the key-derivation name and a repeat input."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from cipherstitch import kdf
from cipherstitch.gui import theme
from cipherstitch.gui.widgets.common import icon_label, label
from cipherstitch.password import STRENGTH_LABELS, password_strength

_SEGMENTS = 4
# Fixed, so the meter keeps its length as the text changes. The longest text is "No password ·
# PBKDF2-SHA256" (Research's crypto.kdf), 184 px in Inter at 12 px.
_STRENGTH_LABEL_WIDTH = 190


class PasswordEdit(QLineEdit):
    """A masked line edit with a trailing eye button that shows or hides the text."""

    def __init__(self, placeholder: str, parent: QWidget | None = None) -> None:
        super().__init__(parent, echoMode=QLineEdit.EchoMode.Password, placeholderText=placeholder)
        self.visibility_toggle = self.addAction(
            theme.icon("eye", theme.MUTED), QLineEdit.ActionPosition.TrailingPosition
        )
        self.visibility_toggle.setCheckable(True)
        self.visibility_toggle.toggled.connect(self._show_text)
        self._show_text(False)

    def _show_text(self, is_shown: bool) -> None:
        self.setEchoMode(QLineEdit.EchoMode.Normal if is_shown else QLineEdit.EchoMode.Password)
        self.visibility_toggle.setIcon(theme.icon("eye-slash" if is_shown else "eye", theme.MUTED))
        text = "Hide password" if is_shown else "Show password"
        self.visibility_toggle.setText(text)  # read out, not drawn: the button shows the icon
        self.visibility_toggle.setToolTip(text)


class PasswordField(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.line_edit = PasswordEdit("Required")
        self.repeat_edit = PasswordEdit("Repeat password")
        self._segments = [QFrame() for _ in range(_SEGMENTS)]
        self.strength_label = label(role="muted")
        self.strength_label.setFixedWidth(_STRENGTH_LABEL_WIDTH)
        self.strength_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        meter = QHBoxLayout()
        meter.setSpacing(4)

        for segment in self._segments:
            segment.setProperty("role", "strengthSegment")
            meter.addWidget(segment, 1)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addLayout(meter, 1)
        row.addWidget(self.strength_label)

        self._min_strength = 0
        self._kdf_name = kdf.Function.ARGON2ID.label
        self._warns_when_empty = False
        self._min_strength_text = label(role="warning", wrap=True)
        self.min_strength_message = QWidget()
        message = QHBoxLayout(self.min_strength_message)
        message.setContentsMargins(0, 0, 0, 0)
        message.setSpacing(8)
        message.addWidget(icon_label("shield-check", theme.ACCENT_300, 15))
        message.addWidget(self._min_strength_text, 1)
        self.min_strength_message.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.line_edit)
        layout.addLayout(row)
        layout.addWidget(self.min_strength_message)
        layout.addWidget(self.repeat_edit)
        self.line_edit.textChanged.connect(self._show_strength)
        self.line_edit.textChanged.connect(self._show_mismatch)
        self.repeat_edit.textChanged.connect(self._show_mismatch)
        self._show_strength("")

    def text(self) -> str:
        return self.line_edit.text()

    def is_confirmed(self) -> bool:
        return self.repeat_edit.text() == self.line_edit.text()

    def is_mismatched(self) -> bool:
        """The repeat input has text that differs from the password."""
        return bool(self.repeat_edit.text()) and not self.is_confirmed()

    def set_min_strength(self, strength: int, message: str, *, warns_when_empty: bool) -> None:
        """Require ``strength`` (0 requires nothing), shown with ``message`` while unmet:
        from the start, or once something is typed."""
        self._min_strength = strength
        self._warns_when_empty = warns_when_empty
        self._min_strength_text.setText(message)
        self._show_strength(self.text())

    def set_kdf_name(self, name: str) -> None:
        """What derives the key, shown beside the strength."""
        self._kdf_name = name
        self._show_strength(self.text())

    def is_strong_enough(self) -> bool:
        return password_strength(self.text()) >= self._min_strength

    def _show_mismatch(self) -> None:
        theme.set_property(self.repeat_edit, "error", self.is_mismatched())

    def _show_strength(self, password: str) -> None:
        strength = password_strength(password)

        for index, segment in enumerate(self._segments):
            theme.set_property(segment, "filled", index < strength)

        self.strength_label.setText(f"{STRENGTH_LABELS[strength]} · {self._kdf_name}")
        is_shown = bool(password) or self._warns_when_empty
        self.min_strength_message.setVisible(is_shown and not self.is_strong_enough())
