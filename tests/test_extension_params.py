"""Extension parameters (docs/extensions.md#parameters): the registry adds an extension's
groups for its own methods, settings and profiles take their keys, and an opener reads them
through the tuning. The fixtures are in tests/extension_fixtures/tuned/."""

import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest

from cipherstitch import extension_loader, extension_testing, methods, service
from cipherstitch.catalog import CarrierKind, CipherId, MethodId
from cipherstitch.errors import InvalidParameterError, NoHiddenDataError
from cipherstitch.extensions import Extension, Group, Scope
from cipherstitch.methods import Tuning, Value
from cipherstitch.params import engine, profile, registry
from cipherstitch.params.model import Kind
from cipherstitch.params.profile import Profile
from cipherstitch.params.settings import Settings
from cipherstitch.payload import HiddenFile
from cipherstitch.service import HideOptions, ResearchReveal
from tests.extension_fixtures.parts import GROUP, METHOD
from tests.extension_fixtures.tuned import bit_plane

from .conftest import use_extensions

STRONG = "quiet harbour 47 lanterns"
FILE = HiddenFile("note.txt", b"meet at noon")
PLANE = {"plane.bit": 3, "plane.fill": True}  # Settings need the extension loaded


@pytest.fixture
def tuned(monkeypatch: pytest.MonkeyPatch) -> extension_loader.Loaded:
    return use_extensions(monkeypatch, "tuned")


def _ids(method: str, *, is_encrypted: bool) -> dict[str, list[str]]:
    shown = registry.groups_for(method, is_encrypted=is_encrypted)

    return {group.id: [param.id for param in group.params] for group in shown}


def _cover(tmp_path: Path, suffix: str) -> Path:
    cover = tmp_path / f"cover{suffix}"
    cover.write_bytes(os.urandom(8192))

    return cover


def _hide(cover: Path, method: str, settings: Settings, cipher: str = CipherId.NONE) -> Path:
    output = cover.with_name(f"embedded{cover.suffix}")
    password = None if cipher == CipherId.NONE else STRONG
    options = HideOptions(
        CarrierKind.DOCUMENT, method, cipher, password, is_research=True, settings=settings
    )
    service.hide(cover, FILE, output, options)

    return output


def _reveal(path: Path, method: str, settings: Settings, cipher: str = CipherId.NONE) -> bytes:
    password = None if cipher == CipherId.NONE else STRONG
    research = ResearchReveal(method, cipher, settings)
    out = Path(tempfile.mkdtemp(dir=path.parent))

    return service.reveal(path, out, password, research=research).path.read_bytes()


@pytest.mark.usefixtures("tuned")
def test_an_extensions_group_shows_for_its_method_only() -> None:
    assert _ids("bit-plane", is_encrypted=False) == {"frame": ["mask"], "plane": ["bit", "fill"]}
    assert "plane" not in _ids("trellis-bytes", is_encrypted=False)
    assert "plane" not in _ids(MethodId.ZERO_WIDTH, is_encrypted=False)
    assert [group.id for group in registry.groups()][-1] == "plane"


@pytest.mark.usefixtures("tuned")
def test_a_method_that_codes_with_the_trellis_reads_the_stc_group() -> None:
    lsb = _ids(MethodId.LSB, is_encrypted=True)
    trellis = _ids("trellis-bytes", is_encrypted=True)

    assert trellis == {group: lsb[group] for group in ("crypto", "frame", "stc")}
    assert "stc" not in _ids("bit-plane", is_encrypted=True)
    # A keyed method follows frame.order, as the built-in ones do. Rounds are the trellis's.
    assert _ids("bit-plane", is_encrypted=True)["frame"] == ["order"]


@pytest.mark.usefixtures("tuned")
def test_an_unknown_method_gets_the_groups_every_method_reads() -> None:
    assert _ids("no-such-method", is_encrypted=False) == {"frame": ["mask"]}
    assert registry.extension_keys("no-such-method") == ()


def test_the_index_follows_the_loaded_extensions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test resets the loader, so one test's extensions never reach another's keys."""
    use_extensions(monkeypatch, "tuned")

    assert registry.knows("plane.bit")
    assert registry.lookup("plane.bit")[1].kind is Kind.FORMAT

    use_extensions(monkeypatch, "empty")

    assert not registry.knows("plane.bit")
    assert registry.knows("stc.h")

    with pytest.raises(InvalidParameterError, match=r"^Unknown parameter plane\.bit$"):
        Settings.of({"plane.bit": 3})


@pytest.mark.usefixtures("tuned")
def test_settings_take_extension_keys_like_built_in_ones() -> None:
    settings = Settings.of({"plane.fill": True, "plane.bit": 3, "stc.h": 5})

    assert settings.changes == (("plane.bit", 3), ("plane.fill", True), ("stc.h", 5))
    assert settings.format_changes() == ("plane.bit", "stc.h")
    assert settings.replaced("plane.bit", 0) == Settings.of({"plane.fill": True, "stc.h": 5})
    assert settings.reset(["plane.fill", "stc.h"]) == Settings.of({"plane.bit": 3})
    assert Settings()["plane.bit"] == 0


@pytest.mark.usefixtures("tuned")
@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("plane.bit", 8, "Bit (plane.bit) must be at most 7"),
        ("plane.bit", 2.0, "Bit (plane.bit) must be a whole number"),
        ("plane.fill", 1, "Fill the rest (plane.fill) must be on or off"),
        ("plane.nope", 1, "Unknown parameter plane.nope"),
    ],
)
def test_settings_refuse_invalid_extension_values(name: str, value: object, message: str) -> None:
    with pytest.raises(InvalidParameterError, match=f"^{re.escape(message)}$"):
        Settings.of({name: value})


@pytest.mark.usefixtures("tuned")
def test_the_tuning_carries_the_methods_extension_parameters() -> None:
    settings = Settings.of(PLANE)
    tuning = engine.tuning(settings, "bit-plane")

    assert tuning.extension_values == (("plane.bit", 3), ("plane.fill", True))
    assert tuning.extension_value("plane.bit") == 3
    assert engine.tuning(Settings(), "bit-plane").extension_values == (
        ("plane.bit", 0),
        ("plane.fill", False),
    )
    # Neither a built-in method nor another extension's reads them.
    assert engine.tuning(settings, MethodId.LSB) == engine.tuning(settings) == methods.STANDARD
    assert engine.tuning(settings, "trellis-bytes").extension_values == ()

    message = "No extension parameter plane.size here. The method's extension has plane.bit"

    with pytest.raises(LookupError, match=f"^{re.escape(message)}, plane.fill$"):
        tuning.extension_value("plane.size")

    with pytest.raises(LookupError, match=r"The method's extension has none$"):
        Tuning().extension_value("plane.bit")


@pytest.mark.usefixtures("tuned")
@pytest.mark.parametrize("cipher", [CipherId.NONE, CipherId.AES_GCM])
def test_a_format_parameter_must_match_to_reveal(tmp_path: Path, cipher: str) -> None:
    cover = _cover(tmp_path, ".fxb")
    embedded = _hide(cover, "bit-plane", Settings.of(PLANE), cipher)
    changed = [a ^ b for a, b in zip(cover.read_bytes(), embedded.read_bytes(), strict=True)]

    assert any(changed)
    assert all(change in (0, 1 << 3) for change in changed)  # plane.bit reached the opener
    assert _reveal(embedded, "bit-plane", Settings.of(PLANE), cipher) == FILE.data
    # plane.fill only changes how the payload is hidden.
    assert _reveal(embedded, "bit-plane", Settings.of({"plane.bit": 3}), cipher) == FILE.data

    with pytest.raises(NoHiddenDataError):
        _reveal(embedded, "bit-plane", Settings(), cipher)


@pytest.mark.usefixtures("tuned")
def test_a_trellis_extension_channel_receives_the_coding(tmp_path: Path) -> None:
    settings = Settings.of({"stc.h": 5})
    embedded = _hide(_cover(tmp_path, ".fxt"), "trellis-bytes", settings, CipherId.AES_GCM)

    assert _reveal(embedded, "trellis-bytes", settings, CipherId.AES_GCM) == FILE.data

    with pytest.raises(NoHiddenDataError):
        _reveal(embedded, "trellis-bytes", Settings(), CipherId.AES_GCM)


@pytest.mark.usefixtures("tuned")
def test_estimates_open_the_channel_with_the_extensions_parameters(tmp_path: Path) -> None:
    """The opener reads them, so it would fail without them."""
    cover = _cover(tmp_path, ".fxb")
    settings = Settings.of(PLANE)
    options = HideOptions(CarrierKind.DOCUMENT, "bit-plane", is_research=True, settings=settings)
    estimate = service.estimate(cover, FILE.name, len(FILE.data), options)
    details = service.describe_carrier(
        cover, CarrierKind.DOCUMENT, "bit-plane", settings, is_research=True
    )

    assert estimate.available == details.capacity == 8192 // 8


@pytest.mark.usefixtures("tuned")
def test_a_profile_keeps_extension_parameters() -> None:
    saved = Profile(Settings.of(PLANE), "plane", method="bit-plane", cipher=CipherId.NONE)
    text = profile.dumps(saved, app="CipherStitch")

    assert '"plane.bit": 3' in text
    assert profile.parse(text.encode(), "p") == saved


def test_a_profile_from_an_extension_that_isnt_loaded_names_its_keys() -> None:
    data = b'{"format": "cipherstitch-profile", "version": 1, "defaults": 1, '
    read = profile.parse(data + b'"params": {"plane.bit": 3, "stc.h": 5}}', "p")

    assert (read.settings, read.unknown) == (Settings.of({"stc.h": 5}), ("plane.bit",))
    assert profile.unknown_warning(read) == (
        "Left out the parameters this version of CipherStitch doesn't know (plane.bit). "
        "An extension that isn't installed may add them"
    )


def _refuses_odd(values: Mapping[str, Value]) -> str | None:
    if values["level"] == 1:
        raise MemoryError

    if values["level"] == 3:
        raise ValueError("three")

    if values["level"] == 5:
        return 5  # type: ignore[return-value]

    return "odd levels never work" if int(values["level"]) % 2 else None


@pytest.mark.parametrize(
    ("level", "problem"),
    [
        (7, "odd levels never work"),
        (3, "the extension checked failed: ValueError: three"),
        (5, "the extension checked failed: its check returned a int"),
    ],
)
def test_a_groups_check_is_guarded(level: int, problem: str) -> None:
    group = replace(GROUP, params=(replace(GROUP.params[0], default=2),), check=_refuses_odd)

    with extension_loader.alone("checked", Extension(methods=(METHOD,), params=(group,))):
        assert Settings.of({"rule.level": 4})["rule.level"] == 4

        with pytest.raises(InvalidParameterError, match=f"^Rule group: {re.escape(problem)}$"):
            Settings.of({"rule.level": level})

        with pytest.raises(MemoryError):
            Settings.of({"rule.level": 1})


def test_check_method_takes_the_extension_that_declares_the_method(tmp_path: Path) -> None:
    spec = bit_plane.EXTENSION.methods[0]

    extension_testing.check_method(spec, _cover(tmp_path, ".fxb"), bit_plane.EXTENSION)

    with pytest.raises(AssertionError, match="The extension bit_plane failed: LookupError"):
        extension_testing.check_method(spec, _cover(tmp_path, ".fxb"))

    with pytest.raises(AssertionError, match=r"^The extension doesn't declare the method bit-"):
        extension_testing.check_method(spec, _cover(tmp_path, ".fxb"), Extension())


def test_check_method_takes_a_group_two_methods_share(tmp_path: Path) -> None:
    """The rules see the whole extension, so the group may name the other method too."""
    first = bit_plane.EXTENSION.methods[0]
    second = replace(first, id="bit-plane-two", wire_id=212, name="Bit plane two")
    shared: Group = replace(bit_plane.PLANE, scope=Scope(frozenset({first.id, second.id})))
    extension = Extension(methods=(first, second), params=(shared,))

    for spec in (first, second):
        extension_testing.check_method(spec, _cover(tmp_path, ".fxb"), extension)


def test_the_tuned_scenario_loads(tuned: extension_loader.Loaded) -> None:
    assert tuned.failures == ()
    assert [method.codes_with_trellis for method in tuned.methods] == [False, True]
    assert [group.id for extension in tuned.extensions for group in extension.params] == ["plane"]
