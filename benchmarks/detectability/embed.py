"""Hide random payloads with CipherStitch at fixed fills of the capacity (main venv).

    uv run python benchmarks/detectability/embed.py --family lsb --fills 1,5 --count 3000
    uv run python benchmarks/detectability/embed.py --family lsb2 --rates 140 --count 3000

Families (carrier → output, the cover each output is compared with):

- ``lsb``: PNG cover → PNG (image LSB).
- ``dct-q75``, ``dct-q95``: Pillow JPEG at that QF → JPEG (DCT).
- ``psi``: the TIFF itself → JPEG (DCT from a precover, with side information); compared with
  P-0, which has the same coefficients before embedding.
- ``pnosi``: P-0 → JPEG (DCT without side information on the same coefficients).
- ``jnosi``: Pillow JPEG at the precover's settings → JPEG.
- Round 2 (``results/round-2/protocol.md``): ``lsb2`` and ``dct2-q75`` hide, at a fill, the
  bytes round 1's family hid there (``payload_from``). ``psi2`` and ``jnosi2`` are ``psi`` and
  ``jnosi`` with ``psi``'s bytes, both compared with Pillow's JPEG, whose coefficients the
  precover now has. ``lsb2-crop`` and ``dct2-q75-crop`` work on the 256 × 256 crops.
- Round 3 (``results/round-3/protocol.md``): ``lsb3``, ``dct3-q75`` and ``psi3`` are round 2's
  ``lsb2``, ``dct2-q75`` and ``psi2`` for the writer of that round.
- Round 4 (``results/round-4/protocol.md``): ``lsb4``, ``dct4-q75`` and ``psi4`` are the same
  for the present writer.

A fill is a share of the capacity; with ``--rates``, an output instead takes the carrier
space of r √N bits, N being the channel's ``element_count``.

Every payload is encrypted with the default cipher. Only the key derivation is made cheaper
here: the sealed bytes are uniformly random either way. Each output gets a JSON record in
``work/manifests/<condition>/``; an image whose record exists is skipped. The salt and the
writer's random choices come from the product unchanged, so a rerun gives statistically the
same files, not the same bytes.
"""

import argparse
import json
import math
import os
import tempfile
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing import Pool
from pathlib import Path
from typing import NamedTuple

import numpy as np
from common import (
    COVERS,
    EMBEDDED,
    MANIFESTS,
    MAX_WORKERS,
    SEED,
    atomic,
    fail,
    logger,
    mark_done,
    name,
    payload,
    selection,
    tiff_path,
)
from conditions import CIPHERSTITCH_FILLS, JNOSI_FILLS, REFERENCE_FILLS, at_fill, at_rate
from PIL import Image
from prepare import jnosi_folder

from cipherstitch import catalog, kdf, methods, risks, service
from cipherstitch.catalog import MethodId
from cipherstitch.errors import CapacityError
from cipherstitch.media import jpeg, precover
from cipherstitch.payload import HiddenFile

kdf.ARGON2_MEMORY_KIB = 32
kdf.ARGON2_ITERATIONS = 1

PASSWORD = "detectability study"  # noqa: S105 - a fixed password for synthetic payloads
PAYLOAD_NAME = "payload.bin"
REVEAL_SHARE = 0.01  # outputs revealed again per condition, at least one
CIPHER = catalog.DEFAULT_CIPHER

log = logger("embed")


@dataclass(frozen=True, slots=True)
class Family:
    method: MethodId
    cover_set: str  # which index set: "lsb", "jpeg" or their crops
    carrier: Callable[[int], Path]
    reference: Callable[[int], Path]  # the cover the output is compared with
    suffix: str
    fills: tuple[int, ...] = CIPHERSTITCH_FILLS
    payload_from: str = ""  # the round 1 family whose payload bytes a fill takes


class Target(NamedTuple):
    """What each image's output holds: a fill, in percent, or a root rate."""

    fill: int | None = None
    rate: int | None = None

    def condition(self, family: str) -> str:
        if self.rate is not None:
            return at_rate(family, self.rate)

        return at_fill(family, self.fill or 0)

    @property
    def seed(self) -> int:
        """Keys the random payload bytes, as round 1 did with the fill."""
        return self.rate if self.rate is not None else self.fill or 0


def _cover(folder: str, suffix: str) -> Callable[[int], Path]:
    return lambda index: COVERS / folder / f"{name(index)}{suffix}"


FAMILIES = {
    "lsb": Family(MethodId.LSB, "lsb", _cover("png", ".png"), _cover("png", ".png"), ".png"),
    "dct-q75": Family(MethodId.DCT, "jpeg", _cover("q75", ".jpg"), _cover("q75", ".jpg"), ".jpg"),
    "dct-q95": Family(MethodId.DCT, "jpeg", _cover("q95", ".jpg"), _cover("q95", ".jpg"), ".jpg"),
    "psi": Family(MethodId.DCT, "jpeg", tiff_path, _cover("p0", ".jpg"), ".jpg"),
    "pnosi": Family(MethodId.DCT, "jpeg", _cover("p0", ".jpg"), _cover("p0", ".jpg"), ".jpg"),
    "jnosi": Family(
        MethodId.DCT,
        "jpeg",
        _cover(jnosi_folder(), ".jpg"),
        _cover(jnosi_folder(), ".jpg"),
        ".jpg",
        JNOSI_FILLS,
    ),
    "lsb2": Family(
        MethodId.LSB,
        "lsb",
        _cover("png", ".png"),
        _cover("png", ".png"),
        ".png",
        REFERENCE_FILLS,
        "lsb",
    ),
    "dct2-q75": Family(
        MethodId.DCT,
        "jpeg",
        _cover("q75", ".jpg"),
        _cover("q75", ".jpg"),
        ".jpg",
        REFERENCE_FILLS,
        "dct-q75",
    ),
    "psi2": Family(
        MethodId.DCT,
        "jpeg",
        tiff_path,
        _cover(jnosi_folder(), ".jpg"),
        ".jpg",
        REFERENCE_FILLS,
        "psi",
    ),
    "jnosi2": Family(
        MethodId.DCT,
        "jpeg",
        _cover(jnosi_folder(), ".jpg"),
        _cover(jnosi_folder(), ".jpg"),
        ".jpg",
        REFERENCE_FILLS,
        "psi",
    ),
    "lsb3": Family(
        MethodId.LSB,
        "lsb",
        _cover("png", ".png"),
        _cover("png", ".png"),
        ".png",
        REFERENCE_FILLS,
        "lsb",
    ),
    "dct3-q75": Family(
        MethodId.DCT,
        "jpeg",
        _cover("q75", ".jpg"),
        _cover("q75", ".jpg"),
        ".jpg",
        REFERENCE_FILLS,
        "dct-q75",
    ),
    "psi3": Family(
        MethodId.DCT,
        "jpeg",
        tiff_path,
        _cover(jnosi_folder(), ".jpg"),
        ".jpg",
        REFERENCE_FILLS,
        "psi",
    ),
    "lsb4": Family(
        MethodId.LSB,
        "lsb",
        _cover("png", ".png"),
        _cover("png", ".png"),
        ".png",
        REFERENCE_FILLS,
        "lsb",
    ),
    "dct4-q75": Family(
        MethodId.DCT,
        "jpeg",
        _cover("q75", ".jpg"),
        _cover("q75", ".jpg"),
        ".jpg",
        REFERENCE_FILLS,
        "dct-q75",
    ),
    "psi4": Family(
        MethodId.DCT,
        "jpeg",
        tiff_path,
        _cover(jnosi_folder(), ".jpg"),
        ".jpg",
        REFERENCE_FILLS,
        "psi",
    ),
    "lsb2-crop": Family(
        MethodId.LSB,
        "lsb-crops",
        _cover("crop-png", ".png"),
        _cover("crop-png", ".png"),
        ".png",
        (),
    ),
    "dct2-q75-crop": Family(
        MethodId.DCT,
        "jpeg-crops",
        _cover("crop-q75", ".jpg"),
        _cover("crop-q75", ".jpg"),
        ".jpg",
        (),
    ),
}


def _target(target: int | Target) -> Target:
    """A bare number is a fill."""
    return target if isinstance(target, Target) else Target(fill=target)


def output_path(family: str, target: int | Target, index: int) -> Path:
    folder = _target(target).condition(family)

    return EMBEDDED / folder / f"{name(index)}{FAMILIES[family].suffix}"


def record_path(family: str, target: int | Target, index: int) -> Path:
    return MANIFESTS / _target(target).condition(family) / f"{name(index)}.json"


def options(method: MethodId) -> service.HideOptions:
    return service.HideOptions(method=method, cipher=CIPHER, password=PASSWORD, is_research=True)


def payload_size(carrier: Path, method: MethodId, target: float) -> int:
    """The largest payload whose share of the capacity, as the app's meter shows it, is at
    most ``target``."""
    low, high = 0, service.estimate(carrier, PAYLOAD_NAME, 0, options(method)).available or 0

    while low < high:
        middle = (low + high + 1) // 2
        share = service.estimate(carrier, PAYLOAD_NAME, middle, options(method)).share or 0.0

        if share <= target:
            low = middle
        else:
            high = middle - 1

    return low


def root_rate_size(carrier: Path, method: MethodId, rate: int) -> int:
    """The largest payload whose carrier space, framing included, is at most ⌊rate √N⌋ / 8
    bytes and the capacity: the square-root law of ``risks.advised_size`` at another rate,
    without its fixed share below ``risks.CALIBRATED_ELEMENTS`` (the crops measure the law)."""
    embedding = methods.get(method)

    if embedding is None:
        fail(f"{method} is not installed")

    elements = embedding.open(carrier).element_count
    empty = service.estimate(carrier, PAYLOAD_NAME, 0, options(method))
    allowed = min(int(rate * math.sqrt(elements)) // 8, empty.available or 0)

    return max(allowed - empty.required, 0)


def _round_one_size(family: Family, fill: int, index: int) -> int | None:
    """The bytes round 1's family hid in this image at this fill; ``None`` if they did not fit."""
    record = MANIFESTS / at_fill(family.payload_from, fill) / f"{name(index)}.json"

    if not record.exists():
        fail(f"{record} is missing: round 1 must have embedded at this fill")

    row = json.loads(record.read_text())

    return row["payload_bytes"] if row["status"] == "ok" else None


def _lsb_changes(cover: Path, output: Path) -> dict[str, float]:
    with Image.open(cover) as before, Image.open(output) as after:
        old = np.asarray(before.convert("RGB"), dtype=np.int16)
        new = np.asarray(after.convert("RGB"), dtype=np.int16)

    return {"values": old.size, "changed": int(np.count_nonzero(old != new))}


def _dct_changes(cover: Path, output: Path, tiff: Path | None) -> dict[str, float]:
    old = jpeg.read_coefficients(cover).ac
    new = jpeg.read_coefficients(output).ac
    changed = np.flatnonzero(old != new)
    result: dict[str, float] = {
        "nonzero_ac": int(np.count_nonzero(old)),
        "changed": int(changed.size),
        "changed_zeros": int(np.count_nonzero(old[changed] == 0)),
    }

    if tiff is not None:
        rounding = precover.compress(tiff).rounding[changed]
        moved = np.sign(new[changed].astype(np.int32) - old[changed])
        result["towards_precover"] = int(
            np.count_nonzero((rounding != 0) & (moved == np.sign(rounding)))
        )
        result["no_side_information"] = int(np.count_nonzero(rounding == 0))

    return result


def _fill_warning(method: MethodId, estimate: service.Estimate) -> str | None:
    if not service.is_conspicuous(estimate):
        return None

    fill = risks.Fill(estimate.required, estimate.available or 0, estimate.advised)

    return risks.detection_risks(method, fill=fill)[0]


def _size(family: Family, carrier: Path, target: Target, index: int) -> int | None:
    """The payload for one output; ``None`` where round 1's did not fit."""
    if target.rate is not None:
        return root_rate_size(carrier, family.method, target.rate)

    fill = target.fill or 0

    if family.payload_from:
        return _round_one_size(family, fill, index)

    return payload_size(carrier, family.method, fill / 100)


def embed_one(job: tuple[str, Target, int]) -> str:
    """Hide one payload in one carrier, then write the record; ``'skipped'`` if done."""
    family_name, target, index = job
    family = FAMILIES[family_name]
    record = record_path(family_name, target, index)

    if record.exists():
        return "skipped"

    carrier, output = family.carrier(index), output_path(family_name, target, index)
    size = _size(family, carrier, target, index)
    base = {
        "index": index,
        "condition": target.condition(family_name),
        "target_share": None if target.fill is None else target.fill / 100,
        "root_rate": target.rate,
    }

    if size is None:
        _write(record, {**base, "status": "does not fit", "message": "did not fit in round 1"})

        return "does not fit"

    hide_options = options(family.method)
    estimate = service.estimate(carrier, PAYLOAD_NAME, size, hide_options)
    is_fill = target.fill is not None and not family.payload_from

    # The framing alone takes more than the fill, or than the root rate allows.
    if (is_fill and (estimate.share or 0.0) > (target.fill or 0) / 100) or not size:
        _write(record, {**base, "status": "does not fit", "share": estimate.share})

        return "does not fit"

    output.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

    try:
        hidden = HiddenFile(PAYLOAD_NAME, payload(index, target.seed, size))
        service.hide(carrier, hidden, output, hide_options)
    except CapacityError as exc:
        _write(record, {**base, "status": "does not fit", "message": str(exc)})

        return "does not fit"

    seconds = time.perf_counter() - started
    warnings = service.detection_risks(hide_options, carrier, estimate)
    fill_warning = _fill_warning(family.method, estimate)
    reference = family.reference(index)

    if family.method == MethodId.LSB:
        changes = _lsb_changes(reference, output)
        rate = estimate.required * 8 / changes["values"]
    else:
        tiff = carrier if family.carrier is tiff_path else None
        changes = _dct_changes(reference, output, tiff)
        rate = estimate.required * 8 / changes["nonzero_ac"]

    row = {
        **base,
        "status": "ok",
        "share": estimate.share,
        "payload_bytes": size,
        "required_bytes": estimate.required,
        "capacity_bytes": estimate.available,
        "rate": rate,  # bits per colour value (LSB) or per non-zero AC coefficient (DCT)
        "seconds": round(seconds, 3),
        "warnings": list(warnings),
        "cover_warned": any(w != fill_warning for w in warnings),
        **changes,
    }
    _write(record, row)

    return "embedded"


def _write(record: Path, row: dict[str, object]) -> None:
    with atomic(record) as partial:
        partial.write_text(json.dumps(row))


def reveal_check(family_name: str, target: Target, indices: list[int]) -> None:
    """Reveal a random :data:`REVEAL_SHARE` of the outputs; every payload must match."""
    rows = {i: json.loads(record_path(family_name, target, i).read_text()) for i in indices}
    hidden = [i for i, row in rows.items() if row["status"] == "ok"]
    rng = np.random.default_rng([SEED, target.seed, zlib.crc32(family_name.encode())])
    count = min(len(hidden), max(1, round(REVEAL_SHARE * len(hidden))))
    label = target.condition(family_name)

    for index in (int(i) for i in rng.choice(np.array(hidden), count, replace=False)):
        size = rows[index]["payload_bytes"]

        with tempfile.TemporaryDirectory() as folder:
            embedded = output_path(family_name, target, index)
            result = service.reveal(embedded, Path(folder), PASSWORD)
            is_same = result.path.read_bytes() == payload(index, target.seed, size)

        if not is_same or result.cipher != CIPHER:
            fail(f"{label}: {name(index)} does not reveal its payload")

    log.info("%s: %d revealed, all match", label, count)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--family", choices=FAMILIES, action="append", required=True)
    parser.add_argument("--fills", help="comma-separated percentages (default: the family's)")
    parser.add_argument("--rates", help="comma-separated root rates, in place of fills")
    parser.add_argument("--count", type=int, required=True)
    args = parser.parse_args()

    os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")  # J-UNIWARD's products, per worker

    for family_name in args.family:
        family = FAMILIES[family_name]
        fills = tuple(int(f) for f in args.fills.split(",")) if args.fills else family.fills
        targets = (
            [Target(rate=int(r)) for r in args.rates.split(",")]
            if args.rates
            else [Target(fill=f) for f in fills]
        )
        indices = selection(family.cover_set, args.count)

        for target in targets:
            started = time.time()
            jobs = [(family_name, target, index) for index in indices]
            label = target.condition(family_name)

            with Pool(MAX_WORKERS) as pool:
                outcomes = pool.map(embed_one, jobs, chunksize=4)

            log.info(
                "%s: %d embedded, %d do not fit, %d skipped in %.0f s",
                label,
                outcomes.count("embedded"),
                outcomes.count("does not fit"),
                outcomes.count("skipped"),
                time.time() - started,
            )
            reveal_check(family_name, target, indices)
            mark_done(f"embed-{label}-{args.count}")


if __name__ == "__main__":
    main()
