"""The package's own API: ``cipherstitch.capacity``, ``hide_file`` and ``reveal_file``."""

from pathlib import Path

import pytest

import cipherstitch
from cipherstitch import catalog, service, standard
from cipherstitch.catalog import CipherId, MethodId
from cipherstitch.service import HideOptions

from .conftest import ImageFactory

PASSWORD = "quiet harbour 47 lanterns"  # noqa: S105
TEXT = "Every word here can carry two bits. " * 400


def _estimate(cover: Path, method: MethodId) -> service.Estimate:
    options = HideOptions(method=method, cipher=CipherId.AES_GCM, password=PASSWORD)

    return service.estimate(cover, "", 0, options)


def test_capacity_is_standards_advised_size_or_researchs_capacity(
    make_image: ImageFactory,
) -> None:
    """A 512 × 512 PNG holds about 76 000 bytes, while Standard mode takes about 8 400."""
    cover = make_image(size=(320, 240))
    estimate = _estimate(cover, MethodId.LSB)

    assert estimate.advised is not None
    assert estimate.available is not None
    assert estimate.advised < estimate.available
    assert cipherstitch.capacity(cover) == estimate.advised
    assert cipherstitch.capacity(cover, research=True) == estimate.available
    assert service.mode_capacity(cover) == service.ModeCapacity(MethodId.LSB, estimate.advised)


def test_standard_capacity_of_a_jpeg_is_dcts(make_image: ImageFactory) -> None:
    """Both modes refuse a JPEG cover for LSB, so the capacity is DCT's."""
    pytest.importorskip("jpeglib")
    cover = make_image("photo.jpg", size=(320, 240))
    estimate = _estimate(cover, MethodId.DCT)

    assert estimate.advised is not None
    assert estimate.available is not None
    assert service.mode_capacity(cover) == service.ModeCapacity(MethodId.DCT, estimate.advised)
    assert service.mode_capacity(cover, research=True) == service.ModeCapacity(
        MethodId.DCT, estimate.available
    )


@pytest.mark.parametrize("research", [False, True], ids=["standard", "research"])
def test_capacity_names_the_extra_a_jpeg_needs(
    make_image: ImageFactory, monkeypatch: pytest.MonkeyPatch, research: bool
) -> None:
    """On a core-only install, LSB's refusal of a lossy cover would hide that DCT needs only
    the jpeg extra."""
    cover = make_image("photo.jpg", size=(320, 240))
    monkeypatch.setattr(
        catalog.Requirement, "is_installed", property(lambda self: self.module != "jpeglib")
    )

    with pytest.raises(cipherstitch.NotAvailableError, match=r"^DCT .* needs the jpeg extra"):
        cipherstitch.capacity(cover, research=research)


def test_standard_capacity_names_why_standard_mode_cant_hide(tmp_path: Path) -> None:
    cover = tmp_path / "cover.txt"
    cover.write_text(TEXT, encoding="utf-8")

    with pytest.raises(cipherstitch.NotAvailableError, match=f"^{standard.OFF}: no recommended"):
        cipherstitch.capacity(cover)

    assert service.mode_capacity(cover, research=True).method == MethodId.ZERO_WIDTH
    assert cipherstitch.capacity(str(cover), research=True) > 0


def test_standard_capacity_names_a_cover_standard_mode_refuses(make_image: ImageFactory) -> None:
    cover = make_image("grey.bmp", size=(160, 120), mode="L")

    with pytest.raises(cipherstitch.NotAvailableError, match=f"^{standard.OFF}: "):
        cipherstitch.capacity(cover)


def test_paths_may_be_strings(make_image: ImageFactory, secret_file: Path, tmp_path: Path) -> None:
    cover, output = make_image(size=(320, 240)), tmp_path / "cover-2.png"

    assert cipherstitch.capacity(str(cover)) == cipherstitch.capacity(cover)

    cipherstitch.hide_file(str(cover), str(secret_file), str(output), PASSWORD)
    revealed = cipherstitch.reveal_file(str(output), str(tmp_path / "out"), PASSWORD)

    assert revealed.read_bytes() == secret_file.read_bytes()


def test_hide_file_raises_the_exported_error_for_a_standard_refusal(
    secret_file: Path, tmp_path: Path
) -> None:
    cover = tmp_path / "cover.txt"
    cover.write_text(TEXT, encoding="utf-8")

    with pytest.raises(cipherstitch.NotAvailableError, match=standard.OFF):
        cipherstitch.hide_file(cover, secret_file, tmp_path / "cover-2.txt", PASSWORD)
