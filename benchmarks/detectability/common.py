"""Paths and helpers shared by the stage scripts. Standard library and numpy only: the
scripts run in both the main venv and the bench venv."""

import json
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NoReturn

import numpy as np

ROOT = Path(__file__).resolve().parent
WORK = ROOT / "work"
RESULTS = ROOT / "results"
LOGS = WORK / "logs"
TIFFS = WORK / "tiff"
COVERS = WORK / "covers"
EMBEDDED = WORK / "embedded"
MANIFESTS = WORK / "manifests"
SCORES = WORK / "scores"

MAX_WORKERS = 10  # leaves two of the study machine's 12 cores free
SEED = 20261020  # every random choice of the study derives from it
SETS = ("lsb", "jpeg")  # two disjoint index sets: PNG covers for LSB, JPEG covers for DCT
TOOLS_IMAGE = "cipherstitch-detectability-tools"
STEGDETECT_IMAGE = "cipherstitch-stegdetect"  # amd64: stegdetect builds only on old x86
HSTEGO_IMAGE = "cipherstitch-hstego"  # amd64: HStego's STC code uses SSE2


def logger(stage: str) -> logging.Logger:
    """A logger writing to ``work/logs/<stage>.log`` and to stderr."""
    LOGS.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger(stage)

    if not log.handlers:
        log.setLevel(logging.INFO)
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

        for handler in (logging.FileHandler(LOGS / f"{stage}.log"), logging.StreamHandler()):
            handler.setFormatter(formatter)
            log.addHandler(handler)

    return log


@contextmanager
def atomic(path: Path) -> Iterator[Path]:
    """A temporary name next to ``path`` to write to; renamed to ``path`` only on success."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.stem}.{os.getpid()}.partial{path.suffix}")

    try:
        yield partial
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)


def done_marker(stage: str) -> Path:
    return WORK / f"{stage}.done"


def mark_done(stage: str) -> None:
    done_marker(stage).parent.mkdir(parents=True, exist_ok=True)
    done_marker(stage).write_text("done\n")


def name(index: int) -> str:
    """ALASKA's file stem for an image index."""
    return f"{index:05d}"


def tiff_path(index: int) -> Path:
    return TIFFS / f"{name(index)}.tif"


def is_usable(condition: str, index: int) -> bool:
    """Whether the image has a file in this condition: a cover always does; an embedded one
    unless its record says the payload did not fit."""
    record = MANIFESTS / condition / f"{name(index)}.json"

    if not record.exists():
        return True

    status = json.loads(record.read_text()).get("status")

    if status is None:
        fail(f"{record} has no status: rerun the stage that wrote it")

    return status == "ok"


def payload(index: int, fill: int, size: int) -> bytes:
    """The random payload of ``size`` bytes for an image at a fill, the same in both venvs."""
    return np.random.default_rng([SEED, index, fill]).bytes(size)


def selection(cover_set: str, count: int) -> list[int]:
    """The first ``count`` images of a set, as ``prepare.py`` chose them."""
    path = WORK / f"selection-{cover_set}.txt"
    indices = [int(line) for line in path.read_text().split()] if path.exists() else []

    if len(indices) < count:
        fail(f"{cover_set}: {len(indices)} images prepared, {count} asked for; run prepare.py")

    return indices[:count]


class StudyError(Exception):
    """A stage cannot go on. An ordinary exception, so that a Pool worker's reaches the parent
    (SystemExit in a worker hangs ``pool.map``)."""


def fail(message: str) -> NoReturn:
    raise StudyError(message)
