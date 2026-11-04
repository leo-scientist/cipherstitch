"""Download ALASKA v2 TIFFs and make the covers (main venv).

    uv run python benchmarks/detectability/prepare.py --set lsb --count 3000
    uv run python benchmarks/detectability/prepare.py --set jpeg --count 3000
    uv run python benchmarks/detectability/prepare.py --set lsb --crops 750

Each set is a fixed order of candidate indices (``SEED``). The first ``count`` that download,
check out and are not mostly grey are the set, so a smaller run is a prefix of a larger one.
Every step skips what is already there.

- ``lsb``: PNG covers, the TIFF's pixels as RGB without metadata.
- ``jpeg``: Pillow JPEGs at QF 75 and 95 (4:4:4, as ALASKA's own JPG sets), Pillow JPEGs at
  the precover's quality with 4:2:0 and optimized tables (J-noSI), and P-0: the TIFF
  compressed by CipherStitch with nothing embedded.

``--crops n`` cuts the first n images of a prepared set into their four 256 × 256 quadrants
for round 2's scaling check: PNG covers for ``lsb``, QF 75 JPEGs for ``jpeg``, listed in
``selection-<set>-crops.txt`` image by image.
"""

import argparse
import dataclasses
import hashlib
import http.client
import json
import re
import time
import urllib.request
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from common import (
    COVERS,
    MAX_WORKERS,
    ROOT,
    SEED,
    SETS,
    TIFFS,
    WORK,
    atomic,
    logger,
    mark_done,
    name,
    selection,
    tiff_path,
)
from conditions import CROPS_PER_IMAGE
from PIL import Image

from cipherstitch.media import jpeg, precover

BASE_URL = "https://alaska.utt.fr/DATASETS/ALASKA_v2_TIFF_512_COLOR/"
CAMERAS_URL = "https://alaska.utt.fr/ALASKAv2Base_by_DSC.txt"
FIRST_INDEX, LAST_INDEX = 1, 80005
EXCLUDED = (  # Leica_Mono (effectively greyscale), then the *_FLATS and *_TEXTURES groups
    range(33325, 33548),
    range(15636, 16262),
    range(38015, 38861),
)
MAX_GREY_SHARE = 0.5  # pixels with R = G = B
TIFF_SIZE = 786572  # every ALASKA_v2_TIFF_512_COLOR file: 512 × 512 × 3 bytes and a header
DOWNLOADS = 4  # parallel downloads at most
RETRIES = 5
TIMEOUT = 60
JPEG_QUALITIES = (75, 95)
JNOSI_SUBSAMPLING = 2  # Pillow's 4:2:0, the precover's chroma
CROP_SIZE = 256
CROP_QUALITY = 75
_CAMERA_LINE = re.compile(r"^\s*\d+\s*::\s*(\d+)\s*-->\s*(\d+)\s+\./(\S+)")

log = logger("prepare")


@dataclass(frozen=True, slots=True)
class Record:
    """What a verified download holds; ``work/tiff/<name>.json``."""

    index: int
    sha256: str
    size: int
    grey_share: float

    @property
    def is_usable(self) -> bool:
        return self.grey_share <= MAX_GREY_SHARE


def candidates(kind: str) -> list[int]:
    """The set's candidate indices in order: a seeded permutation of every index that is
    not excluded, even positions for ``lsb``, odd ones for ``jpeg``."""
    excluded = {index for group in EXCLUDED for index in group}
    allowed = [i for i in range(FIRST_INDEX, LAST_INDEX + 1) if i not in excluded]
    order = np.random.default_rng(SEED).permutation(np.array(allowed))

    return [int(i) for i in order[SETS.index(kind) :: len(SETS)]]


def _record_path(index: int) -> Path:
    return TIFFS / f"{name(index)}.json"


def _fetch(url: str, target: Path, expected_size: int | None) -> None:
    """Download ``url`` to ``target`` with retries and backoff, checking the size."""
    for attempt in range(RETRIES):
        try:
            with atomic(target) as partial:
                with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
                    partial.write_bytes(response.read())

                size = partial.stat().st_size

                if expected_size is not None and size != expected_size:
                    raise OSError(f"{url}: {size} bytes, expected {expected_size}")

            return
        except (OSError, http.client.HTTPException) as exc:  # e.g. IncompleteRead
            delay = 2 ** (attempt + 1)
            log.warning("%s (attempt %d), retrying in %d s", exc, attempt + 1, delay)
            time.sleep(delay)

    raise OSError(f"{url}: failed after {RETRIES} attempts")


def _grey_share(path: Path) -> float:
    with Image.open(path) as opened:
        pixels = np.asarray(opened.convert("RGB"))

    is_grey = (pixels[..., 0] == pixels[..., 1]) & (pixels[..., 1] == pixels[..., 2])

    return float(is_grey.mean())


def record(index: int) -> Record:
    """The verified download of ``index``, fetched first if needed."""
    path = _record_path(index)

    if path.exists():
        return Record(**json.loads(path.read_text()))

    tiff = tiff_path(index)

    if not tiff.exists() or tiff.stat().st_size != TIFF_SIZE:
        _fetch(f"{BASE_URL}{name(index)}.tif", tiff, TIFF_SIZE)

    data = tiff.read_bytes()
    result = Record(index, hashlib.sha256(data).hexdigest(), len(data), _grey_share(tiff))

    with atomic(path) as partial:
        partial.write_text(json.dumps(dataclasses.asdict(result)))

    return result


def selected(kind: str, count: int) -> list[Record]:
    """The first ``count`` usable images of the set, downloading as needed."""
    order = iter(candidates(kind))
    chosen: list[Record] = []

    with ThreadPoolExecutor(DOWNLOADS) as pool:
        while len(chosen) < count:
            batch = [index for _, index in zip(range(count - len(chosen)), order, strict=False)]

            if not batch:
                raise SystemExit(f"{kind}: only {len(chosen)} usable images in ALASKA")

            for item in pool.map(record, batch):
                if item.is_usable:
                    chosen.append(item)
                else:
                    grey = 100 * item.grey_share
                    log.info("%s: %s is %.0f%% grey, left out", kind, name(item.index), grey)

    return chosen


def cameras() -> list[tuple[range, str]]:
    """ALASKA's camera list: each camera's index range."""
    path = WORK / "cameras.txt"

    if not path.exists():
        _fetch(CAMERAS_URL, path, None)

    lines = (_CAMERA_LINE.match(line) for line in path.read_text().splitlines())

    return [(range(int(m[1]), int(m[2]) + 1), m[3]) for m in lines if m]


def camera(index: int, table: list[tuple[range, str]]) -> str:
    return next(model for indices, model in table if index in indices)


def write_lists() -> None:
    """``indices.txt`` (each set's images, in order) and ``sha256.txt`` (every download)."""
    table = cameras()
    rows = ["# set rank index camera"]

    for kind in SETS:
        path = WORK / f"selection-{kind}.txt"

        if path.exists():
            indices = [int(line) for line in path.read_text().split()]
            rows += [
                f"{kind} {rank} {name(i)} {camera(i, table)}" for rank, i in enumerate(indices)
            ]

    records = sorted(
        (Record(**json.loads(p.read_text())) for p in TIFFS.glob("*.json")), key=lambda r: r.index
    )
    digests = ["# index sha256 bytes grey_share"]
    digests += [f"{name(r.index)} {r.sha256} {r.size} {r.grey_share:.4f}" for r in records]
    (ROOT / "indices.txt").write_text("\n".join(rows) + "\n")
    (ROOT / "sha256.txt").write_text("\n".join(digests) + "\n")


def _png_cover(index: int) -> None:
    target = COVERS / "png" / f"{name(index)}.png"

    if target.exists():
        return

    with Image.open(tiff_path(index)) as opened:
        pixels = np.asarray(opened.convert("RGB"))

    with atomic(target) as partial:
        Image.fromarray(pixels).save(partial, format="PNG")  # a new image: no metadata


def _jpeg_covers(index: int) -> None:
    with Image.open(tiff_path(index)) as opened:
        rgb = opened.convert("RGB")

    for quality in JPEG_QUALITIES:
        target = COVERS / f"q{quality}" / f"{name(index)}.jpg"

        if not target.exists():
            with atomic(target) as partial:
                rgb.save(partial, format="JPEG", quality=quality, subsampling=0)

    target = COVERS / jnosi_folder() / f"{name(index)}.jpg"

    if not target.exists():
        with atomic(target) as partial:
            options = {"quality": precover.QUALITY, "subsampling": JNOSI_SUBSAMPLING}
            rgb.save(partial, format="JPEG", optimize=True, **options)

    target = COVERS / "p0" / f"{name(index)}.jpg"

    if not target.exists():
        compressed = precover.compress(tiff_path(index))
        coefficients = compressed.coefficients
        cover = jpeg.EmbeddedCompressedJpeg(
            coefficients.ac,
            coefficients.dc,
            coefficients.components,
            compressed.size,
            compressed.icc_profile,
            compressed.subsampling,
        )

        with atomic(target) as partial:
            cover.save(partial)


def crop_id(index: int, quadrant: int) -> int:
    """A crop's number: its image's index, then its quadrant (0 to 3, row by row)."""
    return 10 * index + quadrant


def _crops(index: int, kind: str) -> None:
    with Image.open(tiff_path(index)) as opened:
        rgb = opened.convert("RGB")

    for quadrant in range(CROPS_PER_IMAGE):
        top, left = (CROP_SIZE * k for k in divmod(quadrant, 2))
        crop = rgb.crop((left, top, left + CROP_SIZE, top + CROP_SIZE))
        stem = name(crop_id(index, quadrant))

        if kind == "lsb":
            target = COVERS / "crop-png" / f"{stem}.png"
            options: dict[str, object] = {"format": "PNG"}
        else:
            target = COVERS / "crop-q75" / f"{stem}.jpg"
            options = {"format": "JPEG", "quality": CROP_QUALITY, "subsampling": 0}

        if not target.exists():
            with atomic(target) as partial:
                crop.save(partial, **options)


def make_crops(kind: str, count: int) -> None:
    indices = selection(kind, count)
    crops = [crop_id(i, q) for i in indices for q in range(CROPS_PER_IMAGE)]
    (WORK / f"selection-{kind}-crops.txt").write_text("\n".join(map(name, crops)) + "\n")
    started = time.time()

    with Pool(MAX_WORKERS) as pool:
        pool.starmap(_crops, [(index, kind) for index in indices])

    log.info("%s: %d crops made in %.0f s", kind, len(crops), time.time() - started)
    mark_done(f"prepare-{kind}-crops-{count}")


def jnosi_folder() -> str:
    """Pillow's JPEGs at the precover's settings, named by its quality."""
    return f"pillow-q{precover.QUALITY}-420"


def _chunks(items: list[int], size: int) -> Iterator[list[int]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--set", choices=SETS, required=True)
    parser.add_argument("--count", type=int)
    parser.add_argument("--crops", type=int, help="cut this many prepared images into crops")
    args = parser.parse_args()

    if args.crops:
        make_crops(args.set, args.crops)

        return

    if not args.count:
        parser.error("--count or --crops is required")

    started = time.time()
    chosen = selected(args.set, args.count)
    indices = [item.index for item in chosen]
    log.info("%s: %d images selected in %.0f s", args.set, len(indices), time.time() - started)
    (WORK / f"selection-{args.set}.txt").write_text("\n".join(map(name, indices)) + "\n")
    write_lists()
    make = _png_cover if args.set == "lsb" else _jpeg_covers
    started = time.time()

    with Pool(MAX_WORKERS) as pool:
        for chunk in _chunks(indices, 100):
            pool.map(make, chunk)

    log.info("%s: covers made in %.0f s", args.set, time.time() - started)
    mark_done(f"prepare-{args.set}-{args.count}")


if __name__ == "__main__":
    main()
