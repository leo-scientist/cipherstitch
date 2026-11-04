"""Built-in Research-mode settings the editor offers by name."""

from dataclasses import dataclass

from cipherstitch.params.settings import Settings


@dataclass(frozen=True, slots=True)
class Preset:
    id: str
    label: str
    settings: Settings


PRESETS: tuple[Preset, ...] = (
    Preset("standard", "Standard values", Settings()),
    Preset("fast-stc", "Fast STC (h = 4)", Settings.of({"stc.h": 4, "stc.kept": 6})),
    Preset("no-side-information", "P-0 · no side information", Settings.of({"pre.si": False})),
)
