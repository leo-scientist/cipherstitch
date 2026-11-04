import itertools
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from cipherstitch import catalog, ciphers, container, kdf, payload
from cipherstitch.catalog import CipherId, MethodId, MethodKey
from cipherstitch.container import Candidate, UnitOrder
from cipherstitch.errors import CapacityError, CipherStitchError, NoHiddenDataError
from cipherstitch.media.image import EmbeddedImage
from cipherstitch.methods import feistel
from cipherstitch.methods.lsb import ImageChannel
from cipherstitch.methods.stream import StreamChannel
from cipherstitch.payload import HiddenFile

from .conftest import ImageFactory, skip_unless_installed, use_extensions

FILE = HiddenFile(name="таємниця.txt", data=b"top secret " * 30)
PASSWORD = "secret"  # noqa: S105
CANDIDATES = tuple(Candidate(MethodId.LSB, order) for order in UnitOrder)
ENCRYPTING = [cipher_id for cipher_id in CipherId if cipher_id != CipherId.NONE]
ALLOWED = [
    *((cipher_id, UnitOrder.KEYED) for cipher_id in ENCRYPTING),
    *((cipher_id, UnitOrder.PUBLIC) for cipher_id in CipherId),
    *((cipher_id, UnitOrder.SEQUENTIAL) for cipher_id in ENCRYPTING),
]
_NAMES = itertools.count()


@pytest.fixture
def cover(make_image: ImageFactory) -> Path:
    return make_image(size=(200, 150))


def _save(embedded: EmbeddedImage, near: Path) -> Path:
    path = near.with_name(f"embedded-{next(_NAMES)}.png")
    embedded.save(path)

    return path


def _hide(
    cover: Path,
    file: HiddenFile = FILE,
    method: MethodId = MethodId.LSB,
    cipher_id: CipherId = CipherId.AES_GCM,
    password: str | None = PASSWORD,
    *,
    order: UnitOrder | None = None,
) -> Path:
    embedded = container.hide(
        ImageChannel(cover),
        file,
        method=method,
        cipher_id=cipher_id,
        password=password,
        order=_default_order(cipher_id) if order is None else order,
    )

    return _save(embedded, cover)


def _default_order(cipher_id: CipherId) -> UnitOrder:
    return UnitOrder.PUBLIC if cipher_id == CipherId.NONE else UnitOrder.KEYED


def _reveal(path: Path, password: str | None) -> container.Revealed:
    return container.reveal([(ImageChannel(path), CANDIDATES)], password)


@pytest.mark.parametrize(("cipher_id", "order"), ALLOWED)
def test_roundtrip_for_every_cipher_and_order(
    cover: Path, cipher_id: CipherId, order: UnitOrder
) -> None:
    skip_unless_installed(cipher_id)
    password = None if cipher_id == CipherId.NONE else "secret"
    embedded = _hide(cover, cipher_id=cipher_id, password=password, order=order)
    assert _reveal(embedded, password) == container.Revealed(FILE, MethodId.LSB, cipher_id)


def test_password_typed_for_a_plain_payload_still_reveals_it(cover: Path) -> None:
    embedded = _hide(cover, cipher_id=CipherId.NONE, password=None)
    assert _reveal(embedded, "anything").file == FILE


@pytest.mark.parametrize("order", list(UnitOrder))
def test_encrypted_image_has_no_readable_marker(cover: Path, order: UnitOrder) -> None:
    channel = ImageChannel(_hide(cover, order=order))
    stream = channel.read(0, channel.capacity(0), None)
    assert container.MAGIC not in stream
    assert FILE.name.encode() not in stream
    assert _plain_method(channel) is None


@pytest.mark.parametrize("password", [None, "guess"], ids=["no-password", "wrong-password"])
def test_encrypted_data_is_indistinguishable_from_clean_image(
    cover: Path, password: str | None
) -> None:
    embedded = _hide(cover)

    with pytest.raises(NoHiddenDataError) as from_embedded:
        _reveal(embedded, password)

    with pytest.raises(NoHiddenDataError) as from_clean:
        _reveal(cover, password)

    assert str(from_embedded.value) == str(from_clean.value)


def test_the_sequential_seed_never_keys_an_order_or_a_subkey(
    cover: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Research's sequential order reaches the channels as SEQUENTIAL_SEED. A permutation or
    a subkey keyed by it would put the units in a keyed order without a word. Trying that
    order costs no key derivation of its own."""
    seeds: list[bytes] = []
    subkey_inputs: list[bytes | str] = []
    derivations: list[bytes] = []
    permutation_init, subkey, master_key = feistel.Permutation.__init__, kdf.subkey, kdf.master_key

    def recorded_init(
        permutation: feistel.Permutation, seed: bytes, count: int, rounds: int = feistel.ROUNDS
    ) -> None:
        seeds.append(seed)
        permutation_init(permutation, seed, count, rounds)

    def recorded_subkey(master: bytes, purpose: str, size: int = 32) -> bytes:
        subkey_inputs.extend((master, purpose))

        return subkey(master, purpose, size)

    def recorded_master_key(password: str, salt: bytes, profile: kdf.Profile | None) -> bytes:
        derivations.append(salt)

        return master_key(password, salt, profile)

    monkeypatch.setattr(feistel.Permutation, "__init__", recorded_init)
    monkeypatch.setattr(kdf, "subkey", recorded_subkey)
    monkeypatch.setattr(kdf, "master_key", recorded_master_key)
    revealed = _reveal(_hide(cover, order=UnitOrder.SEQUENTIAL), PASSWORD)

    assert revealed == container.Revealed(FILE, MethodId.LSB, CipherId.AES_GCM)
    assert seeds
    assert container.SEQUENTIAL_SEED not in seeds
    assert container.SEQUENTIAL_SEED not in subkey_inputs
    assert len(derivations) == 2  # one to hide, one to reveal


@pytest.mark.parametrize("cipher_id", [CipherId.AES_GCM, CipherId.NONE])
def test_data_is_scattered_over_the_whole_image(cover: Path, cipher_id: CipherId) -> None:
    password = None if cipher_id == CipherId.NONE else PASSWORD
    path = _hide(cover, cipher_id=cipher_id, password=password)

    with Image.open(path) as embedded, Image.open(cover) as original:
        changed = np.any(np.asarray(embedded) != np.asarray(original), axis=(1, 2))
        assert np.nonzero(changed)[0].min() < original.height * 0.1
        assert np.nonzero(changed)[0].max() > original.height * 0.9


def _plain_method(channel: ImageChannel) -> MethodKey | None:
    """The method a reveal without a password finds, or ``None``."""
    try:
        return container.reveal([(channel, CANDIDATES)]).method
    except NoHiddenDataError:
        return None


def test_a_plain_payload_reveals_without_a_password(cover: Path) -> None:
    embedded = _hide(cover, cipher_id=CipherId.NONE, password=None)
    assert _plain_method(ImageChannel(embedded)) == MethodId.LSB
    assert _plain_method(ImageChannel(cover)) is None


def test_plain_frame_must_name_a_public_candidate(cover: Path) -> None:
    """A plain frame is in the public order, so keyed and sequential candidates never hold
    one."""
    embedded = _hide(cover, cipher_id=CipherId.NONE, password=None)
    others = (
        Candidate(MethodId.LSB, UnitOrder.KEYED),
        Candidate(MethodId.LSB, UnitOrder.SEQUENTIAL),
    )

    with pytest.raises(NoHiddenDataError):
        container.reveal([(ImageChannel(embedded), others)], None)


def _masked_frame(cover: Path, version: int, length: int) -> Path:
    """A plain frame for an empty file ``a``, masked as the container masks it."""
    nonce = bytes(container.NONCE_SIZE)
    frame = container.MAGIC + bytes([version, 1]) + length.to_bytes(4, "big")
    record = bytes([0, 1]) + b"a"
    embedded = ImageChannel(cover).write(nonce, container._mask(nonce, frame + record), None)

    return _save(embedded, cover)


def test_masked_plain_frame_is_found(cover: Path) -> None:
    revealed = _reveal(_masked_frame(cover, container.VERSION, 3), None)

    assert revealed.file == HiddenFile("a", b"")


@pytest.mark.parametrize(
    ("version", "length"),
    [(container.VERSION + 1, 3), (container.VERSION, 2**31)],
    ids=["unknown-version", "length-beyond-carrier"],
)
def test_invalid_plain_frame_is_not_found(cover: Path, version: int, length: int) -> None:
    with pytest.raises(NoHiddenDataError):
        _reveal(_masked_frame(cover, version, length), None)


def test_plain_payload_has_no_readable_marker(cover: Path) -> None:
    """Masked with a fresh nonce: no magic, no name, and no two payloads alike."""
    streams = []

    for _ in range(2):
        embedded = _hide(cover, cipher_id=CipherId.NONE, password=None)
        channel = ImageChannel(embedded)
        nonce = channel.read(0, container.NONCE_SIZE, None)
        body = channel.read(container.NONCE_SIZE, channel.capacity(container.NONCE_SIZE), None)
        streams.append(nonce + body)

        assert _plain_method(channel) == MethodId.LSB

    for stream in streams:
        assert container.MAGIC not in stream
        assert FILE.name.encode() not in stream
        assert b"top secret" not in stream

    assert streams[0][:64] != streams[1][:64]


def test_an_unmasked_plain_frame_reveals_only_unmasked(cover: Path) -> None:
    """Research's frame.mask off: the signature and the record are readable after the
    nonce."""
    embedded = container.hide(
        ImageChannel(cover),
        FILE,
        method=MethodId.LSB,
        cipher_id=CipherId.NONE,
        password=None,
        order=UnitOrder.PUBLIC,
        is_masked=False,
    )
    path = _save(embedded, cover)
    channel = ImageChannel(path)
    stream = channel.read(container.NONCE_SIZE, 64, None)
    revealed = container.reveal([(channel, CANDIDATES)], is_masked=False)

    assert stream.startswith(container.MAGIC)
    assert FILE.name.encode() in stream
    assert revealed == container.Revealed(FILE, MethodId.LSB, CipherId.NONE)

    with pytest.raises(NoHiddenDataError):
        _reveal(path, None)


def test_header_must_name_the_candidate_method(cover: Path) -> None:
    """An authentic header naming another method is not that candidate's payload."""
    embedded = _hide(cover, method=MethodId.DCT)

    with pytest.raises(NoHiddenDataError):
        _reveal(embedded, PASSWORD)


def test_record_modified_after_hiding_is_rejected(cover: Path) -> None:
    channel = ImageChannel(_hide(cover, order=UnitOrder.PUBLIC))
    body_size = 23 + len(FILE.name.encode()) + 2 + len(FILE.data) + 16
    salt = channel.read(0, kdf.SALT_SIZE, None)
    body = bytearray(channel.read(kdf.SALT_SIZE, body_size, None))
    body[-1] ^= 1

    with pytest.raises(NoHiddenDataError):
        _reveal(_save(channel.write(salt, bytes(body), None), cover), "secret")


@pytest.mark.parametrize(
    ("tiny", "is_header_sized"),
    [(HiddenFile("a", b"b"), False), (HiddenFile("a.txt", b""), True)],
    ids=["another-size", "the-headers-size"],
)
def test_swapped_fernet_blocks_are_not_found(
    cover: Path, tiny: HiddenFile, *, is_header_sized: bool
) -> None:
    """Fernet ignores the block counter: a tiny record seals to a header-sized block. A 4-byte
    record fails the header's size check, a 7-byte one its version byte: a record starts
    with the u16 name length's high byte, 0 for every valid name."""
    assert (len(payload.pack_record(tiny)) == container._HEADER.size) is is_header_sized

    channel = ImageChannel(_hide(cover, tiny, cipher_id=CipherId.FERNET, order=UnitOrder.PUBLIC))
    salt = channel.read(0, kdf.SALT_SIZE, None)
    header, record = (channel.read(kdf.SALT_SIZE, 128, None)[i : i + 64] for i in (0, 64))

    with pytest.raises(NoHiddenDataError):
        _reveal(_save(channel.write(salt, record + header, None), cover), PASSWORD)


class _Memory(StreamChannel[bytes]):
    def __init__(self, stored: bytes = b"") -> None:
        super().__init__(4096)
        self._data = stored

    def _read_stream(self) -> bytes:
        return self._data

    def _written(self, stream: bytes) -> bytes:
        return stream


class _Shrunk(_Memory):
    """A carrier whose capacity embedding lowered below what it stored (DCT does this)."""

    def capacity(self, prefix_size: int) -> int:
        return 0

    def stored_limit(self, prefix_size: int) -> int:
        return super().capacity(prefix_size)


@pytest.mark.parametrize("cipher_id", [CipherId.AES_GCM, CipherId.NONE])
def test_reveal_checks_lengths_against_stored_limit(cipher_id: CipherId) -> None:
    password = None if cipher_id == CipherId.NONE else PASSWORD
    stored = container.hide(
        _Memory(),
        FILE,
        method=MethodId.OBJECT_STREAM,
        cipher_id=cipher_id,
        password=password,
        order=UnitOrder.PUBLIC,
    )
    candidates = (Candidate(MethodId.OBJECT_STREAM, UnitOrder.PUBLIC),)
    revealed = container.reveal([(_Shrunk(stored), candidates)], password)

    assert revealed == container.Revealed(FILE, MethodId.OBJECT_STREAM, cipher_id)


def test_a_plain_payload_that_lost_its_end_is_not_revealed() -> None:
    """Stream channels return what is left: damaged audio codewords, deleted paragraphs."""
    stored = container.hide(
        _Memory(),
        FILE,
        method=MethodId.OBJECT_STREAM,
        cipher_id=CipherId.NONE,
        password=None,
        order=UnitOrder.PUBLIC,
    )
    candidates = (Candidate(MethodId.OBJECT_STREAM, UnitOrder.PUBLIC),)

    with pytest.raises(NoHiddenDataError, match="truncated"):
        container.reveal([(_Memory(stored[:-1]), candidates)], None)


@pytest.mark.parametrize("cipher_id", ENCRYPTING)
def test_an_encrypted_payload_that_lost_its_end_is_truncated(cipher_id: CipherId) -> None:
    """The header still opens, so the payload was there. 30 bytes of the record are left:
    PyNaCl raised ValueError on XChaCha20's 24 to 39."""
    skip_unless_installed(cipher_id)
    stored = container.hide(
        _Memory(),
        FILE,
        method=MethodId.OBJECT_STREAM,
        cipher_id=cipher_id,
        password=PASSWORD,
        order=UnitOrder.PUBLIC,
    )
    header_size = ciphers.get(cipher_id).sealed_size(container._HEADER.size)
    candidates = (Candidate(MethodId.OBJECT_STREAM, UnitOrder.PUBLIC),)
    left = _Memory(stored[: kdf.SALT_SIZE + header_size + 30])

    with pytest.raises(NoHiddenDataError, match=r"^Hidden data is truncated$"):
        container.reveal([(left, candidates)], PASSWORD, cipher_id=cipher_id)


def test_reveal_tries_every_group_with_one_derivation_step() -> None:
    candidates = (Candidate(MethodId.OBJECT_STREAM, UnitOrder.PUBLIC),)
    stored = container.hide(
        _Memory(),
        FILE,
        method=MethodId.OBJECT_STREAM,
        cipher_id=CipherId.AES_GCM,
        password=PASSWORD,
        order=UnitOrder.PUBLIC,
    )
    steps: list[container.Step] = []
    revealed = container.reveal(
        [(_Memory(bytes(range(200))), candidates), (_Memory(stored), candidates)],
        PASSWORD,
        steps.append,
    )
    assert revealed == container.Revealed(FILE, MethodId.OBJECT_STREAM, CipherId.AES_GCM)
    assert steps == [container.Step.LOCATE, container.Step.DERIVE_KEY, container.Step.DECRYPT]


def test_reveal_names_the_ciphers_it_could_not_try(
    cover: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skip_unless_installed(CipherId.XCHACHA20)
    embedded = _hide(cover)

    with pytest.raises(NoHiddenDataError) as found:
        _reveal(embedded, "wrong")

    assert str(found.value) == payload.NOT_FOUND_MESSAGE
    monkeypatch.setattr(catalog.Requirement, "is_installed", property(lambda _self: False))

    with pytest.raises(NoHiddenDataError, match=r"XChaCha20-Poly1305 needs the crypto-extra extra"):
        _reveal(embedded, "wrong")


def test_encryption_needs_a_password(cover: Path) -> None:
    with pytest.raises(CipherStitchError, match="password is required"):
        _hide(cover, password="")


@pytest.mark.parametrize(("cipher_id", "order"), ALLOWED)
def test_max_data_size_is_exact(cover: Path, cipher_id: CipherId, order: UnitOrder) -> None:
    skip_unless_installed(cipher_id)
    password = None if cipher_id == CipherId.NONE else "secret"
    limit = container.max_data_size(ImageChannel(cover), FILE.name, cipher_id)
    fitting = HiddenFile(name=FILE.name, data=bytes(limit))
    embedded = _hide(cover, fitting, cipher_id=cipher_id, password=password, order=order)
    assert _reveal(embedded, password).file == fitting

    too_large = HiddenFile(FILE.name, bytes(limit + 1))

    with pytest.raises(CapacityError):
        _hide(cover, too_large, cipher_id=cipher_id, password=password, order=order)


@pytest.mark.parametrize("size", [(5, 5), (8, 8)], ids=["no-room-for-salt", "no-room-for-header"])
def test_tiny_image(cover: Path, size: tuple[int, int]) -> None:
    tiny = cover.with_name("tiny.png")

    with Image.open(cover) as image:
        image.resize(size).save(tiny)

    channel = ImageChannel(tiny)
    assert container.max_data_size(channel, FILE.name, CipherId.AES_GCM) == 0
    assert container.framing_size(channel, FILE.name, CipherId.AES_GCM) > channel.capacity(0)

    with pytest.raises(NoHiddenDataError):
        _reveal(tiny, "secret")


class _Room:
    """A channel of ``size`` bytes, less its prefix."""

    def __init__(self, size: int) -> None:
        self.size = size

    def capacity(self, prefix_size: int) -> int:
        return max(self.size - prefix_size, 0)


def _needed(cipher_id: CipherId, size: int) -> int:
    """The carrier space a file of ``size`` bytes named FILE.name takes, by the format."""
    overhead = payload.record_overhead(FILE.name)

    if cipher_id == CipherId.NONE:
        return container.NONCE_SIZE + container._PLAIN.size + overhead + size

    cipher = ciphers.get(cipher_id)
    sealed = cipher.sealed_size(container._HEADER.size) + cipher.sealed_size(overhead + size)

    return kdf.SALT_SIZE + sealed


@pytest.mark.parametrize("cipher_id", [CipherId.NONE, CipherId.AES_GCM, CipherId.AES_CBC_HMAC])
@pytest.mark.parametrize("size", [20, 40, 400])
def test_framing_is_what_an_empty_file_needs(cipher_id: CipherId, size: int) -> None:
    """The capacity less the largest file that fits, or above the capacity when none does:
    then the estimate can say how far short the carrier falls. (A channel too small for the
    prefix itself reports no room after it, so it falls short by more; ``test_tiny_image``.)"""
    skip_unless_installed(cipher_id)
    framing = container.framing_size(_Room(size), FILE.name, cipher_id)

    if _needed(cipher_id, 0) > size:
        assert framing == _needed(cipher_id, 0)
    else:
        largest = size - framing
        assert _needed(cipher_id, largest) <= size < _needed(cipher_id, largest + 1)


# Format v1 streams of VECTOR_FILE hidden under VECTOR_PASSWORD with a cheap Argon2id
# profile, VECTOR_ARGON2 (test_kdf pins the production one). Never regenerate them: data
# hidden by a released version must stay readable.
VECTOR_FILE = HiddenFile("a.txt", b"cipherstitch")
VECTOR_PASSWORD = "пароль-йї"  # noqa: S105
VECTOR_ARGON2 = {"ARGON2_MEMORY_KIB": 32, "ARGON2_ITERATIONS": 1}
VECTORS = {
    CipherId.AES_GCM: (
        "8dca9b7e242d2c8ca475644ad50cecb13831131b0a94213b316c6379fadd021287c00bcf67b02de7"
        "9a3132d88079940e54e7f3d0e3b20c61ef9cfaace4d10bec77948d218cbce2fa5f48"
    ),
    CipherId.CHACHA20: (
        "ac9e0840a5c96b9b531af0d8fe91306a0c633b6c09b43bf663dd46c2c2025f67bf1719a87cbd0549"
        "cdb296cd23c934b42842ccf7c5850cb75fb1c5268fba1782ed530b9edda1eb2a1678"
    ),
    CipherId.XCHACHA20: (
        "22c86f6979969d65c5a63b230816c0e1e79dea1c80c74ecbbb7669f87a00ec1835a6a0d8d70c8930"
        "e84201bebfd2d82d77fc5cc93574cfd91b5214840a38c6ee1257c8ae589637d418a9476491e0cb62"
        "f7e45e1197572df7233623626bb4e280a11f653e61c818c5c25c2b40f77c04c7b59370f85bb7be43"
        "5dfb"
    ),
    CipherId.AES_CBC_HMAC: (
        "db7d238c3f4b8e815c6f5dac1c1843743b1f81bf6097dadd335058cefadbbc9e2be7a314ff54389c"
        "fd98ae3535e45d233007f05bba948efdf69bec8e1af7458d131ab10c5c179f427e1325d3f163aa01"
        "efce51d4d006af0a0307a51ae95daf275c813d30d0b34c6a7591babcc7ca84d3261cbe1998a24933"
        "8e8069f900991d1a6dc4369ef70c1f985002174653dd1e9ce9dccef36c5c621d08cd16c602c23d46"
    ),
    CipherId.FERNET: (
        "d7972e78a7fed52c24c0164f62edcfa03f649404cf6289c1bb8fc0a57e4f16265ecaaa8ea8549105"
        "d0e524382dfd23e51475a0b6c3f9a4e73fc5f326c17896c80b361d48b0434eae4d894c13d91a8412"
        "8dac3f803ed34f3b4b04371d311cb163467bfb5789569bd84f99883053d73d358513a1a2b46a52f4"
        "3c25051c8cf9a13479d7619eadfa3688fc8ab3e686545cb9f1ad8c988f8bb429418d59961b1e9dbe"
    ),
    CipherId.NONE: (
        "6a09e667f3bcc908b2fb1366ea957d3eee8c34ca43e8c5cddd2f80fdfc4cf437f2d5ff72eb07810e541a95bb6b"
    ),
}

# The same file under Research's alternatives to Argon2id, at their fixed costs, sealed with
# AES-256-GCM. Never regenerate them either.
KDF_VECTORS = {
    kdf.Function.SCRYPT: (
        "400f4f6f6323813a230959479a9f015540942bdcd4e238e2b2b035b35dea7e9224a58f5bb16b7a06"
        "9b166890f6dd6676472238a89ee3fd72e69f37a2262d76debafd2253c68bcef18a49"
    ),
    kdf.Function.PBKDF2: (
        "cd5ca66af515c00233e7a96044ead65f50702d984869f5a6f55af8c7c447530ca981aa843172cc4d"
        "baf16b349115ccb73db54b3cdd347016c89addedb7300e3e15f58bd493725bf9a193"
    ),
}
# The plain frame with Research's frame.mask off: the nonce, then the frame as it is.
UNMASKED_VECTOR = (
    "6a09e667f3bcc908b2fb1366ea957d3e435053540107000000130005612e747874636970686572737469746368"
)
# The same file hidden with the fixture extensions of tests/extension_fixtures/working/:
# byte_lsb's method (wire id 200), plain and sealed by chacha_copy's cipher (wire id 201, key
# info "cipherstitch extension cipher 201"). Extensions' wire ids and key info are format too.
# Never regenerate them either.
EXTENSION_VECTORS = {
    CipherId.NONE: (
        "6a09e667bb67ae853c6ef372a54ff53a87bf043c4db65e3b8de20f3ff2ebc2d0bdab920b6d7c8c0e1ec632a925"
    ),
    "chacha-copy": (
        "6a09e667bb67ae853c6ef372a54ff53a39070ff2b1cb15ee7f098230a11e17be310d79f979260cc729f6b8"
        "cef179df06d926209bab2f1fe2a40f400e13c8fff961d302c531105638d6bf"
    ),
}


def test_format_v1_wire_ids_are_frozen() -> None:
    assert {method.value: wire for method, wire in container._METHOD_IDS.items()} == {
        "lsb": 1,
        "dct": 3,
        "audio-lsb": 4,
        "phase-coding": 5,
        "echo-hiding": 6,
        "object-stream": 7,
        "whitespace": 8,
        "zero-width": 9,
        "lsb-replacement": 10,
    }
    assert {cipher.value: wire for cipher, wire in container._CIPHER_IDS.items()} == {
        "aes-256-gcm": 1,
        "chacha20-poly1305": 2,
        "xchacha20-poly1305": 3,
        "aes-256-cbc-hmac": 4,
        "fernet": 5,
    }


@pytest.mark.parametrize("cipher_id", list(CipherId))
def test_format_v1_vectors_stay_readable(
    cipher_id: CipherId, monkeypatch: pytest.MonkeyPatch
) -> None:
    skip_unless_installed(cipher_id)

    for name, value in VECTOR_ARGON2.items():
        monkeypatch.setattr(kdf, name, value)

    stored = _Memory(bytes.fromhex(VECTORS[cipher_id]))
    candidates = (Candidate(MethodId.OBJECT_STREAM, UnitOrder.PUBLIC),)
    revealed = container.reveal([(stored, candidates)], VECTOR_PASSWORD)
    assert revealed == container.Revealed(VECTOR_FILE, MethodId.OBJECT_STREAM, cipher_id)


@pytest.mark.parametrize("function", list(KDF_VECTORS))
def test_kdf_vectors_stay_readable(function: kdf.Function) -> None:
    stored = _Memory(bytes.fromhex(KDF_VECTORS[function]))
    candidates = (Candidate(MethodId.OBJECT_STREAM, UnitOrder.PUBLIC),)
    profile = kdf.Profile(32, 1, 1, function=function)
    revealed = container.reveal([(stored, candidates)], VECTOR_PASSWORD, profile=profile)

    assert revealed == container.Revealed(VECTOR_FILE, MethodId.OBJECT_STREAM, CipherId.AES_GCM)


def test_the_unmasked_vector_stays_readable() -> None:
    stored = _Memory(bytes.fromhex(UNMASKED_VECTOR))
    candidates = (Candidate(MethodId.OBJECT_STREAM, UnitOrder.PUBLIC),)
    revealed = container.reveal([(stored, candidates)], is_masked=False)

    assert revealed == container.Revealed(VECTOR_FILE, MethodId.OBJECT_STREAM, CipherId.NONE)


@pytest.mark.parametrize("cipher_id", list(EXTENSION_VECTORS))
def test_the_extension_vectors_stay_readable(
    cipher_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_extensions(monkeypatch, "working")

    for name, value in VECTOR_ARGON2.items():
        monkeypatch.setattr(kdf, name, value)

    stored = _Memory(bytes.fromhex(EXTENSION_VECTORS[cipher_id]))
    candidates = (Candidate("byte-lsb", UnitOrder.PUBLIC),)
    revealed = container.reveal([(stored, candidates)], VECTOR_PASSWORD, cipher_id=cipher_id)

    assert revealed == container.Revealed(VECTOR_FILE, "byte-lsb", cipher_id)
