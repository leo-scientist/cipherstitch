"""Rich-model features for tier 3b (bench venv).

    uv run --project benchmarks/detectability python benchmarks/detectability/features.py \
        --kind lsb --condition cover --condition lsb-f05 --count 3000

``--images lsb-crops`` (or ``jpeg-crops``) reads round 2's 256 × 256 crops instead of the set
named by ``--kind``.

- ``lsb``: SRMQ1 on each colour (3 × 12,753) and CRM with T = 3 (5,404): 43,663 values.
- ``jpeg``: DCTR on luminance (8,000), GFR on luminance (17,000), and DCTR on Cb and Cr at
  their own resolution (2 × 8,000): 41,000 values. Both read the coefficients and decompress
  them without rounding, as sealwatch does for luminance.

The co-occurrences use ``fast_srm`` (the same numbers as sealwatch's, checked by
``fast_srm.py``). Features are written as float32 in chunks of :data:`CHUNK` images,
``work/features/<kind>/<condition>/<chunk>.npy``; a chunk that exists is skipped.
"""

import argparse
import time
import warnings
from functools import partial
from multiprocessing import Pool
from pathlib import Path

import fast_srm
import jpeglib
import numpy as np
import numpy.typing as npt
import sealwatch as sw
from common import (
    COVERS,
    EMBEDDED,
    MAX_WORKERS,
    WORK,
    atomic,
    fail,
    is_usable,
    logger,
    mark_done,
    name,
)
from common import selection as chosen
from PIL import Image
from sealwatch.tools import jpeg as sealwatch_jpeg

CHUNK = 20
DIMENSIONS = {"lsb": 3 * 12753 + 5404, "jpeg": 3 * 8000 + 17000}
FEATURES = WORK / "features"
CRM_T = 3
GFR_STEPS = {75: (2.0, 4.0, 6.0, 8.0), 95: (0.1, 1.0, 1.5, 2.0)}  # Song et al., per scale
COVER_FOLDERS = {  # cover conditions: the folders prepare.py fills
    "cover": ("png", ".png"),
    "q75": ("q75", ".jpg"),
    "q95": ("q95", ".jpg"),
    "p0": ("p0", ".jpg"),
    "jnosi-cover": ("pillow-q90-420", ".jpg"),
    "crop-cover": ("crop-png", ".png"),
    "crop-q75": ("crop-q75", ".jpg"),
}
# Annex K luminance table, row-major; libjpeg scales it by quality as below.
_LUMINANCE = np.array(
    [16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55, 14, 13, 16, 24, 40, 57, 69, 56,
     14, 17, 22, 29, 51, 87, 80, 62, 18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113,
     92, 49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99]
)  # fmt: skip

log = logger("features")
Features = npt.NDArray[np.float32]


def image_path(kind: str, condition: str, index: int) -> Path:
    if condition in COVER_FOLDERS:
        folder, suffix = COVER_FOLDERS[condition]

        return COVERS / folder / f"{name(index)}{suffix}"

    return EMBEDDED / condition / f"{name(index)}{'.png' if kind == 'lsb' else '.jpg'}"


def lsb_features(path: Path) -> Features:
    with Image.open(path) as opened:
        rgb = np.asarray(opened.convert("RGB")).astype(np.float64)

    # sealwatch appends SRM's merged SPAM submodels in set order, which follows the process's
    # hash seed: sorted names keep every chunk in one order.
    srm = [sw.srmq1.extract(rgb[..., c]) for c in range(3)]
    parts = [[p[k] for k in sorted(p)] for p in srm] + [[*sw.crm.extract(rgb, Tc=CRM_T).values()]]

    return np.concatenate([np.ravel(v) for p in parts for v in p]).astype(np.float32)


def quality(table: npt.NDArray[np.integer]) -> int:
    """The libjpeg quality whose scaled Annex K luminance table is ``table``."""
    for qf in range(1, 101):
        scale = 5000 // qf if qf < 50 else 200 - 2 * qf
        steps = np.clip((_LUMINANCE * scale + 50) // 100, 1, 255)

        if np.array_equal(steps, np.ravel(table)):
            return qf

    raise ValueError("not a libjpeg-scaled Annex K table")


def gfr_steps(qf: int) -> tuple[float, ...]:
    """Song et al.'s steps for QF 75 and 95; in between, linear in the quality factor
    (``results/protocol.md``)."""
    if qf in GFR_STEPS:
        return GFR_STEPS[qf]

    low, high = min(GFR_STEPS), max(GFR_STEPS)
    share = min(max((qf - low) / (high - low), 0), 1)

    return tuple(a + share * (b - a) for a, b in zip(GFR_STEPS[low], GFR_STEPS[high], strict=True))


def dctr_step(qf: int) -> float:
    """sealwatch's (and Holub and Fridrich's) DCTR step for a quality factor."""
    return min(8 * (50 / qf), 100) if qf < 50 else max(8 * (2 - qf / 50), 0.2)


def jpeg_features(path: Path) -> Features:
    jpeg = jpeglib.read_dct(str(path))
    qf = quality(jpeg.qt[0])
    luminance = sw.dctr.extract_from_file(path, qf=qf)
    gabor = sw.gfr.extract_from_file(path, quantization_steps=list(gfr_steps(qf)))
    parts = [np.ravel(v) for v in luminance.values()] + [np.ravel(gabor)]

    for component, number in (("Cb", 1), ("Cr", 2)):
        plane = sealwatch_jpeg.decompress(
            y=getattr(jpeg, component), qt=jpeg.get_component_qt(number)
        )
        chroma = sw.dctr.extract(plane - 128, q=dctr_step(qf))
        parts += [np.ravel(v) for v in chroma.values()]

    return np.concatenate(parts).astype(np.float32)


def _init() -> None:
    warnings.simplefilter("ignore")
    fast_srm.install()


def chunk_path(kind: str, condition: str, number: int) -> Path:
    return FEATURES / kind / condition / f"{number:04d}.npy"


def compute_chunk(job: tuple[int, list[int]], kind: str, condition: str) -> str:
    number, indices = job
    target = chunk_path(kind, condition, number)

    if target.exists():
        return "skipped"

    extract = lsb_features if kind == "lsb" else jpeg_features
    missing = np.full(DIMENSIONS[kind], np.nan, dtype=np.float32)  # the payload did not fit
    rows = np.stack(
        [
            extract(image_path(kind, condition, index)) if is_usable(condition, index) else missing
            for index in indices
        ]
    )

    if rows.shape[1] != DIMENSIONS[kind]:
        fail(f"{kind}/{condition}: {rows.shape[1]} features, expected {DIMENSIONS[kind]}")

    with atomic(target) as partial_path, partial_path.open("wb") as file:
        np.save(file, rows)

    return "computed"


def load(kind: str, condition: str, count: int) -> Features:
    """The features of the first ``count`` images, in selection order."""
    chunks = -(-count // CHUNK)
    paths = [chunk_path(kind, condition, n) for n in range(chunks)]
    missing = [p for p in paths if not p.exists()]

    if missing:
        fail(f"{len(missing)} feature chunks missing for {kind}/{condition}; run features.py")

    rows = np.concatenate([np.load(p) for p in paths])

    if len(rows) < count or any(len(np.load(p, mmap_mode="r")) != CHUNK for p in paths[:-1]):
        fail(f"{kind}/{condition}: chunks of the wrong size; delete them and run features.py")

    return rows[:count]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--kind", choices=("lsb", "jpeg"), required=True)
    parser.add_argument("--condition", action="append", required=True)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--images", help="the index set, when not --kind's (the crops)")
    args = parser.parse_args()
    indices = chosen(args.images or args.kind, args.count)
    jobs = [
        (n, indices[start : start + CHUNK]) for n, start in enumerate(range(0, len(indices), CHUNK))
    ]

    with Pool(MAX_WORKERS, initializer=_init) as pool:
        for condition in args.condition:
            started = time.time()
            work = partial(compute_chunk, kind=args.kind, condition=condition)
            outcomes = pool.map(work, jobs)
            seconds = time.time() - started
            log.info(
                "%s/%s: %d chunks computed, %d skipped in %.0f s",
                args.kind,
                condition,
                outcomes.count("computed"),
                outcomes.count("skipped"),
                seconds,
            )
            mark_done(f"features-{args.kind}-{condition}-{args.count}")


if __name__ == "__main__":
    main()
