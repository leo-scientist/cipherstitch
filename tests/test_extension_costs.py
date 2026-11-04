"""Extension cost functions (docs/extensions.md#cost-functions): Research's options, the guard
around their results, the channels that use them, and payloads that reveal without them."""

import json
import re
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest
from PIL import Image

from cipherstitch import extension_checks, extension_loader, risks, service
from cipherstitch.catalog import CarrierKind, CipherId, CipherKey, MethodId, MethodKey
from cipherstitch.errors import (
    ExtensionError,
    InvalidParameterError,
    InvalidProfileError,
    NotAvailableError,
    UnsupportedFormatError,
)
from cipherstitch.extension_guards import GuardedCosts
from cipherstitch.extensions import Coefficients, Component, CostSpec, Domain, Extension
from cipherstitch.media import jpeg
from cipherstitch.methods import Tuning, dct, hill, lsb, uerd
from cipherstitch.params import engine, profile, registry
from cipherstitch.params.model import key
from cipherstitch.params.settings import Settings
from cipherstitch.payload import HiddenFile
from cipherstitch.service import HideOptions, ResearchReveal
from tests.extension_fixtures.parts import COST

from .conftest import use_extensions
from .test_golden import fixed_randomness

PASSWORD = "quiet harbour 47 lanterns"  # noqa: S105
FILE = HiddenFile("note.txt", bytes(range(256)) + b"traces" * 20)
SIDE = 128
SHAPE = (4, 5, 3)
UNKNOWN = "Cost function (limg.cost) must be one of: hill, uniform, wow, s-uniward, mipod"


@pytest.fixture
def png(tmp_path: Path) -> Path:
    """Noise with a flat band, which wet costs keep changes off."""
    rng = np.random.default_rng(seed=5)
    pixels = rng.integers(0, 256, size=(SIDE, SIDE, 3), dtype=np.uint8)
    pixels[:8] = 200
    path = tmp_path / "cover.png"
    Image.fromarray(pixels).save(path)

    return path


@pytest.fixture
def photo(tmp_path: Path) -> Path:
    """A JPEG with texture and smooth parts, which DCT takes as it is."""
    rng = np.random.default_rng(seed=1)
    gradient = np.linspace(0, 255, 160)[None, :] + np.linspace(0, 255, 120)[:, None]
    pixels = np.clip(gradient[..., None] / 2 + rng.normal(0, 30, (120, 160, 3)), 0, 255)
    path = tmp_path / "photo.jpg"
    Image.fromarray(pixels.astype(np.uint8)).save(path, quality=85)

    return path


def _hidden(
    cover: Path,
    output: Path,
    method: MethodKey,
    cipher: CipherKey,
    settings: Settings,
) -> bytes:
    password = None if cipher == CipherId.NONE else PASSWORD
    options = HideOptions(
        CarrierKind.IMAGE, method, cipher, password, settings=settings, is_research=True
    )
    service.hide(cover, FILE, output, options)

    return output.read_bytes()


def _hidden_alike(
    monkeypatch: pytest.MonkeyPatch,
    cover: Path,
    tmp_path: Path,
    method: MethodKey,
    cipher: CipherKey,
    each: list[Settings],
) -> list[bytes]:
    """The files hidden with each of ``each``, every one from the same random bytes."""
    suffix = ".png" if method == MethodId.LSB else ".jpg"
    outputs = []

    for index, settings in enumerate(each):
        fixed_randomness(monkeypatch)
        outputs.append(_hidden(cover, tmp_path / f"{index}{suffix}", method, cipher, settings))

    return outputs


def _costs_of(name: str, *values: str) -> list[Settings]:
    """The default settings, then ``name`` set to each of ``values``."""
    return [Settings(), *(Settings.of({name: value}) for value in values)]


@pytest.mark.parametrize("cipher", [CipherId.NONE, CipherId.AES_GCM])
def test_costs_of_one_hide_byte_for_byte_as_the_built_in_uniform_ones(
    monkeypatch: pytest.MonkeyPatch, png: Path, tmp_path: Path, cipher: CipherId
) -> None:
    """The fixture's spatial costs come as float64: the cast changes nothing."""
    use_extensions(monkeypatch, "costs")
    each = _costs_of("limg.cost", "uniform", "ones")
    default, uniform, ones = _hidden_alike(monkeypatch, png, tmp_path, MethodId.LSB, cipher, each)

    assert ones == uniform != default  # the costs do choose the changes


@pytest.mark.parametrize("cover", ["photo", "png"])
def test_jpeg_costs_of_one_hide_byte_for_byte_as_the_built_in_uniform_ones(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cover: str, request: pytest.FixtureRequest
) -> None:
    """A JPEG's steps are polarized, a precover's side-informed, whatever the costs."""
    pytest.importorskip("jpeglib")
    use_extensions(monkeypatch, "costs")
    path = request.getfixturevalue(cover)
    each = _costs_of("dct.cost", "uniform", "ones")
    default, uniform, ones = _hidden_alike(
        monkeypatch, path, tmp_path, MethodId.DCT, CipherId.AES_GCM, each
    )

    assert ones == uniform != default


def _hill_copy(colors: npt.NDArray[np.uint8], rate: float) -> npt.NDArray[np.float32]:
    return hill.image_costs(colors, hill.FAR)


def _uerd_copy(cover: Coefficients) -> npt.NDArray[np.float32]:
    return uerd.costs(cover)


COPIES = Extension(
    costs=(
        CostSpec("hill-copy", "HILL copy", Domain.SPATIAL, _hill_copy),
        CostSpec("uerd-copy", "UERD copy", Domain.JPEG, _uerd_copy),
    )
)
"""Built-in cost functions as an extension: costs that differ from value to value, so the
same output means the same layout, value for value."""


def test_a_copy_of_hill_hides_byte_for_byte_as_the_built_in(
    monkeypatch: pytest.MonkeyPatch, png: Path, tmp_path: Path
) -> None:
    with extension_loader.alone("copies", COPIES):
        each = _costs_of("limg.cost", "hill-copy", "uniform")
        hill_costs, copy, uniform = _hidden_alike(
            monkeypatch, png, tmp_path, MethodId.LSB, CipherId.AES_GCM, each
        )

    assert copy == hill_costs != uniform


@pytest.mark.parametrize("cover", ["photo", "png"])
def test_a_copy_of_uerd_hides_byte_for_byte_as_the_built_in(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cover: str, request: pytest.FixtureRequest
) -> None:
    pytest.importorskip("jpeglib")
    path = request.getfixturevalue(cover)

    with extension_loader.alone("copies", COPIES):
        each = _costs_of("dct.cost", "uerd", "uerd-copy")
        default, built_in, copy = _hidden_alike(
            monkeypatch, path, tmp_path, MethodId.DCT, CipherId.AES_GCM, each
        )

    assert copy == built_in != default


def _refuse_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    def load() -> extension_loader.Loaded:
        raise AssertionError("The extensions were loaded")

    monkeypatch.setattr(extension_loader, "load", load)


@pytest.fixture
def refuse_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    _refuse_loading(monkeypatch)


def test_a_payload_hidden_with_an_extension_cost_reveals_in_standard_mode(
    monkeypatch: pytest.MonkeyPatch, png: Path, tmp_path: Path
) -> None:
    """Cost functions are Embedder settings: neither Standard mode nor Research mode at its
    defaults needs the extension to reveal."""
    use_extensions(monkeypatch, "costs")
    output = tmp_path / "embedded.png"
    _hidden(png, output, MethodId.LSB, CipherId.AES_GCM, Settings.of({"limg.cost": "ones"}))
    _refuse_loading(monkeypatch)
    standard = service.reveal(output, tmp_path / "standard", PASSWORD)
    research = ResearchReveal(MethodId.LSB, CipherId.AES_GCM)
    defaults = service.reveal(output, tmp_path / "research", PASSWORD, research=research)

    assert standard.path.read_bytes() == defaults.path.read_bytes() == FILE.data


def _guarded(result: object) -> GuardedCosts:
    return GuardedCosts("broken", lambda *_inputs: result)


def _colors() -> npt.NDArray[np.uint8]:
    return np.arange(np.prod(SHAPE), dtype=np.uint8).reshape(SHAPE)


@pytest.mark.parametrize(
    ("result", "problem"),
    [
        (np.full(SHAPE, np.nan), "its costs hold NaN"),
        (np.full(SHAPE, -1.0, dtype=np.float32), "its costs hold a negative value"),
        (np.full(SHAPE, -np.inf), "its costs hold a negative value"),
        (np.ones(60), "its costs must be an array of shape (4, 5, 3), not (60,)"),
        ([1.0] * 60, "its costs must be an array of shape (4, 5, 3), not list"),
        (np.ones(SHAPE, dtype=np.int64), "its costs must be float32 or float64, not int64"),
        (np.ones(SHAPE, dtype=np.float16), "its costs must be float32 or float64, not float16"),
    ],
)
def test_the_guard_refuses_costs_the_channel_cant_use(result: object, problem: str) -> None:
    message = f"^The extension broken failed: {re.escape(problem)}$"

    with pytest.raises(ExtensionError, match=message):
        _guarded(result).spatial(_colors(), 0.1)


def test_costs_above_the_wet_cost_are_clipped_to_it() -> None:
    """Infinity too: the built-in costs never pass the wet cost, and polarization at 0 would
    turn an infinite one into NaN."""
    result = np.ones(SHAPE)
    result[0, 0] = (1e12, np.inf, 2.5)
    costs = _guarded(result).spatial(_colors(), 0.1)

    assert costs.dtype == np.float32
    assert costs.flags.writeable
    assert costs[0, 0].tolist() == [lsb.WET_COST, lsb.WET_COST, 2.5]
    assert np.all(costs[1:] == 1)


def test_the_channel_gets_a_fresh_array() -> None:
    """The channel writes over its costs: the extension's own array stays as it was."""
    result = np.ones(SHAPE, dtype=np.float32)
    costs = _guarded(result).spatial(_colors(), 0.1)
    costs[...] = 0

    assert np.all(result == 1)


def _coefficients() -> Coefficients:
    steps = np.arange(1, 65, dtype=np.uint16)
    blocks = 2

    return Coefficients(
        np.arange(blocks * 63, dtype=np.int16),
        (Component(1, blocks, steps),),
        np.zeros(blocks, dtype=np.int16),
    )


def test_jpeg_costs_are_checked_against_the_ac_coefficients() -> None:
    cover = _coefficients()
    wet = np.full(cover.ac.shape, np.inf, dtype=np.float32)

    assert np.all(_guarded(wet).jpeg(cover) == np.float32(uerd.WET))

    with pytest.raises(ExtensionError, match=r"shape \(126,\), not \(2, 63\)"):
        _guarded(np.ones((2, 63))).jpeg(cover)


def _writes_colors(colors: npt.NDArray[np.uint8], rate: float) -> npt.NDArray[np.float32]:
    colors[0, 0, 0] = 1

    return np.ones(colors.shape, dtype=np.float32)


def _writes_coefficients(cover: Coefficients) -> npt.NDArray[np.float32]:
    cover.components[0].steps[0] = 1

    return np.ones(cover.ac.shape, dtype=np.float32)


def test_cost_functions_get_read_only_values() -> None:
    colors, cover = _colors(), _coefficients()
    read_only = "^The extension broken failed: ValueError: assignment destination is read-only$"

    with pytest.raises(ExtensionError, match=read_only) as caught:
        GuardedCosts("broken", _writes_colors).spatial(colors, 0.1)

    assert isinstance(caught.value.__cause__, ValueError)

    with pytest.raises(ExtensionError, match=read_only):
        GuardedCosts("broken", _writes_coefficients).jpeg(cover)

    assert colors.flags.writeable
    assert cover.components[0].steps[0] == 1


def _refuses(colors: npt.NDArray[np.uint8], rate: float) -> npt.NDArray[np.float32]:
    raise UnsupportedFormatError("not this picture")


def test_cipherstitch_errors_pass_the_guard() -> None:
    with pytest.raises(UnsupportedFormatError, match=r"^not this picture$"):
        GuardedCosts("refusing", _refuses).spatial(_colors(), 0.1)


def _nan_costs(colors: npt.NDArray[np.uint8], rate: float) -> npt.NDArray[np.float32]:
    return np.full(colors.shape, np.nan, dtype=np.float32)


def test_hiding_with_a_broken_cost_function_fails_with_its_name(png: Path, tmp_path: Path) -> None:
    broken = CostSpec("nan", "NaN", Domain.SPATIAL, _nan_costs)

    with extension_loader.alone("nan_costs", Extension(costs=(broken,))):
        settings = Settings.of({"limg.cost": "nan"})

        with pytest.raises(ExtensionError, match=r"^The extension nan_costs failed: its costs"):
            _hidden(png, tmp_path / "out.png", MethodId.LSB, CipherId.NONE, settings)

    assert not (tmp_path / "out.png").exists()


def test_a_channel_refuses_a_cost_function_that_isnt_loaded(png: Path) -> None:
    channel = lsb.ImageChannel(png, rules=lsb.ImageRules(costs="ones"))
    message = "^Unknown cost function ones. No loaded extension adds it$"

    with pytest.raises(NotAvailableError, match=message):
        channel._costs(0.1)


def _options(method: MethodKey, name: str) -> list[tuple[object, str, bool]]:
    groups = registry.groups_for(method, is_encrypted=True)
    param = next(param for group in groups for param in group.params if key(group, param) == name)

    return [(option.value, option.label, option.is_experimental) for option in param.options]


def test_research_offers_the_loaded_cost_functions_after_the_built_in_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_extensions(monkeypatch, "costs")

    assert _options(MethodId.LSB, "limg.cost")[-2:] == [
        ("mipod", "MiPOD", True),
        ("ones", "Ones", True),
    ]
    assert _options(MethodId.DCT, "dct.cost")[-2:] == [
        ("uniform", "Uniform", True),
        ("ones", "Ones", True),
    ]

    settings = Settings.of({"limg.cost": "ones", "dct.cost": "ones"})
    tuning = engine.tuning(settings, MethodId.LSB)

    assert (tuning.image.costs, tuning.dct.costs) == ("ones", "ones")
    assert not isinstance(tuning.image.costs, lsb.CostFunction)
    assert tuning == engine.tuning(settings) and hash(tuning) == hash(engine.tuning(settings))
    assert settings.format_changes() == ()


def test_settings_take_only_loaded_cost_functions(monkeypatch: pytest.MonkeyPatch) -> None:
    """The options are read afresh: once the loader forgets them, they are gone."""
    with pytest.raises(InvalidParameterError, match=f"^{re.escape(UNKNOWN)}$"):
        Settings.of({"limg.cost": "ones"})

    use_extensions(monkeypatch, "costs")

    assert Settings.of({"limg.cost": "ones"})["limg.cost"] == "ones"

    with pytest.raises(InvalidParameterError, match=re.escape(f"{UNKNOWN}, ones")):
        Settings.of({"limg.cost": "twos"})

    use_extensions(monkeypatch, "empty")

    with pytest.raises(InvalidParameterError, match=f"^{re.escape(UNKNOWN)}$"):
        Settings((("limg.cost", "ones"),))


def test_an_extension_cost_without_wet_costs_gets_the_flat_colour_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Nothing says it keeps changes off flat colour, as HILL's own costs do."""
    use_extensions(monkeypatch, "costs")
    rng = np.random.default_rng(seed=12)
    pixels = rng.integers(0, 256, size=(SIDE, SIDE, 3), dtype=np.uint8)
    pixels[40:45] = 90
    cover = tmp_path / "flat.png"
    Image.fromarray(pixels).save(cover)
    wet = engine.tuning(Settings.of({"limg.cost": "ones"}))
    dry = engine.tuning(Settings.of({"limg.cost": "ones", "limg.wet": False}))
    dry_hill = engine.tuning(Settings.of({"limg.wet": False}))

    assert risks.flat_cover(MethodId.LSB, cover, tuning=wet) is None
    assert risks.flat_cover(MethodId.LSB, cover, tuning=dry_hill) is None
    assert risks.flat_cover(MethodId.LSB, cover, tuning=dry) == "4% of the image is flat colour"


def _profile(params: dict[str, object]) -> bytes:
    document = {"format": "cipherstitch-profile", "version": 1, "defaults": 1, "params": params}

    return json.dumps(document).encode()


def test_a_profile_keeps_a_cost_function_nobody_here_has_apart_for_hiding_to_refuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Revealing reads the same values whatever the costs were, so it goes on without it."""
    missing = (
        "p.csprofile sets limg.cost to a cost function this CipherStitch doesn't know, 'ones' "
        "(an extension that isn't installed?)"
    )
    params = {"limg.cost": "ones", "stc.h": 4}
    apart = profile.parse(_profile(params), "p.csprofile")

    assert apart.settings == Settings.of({"stc.h": 4})
    assert apart.missing_costs == (("limg.cost", "ones"),)
    assert apart.unknown == ()

    profile.refuse_unknown(apart, "p.csprofile")

    with pytest.raises(InvalidProfileError, match=f"^{re.escape(missing)}$"):
        profile.refuse_missing_costs(apart, "p.csprofile")

    with pytest.raises(InvalidProfileError, match=f"^{re.escape(UNKNOWN)} in p.csprofile$"):
        profile.parse(_profile({"limg.cost": 3}), "p.csprofile")

    use_extensions(monkeypatch, "costs")
    loaded = profile.parse(_profile({"limg.cost": "ones", "dct.cost": "ones"}), "p.csprofile")

    assert loaded.settings == Settings.of({"dct.cost": "ones", "limg.cost": "ones"})
    assert (loaded.unknown, loaded.missing_costs) == ((), ())

    profile.refuse_missing_costs(loaded, "p.csprofile")


@pytest.mark.usefixtures("refuse_loading")
def test_built_in_cost_functions_never_load_the_extensions() -> None:
    settings = Settings.of({"limg.cost": "wow", "dct.cost": "uerd"})
    tuning = engine.tuning(settings)

    assert tuning.image.costs is lsb.CostFunction.WOW
    assert profile.parse(_profile({"limg.cost": "wow"}), "p").settings["limg.cost"] == "wow"
    assert not registry.is_unknown_cost("limg.cost", "hill")
    assert not registry.is_unknown_cost("stc.h", "hill")


def test_cost_ids_count_within_their_domain() -> None:
    """Only the built-in options of a cost's own parameter are taken, and one id may serve
    both domains (the costs fixture's ones do)."""
    spatial = CostSpec("j-uniward", "Spatial", Domain.SPATIAL, COST.costs)
    jpeg = CostSpec("j-uniward", "JPEG", Domain.JPEG, COST.costs)
    both = (CostSpec("twos", "Twos", domain, COST.costs) for domain in Domain)

    assert extension_checks.problems(Extension(costs=(spatial, *both))) == ()
    assert extension_checks.problems(Extension(costs=(jpeg,))) == (
        "cost function 'j-uniward': the id j-uniward is taken by CipherStitch",
    )


@pytest.mark.usefixtures("refuse_loading")
def test_a_built_in_cost_given_as_text_is_the_built_in(png: Path) -> None:
    """Only a value no built-in has goes to the extensions."""
    as_text = lsb.ImageRules(costs="wow")
    member = lsb.ImageRules(costs=lsb.CostFunction.WOW)
    costs = lsb.ImageChannel(png, rules=as_text)._value_costs(0.1)

    np.testing.assert_array_equal(costs, lsb.ImageChannel(png, rules=member)._value_costs(0.1))
    assert as_text == member and hash(as_text) == hash(member)
    assert risks.flat_cover(MethodId.LSB, png, tuning=Tuning(image=as_text)) is None
    assert risks._avoids_flat(lsb.ImageRules(costs="hill", is_wet=False))


@pytest.mark.usefixtures("refuse_loading")
def test_a_built_in_jpeg_cost_given_as_text_is_the_built_in(photo: Path) -> None:
    pytest.importorskip("jpeglib")
    costs = dct.DctChannel(photo, rules=dct.DctRules(costs="uerd"))._costs(0.1)

    np.testing.assert_array_equal(costs, uerd.costs(jpeg.read_coefficients(photo)))
