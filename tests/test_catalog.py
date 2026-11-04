import sys
from collections.abc import Callable
from pathlib import PurePath

import pytest

from cipherstitch import catalog, gui, methods
from cipherstitch.catalog import CarrierKind, CipherId, MethodId
from cipherstitch.errors import NotAvailableError


def test_every_carrier_has_methods_and_ids_are_unique() -> None:
    methods = [m.id for c in catalog.CARRIERS for m in c.methods]
    assert all(c.methods for c in catalog.CARRIERS)
    assert len(methods) == len(set(methods)) == len(MethodId)
    assert {c.id for c in catalog.CIPHERS} == set(CipherId)


def test_lookups() -> None:
    assert catalog.carrier(CarrierKind.AUDIO).label == "Audio"
    assert catalog.method(MethodId.DCT).name == "DCT coefficients (JPEG)"
    xchacha = catalog.cipher(CipherId.XCHACHA20)
    assert xchacha.requires == catalog.Requirement("nacl", "crypto-extra")
    assert catalog.cipher(catalog.DEFAULT_CIPHER).is_available


def test_lookups_take_plain_strings() -> None:
    assert catalog.method("dct") is catalog.method(MethodId.DCT)
    assert catalog.cipher("fernet") is catalog.cipher(CipherId.FERNET)


@pytest.mark.parametrize(
    ("lookup", "message"),
    [(catalog.method, "Unknown method nsf5"), (catalog.cipher, "Unknown cipher nsf5")],
)
def test_unknown_ids_are_not_available(lookup: Callable[[str], object], message: str) -> None:
    with pytest.raises(NotAvailableError, match=message):
        lookup("nsf5")


@pytest.mark.parametrize(
    ("name", "kind"),
    [("a.PNG", CarrierKind.IMAGE), ("b.flac", CarrierKind.AUDIO), ("c.docx", CarrierKind.DOCUMENT)],
)
def test_carrier_for_path(name: str, kind: CarrierKind) -> None:
    found = catalog.carrier_for_path(PurePath(name))
    assert found is not None
    assert found.kind == kind


def test_raw_carriers_need_the_raw_extra() -> None:
    assert catalog.carrier_for_path(PurePath("IMG_0001.CR2")) == catalog.carrier(CarrierKind.IMAGE)
    assert all(catalog.FORMAT_REQUIREMENTS[s] is catalog.RAWPY for s in catalog.RAW_EXTENSIONS)


def test_carrier_for_unknown_path() -> None:
    assert catalog.carrier_for_path(PurePath("archive.zip")) is None


def test_available_carriers_today() -> None:
    available = [c.kind for c in catalog.CARRIERS if c.is_available]
    assert available == list(CarrierKind)


def test_reveal_accepts_only_formats_a_method_writes() -> None:
    assert methods.reveal_suffixes() == (
        ".png",
        ".bmp",
        ".tif",
        ".tiff",
        ".jpg",
        ".jpeg",
        ".wav",
        ".flac",
        ".pdf",
        ".docx",
        ".txt",
        ".md",
    )


def test_exactly_the_available_methods_are_implemented() -> None:
    for carrier in catalog.CARRIERS:
        for method in carrier.methods:
            assert (methods.get(method.id) is not None) == method.is_available, method.id


def test_an_extras_install_hint_points_at_the_readme() -> None:
    """CipherStitch isn't on PyPI: ``pip install 'cipherstitch[audio]'`` would fail, or one
    day install someone else's package. An extension's own package is on PyPI."""
    extra = catalog.Requirement("soundfile", "audio")
    package = catalog.Requirement("scipy")

    assert extra.install_hint("FLAC") == "FLAC needs the audio extra, see Installation in README.md"
    assert package.install_hint("Line spaces") == "Line spaces needs scipy: pip install scipy"


def test_the_app_without_its_extra_points_at_the_readme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "cipherstitch.gui.app", None)  # its import fails

    with pytest.raises(SystemExit, match=r"^The app needs the gui extra, see Installation in"):
        gui.main()
