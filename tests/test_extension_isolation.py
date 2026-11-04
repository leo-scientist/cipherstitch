"""Standard mode never loads the extensions (docs/architecture.md#extensions), and Research
mode runs a fixture extension's method and cipher through the service."""

import os
from pathlib import Path

import pytest

from cipherstitch import catalog, cli, extension_loader, methods, service, standard
from cipherstitch.catalog import CarrierKind, CipherId, CipherKey, MethodId
from cipherstitch.container import Candidate, UnitOrder
from cipherstitch.errors import InvalidParameterError, NotAvailableError
from cipherstitch.params import engine, profile, registry
from cipherstitch.params.settings import Settings
from cipherstitch.payload import HiddenFile
from cipherstitch.service import HideOptions, ResearchReveal

from .conftest import ImageFactory, use_extensions

STRONG = "Correct-Horse-42!"
FILE = HiddenFile("note.txt", b"meet at noon")


@pytest.fixture
def refuse_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    def load() -> extension_loader.Loaded:
        raise AssertionError("Standard mode loaded the extensions")

    monkeypatch.setattr(extension_loader, "load", load)


@pytest.mark.usefixtures("refuse_loading")
def test_standard_mode_never_loads_the_extensions(make_image: ImageFactory, tmp_path: Path) -> None:
    cover, output = make_image(), tmp_path / "out.png"
    options = service.hide_options(cover, output, CipherId.AES_GCM)
    options = HideOptions(options.carrier, options.method, options.cipher, STRONG)
    estimate = service.estimate(cover, FILE.name, len(FILE.data), options)

    assert service.capacity(cover) > estimate.required
    assert service.describe_carrier(cover, CarrierKind.IMAGE, MethodId.LSB).capacity
    assert service.detection_risks(options, cover, estimate) == ()
    off = service.unavailable_methods(CarrierKind.IMAGE)

    assert off[MethodId.LSB_REPLACEMENT].startswith(standard.OFF)

    service.hide(cover, FILE, output, options)
    revealed = service.reveal(output, tmp_path / "out", STRONG)

    assert revealed.path.read_bytes() == FILE.data
    assert (revealed.method, revealed.cipher) == (MethodId.LSB, CipherId.AES_GCM)


@pytest.mark.usefixtures("refuse_loading")
def test_standard_mode_refuses_extension_ids_unseen(
    make_image: ImageFactory, tmp_path: Path
) -> None:
    cover, output = make_image(), tmp_path / "out.png"
    method = "Unknown method byte-lsb. Extension methods run only in Research mode"
    cipher = "Unknown cipher chacha-copy. Extension ciphers run only in Research mode"

    with pytest.raises(NotAvailableError, match=method):
        service.hide_options(cover, output, CipherId.AES_GCM, method="byte-lsb")

    with pytest.raises(NotAvailableError, match=cipher):
        service.hide(cover, FILE, output, HideOptions(cipher="chacha-copy", password=STRONG))

    for options, reason in (
        (HideOptions(method="byte-lsb"), method),
        (HideOptions(cipher="chacha-copy"), cipher),
    ):
        with pytest.raises(NotAvailableError, match=reason):
            service.estimate(cover, FILE.name, len(FILE.data), options)

        assert service.unavailable_reason(options) == reason

    assert not standard.reveals(Candidate("byte-lsb", UnitOrder.KEYED))


@pytest.mark.usefixtures("refuse_loading")
def test_standard_lists_and_lookups_never_load_the_extensions(make_image: ImageFactory) -> None:
    cover = make_image()

    for kind in CarrierKind:
        assert service.mode_methods(kind) == tuple(
            m.id for m in catalog.carrier(kind).methods if standard.method_reason(m.id) is None
        )
        assert service.carrier_suffixes(kind) == standard.carrier_formats(kind)
        assert service.unavailable_methods(kind, cover).keys() <= {
            m.id for m in catalog.carrier(kind).methods
        }

    assert service.mode_ciphers() == (CipherId.AES_GCM, CipherId.CHACHA20, CipherId.XCHACHA20)
    assert service.unavailable_ciphers().keys() <= set(CipherId)
    assert service.carrier_for_path(Path("a.fxb")) is None
    assert service.reveal_methods(cover) == (MethodId.LSB, MethodId.LSB_REPLACEMENT)
    assert service.reveal_methods() == tuple(m.id for c in catalog.CARRIERS for m in c.methods)
    assert service.reveal_suffixes() == methods.reveal_suffixes()
    assert service.output_suffixes(MethodId.LSB, cover) == (".png", ".bmp", ".tif", ".tiff")

    with pytest.raises(NotAvailableError, match="Extension methods run only in Research mode"):
        service.output_suffixes("byte-lsb", None)

    # An extension's id falls back to the built-in default, as a method that can't read it.
    lsb = service.describe_carrier(cover, CarrierKind.IMAGE, MethodId.LSB)

    assert service.describe_carrier(cover, CarrierKind.IMAGE, "byte-lsb") == lsb
    assert service.detection_risks(HideOptions(method="byte-lsb")) == ()


@pytest.mark.usefixtures("refuse_loading")
def test_built_in_parameters_never_load_the_extensions() -> None:
    """Only a key or a method no built-in has builds the extensions' part of the index, only a
    cost function no built-in has, or the editor's groups of LSB and DCT, which list the
    extensions' cost functions, load them, and Standard mode reads no settings at all."""
    settings = Settings.of({"stc.h": 4, "frame.order": "public", "limg.cost": "wow"})

    assert settings.format_changes() == ("stc.h",)
    assert registry.knows("limg.cost")
    assert engine.tuning(Settings(), MethodId.LSB) == engine.tuning(Settings()) == methods.STANDARD
    assert hash(engine.tuning(Settings(), MethodId.LSB)) == hash(methods.STANDARD)
    assert engine.tuning(settings, MethodId.DCT).extension_values == ()
    assert [group.id for group in registry.groups_for(MethodId.AUDIO_LSB, is_encrypted=True)] == [
        "crypto",
        "frame",
        "stc",
        "laud",
    ]

    data = b'{"format": "cipherstitch-profile", "version": 1, "defaults": 1, "params": {}}'

    assert profile.parse(data, "p").settings == Settings()


@pytest.mark.usefixtures("refuse_loading")
def test_an_unknown_key_in_a_built_in_group_never_loads_the_extensions() -> None:
    """No extension adds stc.new, since its groups take no built-in id: only a newer
    CipherStitch could, so a newer profile's key is unknown without a load."""
    assert not registry.knows("stc.new")

    with pytest.raises(InvalidParameterError, match=r"^Unknown parameter stc\.new$"):
        registry.lookup("stc.new")

    data = (
        b'{"format": "cipherstitch-profile", "version": 1, "defaults": 1, "params": {"stc.new": 1}}'
    )

    assert profile.parse(data, "p").unknown == ("stc.new",)


@pytest.fixture
def password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("getpass.getpass", lambda _prompt: STRONG)


@pytest.mark.usefixtures("refuse_loading", "password")
def test_the_cli_without_research_never_loads_the_extensions(
    make_image: ImageFactory,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cover, secret, output = make_image(), tmp_path / "note.txt", tmp_path / "out.png"
    secret.write_bytes(FILE.data)
    hide = ["hide", str(cover), str(secret), "-o", str(output)]

    assert cli.main(hide) == 0
    assert cli.main(["reveal", str(output), "-d", str(tmp_path / "out"), "-e"]) == 0
    assert cli.main(["capacity", str(cover)]) == 0
    # Research mode's number counts only the built-ins without --research.
    fxb = tmp_path / "cover.fxb"
    fxb.write_bytes(bytes(4096))

    assert cli.main(["capacity", str(fxb)]) == 1
    # The hint tries Research's default parameters, still without the extensions.
    assert cli.main([*hide, "--cipher", "fernet"]) == 1
    assert "hint: Research mode allows this. Add --research" in capsys.readouterr().err


@pytest.mark.usefixtures("refuse_loading")
@pytest.mark.parametrize(
    ("option", "message"),
    [
        ("--method", "Unknown method byte-lsb. Extension methods run only in Research mode"),
        ("--cipher", "Unknown cipher byte-lsb. Extension ciphers run only in Research mode"),
    ],
)
def test_the_cli_refuses_extension_ids_without_research(
    make_image: ImageFactory,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    option: str,
    message: str,
) -> None:
    args = ["hide", str(make_image()), str(tmp_path / "s"), "-o", str(tmp_path / "out.png")]

    assert cli.main([*args, option, "byte-lsb"]) == 1
    assert capsys.readouterr().err == f"error: {message} (--research)\n"


@pytest.mark.parametrize("cipher", [CipherId.NONE, "chacha-copy"])
def test_research_mode_hides_and_reveals_with_an_extension(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cipher: CipherKey
) -> None:
    use_extensions(monkeypatch, "working")
    cover, output = tmp_path / "cover.fxb", tmp_path / "embedded.fxb"
    cover.write_bytes(os.urandom(4096))
    password = None if cipher == CipherId.NONE else STRONG
    options = HideOptions(CarrierKind.DOCUMENT, "byte-lsb", cipher, password, is_research=True)
    estimate = service.estimate(cover, FILE.name, len(FILE.data), options)

    assert estimate.available == 4096 // 8
    assert estimate.advised is None

    service.hide(cover, FILE, output, options)
    research = ResearchReveal("byte-lsb", cipher)
    revealed = service.reveal(output, tmp_path / "out", password, research=research)

    assert revealed.path.read_bytes() == FILE.data
    assert (revealed.method, revealed.cipher) == ("byte-lsb", cipher)
