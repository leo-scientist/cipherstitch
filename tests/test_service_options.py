from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from cipherstitch import catalog, service, standard
from cipherstitch.catalog import CarrierKind, CipherId, MethodId, MethodKey
from cipherstitch.container import UnitOrder
from cipherstitch.errors import (
    CipherStitchError,
    NotAvailableError,
    OperationCancelledError,
    UnsupportedFormatError,
)
from cipherstitch.methods.lsb import ImageChannel
from cipherstitch.params.settings import Settings
from cipherstitch.payload import HiddenFile
from cipherstitch.service import HideOptions

from .conftest import (
    ImageFactory,
    plain_method,
    skip_unless_installed,
    skip_unless_method_installed,
)

PASSWORD = "quiet harbour 47 lanterns"  # noqa: S105
ENCRYPTED = HideOptions(password=PASSWORD, is_research=True)
PLAIN = HideOptions(cipher=CipherId.NONE, is_research=True)
FILE = HiddenFile("note.txt", b"hello" * 50)
ALLOWED = [HideOptions(cipher=c, password=PASSWORD, is_research=True) for c in CipherId]


@pytest.mark.parametrize(
    ("options", "reason"),
    [
        (ENCRYPTED, None),
        (PLAIN, None),
        (HideOptions(cipher=CipherId.FERNET, is_research=True), None),
        (
            HideOptions(carrier=CarrierKind.AUDIO, method=MethodId.PHASE_CODING, is_research=True),
            None,
        ),
        (HideOptions(method=MethodId.DCT, is_research=True), None),
    ],
)
def test_unavailable_reason(options: HideOptions, reason: str | None) -> None:
    skip_unless_method_installed(options.method)
    assert service.unavailable_reason(options) == reason


@pytest.mark.parametrize(
    ("options", "carrier", "reason"),
    [
        (ENCRYPTED, "notes.txt", "LSB can't use .txt files"),
        (ENCRYPTED, "photo.JPG", None),
        (
            HideOptions(CarrierKind.TEXT, MethodId.ZERO_WIDTH, CipherId.NONE, is_research=True),
            "notes.md",
            None,
        ),
    ],
)
def test_unavailable_reason_checks_the_carrier_file(
    options: HideOptions, carrier: str, reason: str | None
) -> None:
    assert service.unavailable_reason(options, Path(carrier)) == reason


@pytest.mark.parametrize(
    ("method", "carrier", "suffixes"),
    [
        (MethodId.LSB, Path("photo.jpg"), (".png", ".bmp", ".tif", ".tiff")),
        (MethodId.ZERO_WIDTH, Path("notes.md"), (".md",)),
        (MethodId.ZERO_WIDTH, None, (".txt", ".md")),
        (MethodId.AUDIO_LSB, Path("song.flac"), (".flac",)),
        (MethodId.AUDIO_LSB, None, (".wav", ".flac")),
    ],
)
def test_output_suffixes(method: MethodId, carrier: Path | None, suffixes: tuple[str, ...]) -> None:
    assert service.output_suffixes(method, carrier) == suffixes


def test_missing_extra_is_the_unavailable_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(catalog.Requirement, "is_installed", property(lambda _self: False))
    assert service.unavailable_reason(HideOptions(cipher=CipherId.XCHACHA20, is_research=True)) == (
        "XChaCha20-Poly1305 needs the crypto-extra extra, see Installation in README.md"
    )


def test_the_options_never_show_the_password() -> None:
    assert PASSWORD not in repr(ENCRYPTED)
    assert PASSWORD not in str(ENCRYPTED)


def test_hide_rejects_unavailable_options(make_image: ImageFactory, tmp_path: Path) -> None:
    cover = make_image("cover.bmp")

    with pytest.raises(NotAvailableError, match="DCT"):
        service.hide(
            cover, FILE, tmp_path / "s.png", HideOptions(method=MethodId.DCT, is_research=True)
        )


def test_hide_needs_a_password_to_encrypt(make_image: ImageFactory, tmp_path: Path) -> None:
    """Even when the Research parameters ask for no particular strength."""
    lowest = HideOptions(is_research=True, settings=Settings.of({"crypto.minpw": 0}))

    with pytest.raises(CipherStitchError, match="password is required"):
        service.hide(make_image(), FILE, tmp_path / "s.png", lowest)

    assert not (tmp_path / "s.png").exists()


@pytest.mark.parametrize("options", ALLOWED, ids=lambda o: f"{o.method}+{o.cipher}")
def test_every_cipher_roundtrips_with_lsb(
    make_image: ImageFactory, tmp_path: Path, options: HideOptions
) -> None:
    skip_unless_installed(options.cipher)
    embedded = tmp_path / "s.tiff"
    service.hide(make_image(), FILE, embedded, options)
    password = options.password if options.is_encrypted else None
    result = service.reveal(embedded, tmp_path, password)
    assert result.path.read_bytes() == FILE.data
    assert (result.method, result.cipher) == (options.method, options.cipher)


@pytest.mark.parametrize("options", ALLOWED, ids=lambda o: f"{o.method}+{o.cipher}")
def test_estimate_matches_real_capacity(make_image: ImageFactory, options: HideOptions) -> None:
    cover = make_image(size=(80, 60))
    capacity = service.capacity(cover, research=True)
    estimate = service.estimate(cover, FILE.name, len(FILE.data), options)
    assert estimate.available == capacity
    assert estimate.excess == 0
    too_big = service.estimate(cover, FILE.name, capacity, options)
    assert too_big.excess == too_big.required - capacity > 0


def test_estimate_for_a_method_that_cannot_use_the_file_is_unknown(tmp_path: Path) -> None:
    estimate = service.estimate(
        tmp_path / "x.bmp", "a", 10, HideOptions(method=MethodId.DCT, is_research=True)
    )
    assert estimate.available is None
    assert estimate.excess == 0


def test_describe_carrier(make_image: ImageFactory) -> None:
    details = service.describe_carrier(make_image(size=(80, 60)), CarrierKind.IMAGE)
    assert details == service.CarrierDetails("80 × 60 · PNG", 1258)


def test_carrier_is_opened_once_until_it_changes(
    make_image: ImageFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[Path] = []
    original = ImageChannel.__init__

    def tracked(channel: ImageChannel, path: Path, *settings: Any) -> None:
        opened.append(path)
        original(channel, path, *settings)

    monkeypatch.setattr(ImageChannel, "__init__", tracked)
    cover = make_image(size=(80, 60))
    service.describe_carrier(cover, CarrierKind.IMAGE, MethodId.LSB)

    for options in ALLOWED:
        for name in ("a", "longer name.txt"):
            service.estimate(cover, name, 10, options)

    assert opened == [cover]

    make_image(size=(81, 60))
    assert service.estimate(cover, "a", 10, ENCRYPTED).available == 1274
    assert opened == [cover, cover]


@pytest.mark.parametrize(
    ("options", "password", "hide_steps", "reveal_steps"),
    [
        (
            ENCRYPTED,
            PASSWORD,
            [
                "Deriving key · Argon2id",
                "Encrypting · AES-256-GCM",
                "Embedding · LSB",
                "Writing PNG",
            ],
            ["Trying every method", "Deriving key · Argon2id", "Decrypting and verifying"],
        ),
        (PLAIN, None, ["Embedding · LSB", "Writing PNG"], ["Trying every method"]),
    ],
)
def test_progress_reports_every_step_in_order(
    make_image: ImageFactory,
    tmp_path: Path,
    options: HideOptions,
    password: str | None,
    hide_steps: list[str],
    reveal_steps: list[str],
) -> None:
    seen: list[tuple[int, int, str]] = []
    embedded = tmp_path / "s.png"
    service.hide(make_image(), FILE, embedded, options, lambda *step: seen.append(step))
    steps = [(i, len(hide_steps), label) for i, label in enumerate(hide_steps)]
    assert seen == [*steps, steps[-1]]  # once more just before saving

    seen.clear()
    result = service.reveal(embedded, tmp_path, password, lambda *step: seen.append(step))
    assert [label for _, _, label in seen] == [*reveal_steps, reveal_steps[-1]]
    assert result.path.read_bytes() == FILE.data
    assert (result.method, result.cipher) == (options.method, options.cipher)
    assert result.is_integrity_verified == (password is not None)


@pytest.mark.parametrize(
    ("options", "steps"),
    [
        (ENCRYPTED, ["Extracting · LSB", "Deriving key · Argon2id", "Decrypting · AES-256-GCM"]),
        (PLAIN, ["Extracting · LSB"]),  # a plain frame takes no password, typed or not
    ],
)
def test_a_research_reveal_names_what_it_tries(
    make_image: ImageFactory, tmp_path: Path, options: HideOptions, steps: list[str]
) -> None:
    embedded = tmp_path / "s.png"
    service.hide(make_image(), FILE, embedded, options)
    research = service.ResearchReveal(options.method, options.cipher)
    seen: list[str] = []
    service.reveal(embedded, tmp_path, PASSWORD, lambda *step: seen.append(step[2]), research)

    assert seen == [*steps, steps[-1]]


@pytest.mark.parametrize(("function", "name"), [("scrypt", "scrypt"), ("pbkdf2", "PBKDF2-SHA256")])
def test_the_steps_name_the_key_derivation_of_the_settings(function: str, name: str) -> None:
    settings = Settings.of({"crypto.kdf": function})
    research = service.ResearchReveal(MethodId.LSB, CipherId.AES_GCM, settings)
    options = replace(ENCRYPTED, settings=settings)
    standard_options = replace(options, is_research=False)  # ignores the settings

    assert service.kdf_name(settings) == name
    assert f"Deriving key · {name}" in service.hide_step_labels(options, Path("s.png"))
    assert f"Deriving key · {name}" in service.reveal_step_labels(PASSWORD, research)
    assert "Deriving key · Argon2id" in service.hide_step_labels(standard_options, Path("s.png"))


def test_research_reveal_offers_the_methods_that_may_have_hidden_in_the_file() -> None:
    implemented = tuple(m.id for c in catalog.CARRIERS for m in c.methods if m.is_available)

    assert service.reveal_methods() == implemented
    assert service.reveal_methods(Path("a.png")) == (MethodId.LSB, MethodId.LSB_REPLACEMENT)
    assert service.reveal_methods(Path("a.JPG")) == (MethodId.DCT,)
    assert service.reveal_methods(Path("a.wav")) == (
        MethodId.AUDIO_LSB,
        MethodId.PHASE_CODING,
        MethodId.ECHO_HIDING,
    )

    with pytest.raises(UnsupportedFormatError, match="can't carry a payload"):
        service.reveal_methods(Path("a.xyz"))


def test_only_a_research_reveal_tries_the_sequential_order() -> None:
    """Standard's tries the orders it hides in, and only for methods that are not
    Experimental."""

    def orders(method: MethodId | None) -> set[tuple[MethodKey, UnitOrder]]:
        groups = service._reveal_candidates(Path("a.png"), method)

        return {(c.method, c.order) for _opener, candidates in groups for c in candidates}

    assert orders(None) == {(MethodId.LSB, UnitOrder.KEYED), (MethodId.LSB, UnitOrder.PUBLIC)}
    assert orders(MethodId.LSB) == {(MethodId.LSB, order) for order in UnitOrder}
    assert orders(MethodId.LSB_REPLACEMENT) == {(MethodId.LSB_REPLACEMENT, UnitOrder.PUBLIC)}


def test_a_standard_reveal_with_no_method_to_try_is_an_unsupported_format(
    make_image: ImageFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(standard, "reveals", lambda _candidate: False)

    with pytest.raises(UnsupportedFormatError, match=r"^\.png files can't hold a payload$"):
        service.reveal(make_image(), tmp_path)


def test_writing_step_names_the_output_format() -> None:
    assert service.hide_step_labels(PLAIN, Path("out.TIF"))[-1] == "Writing TIFF"
    assert service.hide_step_labels(PLAIN, Path("out.md"))[-1] == "Writing MD"


def test_progress_callback_can_cancel(make_image: ImageFactory, tmp_path: Path) -> None:
    def cancel(index: int, _count: int, _label: str) -> None:
        if index == 1:
            raise OperationCancelledError

    with pytest.raises(OperationCancelledError):
        service.hide(make_image(), FILE, tmp_path / "s.png", ENCRYPTED, cancel)

    assert not (tmp_path / "s.png").exists()


def test_cancelling_while_saving_leaves_the_output_as_it_was(
    make_image: ImageFactory, tmp_path: Path
) -> None:
    calls: list[str] = []

    def cancel_after_writing(_index: int, count: int, label: str) -> None:
        calls.append(label)

        if len(calls) > count:  # the call just before the result is saved
            raise OperationCancelledError

    embedded = tmp_path / "s.png"
    embedded.write_bytes(b"previous")

    with pytest.raises(OperationCancelledError):
        service.hide(make_image(), FILE, embedded, PLAIN, cancel_after_writing)

    assert embedded.read_bytes() == b"previous"
    assert not list(tmp_path.glob("*.partial*"))

    service.hide(make_image(), FILE, embedded, PLAIN)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    calls.clear()

    with pytest.raises(OperationCancelledError):
        service.reveal(embedded, out_dir, None, cancel_after_writing)

    assert not any(out_dir.iterdir())


def test_only_unencrypted_payloads_reveal_without_a_password(
    make_image: ImageFactory, tmp_path: Path
) -> None:
    plain, encrypted = tmp_path / "plain.png", tmp_path / "enc.png"
    service.hide(make_image(), FILE, plain, PLAIN)
    service.hide(make_image(), FILE, encrypted, ENCRYPTED)
    assert plain_method(plain) == MethodId.LSB
    assert plain_method(encrypted) is None
    assert plain_method(make_image()) is None


def test_unavailable_methods_follow_the_carrier_file() -> None:
    by_file = service.unavailable_methods(CarrierKind.IMAGE, Path("cover.bmp"))

    assert set(by_file) == {MethodId.DCT, MethodId.LSB_REPLACEMENT}
    assert MethodId.LSB not in service.unavailable_methods(CarrierKind.IMAGE)


def test_a_png_with_jpeg_output_picks_dct(make_image: ImageFactory, tmp_path: Path) -> None:
    skip_unless_method_installed(MethodId.DCT)
    options = service.hide_options(
        make_image(), tmp_path / "out.jpg", CipherId.AES_GCM, is_research=True
    )

    assert options.method == MethodId.DCT


def test_a_raw_carrier_is_only_for_dct(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    carrier = Path("IMG_0001.CR2")

    with pytest.raises(UnsupportedFormatError, match=r"Output must be one of: \.jpg, \.jpeg$"):
        service.hide_options(carrier, tmp_path / "out.png", CipherId.AES_GCM, is_research=True)

    assert service.unavailable_methods(CarrierKind.IMAGE, carrier)[MethodId.LSB] == (
        "LSB can't use .CR2 files"
    )

    monkeypatch.setattr(
        catalog.Requirement, "is_installed", property(lambda self: self.module != "rawpy")
    )

    assert service.unavailable_methods(CarrierKind.IMAGE, carrier)[MethodId.DCT] == (
        "CR2 needs the raw extra, see Installation in README.md"
    )


@pytest.mark.parametrize(
    ("name", "method", "compresses"),
    [
        ("IMG_0001.CR2", MethodId.LSB, True),
        ("scan.TIF", MethodId.DCT, True),
        ("scan.png", MethodId.LSB, False),
        ("photo.jpg", MethodId.DCT, False),
    ],
)
def test_a_carrier_is_compressed_first_only_as_a_precover(
    name: str, method: MethodId, compresses: bool
) -> None:
    assert service.compresses_carrier(Path(name), method) is compresses


def test_unavailable_ciphers_follow_the_extras(monkeypatch: pytest.MonkeyPatch) -> None:
    assert CipherId.NONE not in service.unavailable_ciphers(is_research=True)

    monkeypatch.setattr(catalog.Requirement, "is_installed", property(lambda _self: False))

    assert service.unavailable_ciphers(is_research=True) == {
        CipherId.XCHACHA20: (
            "XChaCha20-Poly1305 needs the crypto-extra extra, see Installation in README.md"
        )
    }
