"""Golden outputs at the default settings: Standard, and Research left at its defaults, must hide
exactly the same way. The digests are of what a reader reads (pixels, samples, coefficients,
the stored stream, the text), not of file bytes, which zlib and libjpeg versions may change.
A deliberate change to a writer regenerates them.

They run on macOS arm64 only, where they were made: numpy's float SIMD paths may round the
costs a little differently on another CPU, and the codes then pick other, equally cheap steps.
"""

import hashlib
import os
import platform
import secrets
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from cipherstitch import service
from cipherstitch.catalog import CarrierKind, CipherId, MethodId
from cipherstitch.media import audio, docx, jpeg, pdf
from cipherstitch.media.audio import Audio, AudioInfo
from cipherstitch.methods import object_stream
from cipherstitch.params.settings import Settings
from cipherstitch.payload import HiddenFile

from .conftest import skip_unless_installed, skip_unless_method_installed

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or platform.machine() != "arm64",
    reason="golden digests are made on macOS arm64",
)

PASSWORD = "golden"  # noqa: S105
# Research at Standard's engine settings, every method and cipher: only the password minimum,
# a Guidance setting that writes nothing, is lowered for the pinned password.
RESEARCH = Settings.of({"crypto.minpw": 0})
STANDARD_CIPHERS = (CipherId.AES_GCM, CipherId.CHACHA20, CipherId.XCHACHA20, CipherId.NONE)
OTHER_CIPHERS = (CipherId.AES_GCM, CipherId.NONE)
FRAMES = 8000
SLOW_RATE = 22050  # echo hiding carries ~40 bits a second: a minute holds one codeword
SLOW_SECONDS = 60
DIGEST_HEX = 32  # 128 bits
WORDS = ["a", "manuscript", "keeps", "traces", "of", "what", "was", "written", "before"]


@dataclass(frozen=True)
class Case:
    method: MethodId
    carrier: CarrierKind
    cover: str  # a name in COVERS
    output: str  # the output's suffix
    payload_size: int


def _image(path: Path) -> None:
    rng = np.random.default_rng(seed=7)
    pixels = rng.integers(0, 256, size=(96, 128, 3), dtype=np.uint8)
    pixels[:16] = 128  # a flat band: wet costs
    Image.fromarray(pixels).save(path)


def _photo(path: Path) -> None:
    rng = np.random.default_rng(seed=1)
    gradient = np.linspace(0, 255, 320)[None, :] + np.linspace(0, 255, 240)[:, None]
    pixels = np.clip(gradient[..., None] / 2 + rng.normal(0, 30, (240, 320, 3)), 0, 255)
    image = Image.fromarray(pixels.astype(np.uint8))
    image.save(path, quality=85)  # PNG has no quality: only the JPEG takes it


def _noise(path: Path) -> None:
    rng = np.random.default_rng(seed=3)
    samples = rng.integers(-(1 << 15), 1 << 15, size=(FRAMES, 2), dtype=np.int32)
    samples[:2000] = 0  # digital silence
    audio.write(path, Audio(AudioInfo(FRAMES, 2, FRAMES, 16), samples))


def _music(path: Path) -> None:
    rng = np.random.default_rng(seed=SLOW_RATE)
    t = np.arange(SLOW_SECONDS * SLOW_RATE) / SLOW_RATE
    tones = sum(
        np.sin(2 * np.pi * frequency * t + phase) * amplitude
        for frequency, phase, amplitude in zip(
            rng.uniform(80, 2000, 12),
            rng.uniform(0, 6.3, 12),
            rng.uniform(0.02, 0.08, 12),
            strict=True,
        )
    )
    mono = tones * (0.6 + 0.4 * np.sin(2 * np.pi * 0.3 * t)) + rng.normal(0, 0.01, t.size)
    samples = np.stack([mono, mono * 0.9], axis=1) * 26000
    info = AudioInfo(t.size, 2, SLOW_RATE, 16)
    audio.write(path, Audio(info, samples.round().astype(np.int32)))


def _pdf(path: Path) -> None:
    pikepdf = pytest.importorskip("pikepdf")
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.pages[0].Contents = pdf.make_stream(b"BT /F1 12 Tf 72 720 Td (Hello) Tj ET")
    pdf.save(path)


def _docx(path: Path) -> None:
    docx = pytest.importorskip("docx")
    document = docx.Document()

    for index in range(120):
        document.add_paragraph(f"Paragraph {index}: a manuscript keeps traces.")

    document.save(str(path))


def _text(path: Path) -> None:
    rng = np.random.default_rng(seed=5)
    path.write_text(" ".join(rng.choice(WORDS, size=1500)) + "\n", encoding="utf-8")


COVERS: dict[str, Callable[[Path], None]] = {
    "image.png": _image,
    "photo.jpg": _photo,
    "photo.png": _photo,
    "noise.wav": _noise,
    "music.wav": _music,
    "cover.pdf": _pdf,
    "cover.docx": _docx,
    "cover.txt": _text,
}

CASES = {
    "lsb": Case(MethodId.LSB, CarrierKind.IMAGE, "image.png", ".png", 400),
    "audio-lsb": Case(MethodId.AUDIO_LSB, CarrierKind.AUDIO, "noise.wav", ".wav", 400),
    "dct": Case(MethodId.DCT, CarrierKind.IMAGE, "photo.jpg", ".jpg", 300),
    "dct-precover": Case(MethodId.DCT, CarrierKind.IMAGE, "photo.png", ".jpg", 300),
    "phase-coding": Case(MethodId.PHASE_CODING, CarrierKind.AUDIO, "music.wav", ".wav", 40),
    "echo-hiding": Case(MethodId.ECHO_HIDING, CarrierKind.AUDIO, "music.wav", ".wav", 40),
    "object-pdf": Case(MethodId.OBJECT_STREAM, CarrierKind.DOCUMENT, "cover.pdf", ".pdf", 300),
    "object-docx": Case(MethodId.OBJECT_STREAM, CarrierKind.DOCUMENT, "cover.docx", ".docx", 300),
    "whitespace": Case(MethodId.WHITESPACE, CarrierKind.DOCUMENT, "cover.docx", ".docx", 200),
    "zero-width": Case(MethodId.ZERO_WIDTH, CarrierKind.TEXT, "cover.txt", ".txt", 200),
}
STANDARD_CASES = ("lsb", "audio-lsb", "dct", "dct-precover")

DIGESTS: dict[str, str] = {
    "lsb/aes-256-gcm": "9e8d817d161e2559a3ae5b9354021646",
    "lsb/chacha20-poly1305": "f8e820bdd1cf47003828bb98858f11f5",
    "lsb/xchacha20-poly1305": "ac2cfcc220bf1efa0856748f93ca7b9b",
    "lsb/none": "a895706b94d1515c4df5f048b091e3d1",
    "audio-lsb/aes-256-gcm": "1b32501bbd9f117e78385eb3c52202b8",
    "audio-lsb/chacha20-poly1305": "072737ff79fe79ee96765794ed157dd9",
    "audio-lsb/xchacha20-poly1305": "e51ee5c1b564dfce6112d5db162ea565",
    "audio-lsb/none": "4d8cfbad764fdcb5baf26e0c51fe0386",
    "dct/aes-256-gcm": "5c03c9d1d9237c8876a0825c27f52ae7",
    "dct/chacha20-poly1305": "e839731c6f73f9f50290935925dbf83c",
    "dct/xchacha20-poly1305": "ed7816b45b9d89735ca616cb033cc1d5",
    "dct/none": "90630d877ea8241e69dbb6a5397e2f59",
    "dct-precover/aes-256-gcm": "98915ba65cacb90f00576a19184620ba",
    "dct-precover/chacha20-poly1305": "aa3be2e1b947b5ad15e13e0bb576398a",
    "dct-precover/xchacha20-poly1305": "2a780a0ea07de42de1f8f99cfdbe724c",
    "dct-precover/none": "4c3450d29c28d0587ba7a5a4c4b40082",
    "phase-coding/aes-256-gcm": "750482591ca957fa266cad1b77c3f5bb",
    "phase-coding/none": "48c9ced090a1d1bd95cfd15f45b91c91",
    "echo-hiding/aes-256-gcm": "43679b92620c5f8286fedb25e8b647ae",
    "echo-hiding/none": "e05aa90275f95971000d437b76b2724d",
    "object-pdf/aes-256-gcm": "76522a149a17b77e3823b6cebc7581b7",
    "object-pdf/none": "0b6a681947c0d27055d6d7294fffae74",
    "object-docx/aes-256-gcm": "e03f0a0040777fed8da5817ed676ce20",
    "object-docx/none": "9cf691c34dd44a92b8ab45f52faa23c6",
    "whitespace/aes-256-gcm": "d15986d456090784b7f333f68cd79743",
    "whitespace/none": "f9548c537b38555be55187e3b9fdbd85",
    "zero-width/aes-256-gcm": "4de31210c949ef478833dc33742e2ce5",
    "zero-width/none": "a4879b35d70bc0ce6415a83f6cfe0486",
}


def _params() -> Iterator[tuple[str, CipherId]]:
    for name in CASES:
        for cipher in STANDARD_CIPHERS if name in STANDARD_CASES else OTHER_CIPHERS:
            yield name, cipher


def _read_values(case: Case, path: Path) -> bytes:
    """What the case's reader reads from ``path``."""
    if case.method == MethodId.LSB:
        with Image.open(path) as image:
            return np.asarray(image).tobytes()

    if case.carrier == CarrierKind.AUDIO:
        return audio.read(path).samples.tobytes()

    if case.method == MethodId.DCT:
        coefficients = jpeg.read_coefficients(path)

        return coefficients.ac.tobytes() + coefficients.dc.tobytes()

    if case.method == MethodId.OBJECT_STREAM:
        reader = pdf.read_data if path.suffix == ".pdf" else docx.read_part

        return reader(path, object_stream.SOFT_LIMIT)

    if case.method == MethodId.WHITESPACE:
        python_docx = pytest.importorskip("docx")
        document = python_docx.Document(str(path))

        return "\n".join(paragraph.text for paragraph in document.paragraphs).encode()

    return path.read_bytes()


def fixed_randomness(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every random byte the writers take, from a counter instead."""
    counter = iter(range(1 << 30))

    def random_bytes(size: int | None = None) -> bytes:
        seed = next(counter).to_bytes(8, "big")

        return hashlib.shake_256(seed).digest(32 if size is None else size)

    def below(limit: int) -> int:
        return int.from_bytes(random_bytes(8)) % limit

    monkeypatch.setattr(os, "urandom", random_bytes)
    monkeypatch.setattr(secrets, "token_bytes", random_bytes)
    monkeypatch.setattr(secrets, "randbelow", below)


def digest(name: str, cipher: CipherId, directory: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """The digest of what a reader reads after hiding case ``name`` with ``cipher``."""
    case = CASES[name]
    skip_unless_method_installed(case.method)
    skip_unless_installed(cipher)
    cover = directory / case.cover

    if not cover.exists():
        COVERS[case.cover](cover)

    fixed_randomness(monkeypatch)
    payload = HiddenFile("golden.bin", hashlib.shake_256(name.encode()).digest(case.payload_size))
    password = None if cipher == CipherId.NONE else PASSWORD
    options = service.HideOptions(
        case.carrier, case.method, cipher, password, is_research=True, settings=RESEARCH
    )
    output = directory / f"{name}-{cipher}{case.output}"
    service.hide(cover, payload, output, options)

    return hashlib.sha256(_read_values(case, output)).hexdigest()[:DIGEST_HEX]


@pytest.fixture(scope="module")
def covers(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("golden")


@pytest.mark.parametrize(("name", "cipher"), list(_params()))
def test_default_settings_hide_as_before(
    name: str, cipher: CipherId, covers: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert digest(name, cipher, covers, monkeypatch) == DIGESTS[f"{name}/{cipher}"]
