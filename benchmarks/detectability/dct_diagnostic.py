"""A diagnostic of DCT's tier 2 results, outside the gate and the headline numbers (bench venv).

    uv run --project benchmarks/detectability python benchmarks/detectability/dct_diagnostic.py \
        --count 3000

Two questions on the jpeg set (``results/protocol.md#dct-diagnostic``):

- Why the F5 estimator finds DCT on a JPEG more easily than nsF5 at the same payload. The
  changes of DCT, nsF5 and steghide are counted by component and mode. Simulations at the
  bound, on the same QF 75 covers and payloads, then tell the costs from the code: binary
  changes towards zero at CipherStitch's UERD costs over every coefficient at once
  (``uerd-bound``), the same split into the lead, head and tail that DCT codes one by one
  (``uerd-regions``), and conseal's ternary UERD per component (``uerd-conseal``). Two more,
  added after the first run, tell its differences apart: changes either way
  (``uerd-both-ways``) and the bits split over the components (``uerd-per-component``).
- What tells P-0 from Pillow's JPEG at the same settings (E-C). Each TIFF is compressed again
  step by step from P-0 towards libjpeg: P-0 rebuilt here (checked against P-0), the same
  float DCT after libjpeg's 8-bit colour conversion and chroma downsampling, then libjpeg
  itself with its float and its integer DCT (jpeglib, checked against Pillow's files).

Writes ``results/dct-diagnostic.md`` and two CSV files next to it. Per-image rows are kept in
``work/diagnostic/``, so a rerun only redoes the summaries.
"""

import argparse
import csv
import itertools
import json
import tempfile
import time
import warnings
import zlib
from collections.abc import Callable
from functools import partial
from pathlib import Path

import conseal as cl
import jpeglib
import numpy as np
import numpy.typing as npt
import sealwatch as sw
from common import (
    MANIFESTS,
    RESULTS,
    SEED,
    WORK,
    atomic,
    fail,
    is_usable,
    logger,
    name,
    selection,
    tiff_path,
)
from conditions import at_fill
from features import image_path
from metrics import evaluate, scalar_trainer
from PIL import Image

FILLS = (5, 20)
FILES = ("dct-q75", "nsf5", "steghide")  # embedded files on the QF 75 covers
SIMULATIONS = ("uerd-bound", "uerd-regions", "uerd-conseal")
LATER_SIMULATIONS = ("uerd-both-ways", "uerd-per-component")  # added after the first run
CONDITIONS = (*FILES, *SIMULATIONS, *LATER_SIMULATIONS)
# Simulations at the bound: how the bits are split, and whether a change may move away from zero.
BOUNDS = {
    "uerd-bound": ("all", False),
    "uerd-regions": ("regions", False),
    "uerd-both-ways": ("all", True),
    "uerd-per-component": ("components", False),
}
MODE_TABLES = ("dct-q75", "nsf5", "uerd-bound")  # shown mode by mode at the last fill
F5_MODES = ((0, 1), (1, 0), (1, 1))  # the luminance modes the F5 estimator reads
STATS = ("bits", "changed", "luminance", "f5_changed", "f5_nonzero", "to_zero", "distortion", "f5")
VARIANTS = ("p0", "p0-rebuilt", "libjpeg-front-end", "libjpeg-float", "libjpeg-integer")
VARIANT_STATS = ("luminance_equal", "chroma_equal", "f5")
LIBJPEG = "turbo210"
QUALITY = 90  # cipherstitch.media.precover.QUALITY
BISECTIONS = 60
PROGRESS_EVERY = 250

# Mirrors of round 1's writer: cipherstitch.methods.uerd (writer policy, since replaced by
# J-UNIWARD) and cipherstitch.methods.dct (layout).
MEDIAN_COST = 8
MAX_COST = 64
NEIGHBOUR_WEIGHT = 0.25
SALT_BYTES = 16
LEAD_BITS = 8 * SALT_BYTES
LEAD_UNITS = 16384
HEAD_UNITS = 65536
HEAD_DATA_BYTES = 64
HEAD_BITS = 8 * (4 + HEAD_DATA_BYTES)  # u32 length ‖ the first data bytes
TAIL_UNITS_PER_BIT = 512

# JFIF's RGB to YCbCr as cipherstitch.media.precover computes it, in floating point.
YCBCR = np.array([[0.299, 0.587, 0.114], [-0.168736, -0.331264, 0.5], [0.5, -0.418688, -0.081312]])
# libjpeg's fixed point (jccolor.c): FIX(x) is x · 2^16 rounded.
SCALE_BITS = 16
ONE_HALF = 1 << (SCALE_BITS - 1)
CBCR_OFFSET = 128 << SCALE_BITS
CENTRE = 128
BLOCK = 8
SUBSAMPLING = 2
CHROMA_420 = ((2, 2), (1, 1), (1, 1))

log = logger("dct-diagnostic")

Array = npt.NDArray[np.float64]
Blocks = npt.NDArray[np.int16]  # rows × columns × 8 × 8, as jpeglib returns them
Table = npt.NDArray[np.uint16]
Modes = npt.NDArray[np.int64]


def _dct_matrix() -> Array:
    k = np.arange(BLOCK)
    matrix = np.sqrt(2 / BLOCK) * np.cos((2 * k[None, :] + 1) * k[:, None] * np.pi / (2 * BLOCK))
    matrix[0] /= np.sqrt(2)

    return matrix


DCT = _dct_matrix()


def _components(jpeg: jpeglib.DCTJPEG) -> tuple[list[Blocks], list[Table]]:
    present = [c for c in ("Y", "Cb", "Cr") if getattr(jpeg, c) is not None]
    tables = [jpeg.qt[jpeg.quant_tbl_no[number]] for number in range(len(present))]

    return [getattr(jpeg, c).astype(np.int16) for c in present], tables


def _read(path: Path) -> list[Blocks]:
    return _components(jpeglib.read_dct(str(path)))[0]


def _ac(shape: tuple[int, ...]) -> npt.NDArray[np.bool_]:
    mask = np.ones(shape, dtype=bool)
    mask[..., 0, 0] = False

    return mask


def uerd_costs(blocks: list[Blocks], tables: list[Table]) -> list[Array]:
    """CipherStitch's UERD cost of every coefficient (``cipherstitch.methods.uerd.Costs`` in
    round 1's writer): the step over the block's energy plus a quarter of its neighbours',
    scaled so the median non-zero AC coefficient costs :data:`MEDIAN_COST`, rounded and
    clipped."""
    raw = []

    for component, table in zip(blocks, tables, strict=True):
        steps = table.astype(np.float64)
        ac_steps = steps.copy()
        ac_steps[0, 0] = 0
        energy = np.einsum("rcij,ij->rc", np.abs(component).astype(np.float64), ac_steps)
        rows, columns = energy.shape
        padded = np.pad(energy, 1)
        window = sum(padded[r : r + rows, c : c + columns] for r in range(3) for c in range(3))
        spread = np.maximum(energy + NEIGHBOUR_WEIGHT * (window - energy), 1)
        raw.append(steps[None, None] / spread[:, :, None, None])

    nonzero = np.concatenate([r[_ac(r.shape) & (b != 0)] for r, b in zip(raw, blocks, strict=True)])
    factor = MEDIAN_COST / np.median(nonzero) if nonzero.size else 1.0

    return [np.clip(np.rint(r * factor), 1, MAX_COST) for r in raw]


def _probabilities(costs: Array, bits: float) -> Array:
    """A binary embedding's change probabilities at the bound: 1 / (1 + e^(λρ)), with λ such
    that their entropy is ``bits``."""

    def entropy(scale: float) -> float:
        exponent = scale * costs
        log_change, log_keep = -np.logaddexp(0, exponent), -np.logaddexp(0, -exponent)
        change = np.exp(log_change)

        return float(-(change * log_change + (1 - change) * log_keep).sum() / np.log(2))

    low, high = 0.0, 1.0

    while entropy(high) > bits:
        high *= 2

    for _ in range(BISECTIONS):
        middle = (low + high) / 2

        if entropy(middle) > bits:
            low = middle
        else:
            high = middle

    return 1 / (1 + np.exp(high * costs))


def _cipherstitch_bits(required_bytes: int) -> tuple[int, int, int]:
    """The bits DCT writes into its lead, head and tail for ``required_bytes`` (the salt and
    the sealed body)."""
    body = required_bytes - SALT_BYTES

    return LEAD_BITS, HEAD_BITS, 8 * max(body - HEAD_DATA_BYTES, 0)


def _regions(count: int, tail_bits: int, rng: np.random.Generator) -> list[npt.NDArray[np.intp]]:
    """DCT's lead, head and tail sizes among ``count`` units (``cipherstitch.methods.dct``),
    each a random subset: the lead's public positions and the keyed order both spread over
    the image."""
    order = rng.permutation(count)
    lead = min(LEAD_UNITS, count // 8)
    head = min(HEAD_UNITS, (count - lead) // 4)
    span = min(count - lead - head, TAIL_UNITS_PER_BIT * tail_bits)

    return [order[:lead], order[lead : lead + head], order[lead + head : lead + head + span]]


def _per_component(
    masks: list[npt.NDArray[np.bool_]], values: npt.NDArray[np.int16], bits: int
) -> list[tuple[npt.NDArray[np.intp], int]]:
    """Each component's units, with the bits in proportion to its non-zero AC coefficients
    (as nsF5 and conseal's UERD split them)."""
    bounds = np.cumsum([0, *(int(m.sum()) for m in masks)])
    units = [np.arange(start, stop) for start, stop in itertools.pairwise(bounds)]
    nonzero = np.array([np.count_nonzero(values[u]) for u in units])

    return [(u, round(bits * n / nonzero.sum())) for u, n in zip(units, nonzero, strict=True)]


def simulate_bound(
    cover: list[Blocks],
    costs: list[Array],
    required_bytes: int,
    rng: np.random.Generator,
    *,
    split: str,
    is_both_ways: bool,
) -> list[Blocks] | None:
    """Binary changes at the bound, at DCT's costs and with DCT's message bits, over every
    non-zero AC coefficient at once (``split`` "all"), region by region ("regions") or
    component by component ("components"). A change lowers the magnitude by one, or moves
    either way at random (``is_both_ways``). ``None`` if a region holds no more non-zero
    coefficients than bits."""
    masks = [_ac(c.shape) for c in cover]
    values = np.concatenate([c[m] for c, m in zip(cover, masks, strict=True)])
    flat_costs = np.concatenate([c[m] for c, m in zip(costs, masks, strict=True)])
    bits = _cipherstitch_bits(required_bytes)

    if split == "regions":
        regions = list(zip(_regions(values.size, bits[2], rng), bits, strict=True))
    elif split == "components":
        regions = _per_component(masks, values, sum(bits))
    else:
        regions = [(np.arange(values.size), sum(bits))]

    changed = np.zeros(values.size, dtype=bool)

    for units, region_bits in regions:
        if not region_bits:
            continue

        dry = units[values[units] != 0]

        if dry.size <= region_bits:
            return None

        changed[dry] = rng.random(dry.size) < _probabilities(flat_costs[dry], region_bits)

    steps = rng.choice(np.array([-1, 1]), values.size) if is_both_ways else -np.sign(values)
    values = values + steps * changed
    result, start = [], 0

    for component, mask in zip(cover, masks, strict=True):
        stop = start + int(mask.sum())
        embedded = component.copy()
        embedded[mask] = values[start:stop]
        result.append(embedded)
        start = stop

    return result


def simulate_conseal(
    cover: list[Blocks], tables: list[Table], rate: float, seed: int
) -> list[Blocks]:
    """conseal's UERD on each component with non-zero AC coefficients, at ``rate`` bits per
    non-zero AC coefficient (as the nsF5 reference)."""
    result = []

    for number, (component, table) in enumerate(zip(cover, tables, strict=True)):
        if not np.count_nonzero(component[_ac(component.shape)]):
            result.append(component)
            continue

        embedded = cl.uerd.simulate_single_channel(component, table, rate, seed=seed + number)
        result.append(embedded.astype(np.int16))

    return result


def change_row(
    cover: list[Blocks], embedded: list[Blocks], costs: list[Array], table: Table, bits: float
) -> tuple[Array, Modes]:
    """One embedding's :data:`STATS`, and its luminance changes per mode."""
    changed = [c != e for c, e in zip(cover, embedded, strict=True)]
    modes = changed[0].sum(axis=(0, 1)).astype(np.int64)
    row = [
        bits,
        sum(int(m.sum()) for m in changed),
        int(changed[0].sum()),
        sum(int(modes[mode]) for mode in F5_MODES),
        sum(int(np.count_nonzero(cover[0][(..., *mode)])) for mode in F5_MODES),
        sum(int(np.count_nonzero(m & (e == 0))) for m, e in zip(changed, embedded, strict=True)),
        sum(float(c[m].sum()) for c, m in zip(costs, changed, strict=True)),
        float(sw.F5.attack(embedded[0], table)),
    ]

    return np.array(row), modes


def _record(condition: str, index: int) -> dict[str, float] | None:
    row = json.loads((MANIFESTS / condition / f"{name(index)}.json").read_text())

    return row if row["status"] == "ok" else None


def _embedded(
    condition: str,
    fill: int,
    index: int,
    cover: tuple[list[Blocks], list[Table], list[Array]],
    record: dict[str, float],
) -> list[Blocks] | None:
    blocks, tables, costs = cover
    key = at_fill(condition, fill)

    if condition in FILES:
        return _read(image_path("jpeg", key, index)) if is_usable(key, index) else None

    rng = np.random.default_rng([SEED, index, fill, zlib.crc32(condition.encode())])

    if condition == "uerd-conseal":
        return simulate_conseal(blocks, tables, record["rate"], int(rng.integers(2**31)))

    split, is_both_ways = BOUNDS[condition]
    required = int(record["required_bytes"])

    return simulate_bound(blocks, costs, required, rng, split=split, is_both_ways=is_both_ways)


def changes_of(index: int, conditions: tuple[str, ...]) -> dict[str, Array]:
    """One image's rows: the cover's F5 score and non-zero luminance AC per mode, then each
    condition's :data:`STATS` and luminance changes per mode at every fill (NaN rows where
    nothing was embedded)."""
    blocks, tables = _components(jpeglib.read_dct(str(image_path("jpeg", "q75", index))))
    cover = (blocks, tables, uerd_costs(blocks, tables))
    nonzero = (blocks[0] != 0).sum(axis=(0, 1))
    result = {"cover": np.concatenate([[sw.F5.attack(blocks[0], tables[0])], nonzero.ravel()])}

    for fill in FILLS:
        record = _record(at_fill("dct-q75", fill), index)

        for condition in conditions:
            key = at_fill(condition, fill)
            embedded = None if record is None else _embedded(condition, fill, index, cover, record)
            row, modes = np.full(len(STATS), np.nan), np.zeros((BLOCK, BLOCK), dtype=np.int64)

            if embedded is not None:
                bits = 8 * record["required_bytes"]
                row, modes = change_row(blocks, embedded, cover[2], tables[0], bits)

            result[key], result[f"{key}-modes"] = row, modes.ravel().astype(np.float64)

    return result


def _float_planes(rgb: npt.NDArray[np.uint8]) -> list[Array]:
    """P-0's planes: level-shifted YCbCr in floating point, chroma averaged over 2 × 2."""
    ycbcr = rgb.astype(np.float64) @ YCBCR.T - np.array([CENTRE, 0, 0])
    rows, columns = ycbcr.shape[0] // SUBSAMPLING, ycbcr.shape[1] // SUBSAMPLING
    chroma = ycbcr[..., 1:].reshape(rows, SUBSAMPLING, columns, SUBSAMPLING, 2).mean(axis=(1, 3))

    return [ycbcr[..., 0], chroma[..., 0], chroma[..., 1]]


def _fix(value: float) -> int:
    return int(value * (1 << SCALE_BITS) + 0.5)


def _libjpeg_planes(rgb: npt.NDArray[np.uint8]) -> list[Array]:
    """libjpeg's 8-bit samples, level-shifted: rgb_ycc_convert (jccolor.c), then
    h2v2_downsample (jcsample.c), whose rounding bias alternates 1, 2 along a row."""
    r, g, b = (rgb[..., c].astype(np.int64) for c in range(3))
    y = (_fix(0.299) * r + _fix(0.587) * g + _fix(0.114) * b + ONE_HALF) >> SCALE_BITS
    offset = CBCR_OFFSET + ONE_HALF - 1
    cb = (-_fix(0.16874) * r - _fix(0.33126) * g + _fix(0.5) * b + offset) >> SCALE_BITS
    cr = (_fix(0.5) * r - _fix(0.41869) * g - _fix(0.08131) * b + offset) >> SCALE_BITS
    rows, columns = y.shape[0] // SUBSAMPLING, y.shape[1] // SUBSAMPLING
    bias = np.where(np.arange(columns) % 2 == 0, 1, 2)
    chroma = [
        (p.reshape(rows, SUBSAMPLING, columns, SUBSAMPLING).sum(axis=(1, 3)) + bias) >> 2
        for p in (cb, cr)
    ]

    return [(p - CENTRE).astype(np.float64) for p in (y, *chroma)]


def _quantized(planes: list[Array], tables: list[Table]) -> list[Blocks]:
    """P-0's float DCT and rounding (``cipherstitch.media.precover._quantized``)."""
    result = []

    for plane, table in zip(planes, tables, strict=True):
        rows, columns = plane.shape[0] // BLOCK, plane.shape[1] // BLOCK
        blocks = plane.reshape(rows, BLOCK, columns, BLOCK).transpose(0, 2, 1, 3)
        result.append(np.rint((DCT @ blocks @ DCT.T) / table).astype(np.int16))

    return result


def _libjpeg(rgb: npt.NDArray[np.uint8], method: jpeglib.DCTMethod, folder: Path) -> list[Blocks]:
    image = jpeglib.from_spatial(np.ascontiguousarray(rgb))
    image.samp_factor = CHROMA_420
    path = folder / "compressed.jpg"
    image.write_spatial(str(path), qt=QUALITY, dct_method=method)

    return _read(path)


def _differing(first: list[Blocks], second: list[Blocks]) -> int:
    return sum(int(np.count_nonzero(a != b)) for a, b in zip(first, second, strict=True))


def compressor_of(index: int, folder: Path) -> dict[str, Array]:
    """One image's row: Pillow's F5 score, each variant's :data:`VARIANT_STATS` against
    Pillow, and the two checks (coefficients that differ between P-0 and its rebuild, and
    between Pillow and libjpeg's integer DCT)."""
    pillow, tables = _components(jpeglib.read_dct(str(image_path("jpeg", "jnosi-cover", index))))

    with Image.open(tiff_path(index)) as opened:
        rgb = np.asarray(opened.convert("RGB"))

    if any(side % (BLOCK * SUBSAMPLING) for side in rgb.shape[:2]):
        fail(f"{name(index)}: {rgb.shape[:2]} is not whole 4:2:0 blocks; pad it like P-0")

    variants = {
        "p0": _read(image_path("jpeg", "p0", index)),
        "p0-rebuilt": _quantized(_float_planes(rgb), tables),
        "libjpeg-front-end": _quantized(_libjpeg_planes(rgb), tables),
        "libjpeg-float": _libjpeg(rgb, jpeglib.JDCT_FLOAT, folder),
        "libjpeg-integer": _libjpeg(rgb, jpeglib.JDCT_ISLOW, folder),
    }
    row = [float(sw.F5.attack(pillow[0], tables[0]))]

    for blocks in variants.values():
        chroma = np.mean([np.mean(a == b) for a, b in zip(blocks[1:], pillow[1:], strict=True)])
        row += [float(np.mean(blocks[0] == pillow[0])), float(chroma)]
        row.append(float(sw.F5.attack(blocks[0], tables[0])))

    row.append(_differing(variants["p0"], variants["p0-rebuilt"]))
    row.append(_differing(variants["libjpeg-integer"], pillow))

    return {"row": np.array(row)}


def _rows(
    label: str, indices: list[int], one: Callable[[int], dict[str, Array]]
) -> dict[str, Array]:
    """Every image's rows, stacked per key, computed once and kept in ``work/diagnostic``."""
    target = WORK / "diagnostic" / f"{label}-{len(indices)}.npz"

    if target.exists():
        with np.load(target) as saved:
            return {key: saved[key] for key in saved.files}

    started, rows = time.time(), []

    for number, index in enumerate(indices, 1):
        rows.append(one(index))

        if number % PROGRESS_EVERY == 0:
            log.info(
                "%s: %d of %d images (%.0f s)", label, number, len(indices), time.time() - started
            )

    stacked = {key: np.stack([r[key] for r in rows]) for key in rows[0]}

    with atomic(target) as temporary, temporary.open("wb") as file:
        np.savez(file, indices=np.array(indices), **stacked)

    return stacked


def _change_summary(rows: dict[str, Array]) -> list[dict[str, object]]:
    cover_f5 = rows["cover"][:, 0]
    column = {stat: number for number, stat in enumerate(STATS)}
    results = []

    for fill in FILLS:
        for condition in CONDITIONS:
            data = rows[at_fill(condition, fill)]
            keep = ~np.isnan(data).any(axis=1)
            total = data[keep].sum(axis=0)

            def share(numerator: str, denominator: str, total: Array = total) -> str:
                return f"{total[column[numerator]] / total[column[denominator]]:.4f}"

            scores = evaluate(cover_f5[keep], data[keep, column["f5"]], scalar_trainer)
            results.append(
                {
                    "condition": condition,
                    "fill": fill,
                    "n": int(keep.sum()),
                    "changes_per_bit": share("changed", "bits"),
                    "distortion_per_bit": share("distortion", "bits"),
                    "luminance_share": share("luminance", "changed"),
                    "f5_mode_change_rate": share("f5_changed", "f5_nonzero"),
                    "to_zero_share": share("to_zero", "changed"),
                    "f5_mean": f"{np.mean(data[keep, column['f5']]):.4f}",
                    "pe_mean": f"{scores.pe_mean:.4f}",
                    "pe_std": f"{scores.pe_std:.4f}",
                    "auc_mean": f"{scores.auc_mean:.4f}",
                }
            )

    return results


def _compressor_summary(rows: dict[str, Array]) -> list[dict[str, object]]:
    data = rows["row"]
    pillow = data[:, 0]
    results = [{"variant": "pillow", "n": len(data), "f5_mean": f"{np.mean(pillow):.4f}"}]

    for number, variant in enumerate(VARIANTS):
        first = 1 + number * len(VARIANT_STATS)
        scores = evaluate(pillow, data[:, first + 2], scalar_trainer)
        results.append(
            {
                "variant": variant,
                "n": len(data),
                "luminance_equal": f"{np.mean(data[:, first]):.5f}",
                "chroma_equal": f"{np.mean(data[:, first + 1]):.5f}",
                "f5_mean": f"{np.mean(data[:, first + 2]):.4f}",
                "pe_mean": f"{scores.pe_mean:.4f}",
                "pe_std": f"{scores.pe_std:.4f}",
                "auc_mean": f"{scores.auc_mean:.4f}",
            }
        )

    return results


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))

    with atomic(path) as temporary, temporary.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, restval="")
        writer.writeheader()
        writer.writerows(rows)


def _mode_table(rows: dict[str, Array], condition: str) -> list[str]:
    """Luminance changes per non-zero coefficient, mode by mode, in percent."""
    key = at_fill(condition, FILLS[-1])
    keep = ~np.isnan(rows[key]).any(axis=1)
    changes = rows[f"{key}-modes"][keep].sum(axis=0).reshape(BLOCK, BLOCK)
    nonzero = rows["cover"][keep, 1:].sum(axis=0).reshape(BLOCK, BLOCK)
    rate = 100 * changes / np.maximum(nonzero, 1)
    lines = [f"`{key}`, % of non-zero coefficients changed:", ""]
    lines += ["| Row \\ column | " + " | ".join(str(c) for c in range(BLOCK)) + " |"]
    lines += ["|---" * (BLOCK + 1) + "|"]

    for row in range(BLOCK):
        cells = [f"{rate[row, column]:.1f}" for column in range(BLOCK)]
        cells[0] = "DC" if row == 0 else cells[0]
        lines.append(f"| {row} | " + " | ".join(cells) + " |")

    return [*lines, ""]


def _markdown(
    changes: list[dict[str, object]],
    compressor: list[dict[str, object]],
    change_rows: dict[str, Array],
    checks: tuple[float, float, int],
    count: int,
) -> str:
    lines = [
        "# DCT diagnostic",
        "",
        f"Written by `dct_diagnostic.py --count {count}` on the jpeg set. Exploratory: not part"
        " of the gate or the headline numbers (`protocol.md#dct-diagnostic`). P_E and AUC are"
        " the F5 estimator's, over the study's 10 splits.",
        "",
        "## Where the changes go (QF 75 covers)",
        "",
        "Per payload bit (CipherStitch's sealed payload at that fill, the same for every row);"
        " distortion in CipherStitch's UERD costs.",
        "",
        "| Condition | Fill | n | Changes per bit | Distortion per bit | Luminance share"
        " | Change rate in modes 01, 10, 11 | Changes to zero | F5 P_E | F5 AUC |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]

    for row in changes:
        lines.append(
            f"| {row['condition']} | {row['fill']}% | {row['n']} | {row['changes_per_bit']}"
            f" | {row['distortion_per_bit']} | {row['luminance_share']}"
            f" | {row['f5_mode_change_rate']} | {row['to_zero_share']}"
            f" | {row['pe_mean']} ± {row['pe_std']} | {row['auc_mean']} |"
        )

    lines += ["", f"## Luminance changes by mode at {FILLS[-1]}%", ""]

    for condition in MODE_TABLES:
        lines += _mode_table(change_rows, condition)

    rebuilt, integer, images = checks
    lines += [
        "## What tells P-0 from Pillow's JPEG (E-C)",
        "",
        f"Each variant against Pillow's JPEG at quality {QUALITY}, 4:2:0, from the same TIFF.",
        "",
        "| Variant | Luminance coefficients equal to Pillow's | Chroma equal | Mean F5 estimate"
        " | F5 P_E against Pillow | F5 AUC |",
        "|---|---|---|---|---|---|",
    ]

    for row in compressor:
        if row["variant"] == "pillow":
            lines.append(f"| pillow | 1 | 1 | {row['f5_mean']} | | |")
            continue

        lines.append(
            f"| {row['variant']} | {row['luminance_equal']} | {row['chroma_equal']}"
            f" | {row['f5_mean']} | {row['pe_mean']} ± {row['pe_std']} | {row['auc_mean']} |"
        )

    lines += [
        "",
        f"Checks over {images} images: P-0 rebuilt here differs from P-0 in {rebuilt:.0f}"
        f" coefficients; libjpeg's integer DCT ({LIBJPEG} in jpeglib) differs from Pillow's"
        f" files in {integer:.0f}.",
        "",
    ]

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, required=True)
    args = parser.parse_args()
    warnings.simplefilter("ignore")
    jpeglib.version.set(LIBJPEG)
    indices = selection("jpeg", args.count)
    (WORK / "diagnostic").mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=WORK / "diagnostic") as folder:
        compressor_rows = _rows("compressor", indices, lambda i: compressor_of(i, Path(folder)))

    change_rows = _rows("changes", indices, partial(changes_of, conditions=(*FILES, *SIMULATIONS)))
    change_rows |= _rows("variants", indices, partial(changes_of, conditions=LATER_SIMULATIONS))
    changes = _change_summary(change_rows)
    compressor = _compressor_summary(compressor_rows)
    checks = (*compressor_rows["row"][:, -2:].sum(axis=0), len(indices))
    _write_csv(RESULTS / "dct-diagnostic-changes.csv", changes)
    _write_csv(RESULTS / "dct-diagnostic-compressor.csv", compressor)
    markdown = _markdown(changes, compressor, change_rows, checks, args.count)

    with atomic(RESULTS / "dct-diagnostic.md") as temporary:
        temporary.write_text(markdown)

    log.info("written results/dct-diagnostic.md")


if __name__ == "__main__":
    main()
