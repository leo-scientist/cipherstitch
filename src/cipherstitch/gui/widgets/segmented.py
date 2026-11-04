"""Segmented control: an exclusive group of checkable buttons in a ``QFrame#seg``."""

from collections.abc import Iterable, Sequence

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QPushButton, QSizePolicy, QWidget

from cipherstitch.gui import theme


class SegmentedControl(QFrame):
    """``options`` are ``(value, label, phosphor icon name)``; emits ``changed(value)``."""

    changed = Signal(str)

    def __init__(
        self, options: Sequence[tuple[str, str, str]], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("seg")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._group = QButtonGroup(self, exclusive=True)
        self._buttons: dict[str, QPushButton] = {}

        for value, text, icon_name in options:
            button = QPushButton(text)
            button.setCheckable(True)
            button.setIcon(theme.icon(icon_name, theme.TEXT, theme.ACCENT))
            button.toggled.connect(lambda checked, v=value: self._on_toggled(v, checked))
            self._group.addButton(button)
            self._buttons[value] = button
            layout.addWidget(button)

        self._buttons[options[0][0]].setChecked(True)
        self.set_shown(self._buttons)

    def _on_toggled(self, value: str, checked: bool) -> None:
        if checked:
            self.changed.emit(value)

    def value(self) -> str:
        return next(value for value, button in self._buttons.items() if button.isChecked())

    def set_value(self, value: str) -> None:
        self._buttons[value].setChecked(True)

    def button(self, value: str) -> QPushButton:
        return self._buttons[value]

    def set_shown(self, values: Iterable[str]) -> None:
        """Show only the options in ``values``, which must include the checked one."""
        wanted = set(values)
        shown = [value for value in self._buttons if value in wanted]
        last = len(shown) - 1

        for value, button in self._buttons.items():
            button.setVisible(value in shown)

        for index, value in enumerate(shown):
            position = "first" if index == 0 else "last" if index == last else "mid"
            theme.set_property(self._buttons[value], "segPos", position)
