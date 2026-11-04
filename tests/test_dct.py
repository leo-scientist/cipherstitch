import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pytest
from PIL import Image, ImageCms

from cipherstitch import catalog, container, kdf, service
from cipherstitch.catalog import CipherId, MethodId
from cipherstitch.container import Candidate, UnitOrder
from cipherstitch.errors import (
    CapacityError,
    NoHiddenDataError,
    NotAvailableError,
    UnsupportedFormatError,
)
from cipherstitch.media import jpeg
from cipherstitch.media.jpeg_header import Header, Segment
from cipherstitch.methods import dct, feistel, juniward, stc, trellis
from cipherstitch.methods.dct import DctChannel
from cipherstitch.params.settings import Settings
from cipherstitch.payload import HiddenFile
from cipherstitch.service import HideOptions

from .conftest import plain_method, skip_unless_installed

pytest.importorskip("jpeglib")

PASSWORD = "quiet harbour 47 lanterns"  # noqa: S105
FILE = HiddenFile("note.txt", "Таємниця".encode() * 40)
ENCRYPTED = HideOptions(
    method=MethodId.DCT, cipher=CipherId.AES_GCM, password=PASSWORD, is_research=True
)
PLAIN = HideOptions(method=MethodId.DCT, cipher=CipherId.NONE, is_research=True)


def _photo(path: Path, mode: str = "RGB", *, is_top_flat: bool = False, **options: Any) -> Path:
    rng = np.random.default_rng(seed=1)
    gradient = np.linspace(0, 255, 320)[None, :] + np.linspace(0, 255, 240)[:, None]
    pixels = np.clip(gradient[..., None] / 2 + rng.normal(0, 30, (240, 320, 3)), 0, 255)

    if is_top_flat:
        pixels[:120] = 128

    Image.fromarray(pixels.astype(np.uint8)).convert(mode).save(path, quality=85, **options)

    return path


@pytest.fixture
def cover(tmp_path: Path) -> Path:
    return _photo(tmp_path / "photo.jpg")


def _ac(path: Path) -> npt.NDArray[np.int16]:
    return jpeg.read_coefficients(path).ac


def _regions(path: Path) -> trellis.Regions:
    return trellis.Regions.of(_ac(path).size)


@pytest.mark.parametrize("options", [ENCRYPTED, PLAIN], ids=["encrypted", "plain"])
def test_roundtrip_changes_coefficients_by_one(
    cover: Path, tmp_path: Path, options: HideOptions
) -> None:
    embedded = tmp_path / "embedded.jpg"
    service.hide(cover, FILE, embedded, options)

    assert np.abs(_ac(embedded) - _ac(cover)).max() == 1

    with Image.open(embedded) as image:
        assert image.format == "JPEG"

    password = options.password if options.is_encrypted else None
    result = service.reveal(embedded, tmp_path, password)
    assert result.path.read_bytes() == FILE.data
    assert (result.method, result.cipher) == (MethodId.DCT, options.cipher)


def _exif() -> bytes:
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: turned a quarter clockwise
    exif[0x010F] = "Camera"

    return exif.tobytes()


def _metadata(header: Header) -> list[Segment]:
    return [segment for segment in header.segments if segment.is_metadata]


def _without_jfif(path: Path) -> Path:
    data = path.read_bytes()
    jfif = 4 + int.from_bytes(data[4:6], "big")  # SOI, then APP0 with its length
    path.write_bytes(data[:2] + data[jfif:])

    return path


@pytest.mark.parametrize(
    ("mode", "options"),
    [
        ("RGB", {}),
        ("L", {}),
        ("RGB", {"subsampling": 0}),
        ("RGB", {"optimize": True}),
        (
            "RGB",
            {
                "exif": _exif(),
                "icc_profile": ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes(),
                "comment": b"A note",
            },
        ),
    ],
    ids=["standard-tables", "grey", "4:4:4", "optimized-tables", "metadata"],
)
def test_output_keeps_the_covers_header(tmp_path: Path, mode: str, options: dict[str, Any]) -> None:
    """Only the optimized Huffman tables differ: libjpeg optimizes them for the new
    coefficients."""
    cover = _photo(tmp_path / "photo.jpg", mode, **options)
    embedded = tmp_path / "embedded.jpg"
    service.hide(cover, FILE, embedded, ENCRYPTED)
    before, after = jpeg.header(cover), jpeg.header(embedded)

    assert [s.marker for s in after.segments] == [s.marker for s in before.segments]
    assert [s for s in after.segments if s.marker != 0xC4] == [
        s for s in before.segments if s.marker != 0xC4
    ]
    assert after.has_standard_tables is before.has_standard_tables
    assert abs(embedded.stat().st_size / cover.stat().st_size - 1) <= 0.02

    with Image.open(embedded) as image:
        image.load()

    assert service.reveal(embedded, tmp_path, PASSWORD).path.read_bytes() == FILE.data


def test_a_cover_without_jfif_does_not_gain_one(tmp_path: Path) -> None:
    cover = _without_jfif(_photo(tmp_path / "camera.jpg", exif=_exif()))
    embedded = tmp_path / "embedded.jpg"
    service.hide(cover, FILE, embedded, ENCRYPTED)
    found = _metadata(jpeg.header(embedded))

    assert found == _metadata(jpeg.header(cover))
    assert [segment.payload[:4] for segment in found] == [b"Exif"]


def test_stray_bytes_in_the_covers_header_are_skipped(tmp_path: Path) -> None:
    """libjpeg reads such a file with a warning, so hiding must not refuse it."""
    cover = _photo(tmp_path / "padded.jpg", comment=b"A note")
    data = cover.read_bytes()
    jfif = 4 + int.from_bytes(data[4:6], "big")
    cover.write_bytes(data[:jfif] + bytes(2) + data[jfif:])
    embedded = tmp_path / "embedded.jpg"
    service.hide(cover, FILE, embedded, ENCRYPTED)

    assert _metadata(jpeg.header(embedded)) == _metadata(jpeg.header(cover))
    assert service.reveal(embedded, tmp_path, PASSWORD).path.read_bytes() == FILE.data


def test_a_progressive_cover_becomes_baseline(tmp_path: Path) -> None:
    cover = _photo(tmp_path / "web.jpg", progressive=True, comment=b"A note")
    embedded = tmp_path / "embedded.jpg"
    service.hide(cover, FILE, embedded, ENCRYPTED)
    before, after = jpeg.header(cover), jpeg.header(embedded)

    assert before.is_progressive
    assert not after.is_progressive
    assert _metadata(after) == _metadata(before)
    assert after.component_ids == before.component_ids
    assert service.reveal(embedded, tmp_path, PASSWORD).path.read_bytes() == FILE.data


def test_largest_sealed_header_fills_the_head(cover: Path, tmp_path: Path) -> None:
    options = HideOptions(
        method=MethodId.DCT, cipher=CipherId.AES_CBC_HMAC, password=PASSWORD, is_research=True
    )
    service.hide(cover, FILE, tmp_path / "embedded.jpg", options)

    result = service.reveal(tmp_path / "embedded.jpg", tmp_path, PASSWORD)
    assert (result.path.read_bytes(), result.cipher) == (FILE.data, CipherId.AES_CBC_HMAC)


@pytest.mark.parametrize("options", [ENCRYPTED, PLAIN], ids=["encrypted", "plain"])
def test_payload_is_spread_over_the_whole_image(
    cover: Path, tmp_path: Path, options: HideOptions
) -> None:
    """Over the luminance: the colour components are coarser and calmer, so changes there
    cost more and are rare."""
    embedded = tmp_path / "embedded.jpg"
    service.hide(cover, FILE, embedded, options)
    luminance = jpeg.read_coefficients(cover).components[0]
    size = luminance.rows * luminance.columns * 63
    changed = np.flatnonzero(_ac(embedded)[:size] != _ac(cover)[:size])

    assert np.histogram(changed, bins=8, range=(0, size))[0].min() > 0


def test_components_carry_their_own_grids_and_steps(cover: Path) -> None:
    """A 320 × 240 cover with 4:2:0 chroma: the colour components share a table, which is
    coarser than the brightness one."""
    read = jpeg.read_coefficients(cover)
    luminance, blue, red = read.components

    assert [(c.rows, c.columns) for c in read.components] == [(30, 40), (15, 20), (15, 20)]
    assert sum(c.rows * c.columns for c in read.components) * 63 == read.ac.size
    assert np.array_equal(blue.steps, red.steps)
    assert blue.steps.sum() > luminance.steps.sum()


def test_changes_avoid_smooth_areas(tmp_path: Path) -> None:
    """The left half is smooth but not flat, so it holds half the coefficients and about a
    third of the non-zero ones. With equal costs it would take half the changes."""
    rng = np.random.default_rng(seed=2)
    pixels = 128 + rng.normal(0, 30, (240, 320))
    pixels[:, :160] = 128 + rng.normal(0, 3, (240, 160))
    cover = tmp_path / "halves.jpg"
    Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8)).save(cover, quality=95)
    service.hide(cover, FILE, tmp_path / "embedded.jpg", ENCRYPTED)
    before, after = _ac(cover), _ac(tmp_path / "embedded.jpg")
    is_left = (np.arange(before.size) // 63) % 40 < 20  # 40 blocks per row
    changed = before != after
    left_changes = np.count_nonzero(changed & is_left) / np.count_nonzero(changed)
    left_nonzero = np.count_nonzero((before != 0) & is_left) / np.count_nonzero(before)

    assert left_nonzero > 0.25
    assert left_changes < left_nonzero / 10


def test_changes_lean_towards_the_smoothed_picture(cover: Path, tmp_path: Path) -> None:
    """A step the way juniward.directions points costs juniward.POLARIZATION times the
    other, so most changes take it, raising a magnitude as well as lowering one."""
    service.hide(cover, FILE, tmp_path / "embedded.jpg", ENCRYPTED)
    before, after = _ac(cover), _ac(tmp_path / "embedded.jpg")
    directions = juniward.directions(jpeg.read_coefficients(cover))
    changed = (after != before) & (directions != 0)
    step = np.sign(after - before)

    assert np.mean(step[changed] == directions[changed]) > 0.65
    assert np.any(changed & (before != 0) & (np.abs(after) > np.abs(before)))


def test_no_step_goes_beyond_the_largest_magnitude(monkeypatch: pytest.MonkeyPatch) -> None:
    """Baseline JPEG codes no AC coefficient beyond ±1023. Both are odd: a step up flips the
    second bit of 1023, and a step down keeps that of −1023."""
    ac = np.array([dct.MAX_MAGNITUDE, -dct.MAX_MAGNITUDE, 1022, -1022], dtype=np.int16)
    coefficients = jpeg.Coefficients(ac, (), np.zeros(0, np.int16))
    monkeypatch.setattr(jpeg, "read_coefficients", lambda _path: coefficients)
    monkeypatch.setattr(juniward, "directions", lambda c: np.zeros(c.ac.size, np.int8))
    channel = DctChannel(Path("cover.jpg"))
    flip, keep = channel._plane_costs(ac, np.ones(4, np.float32), np.zeros(4, np.int8))

    assert flip.tolist() == [np.inf, 1, 1, 1]
    assert keep.tolist() == [1, np.inf, 1, 1]


def test_binary_coding_takes_the_cheaper_legal_step(monkeypatch: pytest.MonkeyPatch) -> None:
    """Either step flips the parity. At ±1023 only one stays in range, and the step the way
    juniward.directions points is cheaper. 1 is the step that flips the second bit, −1 the
    one that keeps it, 0 a tie."""
    ac = np.array([dct.MAX_MAGNITUDE, -dct.MAX_MAGNITUDE, 6, 5, 5, 4], dtype=np.int16)
    coefficients = jpeg.Coefficients(ac, (), np.zeros(0, np.int16))
    monkeypatch.setattr(jpeg, "read_coefficients", lambda _path: coefficients)
    monkeypatch.setattr(juniward, "directions", lambda c: np.zeros(c.ac.size, np.int8))
    channel = DctChannel(Path("cover.jpg"))
    costs = np.ones(ac.size, np.float32)
    directions = np.array([0, 0, 0, 1, -1, -1], dtype=np.int8)
    cheaper = trellis._cheaper_steps(ac, costs, directions, channel._plane_costs)
    toward = np.float32(juniward.POLARIZATION)

    assert costs.tolist() == [1, 1, 1, toward, toward, toward]
    assert cheaper.tolist() == [-1, 1, 0, 1, -1, 1]


def test_binary_coding_never_steps_beyond_a_lower_magnitude(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With Research's dct.mag at 100, 101 never changes, and 100 and −100 step inwards."""
    ac = np.array([101, 100, -100, -101], dtype=np.int16)
    coefficients = jpeg.Coefficients(ac, (), np.zeros(0, np.int16))
    monkeypatch.setattr(jpeg, "read_coefficients", lambda _path: coefficients)
    monkeypatch.setattr(juniward, "directions", lambda c: np.zeros(c.ac.size, np.int8))
    channel = DctChannel(Path("cover.jpg"), rules=dct.DctRules(max_magnitude=100))
    costs = np.ones(ac.size, np.float32)
    cheaper = trellis._cheaper_steps(ac, costs, np.zeros(ac.size, np.int8), channel._plane_costs)

    assert costs.tolist() == [np.inf, 1, 1, np.inf]
    assert cheaper.tolist()[1:3] == [1, -1]  # down from an even 100 flips the second bit


def test_binary_changes_go_towards_the_smoothed_picture(cover: Path, tmp_path: Path) -> None:
    """Each changed coefficient takes its cheaper step, which is the way
    juniward.directions points wherever it points one."""
    binary = replace(ENCRYPTED, settings=Settings.of({"stc.code": "binary"}))
    service.hide(cover, FILE, tmp_path / "embedded.jpg", binary)
    before, after = _ac(cover), _ac(tmp_path / "embedded.jpg")
    directions = juniward.directions(jpeg.read_coefficients(cover))
    changed = (after != before) & (directions != 0)

    assert np.count_nonzero(changed) > 100
    assert np.array_equal(np.sign(after - before)[changed], directions[changed])


def test_encrypted_payload_is_not_detected(cover: Path, tmp_path: Path) -> None:
    service.hide(cover, FILE, tmp_path / "embedded.jpg", ENCRYPTED)
    assert plain_method(tmp_path / "embedded.jpg") is None


def test_plain_payload_is_detected(cover: Path, tmp_path: Path) -> None:
    embedded = tmp_path / "embedded.jpg"
    service.hide(cover, FILE, embedded, PLAIN)
    assert plain_method(embedded) == MethodId.DCT
    assert plain_method(cover) is None


def test_lead_is_spread_over_the_image(tmp_path: Path) -> None:
    """In natural order the lead would lie in the flat top, where the salt would need
    changes at positions anyone can compute. Spread out, it leaves the flat top alone, and
    so does the stream: a change there costs far more than in the noisy bottom."""
    cover = _photo(tmp_path / "sky.jpg", is_top_flat=True)
    embedded = tmp_path / "embedded.jpg"
    service.hide(cover, FILE, embedded, ENCRYPTED)
    regions = _regions(cover)
    before, after = _ac(cover), _ac(embedded)
    flat_top = 13 * 40 * 63  # luminance block rows 0 … 12 of 40 blocks: away from the edge

    assert np.count_nonzero(before[: regions.lead]) == 0
    assert np.array_equal(after[:flat_top], before[:flat_top])


def _never_embed(*_args: object) -> stc.Units:
    raise AssertionError("embedding started")


@pytest.mark.parametrize("cipher_id", [CipherId.AES_GCM, CipherId.NONE])
def test_capacity_is_exact_at_the_cap(
    cover: Path, tmp_path: Path, cipher_id: CipherId, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel = DctChannel(cover)
    ac = _ac(cover)
    regions = _regions(cover)
    dry = np.count_nonzero(ac) - np.count_nonzero(ac[regions.indices(np.arange(regions.lead))])
    stream = dry * trellis.MAX_FILL_PERCENT // 100 // 8 - 6
    assert channel.capacity(kdf.SALT_SIZE) == stream
    assert channel.capacity(0) == trellis.LEAD_BYTES + stream
    assert channel.stored_limit(kdf.SALT_SIZE) == trellis.HEAD_BYTES + regions.tail // 8 > stream

    options = HideOptions(
        method=MethodId.DCT, cipher=cipher_id, password=PASSWORD, is_research=True
    )
    password = PASSWORD if options.is_encrypted else None
    limit = container.max_data_size(channel, FILE.name, cipher_id)
    fitting = HiddenFile(FILE.name, bytes(limit))
    service.hide(cover, fitting, tmp_path / "full.jpg", options)
    assert service.reveal(tmp_path / "full.jpg", tmp_path, password).size == limit

    monkeypatch.setattr(stc, "embed", _never_embed)

    with pytest.raises(CapacityError):
        service.hide(cover, HiddenFile(FILE.name, bytes(limit + 1)), tmp_path / "x.jpg", options)

    with pytest.raises(CapacityError):
        channel.write(bytes(kdf.SALT_SIZE), bytes(stream + 1), feistel.UNKEYED_SEED)


@pytest.mark.parametrize("is_written_in_chunks", [True, False], ids=["write", "read"])
def test_chunk_sizes_do_not_change_the_format(
    cover: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, is_written_in_chunks: bool
) -> None:
    """Large photos take several chunks; small ones make this cover take several too."""

    def small_chunks(patch: pytest.MonkeyPatch) -> None:
        patch.setattr(trellis, "_CHUNK", 1000)
        patch.setattr(stc, "_PLACE_CHUNK", 777)

    embedded = tmp_path / "embedded.jpg"

    with monkeypatch.context() as patch:
        if is_written_in_chunks:
            small_chunks(patch)

        service.hide(cover, FILE, embedded, ENCRYPTED)

    with monkeypatch.context() as patch:
        if not is_written_in_chunks:
            small_chunks(patch)

        assert service.reveal(embedded, tmp_path, PASSWORD).path.read_bytes() == FILE.data


def test_tiny_jpeg_holds_nothing(tmp_path: Path) -> None:
    tiny = tmp_path / "tiny.jpg"
    Image.effect_noise((32, 32), 60).save(tiny, quality=95)

    assert DctChannel(tiny).capacity(0) == DctChannel(tiny).stored_limit(0) == 0
    assert plain_method(tiny) is None

    with pytest.raises(CapacityError):
        service.hide(tiny, FILE, tmp_path / "embedded.jpg", ENCRYPTED)


def test_header_reads_never_order_the_whole_stream(
    cover: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reveal reads the plain frame first and costs header reads for every wrong password:
    neither may order the whole stream."""
    embedded = tmp_path / "embedded.jpg"
    service.hide(cover, FILE, embedded, ENCRYPTED)
    evaluated: list[int] = []
    at = feistel.Permutation.at

    def spy(self: feistel.Permutation, positions: npt.NDArray[np.integer]) -> stc.Units:
        evaluated.append(positions.size)

        return at(self, positions)

    monkeypatch.setattr(feistel.Permutation, "at", spy)

    head = _regions(cover).head

    assert plain_method(embedded) is None
    assert max(evaluated) <= head  # the masked plain frame starts in the unkeyed head

    with pytest.raises(NoHiddenDataError):
        service.reveal(embedded, tmp_path, "wrong")

    assert max(evaluated) <= head < _regions(cover).tail


def test_unreadable_result_raises_capacity_error(
    cover: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    embed = stc.embed
    monkeypatch.setattr(stc, "embed", lambda *args: embed(*args)[1:])

    with pytest.raises(CapacityError, match="can't hold this payload reliably"):
        service.hide(cover, FILE, tmp_path / "embedded.jpg", ENCRYPTED)

    assert not (tmp_path / "embedded.jpg").exists()


def test_grayscale_jpeg_works(tmp_path: Path) -> None:
    cover = _photo(tmp_path / "gray.jpg", mode="L")
    service.hide(cover, FILE, tmp_path / "embedded.jpg", ENCRYPTED)
    assert (
        service.reveal(tmp_path / "embedded.jpg", tmp_path, PASSWORD).path.read_bytes() == FILE.data
    )


def test_default_method_follows_the_output_format(
    cover: Path, secret_file: Path, tmp_path: Path
) -> None:
    """A JPEG cover only for DCT: image LSB refuses lossy covers unless allowed."""
    revealed = tmp_path / "revealed"
    revealed.mkdir()
    png = _photo(tmp_path / "cover.png")
    service.hide_file(png, secret_file, tmp_path / "out.jpg", PASSWORD, research=True)
    assert service.reveal(tmp_path / "out.jpg", revealed, PASSWORD).method == MethodId.DCT
    (revealed / secret_file.name).unlink()
    service.hide_file(png, secret_file, tmp_path / "out.png", PASSWORD, research=True)
    assert service.reveal(tmp_path / "out.png", revealed, PASSWORD).method == MethodId.LSB

    for carrier, output in ((cover, "out.png"), (png, "out.webp")):
        with pytest.raises(UnsupportedFormatError, match="Output must be one of"):
            service.hide_file(carrier, secret_file, tmp_path / output, PASSWORD, research=True)


def test_output_must_stay_jpeg(cover: Path, tmp_path: Path) -> None:
    assert service.output_suffixes(MethodId.DCT, cover) == (".jpg", ".jpeg")
    service.hide(cover, FILE, tmp_path / "embedded.jpeg", ENCRYPTED)  # either spelling

    with pytest.raises(UnsupportedFormatError, match=r"\.jpg"):
        service.hide(cover, FILE, tmp_path / "embedded.png", ENCRYPTED)

    assert service.hide_step_labels(ENCRYPTED, Path("x.JPEG"))[-1] == "Writing JPEG"


@pytest.mark.parametrize(
    ("name", "message"),
    [("cmyk.jpg", "CMYK JPEG is not supported"), ("fake.jpg", "not a readable JPEG")],
)
def test_unsupported_jpegs_are_rejected(tmp_path: Path, name: str, message: str) -> None:
    path = tmp_path / name

    if name == "cmyk.jpg":
        Image.new("CMYK", (64, 64)).save(path)
    else:
        Image.new("RGB", (64, 64)).save(path, format="PNG")

    with pytest.raises(UnsupportedFormatError, match=message):
        DctChannel(path)


@pytest.mark.filterwarnings("ignore::PIL.Image.DecompressionBombWarning")
@pytest.mark.parametrize("side", [10_000, 20_000], ids=["over-the-limit", "bomb"])
def test_huge_declared_dimensions_are_rejected_before_reading(cover: Path, side: int) -> None:
    data = bytearray(cover.read_bytes())
    size = data.index(bytes([0xFF, 0xC0])) + 5  # SOF0: marker, length, precision, height, width
    data[size : size + 4] = side.to_bytes(2, "big") * 2
    cover.write_bytes(bytes(data))

    with pytest.raises(UnsupportedFormatError, match="too large to read"):
        DctChannel(cover)


def test_only_a_jpeg_header_is_read_for_the_size(tmp_path: Path) -> None:
    """Pillow would open any of its formats by content, an EPS among them."""
    fake = tmp_path / "fake.jpg"
    fake.write_bytes(b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 64 64\n%%EndComments\n")

    with pytest.raises(UnsupportedFormatError, match="not a readable JPEG"):
        jpeg._check_size(fake)


def test_the_output_of_a_jpeg_with_several_pictures_holds_the_first(tmp_path: Path) -> None:
    """libjpeg writes only the main picture, so the index of the others goes too."""
    with Image.open(_photo(tmp_path / "photo.jpg")) as first:
        first.save(tmp_path / "pictures.jpg", "MPO", save_all=True, append_images=[first.copy()])

    service.hide(tmp_path / "pictures.jpg", FILE, tmp_path / "embedded.jpg", ENCRYPTED)

    with Image.open(tmp_path / "embedded.jpg") as embedded:
        assert (embedded.format, getattr(embedded, "n_frames", 1)) == ("JPEG", 1)

    segments = jpeg.header(tmp_path / "embedded.jpg").segments
    assert not any(s.payload.startswith(b"MPF" + bytes([0])) for s in segments)
    assert service.reveal(tmp_path / "embedded.jpg", tmp_path, PASSWORD).path.read_bytes() == (
        FILE.data
    )


def test_reveal_needs_the_jpeg_extra(
    cover: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    embedded = tmp_path / "embedded.jpg"
    service.hide(cover, FILE, embedded, ENCRYPTED)
    monkeypatch.setitem(sys.modules, "jpeglib", None)

    with pytest.raises(NotAvailableError, match="needs the jpeg extra"):
        service.reveal(embedded, tmp_path, PASSWORD)


def test_missing_jpeg_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(catalog.Requirement, "is_installed", property(lambda _self: False))
    assert service.unavailable_reason(ENCRYPTED) == (
        "DCT coefficients (JPEG) needs the jpeg extra, see Installation in README.md"
    )


# The two lowest bits of 2560 AC coefficients holding VECTOR_FILE, high bit first, plain and
# encrypted under test_container's VECTOR_PASSWORD and VECTOR_ARGON2 profile. They pin the
# unit numbering, the lead, the streams, the Feistel order, the bit planes and the code
# (docs/format.md#trellis-channels).
VECTOR_FILE = HiddenFile("a.txt", b"cipherstitch" * 8)
VECTOR_PASSWORD = "пароль-йї"  # noqa: S105
VECTOR_ARGON2 = {"ARGON2_MEMORY_KIB": 32, "ARGON2_ITERATIONS": 1}
VECTORS = {
    CipherId.NONE: (
        "0b9bfa650596b9fee69ac5177f8dbaa6a9bdb9d66de6ba72fa6be2aaefe99b55b5fadaa9aa79d676"
        "a555efd935e695babde5dafa9956f9d1d59e6ba66ef5bd599447fdd3de75ba599ffb6cab6979d59d"
        "7ea9bdf7f7fbe75a467ef57b577aaf7ff5e65759e56dc7e567b5bad6aabe956d175d77ffe35d5e99"
        "bd77e5cfe59b6ef6e569b9757da5a67a69bfeabd20797d9d276d7a6fde625db966b6eb695faa7bef"
        "ff1e67eeebbb79edebf7ea9bdad757dadf7e1bdefe69b61e5b9bedaeadabb65addb5869d7c55a66e"
        "ee5bbf91b97bbfebb8aaaafbe9575feaaf7beb62e9567ba2655b62b5e975eb56ad6ff77f5fadebd6"
        "6d8bf9f9ab6fae52a7ebbac56e9a777b7da7d69e6d7965dba55ee6e9f539f595eaa6faba66eefbb9"
        "ddd75dffab5aba5eb5a555ba9c77597ab7f95febb7d967ab6b7d7619576ce9efaffaa9dffa9d497f"
        "d6f579d9da672aaef9dab75ee595f97a9aed5f7afbecf58d7e7edb5bbb79bdfb9af55ba656aff7e9"
        "6aeedb75ea7ebdf975556dfd157abbe67d7979bb95bfebb5ffcfeff793beb778ff6ed9e2aa9b7a18"
        "a81afd6e66d6bdaeeeb7abb3b7ba2595bb9ba739fdfeef796b7fb75a69b95eb7e5da9e25a9fbde6e"
        "55ead65a5beaeefb5b9a56f4e7bcb9a9e57fa6b99a65f7bafbb778ec57eb5ed9bb9a6d39b5775bef"
        "d99dc97b5eedfcb4d5b956bb65aea9955a6b7eb597757eae6eb7deb67759596b6ddf7bcbf9ed99a9"
        "7b79957aea956b95676abf755de6bb6e5ff7ade3fab95e6f66da66a6bea57b77c5edd9e195bcee75"
        "c5ef699bd976d577fffd79ba679767fef6ba6795ba67546749d69b553af5cfdfdbadea967fa6e9b6"
        "61af5966aa9fd2f5faf58fbd9fd959bb9e56ff98ba1a568b55d96e6dd6ed5fb6ddedfe6f99557d52"
    ),
    CipherId.AES_GCM: (
        "579fba255597b9eee69ef4d76f99bb66a8b9b5d65ee6bab6fa7bdea7efe99b55b5fedaa9ea7ad677"
        "a569efd9b525a5aabde1dafad966e919d59e6ba66ef9bd5595b7fdd7de758a595ffb6da76579d69d"
        "7eaabd8af7ebe65b7a72357b5766a23ff5fb975de51df7e567b5bad6dbb2966dd75d77ffaf5d6e99"
        "7d78e5fce29b6efae529b9757d99b57969bfea7ded75bd9d676c3aeede665dbd67f6eb695fa67fdf"
        "efde67a2e8bbb9e9ebf7ea9bda97570adf6ddbdabe29b6da5f9bddaaa1bbb55addb59a9d7955a72d"
        "ef48bf95b977afaabdae66ebe9576faaaf7aeb75e9567bae694b66b6e975eb56ad6fc27f5fadebd6"
        "7d9ff939aaaf9e16a7bebad56aaa777b7d9ad51e616d75dba51ee6b9f9f9a595e9e5babb66fdfbaa"
        "ddd45dfe9b56ba5eb5a555ba9d77493ab7f99bebb7d967ab6bbd46da426fe9ffaffaa9df399d597e"
        "d5f579d9daa7e6eff9ebb74ee596f97aaaed5b7bfbefb6bd7ebedb5bfb4d8dfb96f55ba656bff6a9"
        "5aeedb75efb24dfa75556dfdd576bbe770794d7bd5bfeb45fffeaff79fbab779ff7ed9e6be9b7ad9"
        "ab5efd7e67cbfdaeeb87bba7a7ba65d5bb97a779fdeeef79a73fb75a69b85e77e59aaa65a9fbea6e"
        "55dad65a5ba6defb5baa57f5e3bdbda9f17fe6799a75f3b9fbb749ef57eb5dd9b89a6e49b57697ef"
        "d99df97a5ee9fdb7d08566bb65ef91955a5b4e7697757daa6ef7deb677695a6a69df7bfbf9ed99e9"
        "eaaa956aba956fd55b6eff755dd5fb6e9337edfefe795e6f66da26a6bea57b77452da9ed95afeeb6"
        "d5df296bd973c56bccfd7aba64d767fe86ba6715bb67966659d6eb157a35dfdfdbade6964fa6e5b6"
        "adbf5961a99ed6f736359fb193d92abb9e563c996adb567455d92e69d6fdacb6ed2dfaae9d55ae5e"
    ),
}


@pytest.mark.parametrize("cipher_id", list(VECTORS))
def test_format_vectors_stay_readable(cipher_id: CipherId, monkeypatch: pytest.MonkeyPatch) -> None:
    skip_unless_installed(cipher_id)

    for name, value in VECTOR_ARGON2.items():
        monkeypatch.setattr(kdf, name, value)

    bits = np.unpackbits(np.frombuffer(bytes.fromhex(VECTORS[cipher_id]), dtype=np.uint8))
    ac = (2 * bits[0::2] + bits[1::2]).astype(np.int16)
    monkeypatch.setattr(
        jpeg, "read_coefficients", lambda _path: jpeg.Coefficients(ac, (), np.zeros(0, np.int16))
    )
    candidates = (
        Candidate(MethodId.DCT, UnitOrder.KEYED),
        Candidate(MethodId.DCT, UnitOrder.PUBLIC),
    )
    revealed = container.reveal([(DctChannel(Path("vector.jpg")), candidates)], VECTOR_PASSWORD)

    assert revealed == container.Revealed(VECTOR_FILE, MethodId.DCT, cipher_id)


def test_values_are_the_ac_coefficients_of_y_then_cb_then_cr(tmp_path: Path) -> None:
    """Part of the stored format (docs/format.md#trellis-channels): each component's blocks
    row by row, and each block's 63 AC coefficients row by row as jpeglib holds them."""
    jpeglib = pytest.importorskip("jpeglib")
    y = np.zeros((2, 3, 8, 8), dtype=np.int16)  # 16 × 24 pixels in 4:2:0
    cb = np.zeros((1, 2, 8, 8), dtype=np.int16)
    cr = np.zeros((1, 2, 8, 8), dtype=np.int16)
    y[0, 1, 0, 1] = 5  # the second block's second coefficient
    y[1, 0, 1, 0] = 7  # the fourth block's first coefficient of its second row
    cb[0, 0, 0, 2] = 9
    cr[0, 1, 7, 7] = 11
    picture = jpeglib.from_dct(y, cb, cr, qt=np.ones((2, 8, 8), dtype=np.uint16))
    picture.width, picture.height = 24, 16
    picture.samp_factor = np.array([[2, 2], [1, 1], [1, 1]])
    picture.write_dct(str(tmp_path / "markers.jpg"))
    ac = jpeg.read_coefficients(tmp_path / "markers.jpg").ac

    assert ac.size == 10 * 63
    assert {int(i): int(ac[i]) for i in np.flatnonzero(ac)} == {
        63: 5,
        3 * 63 + 7: 7,
        6 * 63 + 1: 9,
        9 * 63 + 62: 11,
    }


def test_a_missing_or_unreadable_jpeg_raises_its_own_error(tmp_path: Path, cover: Path) -> None:
    """Every other medium lets a missing file's OSError through: only a broken file is "not a
    readable JPEG"."""
    with pytest.raises(FileNotFoundError):
        DctChannel(tmp_path / "gone.jpg")

    if getattr(os, "geteuid", lambda: 1)() == 0:
        pytest.skip("root reads any file")

    cover.chmod(0)

    try:
        with pytest.raises(PermissionError):
            DctChannel(cover)
    finally:
        cover.chmod(0o600)


def test_the_capacity_counts_only_coefficients_within_the_magnitude(cover: Path) -> None:
    """Research's ``dct.mag`` below 1023 leaves the coefficients beyond it unchanged, so they
    hold no bit. At the default nothing changes."""
    ac = _ac(cover)
    standard = DctChannel(cover)
    low = DctChannel(cover, rules=dct.DctRules(max_magnitude=2))

    assert np.array_equal(standard._counted(), ac != 0)
    assert standard.element_count == np.count_nonzero(ac)
    assert np.count_nonzero(np.abs(ac) > 2) > 0
    assert np.array_equal(low._counted(), (ac != 0) & (np.abs(ac) <= 2))
    assert low.capacity(16) < standard.capacity(16)
