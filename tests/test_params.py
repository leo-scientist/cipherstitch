import re
from decimal import Decimal

import pytest

from cipherstitch.catalog import MethodId
from cipherstitch.errors import InvalidParameterError
from cipherstitch.methods import echo, lsb, object_stream
from cipherstitch.params import engine, groups, registry, values
from cipherstitch.params.groups import coding
from cipherstitch.params.model import Cover, Group, Kind, Option, Param, ParamType, Scope, key
from cipherstitch.params.presets import PRESETS
from cipherstitch.params.settings import Settings, checked

ALL_PARAMS = [param for group in groups.GROUPS for param in group.params]


def _ids(shown: tuple[Group, ...]) -> dict[str, list[str]]:
    return {group.id: [param.id for param in group.params] for group in shown}


def test_keys_are_unique_and_dotless() -> None:
    names = [key(group, param) for group in groups.GROUPS for param in group.params]

    assert len(names) == len(set(names)) == len(registry.BUILTIN_PARAMS)
    assert all(name.count(".") == 1 for name in names)


@pytest.mark.parametrize("param", ALL_PARAMS, ids=lambda param: param.id)
def test_every_declaration_is_complete(param: Param) -> None:
    if param.type is ParamType.ENUM:
        values = [option.value for option in param.options]
        default = next(option for option in param.options if option.value == param.default)

        assert len(values) == len(set(values))
        assert not default.is_experimental
    else:
        assert not param.options

    if param.type in (ParamType.INT, ParamType.FLOAT):
        assert param.minimum is not None
        assert param.maximum is not None
        assert param.step is not None
        assert param.minimum <= float(param.default) <= param.maximum

    if param.type is ParamType.BOOL:
        assert isinstance(param.default, bool)


def test_the_defaults_are_standard_and_pass_every_check() -> None:
    standard = Settings()

    for name, (_group, param) in registry.BUILTIN_PARAMS.items():
        assert standard[name] == param.default
        assert checked(name, param.default) == param.default

    for group in groups.GROUPS:
        if group.check is not None:
            assert group.check({param.id: param.default for param in group.params}) is None


def test_literal_defaults_are_the_engines_values() -> None:
    """The registry names these values itself, or in other units than the engine."""
    assert registry.lookup("obj.limit")[1].default * 1024 == object_stream.SOFT_LIMIT
    assert registry.lookup("echo.d0")[1].default * echo.DELAY_SCALE == echo.ZERO_DELAY
    assert registry.lookup("echo.d1")[1].default * echo.DELAY_SCALE == echo.ONE_DELAY


def test_settings_keep_only_changes_sorted() -> None:
    settings = Settings.of({"stc.kept": 6, "stc.h": 4, "stc.fill": 80})

    assert settings.changes == (("stc.h", 4), ("stc.kept", 6))
    assert settings["stc.fill"] == 80
    assert settings == Settings.of({"stc.h": 4, "stc.kept": 6})
    assert hash(settings) == hash(Settings.of({"stc.kept": 6, "stc.h": 4}))
    assert Settings.of({"stc.h": 6}) == Settings()


def test_settings_change_and_reset_without_mutating() -> None:
    settings = Settings()
    changed = settings.replaced("dct.pol", 1)

    assert settings == Settings()
    assert changed["dct.pol"] == 1.0
    assert isinstance(changed["dct.pol"], float)
    assert changed.replaced("dct.pol", 0.65) == Settings()
    assert changed.reset(["dct.pol"]) == Settings()


def test_the_zero_width_options_are_tagged_and_change_the_format() -> None:
    sets, rules = registry.lookup("zw.sym")[1], registry.lookup("zw.rules")[1]
    tags = {option.value: option.is_experimental for option in (*sets.options, *rules.options)}

    assert tags == {4: False, 2: False, 8: True, "strict": False, "relaxed": True}
    assert Settings.of({"zw.sym": 8, "zw.rules": "relaxed"}).format_changes() == (
        "zw.rules",
        "zw.sym",
    )


def test_the_whitespace_symbols_are_tagged_and_change_the_format() -> None:
    symbols = registry.lookup("ws.sym")[1]
    tags = {option.value: option.is_experimental for option in symbols.options}

    assert tags == {"space-nbsp": False, "space-tab": True}
    assert Settings.of({"ws.sym": "space-tab"}).format_changes() == ("ws.sym",)


def test_the_metadata_stream_is_tagged_and_changes_the_format() -> None:
    locations = registry.lookup("obj.loc")[1]
    tags = {option.value: option.is_experimental for option in locations.options}

    assert tags == {"standard": False, "metadata": True}
    assert Settings.of({"obj.loc": "metadata"}).format_changes() == ("obj.loc",)


def test_the_mipod_cost_is_tagged_and_reaches_the_engine() -> None:
    costs = registry.lookup("limg.cost")[1]
    tags = {option.value: option.is_experimental for option in costs.options}
    settings = Settings.of({"limg.cost": "mipod"})

    assert tags == {"hill": False, "uniform": True, "wow": True, "s-uniward": True, "mipod": True}
    assert settings.format_changes() == ()
    assert engine.tuning(settings).image.costs is lsb.CostFunction.MIPOD


def test_format_changes_name_what_reveal_needs() -> None:
    settings = Settings.of({"stc.h": 5, "stc.kept": 4, "crypto.minpw": 4, "crypto.nfc": True})

    assert settings.format_changes() == ("stc.h",)
    assert registry.lookup("crypto.nfc")[1].kind is Kind.FORMAT


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("stc.nope", 1, "Unknown parameter stc.nope"),
        ("stc.h", 9, "must be at most 8"),
        ("stc.h", 3, "must be at least 4"),
        ("stc.h", 5.0, "must be a whole number"),
        ("stc.h", True, "can't be on or off"),
        ("stc.retry", 1, "must be on or off"),
        ("dct.pol", "0.5", "must be a number"),
        ("stc.split", "odd", "must be one of: ideal, even"),
        ("dct.pol", float("nan"), "must be a finite number"),
        ("echo.decay", 10**400, "is far too large"),
        ("stc.h", 10**400, "must be at most 8"),
    ],
)
def test_invalid_values_are_refused(name: str, value: object, message: str) -> None:
    with pytest.raises(InvalidParameterError, match=re.escape(message)):
        Settings.of({name: value})


def test_numbers_move_to_the_nearest_step_from_the_minimum() -> None:
    """A profile's values off the steps: the editor, the Parameters row and the engine
    otherwise each rounded them their own way (2.3, 2.25 and 2.2 ms)."""
    settings = Settings.of({"echo.d1": 2.25, "dct.pol": 0.333, "stc.fill": 83, "echo.rs": 33})

    assert settings.as_dict() == {"dct.pol": 0.35, "echo.d1": 2.3, "echo.rs": 34, "stc.fill": 85}
    assert type(settings["stc.fill"]) is int
    assert engine.tuning(settings).echo.one_delay == 23
    assert Settings.of({"echo.d1": 2.3})["echo.d1"] == 2.3  # no float noise: 0.5 + 18 × 0.1


def test_a_maximum_off_the_steps_takes_the_last_step_below_it() -> None:
    param = Param("x", "X", "X", ParamType.FLOAT, 0.0, Kind.EMBEDDER, (), 0, 1, 0.4)

    assert values.checked(param, "g.x", 1.0) == 0.8
    assert values.checked(param, "g.x", 0.5) == 0.4


def test_values_that_cannot_work_together_are_refused() -> None:
    with pytest.raises(InvalidParameterError, match="Phase coding: The coded bins"):
        Settings.of({"phase.seg": 1024, "phase.first": 480})

    with pytest.raises(InvalidParameterError, match="Echo hiding: The two delays"):
        Settings.of({"echo.d1": 1.2})

    assert Settings.of({"phase.seg": 1024, "phase.first": 448})["phase.bins"] == 64
    assert Settings.of({"echo.d0": 0.9, "echo.d1": 1.4})["echo.d1"] == 1.4


@pytest.mark.parametrize(
    "changes",
    [
        (("stc.h", 6),),  # a default
        (("stc.kept", 4), ("stc.h", 5)),  # unsorted
        (("dct.pol", 1),),  # not canonical: a float parameter
    ],
)
def test_settings_are_always_canonical(changes: tuple[tuple[str, object], ...]) -> None:
    with pytest.raises(InvalidParameterError):
        Settings(changes)  # type: ignore[arg-type]


def test_groups_follow_the_method_and_the_encryption() -> None:
    lsb_encrypted = registry.groups_for(MethodId.LSB, is_encrypted=True)
    lsb_plain = registry.groups_for(MethodId.LSB, is_encrypted=False)
    phase_encrypted = registry.groups_for(MethodId.PHASE_CODING, is_encrypted=True)

    assert list(_ids(lsb_encrypted)) == ["crypto", "frame", "stc", "limg"]
    assert _ids(lsb_encrypted)["frame"] == ["order", "rounds"]
    assert list(_ids(lsb_plain)) == ["frame", "stc", "limg"]
    assert _ids(lsb_plain)["frame"] == ["mask", "rounds"]
    assert list(_ids(phase_encrypted)) == ["crypto", "phase"]
    # Without a password zero-width keeps its units in sequence, so no Feistel rounds.
    assert _ids(registry.groups_for(MethodId.ZERO_WIDTH, is_encrypted=False))["frame"] == ["mask"]
    assert "rounds" in _ids(registry.groups_for(MethodId.ZERO_WIDTH, is_encrypted=True))["frame"]


@pytest.mark.parametrize(
    ("cover", "shown"),
    [
        (None, {"dct": ["cost", "sigma", "pol", "mag", "huff", "app"], "pre": 4}),
        (Cover.JPEG, {"dct": ["cost", "sigma", "pol", "mag", "huff", "app"]}),
        (Cover.PRECOVER, {"dct": ["cost", "sigma", "mag"], "pre": 4}),
    ],
)
def test_dct_groups_follow_the_cover(cover: Cover | None, shown: dict[str, object]) -> None:
    """A precover is costed like a JPEG, but compressed with its own settings."""
    found = _ids(registry.groups_for(MethodId.DCT, is_encrypted=True, cover=cover))
    dct_groups = {
        group: params if group == "dct" else len(params)
        for group, params in found.items()
        if group in ("dct", "pre")
    }

    assert dct_groups == shown


def test_a_new_group_appears_for_its_method(monkeypatch: pytest.MonkeyPatch) -> None:
    extra = Group(
        "extra",
        "Extra",
        "flask",
        "A group a new method would add.",
        (Param("x", "X", "X", ParamType.ENUM, "a", Kind.EMBEDDER, (Option("a", "A"),)),),
        scope=Scope(frozenset({MethodId.ZERO_WIDTH})),
    )
    monkeypatch.setattr(registry, "BUILTIN_GROUPS", (*groups.GROUPS, extra))

    assert "extra" in _ids(registry.groups_for(MethodId.ZERO_WIDTH, is_encrypted=False))
    assert "extra" not in _ids(registry.groups_for(MethodId.LSB, is_encrypted=False))


def test_presets_are_valid_and_distinct() -> None:
    assert PRESETS[0].settings == Settings()
    assert len({preset.settings for preset in PRESETS}) == len(PRESETS)
    assert len({preset.id for preset in PRESETS}) == len(PRESETS)


def test_a_key_declared_twice_is_refused() -> None:
    with pytest.raises(ValueError, match=r"stc\.h is declared twice"):
        registry._index((coding.STC, coding.STC))


@pytest.mark.parametrize(
    ("step", "decimals"),
    [
        (1, 0),
        (2.0, 0),
        (1e6, 0),
        (0.1, 1),
        (0.05, 2),
        (1e-05, 5),
        (2.5e-05, 6),
        (0.1234567, 7),
        (1e-15, 15),
        (0.12345678901234567890, 17),
    ],
)
def test_the_editor_shows_as_many_decimals_as_a_step_needs(step: float, decimals: int) -> None:
    """As many as snapping to the step produces. ``f"{step:g}"`` kept 6 significant digits, so
    a 0.1234567 step got a 6-decimal spin box, and a 20-digit step passed the 15-decimal rule."""
    assert values.decimals(step) == decimals


FLOAT_PARAMS = [param for param in ALL_PARAMS if param.type is ParamType.FLOAT]


@pytest.mark.parametrize("param", FLOAT_PARAMS, ids=lambda param: param.id)
def test_every_built_in_step_keeps_its_decimals(param: Param) -> None:
    """The spin boxes were built from ``f"{step:g}"``: the same count for every built-in step."""
    assert param.step is not None
    exponent = Decimal(f"{param.step:g}").as_tuple().exponent

    assert isinstance(exponent, int)
    assert values.decimals(param.step) == max(0, -exponent) <= values.MAX_DECIMALS
