"""Settings turned into what the engine reads. A value left at its default keeps the engine's
own (``replace`` only overrides what changed), so Standard runs exactly the engine's defaults."""

from collections.abc import Callable, Mapping
from dataclasses import replace
from enum import StrEnum
from typing import Any

from cipherstitch import kdf
from cipherstitch.catalog import MethodKey
from cipherstitch.container import UnitOrder
from cipherstitch.media.jpeg import Subsampling, Tables
from cipherstitch.methods import STANDARD, Tuning, dct, echo, lsb, stc, trellis, zero_width
from cipherstitch.params import registry
from cipherstitch.params.model import Value
from cipherstitch.params.settings import Settings

Field = tuple[str, Callable[[Value], Any]]
"""A config field's name, and how a setting's value becomes its value (each field has its
own type, hence ``Any``)."""


def _same(value: Value) -> Value:
    return value


def _is(expected: str) -> Callable[[Value], bool]:
    return lambda value: value == expected


def _tenths(value: Value) -> int:
    """Milliseconds as the echo's tenths of a millisecond."""
    return round(float(value) * echo.DELAY_SCALE)


def _kib(value: Value) -> int:
    return int(value) * 1024


def _cost_function[F: StrEnum](built_in: type[F]) -> Callable[[Value], F | str]:
    """A built-in cost function, or the id of an extension's as it is
    (docs/extensions.md#cost-functions)."""

    def converted(value: Value) -> F | str:
        name = str(value)

        return built_in(name) if name in built_in else name

    return converted


_KDF: Mapping[str, Field] = {
    "crypto.kdf": ("function", lambda value: kdf.Function(str(value))),
    "crypto.mem": ("memory_kib", _same),
    "crypto.iter": ("iterations", _same),
    "crypto.lanes": ("lanes", _same),
    "crypto.nfc": ("is_normalized", _same),
}
_CODE: Mapping[str, Field] = {
    "stc.h": ("height", _same),
    "stc.cols": ("label_number", _same),
    "stc.seg": ("units_per_segment", _same),
    "stc.maxseg": ("max_segments", _same),
}
_FORMAT: Mapping[str, Field] = {
    "stc.lead": ("lead_units", _same),
    "stc.head": ("head_units", _same),
    "stc.tail": ("tail_units_per_bit", _same),
    "stc.spread": ("is_spread", _is("golden")),
    "stc.code": ("is_ternary", _is("ternary")),
    "frame.rounds": ("rounds", _same),
}
_WRITER: Mapping[str, Field] = {
    "stc.kept": ("kept_per_bit", _same),
    "stc.fill": ("fill_percent", _same),
    "stc.split": ("is_split_ideal", _is("ideal")),
    "stc.retry": ("can_retry", _same),
}
_IMAGE: Mapping[str, Field] = {
    "limg.cost": ("costs", _cost_function(lsb.CostFunction)),
    "limg.far": ("far_window", _same),
    "limg.flat": ("flat_run", _same),
    "limg.wet": ("is_wet", _same),
    "limg.meta": ("keeps_metadata", _same),
}
_AUDIO: Mapping[str, Field] = {
    "laud.row": ("silence_row", _same),
    "laud.flat": ("flat_run", _same),
}
_DCT: Mapping[str, Field] = {
    "dct.cost": ("costs", _cost_function(dct.CostFunction)),
    "dct.sigma": ("sigma", _same),
    "dct.pol": ("polarization", _same),
    "dct.mag": ("max_magnitude", _same),
    "dct.huff": ("tables", lambda value: Tables(str(value))),
    "dct.app": ("keeps_segments", _same),
    "pre.q": ("quality", _same),
    "pre.sub": ("subsampling", lambda value: Subsampling(str(value))),
    "pre.si": ("is_side_informed", _same),
    "pre.icc": ("keeps_icc", _same),
}
_PHASE: Mapping[str, Field] = {
    "phase.seg": ("segment", _same),
    "phase.first": ("first_bin", _same),
    "phase.bins": ("bins", _same),
    "phase.ramp": ("ramp", _same),
    "phase.floor": ("floor", _same),
    "phase.rs": ("parity", _same),
}
_ECHO: Mapping[str, Field] = {
    "echo.seg": ("segment_ms", _same),
    "echo.d0": ("zero_delay", _tenths),
    "echo.d1": ("one_delay", _tenths),
    "echo.decay": ("decay", _same),
    "echo.rs": ("parity", _same),
}
_OBJECT: Mapping[str, Field] = {
    "obj.limit": ("limit", _kib),
    "obj.pad": ("is_padded", _same),
    "obj.loc": ("is_in_metadata", _is("metadata")),
}
_WHITESPACE: Mapping[str, Field] = {
    "ws.bpp": ("bits_per_paragraph", _same),
    "ws.sym": ("has_tabs", _is("space-tab")),
}
_ZERO_WIDTH: Mapping[str, Field] = {
    "zw.sym": ("symbol_set", lambda value: zero_width.SYMBOL_SETS[int(value)]),
    "zw.rules": ("is_relaxed", _is("relaxed")),
}


def kdf_profile(settings: Settings) -> kdf.Profile:
    return replace(kdf.standard_profile(), **_changed(settings, _KDF))


def tuning(settings: Settings, method: MethodKey | None = None) -> Tuning:
    """What the channels read. An extension's ``method`` also gets every parameter of its
    extension (``Tuning.extension_values``), a built-in's none, so Standard mode never loads
    the extensions here."""
    code = replace(stc.STANDARD, **_changed(settings, _CODE))
    format = replace(trellis.STANDARD.format, code=code, **_changed(settings, _FORMAT))
    writer = replace(trellis.STANDARD.writer, **_changed(settings, _WRITER))

    return Tuning(
        coding=trellis.Config(format, writer),
        image=replace(STANDARD.image, **_changed(settings, _IMAGE)),
        audio=replace(STANDARD.audio, **_changed(settings, _AUDIO)),
        rounds=int(settings["frame.rounds"]),
        dct=replace(STANDARD.dct, **_changed(settings, _DCT)),
        phase=replace(STANDARD.phase, **_changed(settings, _PHASE)),
        echo=replace(STANDARD.echo, **_changed(settings, _ECHO)),
        document=replace(STANDARD.document, **_changed(settings, _OBJECT)),
        whitespace=replace(STANDARD.whitespace, **_changed(settings, _WHITESPACE)),
        text=replace(STANDARD.text, **_changed(settings, _ZERO_WIDTH)),
        extension_values=_extension_values(settings, method),
    )


def is_masked(settings: Settings) -> bool:
    """Whether a plain frame is masked (docs/format.md#unencrypted)."""
    return settings["frame.mask"] == "shake256"


def unit_order(settings: Settings) -> UnitOrder:
    """The order of an encrypted payload's units (docs/format.md#keyed-order)."""
    return UnitOrder(str(settings["frame.order"]))


def _extension_values(
    settings: Settings, method: MethodKey | None
) -> tuple[tuple[str, Value], ...]:
    keys = () if method is None else registry.extension_keys(method)

    return tuple((name, settings[name]) for name in keys)


def _changed(settings: Settings, fields: Mapping[str, Field]) -> dict[str, Any]:
    """The config fields of ``fields`` whose settings differ from their defaults."""
    changes = settings.as_dict()

    return {
        name: convert(changes[key]) for key, (name, convert) in fields.items() if key in changes
    }
