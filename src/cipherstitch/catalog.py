"""What the product offers: carriers, embedding methods and ciphers, with their UI text
and the optional extras they need."""

import importlib.util
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePath

from cipherstitch.errors import NotAvailableError


class CarrierKind(StrEnum):
    IMAGE = "image"
    AUDIO = "audio"
    DOCUMENT = "document"
    TEXT = "text"


class MethodId(StrEnum):
    LSB = "lsb"
    DCT = "dct"
    AUDIO_LSB = "audio-lsb"
    PHASE_CODING = "phase-coding"
    ECHO_HIDING = "echo-hiding"
    OBJECT_STREAM = "object-stream"
    WHITESPACE = "whitespace"
    ZERO_WIDTH = "zero-width"
    LSB_REPLACEMENT = "lsb-replacement"


type MethodKey = str
"""Any method's id: a built-in's :class:`MethodId` or an extension's."""


class CipherId(StrEnum):
    """Also the HKDF info of each cipher's key: renaming a value breaks hidden data."""

    AES_GCM = "aes-256-gcm"
    CHACHA20 = "chacha20-poly1305"
    XCHACHA20 = "xchacha20-poly1305"
    AES_CBC_HMAC = "aes-256-cbc-hmac"
    FERNET = "fernet"
    NONE = "none"


type CipherKey = str
"""Any cipher's id: a built-in's :class:`CipherId` or an extension's."""


class Tier(StrEnum):
    RECOMMENDED = "Recommended"
    ACCEPTABLE = "Acceptable"
    NOT_RECOMMENDED = "Not recommended"
    EXPERIMENTAL = "Experimental"  # baselines for detector tests, in Research mode only


@dataclass(frozen=True, slots=True)
class Requirement:
    """An optional dependency: the module to import, and the CipherStitch extra that installs
    it or, for an extension's own dependency, the pip package (by default the module's name).
    """

    module: str
    extra: str | None = None
    package: str | None = None

    @property
    def is_installed(self) -> bool:
        return importlib.util.find_spec(self.module) is not None

    def install_hint(self, subject: str) -> str:
        """E.g. ``FLAC needs the audio extra, see Installation in README.md``, or
        ``Line-end spaces needs scipy: pip install scipy`` for a package. CipherStitch isn't
        on PyPI, so a pip command for an extra would fail or install someone else's package.
        """
        if self.extra is not None:
            return f"{subject} needs the {self.extra} extra, see Installation in README.md"

        package = self.package or self.module

        return f"{subject} needs {package}: pip install {package}"


@dataclass(frozen=True, slots=True)
class Method:
    id: MethodKey
    name: str
    tier: Tier
    summary: str
    pros: tuple[str, ...]
    cons: tuple[str, ...]
    capacity: str
    is_available: bool
    requires: Requirement | None = None
    extension: str | None = None  # the name of the extension it comes from


@dataclass(frozen=True, slots=True)
class Carrier:
    kind: CarrierKind
    label: str
    extensions: tuple[str, ...]
    methods: tuple[Method, ...]

    @property
    def is_available(self) -> bool:
        return any(method.is_available for method in self.methods)


@dataclass(frozen=True, slots=True)
class Cipher:
    id: CipherKey
    name: str
    tier: Tier
    summary: str
    pros: tuple[str, ...]
    cons: tuple[str, ...]
    is_available: bool
    requires: Requirement | None = None
    extension: str | None = None  # the name of the extension it comes from


PYTHON_DOCX = Requirement("docx", "documents")
JPEGLIB = Requirement("jpeglib", "jpeg")
REEDSOLO = Requirement("reedsolo", "ecc")
RAWPY = Requirement("rawpy", "raw")
RAW_EXTENSIONS = (".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".orf", ".rw2", ".pef", ".srw")

CARRIERS: tuple[Carrier, ...] = (
    Carrier(
        CarrierKind.IMAGE,
        "Image",
        (".png", ".bmp", ".tif", ".tiff", ".jpg", ".jpeg", ".webp", *RAW_EXTENSIONS),
        (
            Method(
                MethodId.LSB,
                "LSB",
                Tier.RECOMMENDED,
                "Bits spread over the lowest bits of the colour values.",
                (
                    "Highest capacity",
                    "Small payloads change few pixels",
                    "Secret order with a password",
                ),
                ("Saves as PNG, BMP or TIFF", "Re-saving destroys the data"),
                "up to 2.4 bits per pixel",
                is_available=True,
            ),
            Method(
                MethodId.DCT,
                "DCT coefficients (JPEG)",
                Tier.RECOMMENDED,
                "Hides in a JPEG's coefficients. A RAW, PNG or TIFF is compressed to JPEG first.",
                (
                    "A JPEG keeps its quality",
                    "Flat areas stay untouched",
                    "Secret order with a password",
                ),
                (
                    "Lower capacity than LSB",
                    "Slower on large photos",
                    "Re-saving destroys the data",
                ),
                "~10% of a JPEG's size",
                is_available=True,
                requires=JPEGLIB,
            ),
            Method(
                MethodId.LSB_REPLACEMENT,
                "Sequential LSB replacement",
                Tier.EXPERIMENTAL,
                "Classic baseline: overwrites the lowest bits from the first pixel on.",
                ("Reference point for detector tests",),
                (
                    "Caught by WS, SPA and chi-square tests",
                    "Changes cluster at the top of the image",
                    "Saves as PNG, BMP or TIFF",
                ),
                "3 bits per pixel",
                is_available=True,
            ),
        ),
    ),
    Carrier(
        CarrierKind.AUDIO,
        "Audio",
        (".wav", ".flac"),
        (
            Method(
                MethodId.AUDIO_LSB,
                "Audio LSB",
                Tier.RECOMMENDED,
                "Bits spread over the lowest bits of the samples.",
                (
                    "Highest audio capacity",
                    "Inaudible at 16 or 24 bits",
                    "Small payloads change few samples",
                    "Secret order with a password",
                ),
                (
                    "Converting to MP3, AAC or Ogg destroys the data",
                    "Digital silence gives it away",
                ),
                "up to 0.8 bit per sample",
                is_available=True,
            ),
            Method(
                MethodId.PHASE_CODING,
                "Phase coding",
                Tier.ACCEPTABLE,
                "Bits set the phase of frequency bins.",
                ("Phase changes are hard to hear", "More capacity than echo hiding"),
                ("Low capacity", "Detectable by anyone who knows the method"),
                "~690 bit/s at 44.1 kHz",
                is_available=True,
                requires=REEDSOLO,
            ),
            Method(
                MethodId.ECHO_HIDING,
                "Echo hiding",
                Tier.ACCEPTABLE,
                "Faint 1 or 1.5 ms echoes encode the bits.",
                ("Echoes blend into the sound",),
                (
                    "Very low capacity",
                    "Fails on near silence",
                    "Detectable by anyone who knows the method",
                ),
                "~40 bit/s at 44.1 kHz",
                is_available=True,
                requires=REEDSOLO,
            ),
        ),
    ),
    Carrier(
        CarrierKind.DOCUMENT,
        "Document",
        (".pdf", ".docx"),
        (
            Method(
                MethodId.OBJECT_STREAM,
                "Embedded object stream",
                Tier.ACCEPTABLE,
                "The file is stored whole as one extra object.",
                ("Pages and text are untouched", "Far more capacity than whitespace"),
                ("Visible to anyone who inspects the file",),
                "up to 64 KiB",
                is_available=True,
            ),
            Method(
                MethodId.WHITESPACE,
                "Whitespace padding",
                Tier.ACCEPTABLE,
                "Invisible spaces at the end of paragraphs.",
                ("No extra parts in the file",),
                ("DOCX only", "Unusual whitespace can be noticed"),
                "8 bytes per paragraph",
                is_available=True,
                requires=PYTHON_DOCX,
            ),
        ),
    ),
    Carrier(
        CarrierKind.TEXT,
        "Text",
        (".txt", ".md"),
        (
            Method(
                MethodId.ZERO_WIDTH,
                "Zero-width characters",
                Tier.ACCEPTABLE,
                "Invisible characters between words and letters.",
                ("The text looks the same in most apps",),
                ("Many editors and web forms strip them", "Visible to anyone who looks for them"),
                "2 bits per insertion point",
                is_available=True,
            ),
        ),
    ),
)

CIPHERS: tuple[Cipher, ...] = (
    Cipher(
        CipherId.AES_GCM,
        "AES-256-GCM",
        Tier.RECOMMENDED,
        "Industry standard, hardware-accelerated.",
        ("NIST standard, widely audited", "Smallest size overhead"),
        (),
        is_available=True,
    ),
    Cipher(
        CipherId.CHACHA20,
        "ChaCha20-Poly1305",
        Tier.RECOMMENDED,
        "Fast on any CPU, IETF standard.",
        ("Fast without AES instructions", "Smallest size overhead"),
        (),
        is_available=True,
    ),
    Cipher(
        CipherId.XCHACHA20,
        "XChaCha20-Poly1305",
        Tier.RECOMMENDED,
        "ChaCha20 with 192-bit nonces, from libsodium.",
        ("As fast as ChaCha20",),
        ("More overhead, no gain here",),
        is_available=True,
        requires=Requirement("nacl", "crypto-extra"),
    ),
    Cipher(
        CipherId.AES_CBC_HMAC,
        "AES-256-CBC + HMAC",
        Tier.ACCEPTABLE,
        "Older encrypt-then-MAC construction.",
        ("Only AES and HMAC-SHA-256",),
        ("Largest size overhead", "No gain over AES-256-GCM"),
        is_available=True,
    ),
    Cipher(
        CipherId.FERNET,
        "Fernet",
        Tier.ACCEPTABLE,
        "AES-128-CBC + HMAC as a Fernet token.",
        ("Well-known token format",),
        ("128-bit AES key", "Largest size overhead"),
        is_available=True,
    ),
    Cipher(
        CipherId.NONE,
        "No encryption",
        Tier.NOT_RECOMMENDED,
        "Hidden, but readable by anyone who finds it.",
        ("Reveals without a password",),
        (
            "Anyone who finds the payload can read it",
            "Easier to find, with no secret order",
            "No tamper detection",
        ),
        is_available=True,
    ),
)

DEFAULT_CIPHER = CipherId.AES_GCM


def carrier(kind: CarrierKind) -> Carrier:
    return next(item for item in CARRIERS if item.kind == kind)


_BUILTIN_METHODS = frozenset(item.id for carrier in CARRIERS for item in carrier.methods)
_BUILTIN_CIPHERS = frozenset(item.id for item in CIPHERS)


def is_builtin_method(method_id: MethodKey) -> bool:
    """Standard mode asks this before any lookup: :func:`method` loads the extensions for an
    id that isn't a built-in's (docs/architecture.md#extensions)."""
    return method_id in _BUILTIN_METHODS


def is_builtin_cipher(cipher_id: CipherKey) -> bool:
    """Standard mode asks this before any lookup, as :func:`is_builtin_method`."""
    return cipher_id in _BUILTIN_CIPHERS


def cipher(cipher_id: CipherKey) -> Cipher:
    """A built-in cipher, or else an extension's, which loads the extensions."""
    found = next((item for item in CIPHERS if item.id == cipher_id), None)

    if found is None:
        found = _extension_cipher(cipher_id)

    if found is None:
        raise NotAvailableError(f"Unknown cipher {cipher_id}")

    return found


def method(method_id: MethodKey) -> Method:
    """A built-in method, or else an extension's, which loads the extensions."""
    methods = (item for carrier in CARRIERS for item in carrier.methods)
    found = next((item for item in methods if item.id == method_id), None)

    if found is None:
        found = _extension_method(method_id)

    if found is None:
        raise NotAvailableError(f"Unknown method {method_id}")

    return found


def _extension_method(method_id: MethodKey) -> Method | None:
    from cipherstitch import extension_loader  # it builds on this module

    loaded = extension_loader.find_method(method_id)

    return None if loaded is None else loaded.entry


def _extension_cipher(cipher_id: CipherKey) -> Cipher | None:
    from cipherstitch import extension_loader  # it builds on this module

    loaded = extension_loader.find_cipher(cipher_id)

    return None if loaded is None else loaded.entry


FORMAT_REQUIREMENTS = {
    ".flac": Requirement("soundfile", "audio"),
    ".pdf": Requirement("pikepdf", "documents"),
    **dict.fromkeys(RAW_EXTENSIONS, RAWPY),
}


def carrier_for_path(path: PurePath) -> Carrier | None:
    """The carrier whose extensions include the suffix of ``path``, if any."""
    suffix = path.suffix.lower()

    return next((item for item in CARRIERS if suffix in item.extensions), None)
