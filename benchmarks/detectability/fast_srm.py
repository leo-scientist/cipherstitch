"""Exact, faster co-occurrences for sealwatch's SRM and CRM (bench venv).

sealwatch 2025.9 counts each co-occurrence table with nested Python loops over (2T + 1)^n
boolean masks: about 90% of SRMQ1's 5.3 s per 512 × 512 plane, and of CRM's time. The
functions here count the same tables with one ``np.bincount``. :func:`install` swaps them
in; ``python fast_srm.py`` checks that every feature matches the originals exactly.
"""

import sys
from collections.abc import Callable
from pathlib import Path

import numpy as np
import numpy.typing as npt
from sealwatch.srm import cooccurrence, crm

Table = npt.NDArray[np.float64]

_ORIGINAL4 = cooccurrence.cooccurrence4
_ORIGINAL3 = cooccurrence.cooccurrence3_col
_BUILDERS = (
    cooccurrence.all1st,
    cooccurrence.all2nd,
    cooccurrence.all3rd,
    cooccurrence.all3x3,
    cooccurrence.all5x5,
)


def _slices(d: np.ndarray, kind: str) -> tuple[np.ndarray, ...]:
    """The four neighbours of each position, as ``cooccurrence4`` takes them."""
    slices = {
        "hor": lambda: (d[:, :-3], d[:, 1:-2], d[:, 2:-1], d[:, 3:]),
        "ver": lambda: (d[:-3], d[1:-2], d[2:-1], d[3:]),
        "diag": lambda: (d[:-3, :-3], d[1:-2, 1:-2], d[2:-1, 2:-1], d[3:, 3:]),
        "mdiag": lambda: (d[3:, :-3], d[2:-1, 1:-2], d[1:-2, 2:-1], d[:-3, 3:]),
        "square": lambda: (d[1:, :-1], d[1:, 1:], d[:-1, 1:], d[:-1, :-1]),
    }

    if kind not in slices:  # sealwatch's 'square-ori' never sets R and cannot run either
        raise NotImplementedError(f"type {kind} not implemented")

    return slices[kind]()


def _table(parts: tuple[np.ndarray, ...], t: int) -> Table:
    """The normalized joint histogram of ``parts`` over [-T, T]^n; values outside it, or not
    whole, count nowhere, as in sealwatch's loops."""
    bins = 2 * t + 1
    is_counted = np.ones(parts[0].shape, dtype=bool)
    index = np.zeros(parts[0].shape, dtype=np.int64)

    for part in parts:
        is_counted &= (part == np.rint(part)) & (np.abs(part) <= t)
        index = index * bins + np.where(is_counted, part + t, 0).astype(np.int64)

    counts = np.bincount(index[is_counted], minlength=bins ** len(parts))

    return _normalized(counts, len(parts), bins)


def _small_table(parts: tuple[np.ndarray, ...], t: int) -> Table:
    """:func:`_table` for parts known to be whole numbers in [-T, T], in 16-bit indices:
    the residual arrays are read once instead of several times."""
    bins = 2 * t + 1
    index = parts[0].astype(np.int16) + t

    for part in parts[1:]:
        index *= bins
        index += part
        index += t

    counts = np.bincount(index.ravel(), minlength=bins ** len(parts))

    return _normalized(counts, len(parts), bins)


def _normalized(counts: npt.NDArray[np.int64], order: int, bins: int) -> Table:
    table = counts.reshape((bins,) * order)

    return table / np.sum(table)


def _as_small(d: np.ndarray, t: int) -> npt.NDArray[np.int8] | None:
    """``d`` as int8 if every value is a whole number in [-T, T], else ``None``."""
    small = d.astype(np.int8)  # a value out of range or NaN fails the comparison below

    if small.size and (small.min() < -t or small.max() > t):
        return None

    return small if np.array_equal(small, d) else None


def bins_fit(t: int) -> bool:
    """Whether a fourth-order index over [-T, T] fits 16 bits."""
    return (2 * t + 1) ** 4 <= np.iinfo(np.int16).max


def cooccurrence4(d: np.ndarray, type: str, T: int) -> Table:  # noqa: N803 - sealwatch's names
    small = _as_small(d, T) if bins_fit(T) else None

    if small is None:
        return _table(_slices(d, type), T)

    return _small_table(_slices(small, type), T)


def cooccurrence3_col(d: np.ndarray, type: str, T: int) -> Table:  # noqa: N803
    small = _as_small(d, T) if bins_fit(T) else None
    source = d if small is None else small
    parts = (source[:, :, 0], source[:, :, 1], source[:, :, 2])

    return _table(parts, T) if small is None else _small_table(parts, T)


def _set_cooc(builder: Callable[..., object], table: Callable[..., Table]) -> None:
    """Replace the builder's default ``CoocN``: keyword-only in some, positional in others."""
    if builder.__kwdefaults__ and "CoocN" in builder.__kwdefaults__:
        builder.__kwdefaults__["CoocN"] = table

        return

    code = builder.__code__
    names = code.co_varnames[: code.co_argcount]
    defaults = list(builder.__defaults__ or ())
    defaults[names.index("CoocN") - (len(names) - len(defaults))] = table
    builder.__defaults__ = tuple(defaults)


def install() -> None:
    """Use the fast tables in SRM (the builders' default ``CoocN``) and in CRM."""
    for builder in _BUILDERS:
        _set_cooc(builder, cooccurrence4)

    crm.Cooc3 = cooccurrence3_col


def _restore() -> None:
    for builder in _BUILDERS:
        _set_cooc(builder, _ORIGINAL4)

    crm.Cooc3 = _ORIGINAL3


def _features(rgb: np.ndarray) -> list[np.ndarray]:
    import sealwatch as sw

    planes = [sw.srmq1.extract(rgb[..., c].astype(np.float64)) for c in range(3)]
    colour = sw.crm.extract(rgb.astype(np.float64), Tc=3)

    return [v for features in (*planes, colour) for v in features.values()]


def check(paths: list[Path]) -> None:
    """Exit with an error unless every SRMQ1 and CRM feature of ``paths`` is the same."""
    from PIL import Image

    for path in paths:
        with Image.open(path) as opened:
            rgb = np.asarray(opened.convert("RGB"))

        _restore()
        expected = _features(rgb)
        install()
        found = _features(rgb)
        same = len(found) == len(expected) and all(
            np.array_equal(a, b) for a, b in zip(found, expected, strict=True)
        )
        print(f"{path.name}: {'same' if same else 'DIFFERENT'}")

        if not same:
            raise SystemExit(1)


if __name__ == "__main__":
    check([Path(p) for p in sys.argv[1:]])
