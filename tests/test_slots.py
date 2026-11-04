import hashlib
import math
import struct
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pytest

from cipherstitch import container, kdf
from cipherstitch.catalog import CipherId, MethodId
from cipherstitch.container import SEQUENTIAL_SEED, Candidate, UnitOrder
from cipherstitch.errors import CapacityError, NoHiddenDataError
from cipherstitch.methods import hill, lsb, stc, trellis
from cipherstitch.methods.feistel import UNKEYED_SEED, Permutation
from cipherstitch.methods.lsb import LsbChannel
from cipherstitch.payload import HiddenFile

from .conftest import skip_unless_installed

Values = npt.NDArray[np.int64]
PREFIX = bytes(range(16))
SEED = b"seed"
WIDTH = 120  # values per line
BINARY = trellis.Config(trellis.Format(is_ternary=False))
CODINGS = pytest.mark.parametrize("config", [trellis.STANDARD, BINARY], ids=["ternary", "binary"])


class _Values(LsbChannel[Values]):
    """An LSB channel over values in 0 … 255, in lines of :data:`WIDTH` when they fill them."""

    def __init__(self, values: Values, config: trellis.Config = trellis.STANDARD) -> None:
        self._array = values
        super().__init__(Path("values"), values.size, config)

    def _values(self) -> Values:
        return self._array

    def _rebuilt(self, values: trellis.Values) -> Values:
        return values.astype(np.int64)

    def _value_range(self) -> tuple[int, int]:
        return 0, 255

    def _flat_values(self) -> npt.NDArray[np.bool_]:
        return lsb._flat_runs(self._lines(), lsb.FLAT_RUN).reshape(-1)

    def _value_costs(self, rate: float) -> trellis.Costs:
        return hill.image_costs(self._lines()[..., None].astype(np.uint8)).reshape(-1)

    def _lines(self) -> Values:
        width = WIDTH if self._array.size % WIDTH == 0 else self._array.size

        return self._array.reshape(-1, width)


def _cover(count: int, seed: int = 1) -> Values:
    return np.random.default_rng(seed).integers(0, 256, count)


def _random_bytes(size: int) -> bytes:
    return np.random.default_rng(size).integers(0, 256, size, dtype=np.uint8).tobytes()


@CODINGS
@pytest.mark.parametrize("size", [1, 63, 64, 65, 200, 3000])
@pytest.mark.parametrize(
    "seed", [None, SEED, SEQUENTIAL_SEED], ids=["unkeyed", "keyed", "sequential"]
)
def test_bodies_around_the_head_read_back(
    size: int, seed: bytes | None, config: trellis.Config
) -> None:
    body = _random_bytes(size)
    channel = _Values(_cover(48_000), config)
    embedded = _Values(channel.write(PREFIX, body, seed), config)

    assert embedded.read(0, len(PREFIX), None) == PREFIX
    assert embedded.read(len(PREFIX), size, seed) == body
    assert embedded.read(len(PREFIX), min(size, 10), seed) == body[:10]


def test_capacity_is_80_percent_of_the_values_that_are_not_flat_or_at_an_end() -> None:
    """docs/format.md#trellis-channels: of the stream's units, in bits, after the length
    and the split."""
    cover = _cover(48_000)
    cover[:12_000] = 7
    cover[12_000:24_000:2] = 255
    channel = _Values(cover)
    regions = trellis.Regions.of(cover.size)
    counted = ~channel._flat_values() & (cover > 0) & (cover < 255)
    stream = np.count_nonzero(counted) - np.count_nonzero(counted[regions.lead_indices()])

    assert channel.capacity(16) == stream * 80 // 100 // 8 - 6
    assert channel.capacity(0) == 16 + channel.capacity(16)
    assert channel.stored_limit(16) == 64 + regions.tail // 8


def _golden(count: int) -> int:
    """docs/format.md#trellis-channels: the first integer from ⌊(⌊√(5N²)⌋ − N) / 2⌋ on
    that is coprime to N."""
    multiplier = (math.isqrt(5 * count * count) - count) // 2

    while math.gcd(multiplier, count) != 1:
        multiplier += 1

    return multiplier


def _reference_syndrome(bits: list[int], m: int) -> list[int]:
    """The reference implementation printed in docs/format.md#syndrome-trellis-codes."""
    n = len(bits)
    segments = min(max(n // 32768, 1), 4096)
    columns = hashlib.shake_256(b"cipherstitch stc columns 297").digest(-(-n // m))
    message = [0] * m
    for unit, bit in enumerate(bits):
        block = unit * m // n
        segment = ((block + 1) * segments + m - 1) // m - 1
        end = (segment + 1) * m // segments
        column = columns[unit - (block * n + m - 1) // m] & 0x3F | 0x21
        for k in range(min(6, end - block)):
            message[block + k] ^= bit & column >> k
    return message


def _reference_read(values: Values, seed: bytes, *, is_ternary: bool = True) -> tuple[bytes, bytes]:
    """The lead and a stream's data, read as docs/format.md#trellis-channels lays them out:
    in the keyed order of ``seed``, or in sequence for the sequential order."""
    count = len(values)
    lead = min(16_384, count // 8)
    head = min(65_536, (count - lead) // 4)
    multiplier = _golden(count)
    order = None if seed == SEQUENTIAL_SEED else Permutation(seed, count - lead)

    def code(units: npt.NDArray[np.integer], m: int, split: int) -> bytes:
        """The message's first bits in the second bits, as many as the split gives them, then
        the rest in the parities. Binary coding has them all in the parities."""
        unit_values = values[units * multiplier % count]
        if not is_ternary:
            return bytes(np.packbits(_reference_syndrome([int(v & 1) for v in unit_values], m)))
        first = min(max((m * split + 32768) // 65536, 1), m - 1)
        second = _reference_syndrome([int(v >> 1 & 1) for v in unit_values], first)
        parities = _reference_syndrome([int(v & 1) for v in unit_values], m - first)
        return bytes(np.packbits(second + parities))

    def stream_code(positions: range, m: int, split: int) -> bytes:
        steps = np.arange(positions.start, positions.stop)

        return code(lead + (steps if order is None else order.at(steps)), m, split)

    lead_data = code(np.arange(lead), 128, 32768)
    head_data = stream_code(range(head), 560, 32768)
    length, split = struct.unpack_from(">IH", head_data)
    data = head_data[6 : 6 + length]

    if length > 64:
        m = 8 * (length - 64)
        span = min(count - lead - head, 512 * m)
        data += stream_code(range(head, head + span), m, split)

    return lead_data, data


@CODINGS
@pytest.mark.parametrize("size", [10, 64, 65, 300])
def test_matches_the_reference_implementation(size: int, config: trellis.Config) -> None:
    body = _random_bytes(size)
    embedded = _Values(_cover(30_000), config).write(PREFIX, body, SEED)
    other = BINARY if config.format.is_ternary else trellis.STANDARD

    assert _reference_read(embedded, SEED, is_ternary=config.format.is_ternary) == (PREFIX, body)
    assert _Values(embedded, other).read(0, len(PREFIX), None) != PREFIX
    assert _Values(embedded, other).read(len(PREFIX), size, SEED) != body


def test_a_binary_stream_stores_a_split_of_0() -> None:
    """docs/format.md#trellis-channels: the field stays, and readers ignore it."""
    embedded = _Values(_cover(30_000), BINARY).write(PREFIX, _random_bytes(300), SEED)
    regions = trellis.Regions.of(embedded.size, BINARY.format)
    head = trellis.Units(embedded, regions)._head(SEED)[1]

    assert trellis._FIELDS.unpack_from(head) == (300, 0)


def test_binary_coding_fills_the_capacity() -> None:
    """The capacity is the same as with ternary coding."""
    channel = _Values(_cover(48_000), BINARY)
    body = _random_bytes(channel.capacity(16))
    embedded = channel.write(PREFIX, body, SEED)

    assert channel.capacity(16) == _Values(_cover(48_000)).capacity(16)
    assert _Values(embedded, BINARY).read(16, len(body), SEED) == body


def test_unkeyed_data_starts_in_the_lead() -> None:
    """Without a seed the first 16 bytes of ``prefix ‖ body`` fill the lead, and the rest
    goes to the stream in the public order."""
    data = _random_bytes(100)
    embedded = _Values(_cover(30_000)).write(data[:16], data[16:], None)

    assert _reference_read(embedded, UNKEYED_SEED) == (data[:16], data[16:])


def test_a_sequential_stream_is_in_sequence() -> None:
    """Research's sequential order: stream position ``i`` is unit ``R + i``."""
    body = _random_bytes(300)
    embedded = _Values(_cover(30_000)).write(PREFIX, body, SEQUENTIAL_SEED)

    assert _reference_read(embedded, SEQUENTIAL_SEED) == (PREFIX, body)


def test_few_values_change_per_bit() -> None:
    """Plain LSB changes half a value per bit; the trellis well under a quarter at a low fill."""
    cover = _cover(240_000)
    body = _random_bytes(800)
    embedded = _Values(cover).write(PREFIX, body, SEED)

    assert np.count_nonzero(embedded != cover) < 0.25 * 8 * (len(PREFIX) + len(body))
    assert _Values(embedded).read(16, len(body), SEED) == body


def test_changes_avoid_smooth_areas() -> None:
    """The left half of every line is smooth but not flat: with equal costs it would take
    half the changes."""
    rng = np.random.default_rng(seed=2)
    lines = 128 + rng.normal(0, 40, (400, WIDTH))
    lines[:, : WIDTH // 2] = 128 + rng.normal(0, 2, (400, WIDTH // 2))
    cover = np.clip(lines, 0, 255).astype(np.int64).reshape(-1)
    embedded = _Values(cover).write(PREFIX, _random_bytes(400), SEED)
    changed = (embedded != cover).reshape(400, WIDTH)

    assert np.count_nonzero(changed[:, : WIDTH // 2]) < np.count_nonzero(changed) / 10


@CODINGS
@pytest.mark.parametrize("fill", [0.05, 0.3, 1.0])
def test_flat_values_change_only_in_blocks_without_others(
    fill: float, config: trellis.Config
) -> None:
    """A third of the values are flat. They change only in blocks with no other values, so
    never more than :func:`lsb.flat_blocks` of them, and none without such blocks. The code
    lets most of those blocks keep their values too."""
    cover = _cover(240_000)
    cover[:80_000] = 7
    channel = _Values(cover, config)
    flat = channel._flat_values()
    size = int(channel.capacity(16) * fill)
    body = _random_bytes(size)
    embedded = channel.write(PREFIX, body, SEED)
    share = np.count_nonzero(flat) / cover.size
    blocks = lsb.flat_blocks(cover.size, size, share, config.format)
    changed = np.count_nonzero((embedded != cover) & flat)

    assert changed <= blocks
    assert changed == 0 or round(blocks) > 0
    assert _Values(embedded, config).read(16, size, SEED) == body


def test_binary_coding_has_more_blocks_of_flat_values() -> None:
    """Its blocks are half as long as each plane's: more of them hold only flat values."""
    share = 1 / 3

    assert lsb.flat_blocks(240_000, 9000, share, BINARY.format) > lsb.flat_blocks(
        240_000, 9000, share
    )


@CODINGS
def test_steps_never_leave_the_range(config: trellis.Config) -> None:
    """Every other value sits at an end of the range, where it can step only inwards: 0 and
    255 can never flip their second bit. Binary coding takes the step that keeps it there."""
    cover = _cover(48_000)
    cover[::2] = np.where(cover[::2] < 128, 0, 255)
    channel = _Values(cover, config)
    body = _random_bytes(channel.capacity(16) // 2)
    embedded = channel.write(PREFIX, body, SEED)
    step = embedded - cover

    assert (int(embedded.min()), int(embedded.max())) == (0, 255)
    assert set(step[cover == 0].tolist()) == {0, 1}
    assert set(step[cover == 255].tolist()) == {0, -1}
    assert _Values(embedded, config).read(16, len(body), SEED) == body


def test_binary_steps_go_either_way_where_both_cost_the_same() -> None:
    """HILL costs a step as much either way, so binary coding picks it at random, as LSB
    matching does: about as many steps up as down from even values and from odd ones."""
    cover = _cover(240_000)
    channel = _Values(cover, BINARY)
    embedded = channel.write(PREFIX, _random_bytes(channel.capacity(16) // 2), SEED)
    step = embedded - cover
    inner = (step != 0) & (cover > 0) & (cover < 255)

    for parity in (0, 1):
        ups = step[inner & (cover % 2 == parity)] > 0

        assert abs(np.mean(ups) - 0.5) < 0.03


def test_steps_within_a_pair_are_about_as_many_as_across(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the tail's bits split as an ideal ternary code would, steps that keep a value in
    its pair, 2k and 2k + 1, are about as many as steps that leave it. With halves they
    outnumber them, as LSB replacement's do
    (docs/detectability.md#popular-tools-and-classical-attacks)."""
    rng = np.random.default_rng(seed=4)
    wave = 128 + 60 * np.sin(np.linspace(0, 6, WIDTH))
    lines = wave + rng.normal(0, 1, (400, WIDTH)).cumsum(axis=1)
    lines[:, WIDTH // 2 :] += rng.normal(0, 6, (400, WIDTH // 2))
    cover = np.clip(lines, 1, 254).astype(np.int64).reshape(-1)
    body = _random_bytes(_Values(cover).capacity(16) // 2)

    def within(embedded: Values) -> float:
        step = embedded - cover
        moved = step != 0

        return float(np.mean((cover[moved] % 2 == 0) == (step[moved] > 0)))

    ideal = within(_Values(cover).write(PREFIX, body, SEED))
    monkeypatch.setattr(trellis, "_ideal_split", lambda *_: trellis.EVEN_SPLIT)
    halves = within(_Values(cover).write(PREFIX, body, SEED))

    assert abs(ideal - 0.5) < 0.01 < halves - 0.5


def test_binary_coding_tries_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """It has no split to move, so a tail without a solution is refused at once."""
    cover = _cover(48_000)
    body = _random_bytes(3000)
    tail_units = trellis.Regions.of(cover.size).tail_layout(len(body)).unit_count
    embed = stc.embed
    tries = []

    def embed_or_fail(layout: stc.Layout, *args: Any) -> stc.Units:
        if layout.unit_count == tail_units:
            tries.append(layout)

            raise stc.UnsolvableError

        return embed(layout, *args)

    monkeypatch.setattr(stc, "embed", embed_or_fail)

    with pytest.raises(CapacityError, match="can't hold this payload reliably"):
        _Values(cover, BINARY).write(PREFIX, body, SEED)

    assert len(tries) == 1
    assert tries[0].message_bits == 8 * (len(body) - trellis.HEAD_BYTES)


def test_a_tail_without_a_solution_is_tried_again_with_fewer_parity_bits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A full LSB cover's parities can leave a run of blocks without enough units. The split
    is in the head, so the writer may move it: the reader needs nothing else."""
    cover = _cover(48_000)
    body = _random_bytes(3000)
    regions = trellis.Regions.of(cover.size)

    def stored_split(values: Values) -> int:
        split: int = trellis._FIELDS.unpack_from(trellis.Units(values, regions)._head(SEED)[1])[1]

        return split

    first_try = stored_split(_Values(cover).write(PREFIX, body, SEED))
    embed = stc.embed
    failures = [2]

    def embed_or_fail(layout: stc.Layout, *args: Any) -> stc.Units:
        is_tail = layout.unit_count == regions.tail_layout(len(body)).unit_count

        if is_tail and failures[0]:
            failures[0] -= 1

            raise stc.UnsolvableError

        return embed(layout, *args)

    monkeypatch.setattr(stc, "embed", embed_or_fail)
    embedded = _Values(cover).write(PREFIX, body, SEED)

    assert failures == [0]
    assert stored_split(embedded) == first_try + 2 * trellis.SPLIT_SCALE // 20
    assert _Values(embedded).read(16, len(body), SEED) == body


@pytest.mark.parametrize(
    ("size", "rate"),
    [
        (10, (128 + 560) / (16_384 + 65_536)),  # the lead and the head alone
        (200, (128 + 560 + 1088) / (16_384 + 65_536 + 512 * 1088)),  # a tail of 557 056 units
        (60_000, (128 + 560 + 479_488) / 1_000_000),  # a tail over the whole carrier
    ],
)
def test_the_payload_rate_is_over_the_units_the_codes_change(size: int, rate: float) -> None:
    """A million units: lead 16 384, head 65 536, tail 918 080, 512 tail units per bit."""
    channel = _Values(np.zeros(1_000_000, dtype=np.int64))

    assert channel._rate(PREFIX, _random_bytes(size)) == pytest.approx(rate, rel=1e-12)


def test_the_payload_rate_reaches_the_costs_on_every_try(monkeypatch: pytest.MonkeyPatch) -> None:
    """48 000 units: lead 6000, head 10 500 and tail 31 500, all of which a 3000-byte body's
    23 488 tail bits span."""
    rates = []
    value_costs = _Values._value_costs

    def recorded(channel: _Values, rate: float) -> trellis.Costs:
        rates.append(rate)

        return value_costs(channel, rate)

    def unspelled(*args: Any) -> trellis.Steps:
        raise trellis._UnspelledError

    monkeypatch.setattr(_Values, "_value_costs", recorded)
    monkeypatch.setattr(trellis, "_solve", unspelled)
    channel = _Values(_cover(48_000))

    with pytest.raises(CapacityError):
        channel.write(PREFIX, _random_bytes(3000), SEED)

    assert rates == [(128 + 560 + 23_488) / 48_000] * len(trellis._SPLIT_SHIFTS)


def test_flat_share_counts_runs_of_equal_neighbours_in_each_line() -> None:
    lines = _cover(1200).reshape(10, WIDTH)
    lines[0, 10:26] = 7  # a run of 16: flat
    lines[1, 110:] = 7  # 10 at the end of one line and 6 at the start of the next: not flat
    lines[2, :6] = 7
    lines[3, 50:65] = 7  # 15: not flat
    lines[4:6] = 0  # two whole lines of silence
    assert _Values(lines.reshape(-1)).flat_share() == (16 + 2 * WIDTH) / 1200


# The two lowest bits of 2560 values holding VECTOR_FILE, high bit first: plain (in the public
# order) and encrypted (keyed) under test_container's VECTOR_PASSWORD and VECTOR_ARGON2
# profile. They pin the unit numbering, the lead, the streams, both orders, the bit planes and
# the code (docs/format.md#trellis-channels).
VECTOR_FILE = HiddenFile("a.txt", b"cipherstitch" * 8)
VECTOR_PASSWORD = "пароль-йї"  # noqa: S105
VECTOR_ARGON2 = {"ARGON2_MEMORY_KIB": 32, "ARGON2_ITERATIONS": 1}
VECTORS = {
    CipherId.NONE: (
        "2f7731e2e8e128b2e73a621ce1a9f5ab334982da143f6a7420e7c680fede837854bbe05e54661c38"
        "4f080699cba04d40b50df75b7a7e0236e105212cc479ce595b42e2722e4769e6cb12a8d15d289079"
        "1c2f9b2cb356d07f5f78bb992401d6e8a68f1667129a8c0213198aa416665aec7b7dde08414a79d7"
        "acdccfe56c024974f68638cc07b1760f64233092506e241edde60aa715a4667896c54ebeb5aae616"
        "a220de91c431261f37de1fa5df7557e5c9b47fd07027396c9a385c59b08b02153681481edd7e1c22"
        "4d98255f483530d32f5dabc4abdc84c7698489156532664c755b21198a1969d472bae133acb231ae"
        "e6af044f1e8b16ea9dc6287afeeb23e7afbaaf362482a445c22af983e1dc30846335ef9a57ee0947"
        "8343f817b627d61788b4546d31d8e4b76eed5a4949d0f2795279da37465b307a32af16a537f7942b"
        "23a9d3f0dd10c8756a4322858bc4afaeaa9f5092fed6ab08f35b51460181a66bcc2b0606829d418d"
        "ce8d0d5b461b7dd2a0406f4b79f7988ad7fa4600001dabe7455220ec0f72b8c1e269e5072c6d3a32"
        "6dc7335b6101a465e4a39a35161b6aa324bcba542591a73c4b5fa13eb1feb41f5d2aa6f9f8074b0e"
        "1583ab5174badaeb4b631b0a6399b8ee571887f06c5a8a08497bd4d72fc684f58b25b49cab7fa78d"
        "c075a4fdc08d46427494ec732fff5353b8c844576173d0a8af325b845b05b6f77ded4b9e252e9281"
        "12f1d2fcec9676aad42a5c2afd63df8ad478b33a0b8b0928e996c798aa5a93bb2f8b00c0e4180949"
        "f775c377d9be32a85b3ac943e2ec7f7d626552f027414787de3684120c1f72b97374d371b18a29f6"
        "31394a4689745a804ddf70be838f8c1145cd396effe05018963cfd9c3762d62a1b67ecffec914043"
    ),
    CipherId.AES_GCM: (
        "2f7731a2e8e628b2e729625ce1e8356b3f4982da143f6a40102777b4fee5737864bbe35e54661db8"
        "4ac8069acba04d70703df75b7a7e0236e102213cc479c2195a42a2723e57a8e6db12a8d11d399069"
        "2c2fa8e0b356d05f577878d92442d298a7bbe667229f8c0600197aa416665aef7b3fce0d4c4a79d7"
        "addccfe46c324964f689398c0bb0751f642f3091146a241eede60ae7d5e8567885c45dbe65aae616"
        "a2230e91e504251f37d21fa5df745ba989c44fd0422709689a384c49b09f06150685081ed97e5822"
        "0d9925538c3533132f5dabc8eadc979769b58dc5a472565c785f911586d969c87ebae1346fb3f19e"
        "f6af040f1e8756e99dc62449fee822d7afbba8362482a40902eacc83e0dc30b46201ef9a47ee0943"
        "8343f814bbe3d6178870545d46ede4f76aed5a444dd0f6b952b89a33475ab37bf2af16a537c6942b"
        "22bdd338dd1ccc776a4322858bc0eb9feacc5092fed6ab08f35f65463581a667c8280606829c4d8d"
        "ce8d0d57461f2de2ac406f4779f7189987fa7610301dbbeb459220e3cf72b8c0e169e5042c1d3af1"
        "2ec7025b6141645526e31e351618aa6314bc8a532591973c4f5fad3eb2fea85f7d2aa679f8c44b02"
        "15b3eb5175bbdaeb4b631b1b5198b4ae975b87b06c8e890859bbc4db1fc6c1f9cb25f49cab3f678d"
        "cc65a4fd808d85424494ecb32fff1353f8c887665173d8a8af321c845b45f2f77ddc4b9a263a92c1"
        "16c192ccf8a676aa943a5c2afd30d38a0578a3ca0b8b0928e986839bda5b90bb2f8b40c3e45c0949"
        "d7b5337319fe32a85b36d903e32d3f71626152f026418b86de368412cc5342f54377d771b58a69e6"
        "31f99a4689785a8142df70ae938f8c2145cc396ddfe01018963cfd9d3762062edf27e8ffe891704f"
    ),
}


# VECTOR_FILE under AES-256-GCM in Research's sequential order, in the same form and profile.
# It pins that order: stream position i is unit R + i. Never regenerate it either.
SEQUENTIAL_VECTOR = (
    "1c276de42d657970b207bf2296ca15ed199ed92ba20510445b320b636f2a71aac87469faf525c2f0"
    "6a351713cee9e3eadf7704afaa1502e4b7b738a19f3d8727734e72272608d95b9b1479e0e548cb56"
    "20b388ffdb73aa19b42b259465f8f4661bcf5cf94bb0832f421226f4b7cd9d86cc4ed87c70a6b37e"
    "fc681ad8e654a9890a4c75a0d6aa9dc0d1174fe505cb8719e617c9c96360b415c088b5040d57dd13"
    "77495d24042d462a452a3529099e2ab6a552e20fb87a2fb457309b3add3e6385124383417f6fa752"
    "a3350290a2786aa0e1149802e6c1b83be459ff1b732b7e840044283a5a228826ee34293b092145d0"
    "15cc303f05918a6ae6ee71c4ad677d006dbf0d7a6bf25e4e07740eedfac7f9b2eb236a329d68c9ae"
    "0ce3e33c1dbe7eae84a16a86144ea3ceefda284ee337a489553b351344680bbb5d078d19743c799e"
    "783a8cf3eeb950479e1f17de817b9a1734026496a15f434122bdf2b8a04516697d8a896f265dc3bf"
    "19dbd3d3a2e69b9a1be0020fd9c63bae50725e25fae97a0c5866f8f05028e0e4221427f8f962c608"
    "0b13b2a9f0665aab0558840d8839212da06acca9624d645cc40c0e2ff51f48988745cd2d77ffd7e1"
    "136f320de6132827c944c3e7542eb7a8c5e33bb1c584951de8240b365c4b1d07c7857b508011bbfe"
    "aba973a3a05eb8e59a516641c369ba87ab3d4e4576e7ba0e84ed02fd1295d37b063abd3a87e75622"
    "44f9fd38c8c068f3ef56678253233853348c3d72c99586afddf4c099f06276fc40155da591eef7ee"
    "6283113594416aa2ece8988e43682005c274e87dee04a6bc03e09958581ad5144f51762a96e01fb2"
    "29a4f306648c4e7196c4136fa862a91b31b927bcaf9a984778b6f5a3fcea605353772d12bce46dc3"
)


# VECTOR_FILE under AES-256-GCM in the keyed order with Research's binary coding, in the same
# profile: the parities of 2560 values, high bit first, all binary coding reads. It pins that
# every code is one code over the parities. Never regenerate it either.
BINARY_VECTOR = (
    "5a1ee507873df0cf754222a80dc843976f8e164a1944dddaede8bd2c760e0365b4b630483c657604"
    "d4773b562fc70d6791f50a70d1e051645fe5587f08a5a3ad2471340e698b574febed823394710721"
    "12128f8a510ddf01571ca0590b2b23cc4c5c56a0342492a08e52993361f6ea925070b014699f294b"
    "ed4758e6366e0a85decf560c13bb044fea16501a597a94231b441055f5a53e47e1121004dfa1b2cd"
    "ecf8a8b3e409ea5e62d1c0b7c1465f1f8bb5bb7a2ad8b778ccf6f85fba3aa136b65671f1d45e0e7c"
    "99d42efea2b694cda7f6b74cc4134f0f4e6f6960d13d0457a0c079bdb13d4a74f3061f2b6abc94e2"
    "005e48e908fe2a760068cfdfe730ec9117d29be620c1e364bbb46c3ebd8d08d1e4021460421709c3"
    "7a2e7d9786c76195126219fca68ae9499ac20f8dc2a493adba0894a7c36f4e3577393a1a6144644c"
)


def _vector_values(text: str) -> Values:
    bits = np.unpackbits(np.frombuffer(bytes.fromhex(text), dtype=np.uint8)).astype(np.int64)

    return 2 * bits[0::2] + bits[1::2]


@pytest.mark.parametrize("cipher_id", list(VECTORS))
def test_format_vectors_stay_readable(cipher_id: CipherId, monkeypatch: pytest.MonkeyPatch) -> None:
    skip_unless_installed(cipher_id)

    for name, value in VECTOR_ARGON2.items():
        monkeypatch.setattr(kdf, name, value)

    candidates = (
        Candidate(MethodId.LSB, UnitOrder.KEYED),
        Candidate(MethodId.LSB, UnitOrder.PUBLIC),
    )
    channel = _Values(_vector_values(VECTORS[cipher_id]))
    revealed = container.reveal([(channel, candidates)], VECTOR_PASSWORD)

    assert revealed == container.Revealed(VECTOR_FILE, MethodId.LSB, cipher_id)


def test_the_sequential_vector_stays_readable(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in VECTOR_ARGON2.items():
        monkeypatch.setattr(kdf, name, value)

    channel = _Values(_vector_values(SEQUENTIAL_VECTOR))
    every = tuple(Candidate(MethodId.LSB, order) for order in UnitOrder)
    standard = (Candidate(MethodId.LSB, UnitOrder.KEYED), Candidate(MethodId.LSB, UnitOrder.PUBLIC))
    revealed = container.reveal([(channel, every)], VECTOR_PASSWORD)

    assert revealed == container.Revealed(VECTOR_FILE, MethodId.LSB, CipherId.AES_GCM)

    with pytest.raises(NoHiddenDataError):
        container.reveal([(channel, standard)], VECTOR_PASSWORD)


def test_the_binary_vector_stays_readable(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in VECTOR_ARGON2.items():
        monkeypatch.setattr(kdf, name, value)

    raw = np.frombuffer(bytes.fromhex(BINARY_VECTOR), dtype=np.uint8)
    parities = np.unpackbits(raw).astype(np.int64)
    every = tuple(Candidate(MethodId.LSB, order) for order in UnitOrder)
    revealed = container.reveal([(_Values(parities, BINARY), every)], VECTOR_PASSWORD)

    assert revealed == container.Revealed(VECTOR_FILE, MethodId.LSB, CipherId.AES_GCM)

    with pytest.raises(NoHiddenDataError):
        container.reveal([(_Values(parities), every)], VECTOR_PASSWORD)


def test_ternary_vectors_are_not_read_with_binary_coding(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in VECTOR_ARGON2.items():
        monkeypatch.setattr(kdf, name, value)

    channel = _Values(_vector_values(VECTORS[CipherId.AES_GCM]), BINARY)
    every = tuple(Candidate(MethodId.LSB, order) for order in UnitOrder)

    with pytest.raises(NoHiddenDataError):
        container.reveal([(channel, every)], VECTOR_PASSWORD)


def test_the_split_ladder_keeps_its_first_rungs_and_climbs_to_thirty_percent() -> None:
    """A larger cover fails a try more often (a 6 MP cover failed all four rungs at 100%): the
    ladder grew, and the first try, which the golden digests pin, stays the same."""
    shifts = trellis.Writer().split_shifts
    scale = trellis.SPLIT_SCALE

    assert shifts[:4] == (0, scale // 20, scale // 10, 3 * scale // 20)
    assert shifts == tuple(percent * scale // 100 for percent in range(0, 35, 5))
    assert trellis.Writer(can_retry=False).split_shifts == (0,)
