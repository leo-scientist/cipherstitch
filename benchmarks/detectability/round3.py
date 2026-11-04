"""Round 3's tables, targets and root-rate calibration (bench venv).

    uv run --project benchmarks/detectability python benchmarks/detectability/round3.py \
        --count 3000

Reads round 1's and round 2's ``ensemble.csv`` and round 3's ``results/round-3/*.csv``, as
``results/round-3/protocol.md`` lays them out, and the manifests. Writes
``results/round-3/report.md`` and ``results/round-3/calibration.csv``. A stage that has not
run yet leaves its rows empty.
"""

import argparse
import csv
import math

import numpy as np
from common import RESULTS, atomic
from conditions import BY_NAME, DCT3_RATES, LSB3_RATES, PSI3_RATES, ROUND_FOLDERS, at_rate
from report import Table, fmt, lookup, manifest_rows, markdown, pe, read
from round2 import (
    ADVISED_PE,
    CLASSICAL_PE,
    EK_PE,
    F5_PE,
    crossing,
    larger_std,
    mean_share,
    median_rate,
    met,
    tools_table,
)

ROUND_2 = RESULTS / ROUND_FOLDERS[2]
ROUND_3 = RESULTS / ROUND_FOLDERS[3]

# (label, round 3's condition, round 2's at the same payload, the tools at that payload)
COMPARISONS = (
    ("Image LSB, PNG, 5%", "lsb3-f05", "lsb2-f05", ("openstego-f05", "hstego-png-f05")),
    ("Image LSB, PNG, 20%", "lsb3-f20", "lsb2-f20", ("openstego-f20", "hstego-png-f20")),
    ("DCT, JPEG QF 75, 5%", "dct3-q75-f05", "dct2-q75-f05", ("steghide-f05", "hstego-q75-f05")),
    ("DCT, JPEG QF 75, 20%", "dct3-q75-f20", "dct2-q75-f20", ("steghide-f20", "hstego-q75-f20")),
    ("DCT from a TIFF, 5%", "psi3-f05", "psi2-f05", ("jnosi2-f05",)),
    ("DCT from a TIFF, 20%", "psi3-f20", "psi2-f20", ("jnosi2-f20",)),
)
MINIMUMS = (  # round 2's tier 3b targets: (condition, the least mean P_E)
    ("lsb3-f05", 0.46),
    ("lsb3-f20", 0.33),
    ("dct3-q75-f05", 0.45),
    ("dct3-q75-f20", 0.25),
    ("psi3-f05", 0.47),
    ("psi3-f20", 0.30),
)
NOT_BELOW = tuple((new, old) for _label, new, old, _tools in COMPARISONS)
ABOVE = (
    ("lsb3-f05", "hstego-png-f05"),
    ("lsb3-f20", "hstego-png-f20"),
    ("dct3-q75-f05", "hstego-q75-f05"),
    ("dct3-q75-f20", "hstego-q75-f20"),
)
CALIBRATED = (  # (method, the conditions at its rates, the rates)
    ("lsb", "lsb3", LSB3_RATES),
    ("dct", "dct3-q75", DCT3_RATES),
    ("precover", "psi3", PSI3_RATES),
)
FIXED_RATE_FILLS = {"lsb": ("lsb3-f05",)}  # round 2's change log, 2026-10-23
EK_CONDITIONS = ("lsb3-f01", "lsb3-f05", "dct3-q75-f01", "dct3-q75-f05")
CLASSICAL = ("lsb3-f05", "lsb3-f20")
F5_CONDITIONS = ("dct3-q75-f20", "psi3-f20")


def comparison_table(earlier: Table, after: Table) -> str:
    rows = []

    for label, new, old, tools in COMPARISONS:
        others = ", ".join(
            f"{t.rsplit('-f', 1)[0]} {fmt(lookup(earlier, condition=t))}" for t in tools
        )
        rows.append(
            [label, fmt(lookup(earlier, condition=old)), fmt(lookup(after, condition=new)), others]
        )

    return markdown(["Payload (round 1's bytes)", "Round 2", "Round 3", "Others, round 2"], rows)


def target_rows(earlier: Table, after: Table) -> list[list[str]]:
    rows = []

    for condition, minimum in MINIMUMS:
        row = lookup(after, condition=condition)
        rows.append(
            [condition, fmt(row), f"≥ {minimum}", met(None if row is None else pe(row) >= minimum)]
        )

    for condition, reference in NOT_BELOW:
        row, other = lookup(after, condition=condition), lookup(earlier, condition=reference)
        is_met = None if not (row and other) else pe(other) - pe(row) <= larger_std(row, other)
        rows.append([condition, fmt(row), f"not below {reference} ({fmt(other)})", met(is_met)])

    for condition, reference in ABOVE:
        row, other = lookup(after, condition=condition), lookup(earlier, condition=reference)
        is_met = None if not (row and other) else pe(row) - pe(other) > larger_std(row, other)
        rows.append([condition, fmt(row), f"above {reference} ({fmt(other)})", met(is_met)])

    return rows


def median_elements(condition: str, count: int) -> float:
    """The median non-zero AC count of the condition's covers: the app's ``element_count``."""
    rows = manifest_rows(condition, count, BY_NAME[condition].image_set)
    counts = [r["nonzero_ac"] for r in rows if r.get("status") == "ok"]

    return float(np.median(counts)) if counts else math.nan


def calibration(after: Table, count: int) -> tuple[str, list[dict[str, object]]]:
    """The calibration table and the rates."""
    rates, calibrated = [], []

    for method, prefix, measured in CALIBRATED:
        points = []

        for rate in measured:
            row = lookup(after, condition=at_rate(prefix, rate))
            share = mean_share(at_rate(prefix, rate), count) if row else math.nan
            calibrated.append([method, str(rate), f"{100 * share:.1f}%" if row else "–", fmt(row)])

            if row:
                points.append((rate, pe(row)))

        for condition in FIXED_RATE_FILLS.get(method, ()):
            row = lookup(after, condition=condition)

            if row:
                rate = median_rate(condition, count)
                points.append((rate, pe(row)))
                share = f"{100 * mean_share(condition, count):.1f}%"
                calibrated.append([method, f"{rate:.1f} ({condition})", share, fmt(row)])

        if len(points) >= len(measured):
            rate = math.floor(crossing(points))
            result: dict[str, object] = {"method": method, "rate": rate, "advised_pe": ADVISED_PE}

            if method == "precover":
                elements = median_elements(at_rate(prefix, measured[0]), count)
                result["calibrated_elements"] = int(elements)

            rates.append(result)
            calibrated.append([method, f"**{rate}**", "", f"P_E crosses {ADVISED_PE} here"])

    header = ["Method", "Root rate (bits per √N)", "Mean share of the capacity, 512 × 512", "P_E"]

    return markdown(header, calibrated), rates


def ek_table(table: Table) -> str:
    rows = [
        [r["condition"], r["statistic"], fmt(r), met(pe(r) >= EK_PE)]
        for r in table
        if r["condition"] in EK_CONDITIONS
    ]

    return markdown(["Condition", "Statistic", "P_E", f"≥ {EK_PE}"], rows)


def classical_table(attacks: Table) -> str:
    rows = [
        [r["condition"], r["statistic"], fmt(r), met(pe(r) >= CLASSICAL_PE)]
        for r in attacks
        if r["condition"] in CLASSICAL
    ]
    rows += [
        [r["condition"], r["statistic"], fmt(r), met(pe(r) >= F5_PE)]
        for r in attacks
        if r["condition"] in F5_CONDITIONS
    ]
    rows += [
        [r["condition"], r["statistic"], fmt(r), "–"]
        for r in attacks
        if r["condition"] not in CLASSICAL + F5_CONDITIONS
    ]

    return markdown(["Condition", "Statistic", "P_E", "Target"], rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, required=True)
    args = parser.parse_args()
    earlier = read(RESULTS / "ensemble.csv") + read(ROUND_2 / "ensemble.csv")
    after = read(ROUND_3 / "ensemble.csv")
    calibrated, rates = calibration(after, args.count)
    ek = read(ROUND_3 / "kerckhoffs.csv") + read(ROUND_3 / "kerckhoffs-dct.csv")
    sections = [
        "# Round 3",
        "Mean P_E ± std over 10 splits. See `protocol.md`.",
        "## Tier 3b: round 2 against round 3 at the same payload",
        comparison_table(earlier, after),
        "## Targets (tier 3b)",
        markdown(["Condition", "P_E", "Target", "Result"], target_rows(earlier, after)),
        "## Calibration of the root rates",
        calibrated,
        "## E-K: the lead's public positions",
        ek_table(ek),
        "## Tier 2",
        classical_table(read(ROUND_3 / "attacks.csv")),
        "## Tier 1",
        tools_table(read(ROUND_3 / "scan.csv")),
    ]

    with atomic(ROUND_3 / "report.md") as partial:
        partial.write_text("\n\n".join(sections) + "\n")

    if rates:
        fields = ["method", "rate", "advised_pe", "calibrated_elements"]

        with atomic(ROUND_3 / "calibration.csv") as partial, partial.open("w", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fields, restval="")
            writer.writeheader()
            writer.writerows(rates)


if __name__ == "__main__":
    main()
