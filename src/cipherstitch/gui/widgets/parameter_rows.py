"""One row of the parameter editor: what a parameter is, its control and its reset button."""

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from cipherstitch.gui import theme
from cipherstitch.gui.widgets.common import icon_label, plain_label, row, tag
from cipherstitch.gui.widgets.marquee_combo import MarqueeComboBox
from cipherstitch.gui.widgets.toggle import ToggleSwitch
from cipherstitch.params import values
from cipherstitch.params.model import Kind, Param, ParamType, Value

KINDS = {  # the tag's text and style, and what the kind means
    Kind.FORMAT: (
        "Format",
        "recommended",
        "Part of the file format. Reveal needs the same value. Nothing is stored in the file.",
    ),
    Kind.EMBEDDER: (
        "Embedder",
        "acceptable",
        "Only affects how the payload is hidden. Reveal is unaffected.",
    ),
    Kind.GUIDANCE: (
        "Guidance",
        "notRecommended",
        "A warning or Standard-mode rule. Does not change the file.",
    ),
}
EXPERIMENTAL = " · experimental"
_CONTROL_WIDTH = 232
_RESET_SIZE = 28
_DOT_SIZE = 6
_TEXT_MARGIN = 8  # the spin box's edit field ignores the style sheet's padding

Control = QComboBox | QSpinBox | QDoubleSpinBox | ToggleSwitch


def kind_tag(kind: Kind) -> QLabel:
    text, style, meaning = KINDS[kind]
    widget = tag(text, style)
    widget.setToolTip(meaning)

    return widget


class ParamRow(QWidget):
    """Emits ``changed(value)`` when the control changes and ``reset()`` from its button."""

    changed = Signal(object)
    reset = Signal()

    def __init__(self, param: Param, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.param = param
        self.dot = QFrame()
        self.dot.setObjectName("changedDot")
        self.dot.setFixedSize(_DOT_SIZE, _DOT_SIZE)
        self.dot.setToolTip("Changed from Standard")
        self.control = self._control()
        self.reset_button = QPushButton()
        self.reset_button.setObjectName("resetButton")
        self.reset_button.setIcon(theme.icon("arrow-counter-clockwise", theme.MUTED))
        self.reset_button.setFixedSize(_RESET_SIZE, _RESET_SIZE)
        self.reset_button.setToolTip("Reset to Standard")
        self.reset_button.setAccessibleName("Reset to Standard")
        self.reset_button.clicked.connect(self.reset)
        policy = self.reset_button.sizePolicy()
        policy.setRetainSizeWhenHidden(True)  # the controls stay lined up
        self.reset_button.setSizePolicy(policy)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 10, 0, 10)
        layout.setSpacing(14)
        layout.addWidget(self._description(), 1)
        layout.addWidget(self.control, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.reset_button, 0, Qt.AlignmentFlag.AlignTop)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt override)
        """A wheel over a control without focus scrolls the rows, never steps the value."""
        if event.type() == QEvent.Type.Wheel and not self.control.hasFocus():
            event.ignore()

            return True

        return super().eventFilter(watched, event)

    def show_value(self, value: Value) -> None:
        """Show ``value`` without emitting ``changed``, and whether it differs from Standard."""
        control = self.control
        control.blockSignals(True)

        if isinstance(control, QComboBox):
            index = [option.value for option in self.param.options].index(value)
            control.setCurrentIndex(index)
        elif isinstance(control, ToggleSwitch):
            control.setChecked(bool(value))
        elif isinstance(control, QSpinBox):
            control.setValue(int(value))
        else:
            control.setValue(float(value))

        control.blockSignals(False)
        is_changed = value != self.param.default
        self.dot.setVisible(is_changed)
        self.reset_button.setVisible(is_changed)

    def _description(self) -> QWidget:
        param = self.param
        # An extension's texts are never markup.
        title = row(plain_label(param.label), kind_tag(param.kind), self.dot, QWidget(), spacing=8)
        lines: list[QWidget] = [title, plain_label(param.code, "code", wrap=True)]

        if param.note:
            lines.append(plain_label(param.note, "muted", wrap=True))

        if param.known:
            known = plain_label(param.known, "warning", wrap=True)
            icon = icon_label("chart-line-up", theme.ACCENT_300, 14)
            lines.append(row(icon, known, spacing=6))

        description = QWidget()
        layout = QVBoxLayout(description)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        for line in lines:
            layout.addWidget(line)

        description.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        return description

    def _control(self) -> Control:
        param = self.param
        control: Control

        if param.type is ParamType.ENUM:
            control = self._combo()
        elif param.type is ParamType.BOOL:
            control = ToggleSwitch()
            control.toggled.connect(lambda is_on: self.changed.emit(is_on))
        else:
            control = self._spin_box()

        control.setFixedWidth(_CONTROL_WIDTH)
        control.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # the wheel does not take focus
        control.installEventFilter(self)

        return control

    def _combo(self) -> QComboBox:
        combo = MarqueeComboBox()

        for option in self.param.options:
            suffix = EXPERIMENTAL if option.is_experimental else ""
            combo.addItem(f"{option.label}{suffix}")

        options = self.param.options
        combo.activated.connect(lambda index: self.changed.emit(options[index].value))

        return combo

    def _spin_box(self) -> QSpinBox | QDoubleSpinBox:
        param = self.param
        spin: QSpinBox | QDoubleSpinBox

        if param.type is ParamType.INT:
            spin = QSpinBox()
            spin.setRange(int(param.minimum or 0), int(param.maximum or 0))
            spin.setSingleStep(int(param.step or 1))
        else:
            spin = QDoubleSpinBox()
            step = param.step or 0.1
            spin.setDecimals(values.decimals(step))
            spin.setRange(param.minimum or 0.0, param.maximum or 0.0)
            spin.setSingleStep(step)

        spin.lineEdit().setTextMargins(_TEXT_MARGIN, 0, _TEXT_MARGIN, 0)
        spin.setKeyboardTracking(False)  # typing 1000 must not try 1, 10 and 100 first
        spin.setSuffix(f" {param.unit}" if param.unit else "")
        spin.valueChanged.connect(lambda value: self.changed.emit(value))

        return spin
