"""Method field: the carrier's embedding methods in a :class:`PickerPopover`, with capacities."""

from collections.abc import Sequence

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget

from cipherstitch import catalog
from cipherstitch.catalog import Method, MethodKey
from cipherstitch.gui.widgets.picker import PickerField


def _capacity(method: Method) -> str:
    return f"Capacity {method.capacity}"


class MethodField(PickerField[Method]):
    """Eye · method name · tier tag · caret; emits ``method_changed(method id)``."""

    method_changed = Signal(object)

    def __init__(self, methods: Sequence[Method], parent: QWidget | None = None) -> None:
        super().__init__("eye-slash", _capacity, methods[0], parent)
        self.popover.set_options(methods)
        self.popover.chosen.connect(self.set_method)

    def method(self) -> Method:
        return self.current()

    def set_methods(self, methods: Sequence[Method], shown: Method | None = None) -> None:
        """Offer ``methods`` (one carrier's) and show ``shown`` or the first, without emitting."""
        self.popover.set_options(methods)
        self._show(shown or methods[0])

    def set_method(self, method_id: MethodKey) -> None:
        """Show ``method_id`` and emit ``method_changed``, unless it is already shown."""
        if method_id == self.current().id:
            return

        self._show(catalog.method(method_id))
        self.method_changed.emit(method_id)
