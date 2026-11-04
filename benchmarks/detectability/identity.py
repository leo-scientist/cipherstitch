"""Is the precover's JPEG Pillow's JPEG? (main venv)

    uv run python benchmarks/detectability/identity.py --count 3000

For each TIFF of the jpeg set, ``precover.compress`` against Pillow's JPEG of the same
picture at the precover's settings (``prepare.py``'s quality 90, 4:2:0, optimized tables):
whether every DC and AC coefficient is the same, and whether the files are the same bytes.
Pillow's JPEG is made from the TIFF converted to RGB, without its ICC profile, so bytes are
compared only where the TIFF has none. Writes ``results/identity.csv``.
"""

import argparse
import csv
import tempfile
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from common import COVERS, MAX_WORKERS, RESULTS, atomic, logger, mark_done, name, selection
from common import tiff_path as tiff
from prepare import jnosi_folder

from cipherstitch.media import jpeg, precover

FIELDS = ("index", "coefficients_equal", "has_profile", "bytes_equal")

log = logger("identity")


def compare(index: int) -> dict[str, object]:
    cover = precover.compress(tiff(index))
    pillow = COVERS / jnosi_folder() / f"{name(index)}.jpg"
    read = jpeg.read_coefficients(pillow)
    coefficients = cover.coefficients
    is_equal = np.array_equal(read.ac, coefficients.ac) and np.array_equal(read.dc, coefficients.dc)
    row: dict[str, object] = {
        "index": index,
        "coefficients_equal": int(is_equal),
        "has_profile": int(cover.icc_profile is not None),
        "bytes_equal": "",
    }

    if cover.icc_profile is None:
        with tempfile.TemporaryDirectory() as folder:
            ours = Path(folder) / "ours.jpg"
            jpeg.EmbeddedCompressedJpeg(
                coefficients.ac,
                coefficients.dc,
                coefficients.components,
                cover.size,
                None,
                cover.subsampling,
            ).save(ours)
            row["bytes_equal"] = int(ours.read_bytes() == pillow.read_bytes())

    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, required=True)
    args = parser.parse_args()
    indices = selection("jpeg", args.count)

    with Pool(MAX_WORKERS) as pool:
        rows = pool.map(compare, indices, chunksize=8)

    with atomic(RESULTS / "identity.csv") as partial, partial.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    plain = [r for r in rows if not r["has_profile"]]
    log.info(
        "%d of %d with the same coefficients; %d of %d without a profile with the same bytes",
        sum(int(r["coefficients_equal"]) for r in rows),
        len(rows),
        sum(int(r["bytes_equal"]) for r in plain),
        len(plain),
    )
    mark_done(f"identity-{args.count}")


if __name__ == "__main__":
    main()
