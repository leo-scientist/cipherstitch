"""What Research mode's service offers from the extensions: the lists, the carrier of a path,
descriptions, the Detection row, failures and the checks before a reveal
(docs/architecture.md#extensions). Standard's side is in test_extension_isolation."""

import os
from pathlib import Path

import pytest

from cipherstitch import catalog, extension_loader, risks, service
from cipherstitch.catalog import CarrierKind, CipherId, MethodId
from cipherstitch.errors import NotAvailableError, UnsupportedFormatError
from cipherstitch.service import HideOptions, ResearchReveal

from .conftest import use_extensions

MISSING = "cipherstitch_missing_dependency"


@pytest.fixture
def working(monkeypatch: pytest.MonkeyPatch) -> None:
    use_extensions(monkeypatch, "working")


@pytest.fixture
def fxb(tmp_path: Path) -> Path:
    """A cover only the fixture's byte-lsb method takes."""
    cover = tmp_path / "cover.fxb"
    cover.write_bytes(os.urandom(4096))

    return cover


@pytest.mark.usefixtures("working")
def test_research_lists_the_extensions_after_the_built_ins() -> None:
    documents = (MethodId.OBJECT_STREAM, MethodId.WHITESPACE)

    assert service.mode_methods(CarrierKind.DOCUMENT, is_research=True) == (*documents, "byte-lsb")
    assert service.mode_methods(CarrierKind.TEXT, is_research=True) == (MethodId.ZERO_WIDTH,)
    assert service.mode_ciphers(is_research=True) == (*CipherId, "chacha-copy")
    assert "byte-lsb" not in service.unavailable_methods(CarrierKind.DOCUMENT, is_research=True)
    assert "chacha-copy" not in service.unavailable_ciphers(is_research=True)
    assert service.reveal_methods(is_research=True)[-1] == "byte-lsb"


@pytest.mark.usefixtures("working")
def test_research_takes_the_suffixes_the_extensions_add() -> None:
    document = service.carrier_for_path(Path("a.FXB"), is_research=True)

    assert document is not None
    assert document.kind is CarrierKind.DOCUMENT
    assert service.carrier_for_path(Path("a.zip"), is_research=True) is None
    assert service.carrier_suffixes(CarrierKind.DOCUMENT, is_research=True) == (
        ".pdf",
        ".docx",
        ".fxb",
    )
    assert service.carrier_suffixes(CarrierKind.IMAGE, is_research=True) == (
        catalog.carrier(CarrierKind.IMAGE).extensions
    )
    assert service.reveal_suffixes(is_research=True)[-1] == ".fxb"
    assert service.reveal_methods(Path("a.fxb"), is_research=True) == ("byte-lsb",)
    assert service.output_suffixes("byte-lsb", Path("a.fxb"), is_research=True) == (".fxb",)
    assert service.output_suffixes("byte-lsb", None, is_research=True) == (".fxb",)


@pytest.mark.usefixtures("working")
def test_a_file_only_an_extension_takes_gets_its_method(fxb: Path, tmp_path: Path) -> None:
    output = tmp_path / "out.fxb"
    options = service.hide_options(fxb, output, CipherId.NONE, is_research=True)
    details = service.describe_carrier(fxb, CarrierKind.DOCUMENT, is_research=True)

    assert (options.carrier, options.method) == (CarrierKind.DOCUMENT, "byte-lsb")
    assert details == service.CarrierDetails("4.0 KiB · FXB", 4096 // 8)
    assert service.describe_carrier(fxb, CarrierKind.DOCUMENT, "byte-lsb", is_research=True) == (
        details
    )

    text, document = tmp_path / "a.txt", CarrierKind.DOCUMENT
    suffixes = r"^Document carriers are \.pdf, \.docx, \.fxb files$"

    with pytest.raises(UnsupportedFormatError, match=suffixes):
        service.hide_options(text, output, CipherId.NONE, carrier=document, is_research=True)


def test_built_ins_stay_the_default_method(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    use_extensions(monkeypatch, "mixed")  # faulty takes .txt too
    cover = tmp_path / "cover.txt"
    cover.write_text("lanterns " * 100, encoding="utf-8")
    options = service.hide_options(cover, tmp_path / "out.txt", CipherId.NONE, is_research=True)

    assert options.method == MethodId.ZERO_WIDTH
    assert service.mode_methods(CarrierKind.TEXT, is_research=True) == (
        MethodId.ZERO_WIDTH,
        "faulty",
    )

    with pytest.raises(UnsupportedFormatError, match=r"Output must be one of: \.txt$"):
        service.hide_options(cover, tmp_path / "out.md", CipherId.NONE, is_research=True)


@pytest.mark.usefixtures("working")
def test_the_detection_row_names_the_extensions_risk(fxb: Path) -> None:
    options = HideOptions(CarrierKind.DOCUMENT, "byte-lsb", CipherId.NONE, is_research=True)

    assert service.detection_risks(options, fxb) == (
        "Anyone who compares the file with its original sees the changed bytes.",
    )


def test_an_extension_method_without_its_own_risk_is_unmeasured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_extensions(monkeypatch, "mixed")
    options = HideOptions(CarrierKind.TEXT, "faulty", CipherId.NONE, is_research=True)

    assert service.detection_risks(options) == (risks.EXTENSION_RISK,)
    assert risks.EXTENSION_RISK == (
        "This method comes from an extension. CipherStitch hasn't measured how detectable it is."
    )


def test_failures_are_listed_with_what_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    use_extensions(monkeypatch, "mixed")
    loaded = service.loaded_extensions()
    (failure,) = service.extension_failures()

    assert [extension.name for extension in loaded.extensions] == ["faulty", "good"]
    assert failure.extension == "broken"
    assert failure.brief == f"ModuleNotFoundError: No module named '{MISSING}'"
    assert failure.traceback is not None


def test_a_brief_failure_counts_the_other_problems() -> None:
    failure = extension_loader.Failure("x", ("first", "second", "third"))

    assert failure.brief == "first (and 2 more)"


@pytest.mark.usefixtures("working")
@pytest.mark.parametrize(
    ("method", "cipher", "message"),
    [
        ("missing-method", CipherId.NONE, "Unknown method missing-method. No loaded extension"),
        ("byte-lsb", "missing-cipher", "Unknown cipher missing-cipher. No loaded extension"),
    ],
)
def test_a_research_reveal_refuses_unknown_ids_first(
    fxb: Path, tmp_path: Path, method: str, cipher: str, message: str
) -> None:
    with pytest.raises(NotAvailableError, match=message):
        service.reveal(fxb, tmp_path, research=ResearchReveal(method, cipher))


def test_a_research_reveal_refuses_a_missing_dependency(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_extensions(monkeypatch, "missing_dependency")
    cover = tmp_path / "cover.txt"
    cover.write_text("text", encoding="utf-8")
    hint = f"Optional method needs {MISSING}: pip install {MISSING}"

    with pytest.raises(NotAvailableError, match=hint):
        service.reveal(cover, tmp_path, research=ResearchReveal("optional-method", CipherId.NONE))

    unavailable = service.unavailable_ciphers(is_research=True)

    assert unavailable["optional-cipher"] == (
        "Optional cipher needs missing-dependency: pip install missing-dependency"
    )


def test_a_raw_an_extension_method_takes_itself_is_not_compressed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Describing a RAW for another method falls back to DCT, which compresses it."""
    use_extensions(monkeypatch, "suffixes")
    raw = Path("IMG_0001.dng")
    options = HideOptions(method="raw-reader", cipher=CipherId.NONE, is_research=True)

    assert not service.compresses_carrier(raw, "raw-reader")
    assert not service.hide_step_labels(options, Path("out.dng"), raw)[0].startswith("Compress")

    for method in ("pgm-writer", MethodId.LSB, MethodId.DCT):
        assert service.compresses_carrier(raw, method)


def test_a_suffix_a_method_only_writes_is_no_carrier(monkeypatch: pytest.MonkeyPatch) -> None:
    use_extensions(monkeypatch, "suffixes")

    assert service.carrier_for_path(Path("x.pgm"), is_research=True) is None
    assert ".pgm" not in service.carrier_suffixes(CarrierKind.IMAGE, is_research=True)
    assert service.output_suffixes("pgm-writer", Path("x.png"), is_research=True) == (".pgm",)
    assert ".pgm" in service.reveal_suffixes(is_research=True)
