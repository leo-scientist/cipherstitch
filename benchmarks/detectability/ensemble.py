"""Tier 3b: an FLD ensemble per condition over 10 splits (bench venv).

    uv run --project benchmarks/detectability python benchmarks/detectability/ensemble.py \
        --count 3000 [--round 2] [--condition lsb-f05 ...]

The protocol is in ``results/protocol.md#tier-3b-rich-models-and-the-fld-ensemble``. Each
(condition, split) writes its testing half's confidences to
``work/scores/ensemble-<count>/<condition>/<split>.npz``; one that exists is skipped. The
summary goes to ``results/ensemble.csv``, or ``results/round-2/ensemble.csv`` for round 2.
"""

import argparse
import csv
import os
import time
import warnings
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import numpy.typing as npt
from common import RESULTS, SCORES, SEED, atomic, fail, is_usable, logger, mark_done, selection
from conditions import BY_NAME, CROPS_PER_IMAGE, ROUND_FOLDERS, ROUNDS, Condition
from features import FEATURES, load
from metrics import SPLITS, VOTE_CUT, auc, error_at, split

ENSEMBLE_WORKERS = 6  # each holds two feature matrices and the trainer's copies
MAX_BASE_LEARNERS = 500

log = logger("ensemble")


def matrix_path(kind: str, condition: str, count: int) -> Path:
    return FEATURES / kind / f"{condition}-{count}.npy"


def scores_folder(condition: str, count: int) -> Path:
    return SCORES / f"ensemble-{count}" / condition


def assembled(kind: str, condition: str, count: int) -> Path:
    """The condition's features as one ``.npy``, so that workers can map it."""
    path = matrix_path(kind, condition, count)

    if not path.exists():
        with atomic(path) as partial_path, partial_path.open("wb") as file:
            np.save(file, load(kind, condition, count))

    return path


def condition_split(
    condition: Condition, count: int, number: int
) -> tuple[npt.NDArray[np.intp], npt.NDArray[np.intp]]:
    """Split ``number``'s halves. A crop goes where its picture goes: the split is drawn over
    the pictures, as for any other set of that size."""
    if not condition.images:
        return split(count, number)

    train, test = split(count // CROPS_PER_IMAGE, number)
    quadrants = np.arange(CROPS_PER_IMAGE)

    def crops(pictures: npt.NDArray[np.intp]) -> npt.NDArray[np.intp]:
        return (pictures[:, None] * CROPS_PER_IMAGE + quadrants).ravel()

    return crops(train), crops(test)


def usable(condition: Condition, indices: list[int]) -> npt.NDArray[np.bool_]:
    """Images with a file in both the condition and its cover: a payload may not fit."""
    return np.array(
        [is_usable(condition.name, i) and is_usable(condition.cover, i) for i in indices]
    )


def train_split(job: tuple[str, int, int]) -> str:
    condition_name, number, count = job
    target = scores_folder(condition_name, count) / f"{number:02d}.npz"

    if target.exists():
        return "skipped"

    warnings.simplefilter("ignore")
    from sealwatch.ensemble_classifier import FldEnsembleTrainer

    condition = BY_NAME[condition_name]
    indices = selection(condition.image_set, count)
    keep = usable(condition, indices)
    cover = np.load(matrix_path(condition.kind, condition.cover, count), mmap_mode="r")
    embedded = np.load(matrix_path(condition.kind, condition.name, count), mmap_mode="r")
    train, test = condition_split(condition, len(indices), number)
    train, test = train[keep[train]], test[keep[test]]
    started = time.time()
    trainer = FldEnsembleTrainer(
        np.asarray(cover[train], dtype=np.float64),
        np.asarray(embedded[train], dtype=np.float64),
        seed=SEED + number,
        verbose=0,
        max_num_base_learners=MAX_BASE_LEARNERS,
    )
    classifier, _ = trainer.train()
    cover_scores = classifier.predict_confidence(np.asarray(cover[test], dtype=np.float64))
    embedded_scores = classifier.predict_confidence(np.asarray(embedded[test], dtype=np.float64))

    with atomic(target) as partial_path, partial_path.open("wb") as file:
        np.savez(
            file,
            test=test,
            cover=cover_scores,
            embedded=embedded_scores,
            d_sub=classifier.d_sub,
            learners=classifier.num_base_learners,
            seconds=time.time() - started,
        )

    return "trained"


def summary(condition: Condition, count: int) -> dict[str, object]:
    folder = scores_folder(condition.name, count)
    pes, aucs, sizes = [], [], []

    for number in range(SPLITS):
        scores = np.load(folder / f"{number:02d}.npz")
        pes.append(error_at(scores["cover"], scores["embedded"], VOTE_CUT))
        aucs.append(auc(scores["cover"], scores["embedded"]))
        sizes.append(len(scores["test"]))

    return {
        "condition": condition.name,
        "kind": condition.kind,
        "group": condition.group,
        "fill": condition.fill or "",
        "rate": condition.rate or "",
        "test_pairs": int(np.mean(sizes)),
        "pe_mean": f"{np.mean(pes):.4f}",
        "pe_std": f"{np.std(pes):.4f}",
        "auc_mean": f"{np.mean(aucs):.4f}",
        "auc_std": f"{np.std(aucs):.4f}",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--condition", action="append", help="default: the round's every one")
    parser.add_argument("--round", type=int, choices=ROUNDS, default=1)
    parser.add_argument("--workers", type=int, default=ENSEMBLE_WORKERS)
    args = parser.parse_args()
    conditions = ROUNDS[args.round]
    unknown = [c for c in args.condition or () if c not in {d.name for d in conditions}]

    if unknown:
        fail(f"not round {args.round} conditions: {', '.join(unknown)}")

    chosen = [BY_NAME[c] for c in args.condition] if args.condition else conditions

    for condition in chosen:
        assembled(condition.kind, condition.cover, args.count)
        assembled(condition.kind, condition.name, args.count)

    os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")  # inherited by the workers
    jobs = [(c.name, number, args.count) for c in chosen for number in range(SPLITS)]
    started = time.time()

    with Pool(args.workers) as pool:
        for outcome, job in zip(pool.imap(train_split, jobs), jobs, strict=True):
            log.info(
                "%s split %d: %s (%.0f s so far)", job[0], job[1], outcome, time.time() - started
            )

    last = f"{SPLITS - 1:02d}.npz"
    rows = [
        summary(c, args.count)
        for c in conditions
        if (scores_folder(c.name, args.count) / last).exists()
    ]

    with (
        atomic(RESULTS / ROUND_FOLDERS[args.round] / "ensemble.csv") as partial_path,
        partial_path.open("w", newline="") as file,
    ):
        # Round 1's results keep their columns: its conditions have no root rate.
        fields = [f for f in rows[0] if f != "rate" or args.round != 1]
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    for row in rows:
        log.info(
            "%s: P_E %s ± %s, AUC %s",
            row["condition"],
            row["pe_mean"],
            row["pe_std"],
            row["auc_mean"],
        )

    mark_done(
        f"ensemble-{args.count}" if args.round == 1 else f"ensemble-round{args.round}-{args.count}"
    )


if __name__ == "__main__":
    main()
