"""Round 2's tables, targets and root-rate calibration (bench venv).

    uv run --project benchmarks/detectability python benchmarks/detectability/round2.py \
        --count 3000

Reads round 1's ``results/ensemble.csv`` and round 2's ``results/round-2/*.csv``, as
``results/round-2/protocol.md`` lays them out, and the manifests. Writes
``results/round-2/report.md`` and ``results/round-2/calibration.csv``. A stage that has not
run yet leaves its rows empty.
"""

import argparse
import csv
import itertools
import math

import numpy as np
from common import RESULTS, atomic
from conditions import BY_NAME, DCT_RATES, LSB_RATES, ROUND_FOLDERS, at_rate
from report import Table, fmt, lookup, manifest_rows, markdown, pe, read

ADVISED_PE = 0.45  # the owner's decision D1
SCALING_TOLERANCE = 0.03
CHANCE = 0.5
EK_PE = 0.45
CLASSICAL_PE = 0.48
F5_PE = 0.45
ROUND_2 = RESULTS / ROUND_FOLDERS[2]

# (label, round 2's condition, round 1's at the same payload, the tools at that payload)
COMPARISONS = (
    ("Image LSB, PNG, 5%", "lsb2-f05", "lsb-f05", ("openstego-f05", "hstego-png-f05")),
    ("Image LSB, PNG, 20%", "lsb2-f20", "lsb-f20", ("openstego-f20", "hstego-png-f20")),
    ("DCT, JPEG QF 75, 5%", "dct2-q75-f05", "dct-q75-f05", ("steghide-f05", "hstego-q75-f05")),
    ("DCT, JPEG QF 75, 20%", "dct2-q75-f20", "dct-q75-f20", ("steghide-f20", "hstego-q75-f20")),
)
# (payload, round 1's precover conditions with side information and without, round 2's)
PRECOVER = (
    ("5%", "psi-f05", "pnosi-f05", "psi2-f05", "jnosi2-f05"),
    ("20%", "psi-f20", "pnosi-f20", "psi2-f20", "jnosi2-f20"),
)
MINIMUMS = (  # tier 3b targets: (condition, the least mean P_E)
    ("lsb2-f05", 0.46),
    ("lsb2-f20", 0.33),
    ("dct2-q75-f05", 0.45),
    ("dct2-q75-f20", 0.25),
    ("psi2-f05", 0.47),
    ("psi2-f20", 0.30),
)
ABOVE = (("dct2-q75-f05", "nsf5-f05"), ("dct2-q75-f20", "nsf5-f20"))
GAINS = (("psi2-f20", "jnosi2-f20", 0.03),)  # (condition, round 2's other, the least gain)
NOT_BELOW = (("psi2-f05", "jnosi2-f05"), ("psi2-f20", "jnosi2-f20"))
CALIBRATED = (  # (method, the conditions at its rates, their crops)
    ("lsb", "lsb2", "lsb2-crop", LSB_RATES),
    ("dct", "dct2-q75", "dct2-q75-crop", DCT_RATES),
)
# Fills whose payload is one root rate on every image (every PNG has the same N): measured
# rates too, by the owner's decision (protocol change log, 2026-10-23).
FIXED_RATE_FILLS = {"lsb": ("lsb2-f05",)}
EK_CONDITIONS = ("lsb2-f01", "lsb2-f05", "dct2-q75-f01", "dct2-q75-f05")
CLASSICAL = ("lsb2-f05", "lsb2-f20")
F5_CONDITIONS = ("dct2-q75-f20", "psi2-f20", "jnosi2-f20")


def crossing(points: list[tuple[int, float]]) -> float:
    """Where the mean P_E first falls below :data:`ADVISED_PE`, linear in the root rate, from
    (0, 0.5) on; the largest rate when it never does."""
    line = [(0, CHANCE), *sorted(points)]

    for (low, low_pe), (high, high_pe) in itertools.pairwise(line):
        if high_pe < ADVISED_PE <= low_pe:
            return low + (low_pe - ADVISED_PE) / (low_pe - high_pe) * (high - low)

    return float(line[-1][0])


def median_rate(condition: str, count: int) -> float:
    """The median root rate of the condition's payloads, in bits per square root of N."""
    rows = manifest_rows(condition, count, BY_NAME[condition].image_set)
    rates = [
        8 * r["required_bytes"] / math.sqrt(r.get("nonzero_ac") or r["values"])
        for r in rows
        if r.get("status") == "ok"
    ]

    return float(np.median(rates)) if rates else math.nan


def mean_share(condition: str, count: int) -> float:
    """The mean share of the capacity the condition's payloads take."""
    rows = manifest_rows(condition, count, BY_NAME[condition].image_set)
    shares = [r["share"] for r in rows if r.get("status") == "ok"]

    return float(np.mean(shares)) if shares else math.nan


def met(is_met: bool | None) -> str:
    return "–" if is_met is None else ("met" if is_met else "**missed**")


def larger_std(*rows: dict[str, str] | None) -> float:
    return max(float(r["pe_std"]) for r in rows if r)


def comparison_table(before: Table, after: Table) -> str:
    rows = []

    for label, new, old, tools in COMPARISONS:
        others = ", ".join(
            f"{t.split('-f')[0]} {fmt(lookup(before + after, condition=t))}" for t in tools
        )
        rows.append(
            [label, fmt(lookup(before, condition=old)), fmt(lookup(after, condition=new)), others]
        )

    return markdown(["Payload (round 1's bytes)", "Round 1", "Round 2", "Other tools"], rows)


def precover_table(before: Table, after: Table) -> str:
    rows = [
        [
            payload,
            fmt(lookup(before, condition=old)),
            fmt(lookup(before, condition=old_without)),
            fmt(lookup(after, condition=new)),
            fmt(lookup(after, condition=new_without)),
        ]
        for payload, old, old_without, new, new_without in PRECOVER
    ]
    header = [
        "Payload (round 1's P-SI bytes)",
        "P-SI, round 1",
        "P-noSI, round 1",
        "P-SI, round 2",
        "P-noSI (`jnosi2`), round 2",
    ]

    return markdown(header, rows)


def target_rows(before: Table, after: Table) -> list[list[str]]:
    rows = []

    for condition, minimum in MINIMUMS:
        row = lookup(after, condition=condition)
        rows.append(
            [condition, fmt(row), f"≥ {minimum}", met(None if row is None else pe(row) >= minimum)]
        )

    for condition, reference in ABOVE:
        row, other = lookup(after, condition=condition), lookup(before, condition=reference)
        is_met = None if not (row and other) else pe(row) - pe(other) > larger_std(row, other)
        rows.append([condition, fmt(row), f"above {reference} ({fmt(other)})", met(is_met)])

    for condition, reference, gain in GAINS:
        row, other = lookup(after, condition=condition), lookup(after, condition=reference)
        is_met = None if not (row and other) else pe(row) - pe(other) >= gain
        target = f"{gain} above {reference} ({fmt(other)})"
        rows.append([condition, fmt(row), target, met(is_met)])

    for condition, reference in NOT_BELOW:
        row, other = lookup(after, condition=condition), lookup(after, condition=reference)
        is_met = None if not (row and other) else pe(other) - pe(row) <= larger_std(row, other)
        rows.append([condition, fmt(row), f"not below {reference} ({fmt(other)})", met(is_met)])

    return rows


def calibration(after: Table, count: int) -> tuple[str, str, list[dict[str, object]]]:
    """The calibration and scaling tables, and the rates."""
    rates, calibrated, scaling = [], [], []

    for method, prefix, crops, measured in CALIBRATED:
        points = []

        for rate in measured:
            row, crop = (
                lookup(after, condition=at_rate(prefix, rate)),
                lookup(after, condition=at_rate(crops, rate)),
            )
            share = mean_share(at_rate(prefix, rate), count) if row else math.nan
            calibrated.append([method, str(rate), f"{100 * share:.1f}%" if row else "–", fmt(row)])

            if row:
                points.append((rate, pe(row)))

            if row and crop:
                difference = pe(crop) - pe(row)
                holds = abs(difference) < SCALING_TOLERANCE
                scaling.append(
                    [method, str(rate), fmt(row), fmt(crop), f"{difference:+.3f}", met(holds)]
                )

        for condition in FIXED_RATE_FILLS.get(method, ()):
            row = lookup(after, condition=condition)

            if row:
                rate = median_rate(condition, count)
                points.append((rate, pe(row)))
                share = f"{100 * mean_share(condition, count):.1f}%"
                calibrated.append([method, f"{rate:.1f} ({condition})", share, fmt(row)])

        if len(points) >= len(measured):
            rate = math.floor(crossing(points))
            rates.append({"method": method, "rate": rate, "advised_pe": ADVISED_PE})
            calibrated.append([method, f"**{rate}**", "", f"P_E crosses {ADVISED_PE} here"])

    header = ["Method", "Root rate (bits per √N)", "Mean share of the capacity, 512 × 512", "P_E"]
    scaled = ["Method", "Root rate", "P_E, 512 × 512", "P_E, 256 × 256", "Difference", "Law holds"]

    return markdown(header, calibrated), markdown(scaled, scaling), rates


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


def tools_table(scan: Table) -> str:
    """Each tool's rate on the outputs against its rate on their covers."""
    covers = {(r["tool"], r["condition"]): r for r in scan if r["is_cover"] == "True"}
    rows = []

    for r in (r for r in scan if r["is_cover"] == "False"):
        condition = BY_NAME.get(r["condition"])
        cover = covers.get((r["tool"], condition.cover)) if condition else None
        is_flagged = cover is not None and float(r["wilson_low"]) > float(cover["wilson_high"])
        cover_rate = f"{float(cover['rate']):.3f}" if cover else "– (paired diff)"
        verdict = "–" if cover is None else ("**flagged**" if is_flagged else "met")
        rows.append(
            [r["condition"], r["tool"], f"{float(r['rate'] or 0):.3f}", cover_rate, verdict]
        )

    return markdown(["Condition", "Tool", "Rate on outputs", "Rate on covers", "Target"], rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, required=True)
    args = parser.parse_args()
    before, after = read(RESULTS / "ensemble.csv"), read(ROUND_2 / "ensemble.csv")
    calibrated, scaling, rates = calibration(after, args.count)
    ek = read(ROUND_2 / "kerckhoffs.csv") + read(ROUND_2 / "kerckhoffs-dct.csv")
    sections = [
        "# Round 2",
        "Mean P_E ± std over 10 splits. See `protocol.md`.",
        "## Tier 3b: round 1 against round 2 at the same payload",
        comparison_table(before, after),
        "## DCT from a precover",
        precover_table(before, after),
        "## Targets (tier 3b)",
        markdown(["Condition", "P_E", "Target", "Result"], target_rows(before, after)),
        "## Calibration of the root rates",
        calibrated,
        "## Scaling check (256 × 256 crops)",
        scaling,
        "## E-K: the lead's public positions",
        ek_table(ek),
        "## Tier 2",
        classical_table(read(ROUND_2 / "attacks.csv")),
        "## Tier 1",
        tools_table(read(ROUND_2 / "scan.csv")),
    ]

    with atomic(ROUND_2 / "report.md") as partial:
        partial.write_text("\n\n".join(sections) + "\n")

    if rates:
        with atomic(ROUND_2 / "calibration.csv") as partial, partial.open("w", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(rates[0]))
            writer.writeheader()
            writer.writerows(rates)


if __name__ == "__main__":
    main()
