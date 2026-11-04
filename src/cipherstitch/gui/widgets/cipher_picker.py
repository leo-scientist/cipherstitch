"""Encryption field: the mode's ciphers in a :class:`PickerPopover`, with size overheads."""

from collections.abc import Sequence

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget

from cipherstitch import catalog, ciphers
from cipherstitch.catalog import Cipher, CipherKey
from cipherstitch.gui.widgets.picker import PickerField


def _overhead(cipher: Cipher) -> str:
    return f"Size overhead {ciphers.overhead_label(cipher.id)}"


class CipherField(PickerField[Cipher]):
    """Lock · cipher name · tier tag · caret; emits ``cipher_changed(cipher id)``."""

    cipher_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("lock", _overhead, catalog.cipher(catalog.DEFAULT_CIPHER), parent)
        self.popover.set_options(catalog.CIPHERS)
        self.popover.chosen.connect(self.set_cipher)

    def cipher(self) -> Cipher:
        return self.current()

    def set_ciphers(self, offered: Sequence[Cipher]) -> None:
        """Offer only ``offered``, which must include the shown cipher."""
        self.popover.set_options(offered)

    def set_cipher(self, cipher_id: CipherKey) -> None:
        self._show(catalog.cipher(cipher_id))
        self.cipher_changed.emit(cipher_id)
