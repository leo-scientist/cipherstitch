"""Round 4's tables and targets (bench venv).

    uv run --project benchmarks/detectability python benchmarks/detectability/round4.py

Reads round 2's and round 3's ``ensemble.csv`` and round 4's ``results/round-4/*.csv``, as
``results/round-4/protocol.md`` lays them out. Writes ``results/round-4/report.md``. A stage
that has not run yet leaves its rows empty.
"""

from common import RESULTS, atomic
from conditions import ROUND_FOLDERS
from report import Table, fmt, lookup, markdown, pe, read
from round2 import CLASSICAL_PE, F5_PE, larger_std, met, tools_table

ROUND_2 = RESULTS / ROUND_FOLDERS[2]
ROUND_3 = RESULTS / ROUND_FOLDERS[3]
ROUND_4 = RESULTS / ROUND_FOLDERS[4]

# (label, round 4's condition, round 3's at the same payload, HStego's there if it ran)
COMPARISONS = (
    ("Image LSB, PNG, 5%", "lsb4-f05", "lsb3-f05", "hstego-png-f05"),
    ("Image LSB, PNG, 20%", "lsb4-f20", "lsb3-f20", "hstego-png-f20"),
    ("DCT, JPEG QF 75, 5%", "dct4-q75-f05", "dct3-q75-f05", "hstego-q75-f05"),
    ("DCT, JPEG QF 75, 20%", "dct4-q75-f20", "dct3-q75-f20", "hstego-q75-f20"),
    ("DCT from a TIFF, 5%", "psi4-f05", "psi3-f05", ""),
    ("DCT from a TIFF, 20%", "psi4-f20", "psi3-f20", ""),
)
MINIMUMS = (  # round 2's tier 3b targets, as in round 3
    ("lsb4-f05", 0.46),
    ("lsb4-f20", 0.33),
    ("dct4-q75-f05", 0.45),
    ("dct4-q75-f20", 0.25),
    ("psi4-f05", 0.47),
    ("psi4-f20", 0.30),
)
RATES = (  # (method, its round 4 conditions): round 3's root rate holds if none is below
    ("lsb", ("lsb4-f05", "lsb4-f20")),
    ("dct", ("dct4-q75-f05", "dct4-q75-f20")),
    ("precover", ("psi4-f05", "psi4-f20")),
)
CLASSICAL = ("lsb4-f05", "lsb4-f20")
F5_CONDITIONS = ("dct4-q75-f20", "psi4-f20")


def is_below(row: dict[str, str] | None, other: dict[str, str] | None) -> bool | None:
    """Whether ``row`` is below ``other`` by more than the larger std."""
    if not (row and other):
        return None

    return pe(other) - pe(row) > larger_std(row, other)


def comparison_table(earlier: Table, after: Table) -> str:
    rows = []

    for label, new, old, hstego in COMPARISONS:
        row, other = lookup(after, condition=new), lookup(earlier, condition=old)
        difference = f"{pe(row) - pe(other):+.4f}" if row and other else "–"
        tool = fmt(lookup(earlier, condition=hstego)) if hstego else "–"
        rows.append([label, fmt(other), fmt(row), difference, tool])

    header = ["Payload (round 1's bytes)", "Round 3", "Round 4", "Difference", "HStego"]

    return markdown(header, rows)


def target_rows(earlier: Table, after: Table) -> list[list[str]]:
    rows = []

    for condition, minimum in MINIMUMS:
        row = lookup(after, condition=condition)
        rows.append(
            [condition, fmt(row), f"≥ {minimum}", met(None if row is None else pe(row) >= minimum)]
        )

    for _label, new, old, _hstego in COMPARISONS:
        row, other = lookup(after, condition=new), lookup(earlier, condition=old)
        below = is_below(row, other)
        is_met = None if below is None else not below
        rows.append([new, fmt(row), f"not below {old} ({fmt(other)})", met(is_met)])

    for _label, new, _old, hstego in COMPARISONS:
        if hstego:
            row, other = lookup(after, condition=new), lookup(earlier, condition=hstego)
            is_met = None if not (row and other) else pe(row) - pe(other) > larger_std(row, other)
            rows.append([new, fmt(row), f"above {hstego} ({fmt(other)})", met(is_met)])

    return rows


def rate_rows(earlier: Table, after: Table) -> list[list[str]]:
    """Round 3's root rate holds for a method none of whose conditions is below round 3's."""
    old_of = {new: old for _label, new, old, _hstego in COMPARISONS}
    rows = []

    for method, conditions in RATES:
        verdicts = [
            is_below(lookup(after, condition=c), lookup(earlier, condition=old_of[c]))
            for c in conditions
        ]

        if None in verdicts:
            verdict = "–"
        elif any(verdicts):
            verdict = "measure again"
        else:
            verdict = "holds"

        rows.append([method, ", ".join(conditions), verdict])

    return rows


def classical_table(attacks: Table) -> str:
    rows = []

    for r in attacks:
        if r["condition"] in CLASSICAL:
            target = met(pe(r) >= CLASSICAL_PE)
        elif r["condition"] in F5_CONDITIONS:
            target = met(pe(r) >= F5_PE)
        else:
            target = "–"

        rows.append([r["condition"], r["statistic"], fmt(r), target])

    return markdown(["Condition", "Statistic", "P_E", "Target"], rows)


def main() -> None:
    earlier = read(ROUND_2 / "ensemble.csv") + read(ROUND_3 / "ensemble.csv")
    after = read(ROUND_4 / "ensemble.csv")
    sections = [
        "# Round 4",
        "Mean P_E ± std over 10 splits. See `protocol.md`.",
        "## Tier 3b: round 3 against round 4 at the same payload",
        comparison_table(earlier, after),
        "## Targets (tier 3b)",
        markdown(["Condition", "P_E", "Target", "Result"], target_rows(earlier, after)),
        "## Root rates",
        markdown(["Method", "Conditions", "Round 3's rate"], rate_rows(earlier, after)),
        "## Tier 2",
        classical_table(read(ROUND_4 / "attacks.csv")),
        "## Tier 1",
        tools_table(read(ROUND_4 / "scan.csv")),
    ]

    with atomic(ROUND_4 / "report.md") as partial:
        partial.write_text("\n\n".join(sections) + "\n")


if __name__ == "__main__":
    main()
