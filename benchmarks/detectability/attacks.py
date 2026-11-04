"""Tier 2: classical statistical attacks (bench venv).

    uv run --project benchmarks/detectability python benchmarks/detectability/attacks.py \
        --count 3000 [--round 2]

LSB conditions: sealwatch's Weighted Stego (KB predictor, weighted), Sample Pairs and the
chi-square attack, per colour; an image's score is the mean of WS and of SPA over its
colours and the largest chi-square p-value. JPEG conditions: the F5 calibration estimator on
luminance. Each estimate is a scalar score
(``results/protocol.md#tier-2-classical-statistical-attacks``). Per-image scores go to
``work/scores/attacks-<count>/<condition>.npz``, the summary to ``results/attacks.csv``.
Round 2 scores its conditions at a fill (``results/round-2/protocol.md``) into
``results/round-2/attacks.csv``.
"""

import argparse
import csv
import json
import multiprocessing
import multiprocessing.pool
import time
import warnings
from functools import partial

import jpeglib
import numpy as np
import numpy.typing as npt
import sealwatch as sw
from common import (
    MANIFESTS,
    MAX_WORKERS,
    RESULTS,
    SCORES,
    atomic,
    is_usable,
    logger,
    mark_done,
    name,
    selection,
)
from conditions import COVERS, ROUND_FOLDERS, ROUNDS, Condition
from features import image_path
from metrics import evaluate, scalar_trainer
from PIL import Image

STATISTICS = {"lsb": ("ws", "spa", "chi2"), "jpeg": ("f5",)}

log = logger("attacks")
Row = npt.NDArray[np.float64]


def lsb_scores(path_text: str) -> Row:
    with Image.open(path_text) as opened:
        pixels = np.asarray(opened.convert("RGB"))

    planes = [np.ascontiguousarray(pixels[..., c]) for c in range(3)]
    ws = np.mean([sw.ws.attack(p) for p in planes])
    spa = np.mean([sw.spa.attack(p) for p in planes])
    chi2 = max(float(sw.chi2.attack(p)[1]) for p in planes)

    return np.array([ws, spa, chi2])


def jpeg_scores(path_text: str) -> Row:
    jpeg = jpeglib.read_dct(path_text)

    return np.array([sw.F5.attack(jpeg.Y, jpeg.qt[0])])


def image_scores(index: int, kind: str, condition: str) -> Row:
    if not is_usable(condition, index):
        return np.full(len(STATISTICS[kind]), np.nan)

    warnings.simplefilter("ignore")
    path = str(image_path(kind, condition, index))

    return lsb_scores(path) if kind == "lsb" else jpeg_scores(path)


def scores(kind: str, condition: str, indices: list[int], pool: multiprocessing.pool.Pool) -> Row:
    """The per-image scores of a condition, computed once."""
    target = SCORES / f"attacks-{len(indices)}" / f"{condition}.npz"

    if target.exists():
        return np.load(target)["scores"]

    work = partial(image_scores, kind=kind, condition=condition)
    rows = np.stack(pool.map(work, indices, chunksize=16))

    with atomic(target) as partial_path, partial_path.open("wb") as file:
        np.savez(file, indices=np.array(indices), scores=rows)

    return rows


def true_values(condition: Condition, indices: list[int]) -> tuple[float, float]:
    """The mean rate (bits per value or per non-zero AC) and change rate of the condition."""
    rates, changes = [], []

    for index in indices:
        path = MANIFESTS / condition.name / f"{name(index)}.json"

        if not path.exists():  # E-C's P-0 is a cover class: nothing embedded
            return 0.0, 0.0

        row = json.loads(path.read_text())

        if row.get("status", "ok") == "ok":
            rates.append(row["rate"])
            denominator = row.get("values") or row.get("nonzero_ac")  # steghide has neither
            changes.append(row["changed"] / denominator if denominator else np.nan)

    return float(np.mean(rates)), float(np.nanmean(changes))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--round", type=int, choices=ROUNDS, default=1)
    args = parser.parse_args()
    results = []
    # Round 2's root rates and crops serve the calibration, which tier 3b alone measures.
    chosen = [c for c in ROUNDS[args.round] if args.round == 1 or c.fill is not None]

    with multiprocessing.Pool(MAX_WORKERS) as pool:
        cover_rows = {
            (kind, cover): scores(kind, cover, selection(kind, args.count), pool)
            for kind, covers in COVERS.items()
            for cover in covers
        }

        for condition in chosen:
            started = time.time()
            indices = selection(condition.kind, args.count)
            embedded = scores(condition.kind, condition.name, indices, pool)
            cover = cover_rows[(condition.kind, condition.cover)]
            keep = ~np.isnan(embedded).any(axis=1) & ~np.isnan(cover).any(axis=1)
            rate, change_rate = true_values(condition, indices)

            for number, statistic in enumerate(STATISTICS[condition.kind]):
                result = evaluate(cover[keep, number], embedded[keep, number], scalar_trainer)
                results.append(
                    {
                        "condition": condition.name,
                        "statistic": statistic,
                        "n": int(keep.sum()),
                        "pe_mean": f"{result.pe_mean:.4f}",
                        "pe_std": f"{result.pe_std:.4f}",
                        "auc_mean": f"{result.auc_mean:.4f}",
                        "auc_std": f"{result.auc_std:.4f}",
                        "cover_estimate": f"{np.mean(cover[keep, number]):.4f}",
                        "embedded_estimate": f"{np.mean(embedded[keep, number]):.4f}",
                        "true_rate": f"{rate:.4f}",
                        "true_change_rate": f"{change_rate:.5f}",
                    }
                )
                log.info(
                    "%s %s: P_E %.3f, AUC %.3f (%.0f s)",
                    condition.name,
                    statistic,
                    result.pe_mean,
                    result.auc_mean,
                    time.time() - started,
                )

    with (
        atomic(RESULTS / ROUND_FOLDERS[args.round] / "attacks.csv") as partial_path,
        partial_path.open("w", newline="") as file,
    ):
        writer = csv.DictWriter(file, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)

    mark_done(
        f"attacks-{args.count}" if args.round == 1 else f"attacks-round{args.round}-{args.count}"
    )


if __name__ == "__main__":
    main()
