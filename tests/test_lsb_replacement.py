"""Sequential LSB replacement, Research mode's Experimental baseline
(docs/format.md#lsb-replacement)."""

from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest
from PIL import Image, PngImagePlugin

from cipherstitch import container, kdf, methods, risks, service
from cipherstitch.catalog import CarrierKind, CipherId, MethodId
from cipherstitch.container import Candidate, UnitOrder
from cipherstitch.errors import NoHiddenDataError
from cipherstitch.methods.lsb_replacement import LsbReplacementChannel
from cipherstitch.methods.slots import to_bits
from cipherstitch.params import registry
from cipherstitch.params.settings import Settings
from cipherstitch.payload import HiddenFile
from cipherstitch.service import ResearchReveal

from .conftest import skip_unless_installed

PASSWORD = "quiet harbour 47 lanterns"  # noqa: S105
FILE = HiddenFile("n.txt", b"traces " * 20)
PREFIX = bytes(range(16))
SIDE = 24  # 1728 values, 216 bytes

# The lowest bits of the first values of a cover holding VECTOR_FILE, high bit first: plain
# and encrypted under test_container's VECTOR_PASSWORD and VECTOR_ARGON2 profile. They pin
# the method id, the numbering of the values, the bit order and the sequence. Never
# regenerate them: data hidden by earlier versions must stay readable.
VECTOR_FILE = HiddenFile("a.txt", b"cipherstitch" * 8)
VECTOR_PASSWORD = "пароль-йї"  # noqa: S105
VECTOR_ARGON2 = {"ARGON2_MEMORY_KIB": 32, "ARGON2_ITERATIONS": 1}
VECTORS = {
    CipherId.NONE: (
        "ab33cc9d3acf08f5c2f11d736add3d9db0ea8a215374fa00d83f9edc3e77a5d8f89748754a881dce822bd5"
        "0f6f52faddaddd3f4087da9975f454281518a79c5b5954f48d37bb8e1d4cccfe0d22b1a459a16e61643ba7"
        "62f3d4bcdb02d4f1b9cc3d847274cf3fe96cbd9ef9fba009fcbbf9ace5a070e081cc515dd536e68a057b76"
    ),
    CipherId.AES_GCM: (
        "47327a5e55789ff4203ef00e9ecfb8e5941a74def5d9dfda589849a5f24cd66d5d798edaf519d8fa32a99d"
        "7ce36c6a84ebe5e727de45d422336a61cef9f770d233eb53adb48a6af553fa40b211ca8ab5be88a6abd072"
        "68bf2a7722feb8c1f9b52c9cc62129c7ec1657a8f9eb27b0dcbedcad181d5100957554832e7d0632863598"
        "c692710fb0813bc5c73b76fea1ff89219514d4db8e4d91b137d9a6e46f"
    ),
}


def _values(seed: int = 7) -> npt.NDArray[np.uint8]:
    return np.random.default_rng(seed).integers(0, 256, SIDE * SIDE * 3, dtype=np.uint8)


def _png(values: npt.NDArray[np.uint8], path: Path, mode: str = "RGB") -> Path:
    Image.fromarray(values.reshape(SIDE, SIDE, len(mode)), mode).save(path)

    return path


def _saved_values(path: Path) -> npt.NDArray[np.uint8]:
    with Image.open(path) as opened:
        return np.asarray(opened.convert("RGB")).reshape(-1)


def test_the_stream_overwrites_the_lowest_bits_from_the_first_value_on(tmp_path: Path) -> None:
    cover = _values()
    body = bytes(range(100))
    embedded = LsbReplacementChannel(_png(cover, tmp_path / "cover.png")).write(PREFIX, body, None)
    embedded.save(tmp_path / "out.png")
    written = _saved_values(tmp_path / "out.png")
    stream = to_bits(PREFIX + body)

    assert np.array_equal(written[: stream.size] & 1, stream)
    assert np.array_equal(written >> 1, cover >> 1)
    assert np.array_equal(written[stream.size :], cover[stream.size :])
    assert LsbReplacementChannel(tmp_path / "out.png").read(16, len(body), None) == body


def test_the_capacity_is_one_bit_a_colour_value(tmp_path: Path) -> None:
    channel = LsbReplacementChannel(_png(_values(), tmp_path / "cover.png"))

    assert channel.capacity(0) == SIDE * SIDE * 3 // 8
    assert channel.capacity(16) == SIDE * SIDE * 3 // 8 - 16


def test_alpha_is_left_as_it_is(tmp_path: Path) -> None:
    rgba = np.random.default_rng(3).integers(0, 256, SIDE * SIDE * 4, dtype=np.uint8)
    cover = _png(rgba, tmp_path / "cover.png", "RGBA")
    LsbReplacementChannel(cover).write(PREFIX, bytes(150), None).save(tmp_path / "out.png")

    with Image.open(tmp_path / "out.png") as written:
        alpha = np.asarray(written)[..., 3].reshape(-1)

    assert np.array_equal(alpha, rgba.reshape(-1, 4)[:, 3])


@pytest.mark.parametrize("cipher_id", [CipherId.NONE, CipherId.AES_GCM])
def test_research_mode_hides_and_reveals_it(tmp_path: Path, cipher_id: CipherId) -> None:
    password = None if cipher_id == CipherId.NONE else PASSWORD
    cover, embedded = _png(_values(), tmp_path / "cover.png"), tmp_path / "embedded.png"
    options = service.HideOptions(
        CarrierKind.IMAGE, MethodId.LSB_REPLACEMENT, cipher_id, password, is_research=True
    )
    service.hide(cover, FILE, embedded, options)
    research = ResearchReveal(MethodId.LSB_REPLACEMENT, cipher_id, Settings())
    result = service.reveal(embedded, tmp_path / "out", password, research=research)

    assert result.path.read_bytes() == FILE.data
    assert result.method == MethodId.LSB_REPLACEMENT


def test_standard_mode_neither_hides_with_it_nor_tries_it_on_reveal(tmp_path: Path) -> None:
    cover, embedded = _png(_values(), tmp_path / "cover.png"), tmp_path / "embedded.png"
    research = service.HideOptions(
        CarrierKind.IMAGE, MethodId.LSB_REPLACEMENT, CipherId.NONE, None, is_research=True
    )
    service.hide(cover, FILE, embedded, research)

    assert MethodId.LSB_REPLACEMENT in service.unavailable_methods(CarrierKind.IMAGE)
    assert MethodId.LSB_REPLACEMENT not in service.unavailable_methods(
        CarrierKind.IMAGE, is_research=True
    )
    assert MethodId.LSB_REPLACEMENT in service.reveal_methods(embedded)

    with pytest.raises(NoHiddenDataError):
        service.reveal(embedded, tmp_path / "out")


def test_image_lsbs_cover_rules_and_metadata_setting_apply_to_it() -> None:
    shown = registry.groups_for(MethodId.LSB_REPLACEMENT, is_encrypted=False)
    params = {group.id: [param.id for param in group.params] for group in shown}

    assert params["limg"] == ["lossy", "meta"]
    assert "stc" not in params


@pytest.mark.parametrize(("keeps", "is_kept"), [(True, True), (False, False)])
def test_the_metadata_setting_reaches_it(tmp_path: Path, keeps: bool, is_kept: bool) -> None:
    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", "a cover's own text")
    cover, embedded = tmp_path / "cover.png", tmp_path / "embedded.png"
    Image.fromarray(_values().reshape(SIDE, SIDE, 3)).save(cover, pnginfo=info)
    options = service.HideOptions(
        CarrierKind.IMAGE,
        MethodId.LSB_REPLACEMENT,
        CipherId.NONE,
        None,
        settings=Settings.of({"limg.meta": keeps}),
        is_research=True,
    )
    service.hide(cover, FILE, embedded, options)

    with Image.open(embedded) as written:
        assert ("Comment" in written.info) is is_kept


def test_research_refuses_a_jpeg_cover_unless_lossy_covers_are_allowed(tmp_path: Path) -> None:
    cover = tmp_path / "cover.jpg"
    Image.fromarray(_values().reshape(SIDE, SIDE, 3)).save(cover)
    refused = service.HideOptions(
        CarrierKind.IMAGE, MethodId.LSB_REPLACEMENT, CipherId.NONE, None, is_research=True
    )
    allowed = service.HideOptions(
        CarrierKind.IMAGE,
        MethodId.LSB_REPLACEMENT,
        CipherId.NONE,
        None,
        settings=Settings.of({"limg.lossy": True}),
        is_research=True,
    )

    assert service.unavailable_reason(refused, cover) is not None
    assert service.unavailable_reason(allowed, cover) is None


def test_the_detection_row_names_the_attacks_that_catch_it() -> None:
    assert any(
        "WS, SPA and chi-square" in risk for risk in risks.detection_risks(MethodId.LSB_REPLACEMENT)
    )


def test_the_method_writes_lossless_images_only() -> None:
    embedding = methods.get(MethodId.LSB_REPLACEMENT)

    assert embedding is not None
    assert embedding.output_suffixes(Path("cover.jpg")) == (".png", ".bmp", ".tif", ".tiff")
    assert embedding.body_order(UnitOrder.KEYED, encrypted=True) is UnitOrder.PUBLIC


@pytest.mark.parametrize("cipher_id", list(VECTORS))
def test_format_vectors_stay_readable(
    cipher_id: CipherId, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skip_unless_installed(cipher_id)

    for name, value in VECTOR_ARGON2.items():
        monkeypatch.setattr(kdf, name, value)

    bits = to_bits(bytes.fromhex(VECTORS[cipher_id]))
    values = _values()
    values[: bits.size] = (values[: bits.size] & 0xFE) | bits
    channel = LsbReplacementChannel(_png(values, tmp_path / "vector.png"))
    candidates = (Candidate(MethodId.LSB_REPLACEMENT, UnitOrder.PUBLIC),)
    revealed = container.reveal([(channel, candidates)], VECTOR_PASSWORD)

    assert revealed == container.Revealed(VECTOR_FILE, MethodId.LSB_REPLACEMENT, cipher_id)
