"""Tables from the stage results (bench venv).

    uv run --project benchmarks/detectability python benchmarks/detectability/report.py \
        --count 3000

Reads ``results/{kerckhoffs,attacks,scan,scan-statistics,ensemble}.csv``, the ensemble's
per-split scores and the manifests. Writes ``results/summary.csv``, ``results/tables.md``
(the validation gate, the checks on CipherStitch, E-C and E-K included) and
``results/scores.csv.gz`` (per-image scores, to recompute the metrics without images).
"""

import argparse
import csv
import gzip
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
from common import MANIFESTS, RESULTS, ROOT, SCORES, atomic, name, selection
from conditions import ALL, BY_NAME, Condition
from metrics import SPLITS, VOTE_CUT, error_at

GATE_REPLACEMENT_PE = 0.10
GATE_DETECTABLE_PE = 0.25
IMPLEMENTATION_TOLERANCE = 0.03
FINGERPRINT_PE = 0.40
MIN_CAMERA_PREDICTIONS = 100
HEADER_SAMPLE = 50

Table = list[dict[str, str]]


def read(path: Path) -> Table:
    if not path.exists():
        return []

    with path.open(newline="") as file:
        return list(csv.DictReader(file))


def lookup(table: Table, **keys: str) -> dict[str, str] | None:
    return next((r for r in table if all(r.get(k) == v for k, v in keys.items())), None)


def pe(row: dict[str, str] | None) -> float:
    return float(row["pe_mean"]) if row else float("nan")


def fmt(row: dict[str, str] | None) -> str:
    if row is None:
        return "–"

    return f"{float(row['pe_mean']):.3f} ± {float(row['pe_std']):.3f}"


def markdown(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]

    return "\n".join(lines + ["| " + " | ".join(row) + " |" for row in rows])


def manifest_rows(condition: str, count: int, kind: str) -> list[dict[str, object]]:
    folder = MANIFESTS / condition

    if not folder.exists():
        return []

    return [json.loads((folder / f"{name(i)}.json").read_text()) for i in selection(kind, count)]


def payload_table(count: int) -> str:
    rows = []

    for condition in ALL:
        records = manifest_rows(condition.name, count, condition.kind)

        if not records:
            continue

        fitted = [r for r in records if r.get("status", "ok") == "ok"]
        rate = np.mean([r["rate"] for r in fitted])
        base = [r.get("values") or r.get("nonzero_ac") for r in fitted]
        changes = [r["changed"] / b for r, b in zip(fitted, base, strict=True) if b]
        towards = sum(r.get("towards_precover", 0) for r in fitted)
        changed = sum(r["changed"] for r in fitted)
        extra = f"{towards / changed:.3f}" if condition.name.startswith("psi") and changed else ""
        rows.append(
            [
                condition.name,
                str(len(fitted)),
                str(len(records) - len(fitted)),
                f"{rate:.4f}",
                f"{np.mean(changes):.5f}" if changes else "–",
                extra,
            ]
        )

    header = ["condition", "n", "did not fit", "rate", "change rate", "towards precover"]

    return markdown(header, rows)


def ensemble_scores(condition: str, count: int) -> list[dict[str, np.ndarray]]:
    folder = SCORES / f"ensemble-{count}" / condition
    paths = [folder / f"{n:02d}.npz" for n in range(SPLITS)]

    return [dict(np.load(p)) for p in paths] if all(p.exists() for p in paths) else []


def cameras() -> dict[tuple[str, int], str]:
    table = {}

    for line in (ROOT / "indices.txt").read_text().splitlines()[1:]:
        kind, _rank, index, camera = line.split()
        table[(kind, int(index))] = camera

    return table


def pooled(condition: Condition, count: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(image, label, score) of every testing-half prediction over the splits."""
    indices = np.array(selection(condition.kind, count))
    images, labels, scores = [], [], []

    for split in ensemble_scores(condition.name, count):
        test = indices[split["test"]]
        images += [test, test]
        labels += [np.zeros(len(test)), np.ones(len(test))]
        scores += [split["cover"], split["embedded"]]

    if not images:
        return np.array([]), np.array([]), np.array([])

    return np.concatenate(images), np.concatenate(labels), np.concatenate(scores)


def pooled_pe(labels: np.ndarray, scores: np.ndarray) -> float:
    return error_at(scores[labels == 0], scores[labels == 1], VOTE_CUT)


def camera_table(count: int) -> str:
    table = cameras()
    chosen = [c for c in ALL if c.group.startswith("CipherStitch") and c.fill in (5, 20)]
    columns: dict[str, dict[str, float]] = {}

    for condition in chosen:
        images, labels, scores = pooled(condition, count)

        if not images.size:
            continue

        by_camera = defaultdict(list)

        for position, image in enumerate(images):
            by_camera[table[(condition.kind, int(image))]].append(position)

        columns[condition.name] = {
            camera: pooled_pe(labels[p], scores[p])
            for camera, p in by_camera.items()
            if len(p) >= MIN_CAMERA_PREDICTIONS
        }

    cameras_seen = sorted({camera for column in columns.values() for camera in column})
    rows = [
        [camera, *(f"{columns[c].get(camera, float('nan')):.3f}" for c in columns)]
        for camera in cameras_seen
    ]

    return markdown(["camera", *columns], rows) if columns else "_No ensemble scores yet._"


def no_warning_table(count: int) -> str:
    """P_E on the images whose cover the Detection row does not warn about."""
    rows = []

    for condition in (c for c in ALL if c.group.startswith("CipherStitch")):
        images, labels, scores = pooled(condition, count)
        records = {int(r["index"]): r for r in manifest_rows(condition.name, count, condition.kind)}

        if not images.size:
            continue

        quiet = np.array([not records[int(i)].get("cover_warned", False) for i in images])
        warned = len({int(i) for i in images[~quiet]})
        rows.append(
            [
                condition.name,
                f"{pooled_pe(labels, scores):.3f}",
                f"{pooled_pe(labels[quiet], scores[quiet]):.3f}",
                str(warned),
            ]
        )

    header = ["condition", "P_E, all", "P_E, no cover warning", "warned images"]

    return markdown(header, rows) if rows else "_No ensemble scores yet._"


def gate(attacks: Table, ensemble: Table) -> tuple[bool, str]:
    items = []
    shown = []  # reported next to the gate, not part of it (results/protocol.md, change log)

    for fill, target in (("f20", items), ("f05", shown)):
        for condition in (f"lsbr-{fill}", f"openstego-{fill}"):
            for statistic in ("ws", "spa"):
                value = pe(lookup(attacks, condition=condition, statistic=statistic))
                text = (
                    f"{statistic.upper()} on {condition}: P_E {value:.3f} < {GATE_REPLACEMENT_PE}"
                )
                target.append((text, value < GATE_REPLACEMENT_PE))

    for fill in ("f05", "f20"):
        rows = [lookup(ensemble, condition=f"{k}-{fill}") for k in ("lsbr", "lsbm", "hill")]
        replacement, matching, hill = (pe(r) for r in rows)
        band = max(float(r["pe_std"]) for r in rows[:2]) if all(rows[:2]) else float("nan")
        holds = replacement <= matching + band and max(replacement, matching) < hill
        text = f"LSBR {replacement:.3f} ≤ LSBM {matching:.3f} + {band:.3f}, both < HILL {hill:.3f}"
        items.append((f"{fill}: {text}", holds))

    for condition in ("lsbm-f20", "nsf5-bpnzac020"):
        value = pe(lookup(ensemble, condition=condition))
        items.append(
            (f"{condition}: P_E {value:.3f} ≤ {GATE_DETECTABLE_PE}", value <= GATE_DETECTABLE_PE)
        )

    rows = [[text, "pass" if ok else "**fail**"] for text, ok in items]
    rows += [[f"{text} (not part of the gate)", "yes" if ok else "no"] for text, ok in shown]

    return all(ok for _, ok in items), markdown(["item", "result"], rows)


def checks(ensemble: Table) -> str:
    rows = []

    for fill in ("f05", "f20"):
        ours, theory = (
            lookup(ensemble, condition=f"lsb-{fill}"),
            lookup(ensemble, condition=f"lsbmh-{fill}"),
        )

        if ours is None or theory is None:
            continue

        gap = pe(ours) - pe(theory)

        if gap < -IMPLEMENTATION_TOLERANCE:
            verdict = "finding: more detectable than theory"
        elif gap > IMPLEMENTATION_TOLERANCE:
            verdict = "less detectable than the simulation"
        else:
            verdict = "as theory says"

        rows.append(
            [
                f"implementation, {fill}",
                f"CipherStitch {fmt(ours)} · LSBM + Hamming {fmt(theory)}",
                verdict,
            ]
        )

    for fill in ("f01", "f05", "f10", "f20", "f50"):
        side, plain = (
            lookup(ensemble, condition=f"psi-{fill}"),
            lookup(ensemble, condition=f"pnosi-{fill}"),
        )

        if side is None or plain is None:
            continue

        band = max(float(side["pe_std"]), float(plain["pe_std"]))
        gain = pe(side) - pe(plain)
        verdict = "finding: worse" if gain < -band else ("gains" if gain > band else "no gain")
        rows.append(
            [f"side information, {fill}", f"P-SI {fmt(side)} · P-noSI {fmt(plain)}", verdict]
        )

    fingerprint = lookup(ensemble, condition="p0")

    if fingerprint is not None:
        verdict = "fingerprint" if pe(fingerprint) <= FINGERPRINT_PE else "no fingerprint"
        rows.append(["E-C, P-0 against Pillow", fmt(fingerprint), verdict])

    return markdown(["check", "P_E", "verdict"], rows)


def _segments(data: bytes) -> list[tuple[int, bytes]]:
    """The marker segments before the first scan: (marker, payload)."""
    segments, position = [], 2

    while position + 4 <= len(data) and data[position] == 0xFF:
        marker = data[position + 1]
        length = int.from_bytes(data[position + 2 : position + 4], "big")
        segments.append((marker, data[position + 4 : position + 2 + length]))

        if marker == 0xDA:  # SOS
            break

        position += 2 + length

    return segments


def header_comparison(count: int) -> str:
    """P-0 against Pillow's JPEG at the precover's settings: what in the headers differs."""
    from features import image_path  # features.py imports sealwatch: only when needed

    differences: dict[str, int] = defaultdict(int)
    indices = selection("jpeg", count)[:HEADER_SAMPLE]

    for index in indices:
        ours = _segments(image_path("jpeg", "p0", index).read_bytes())
        pillow = _segments(image_path("jpeg", "jnosi-cover", index).read_bytes())

        if [m for m, _ in ours] != [m for m, _ in pillow]:
            differences["segment order"] += 1

        for marker, label in (
            (0xE0, "JFIF (APP0)"),
            (0xDB, "quantization tables"),
            (0xC4, "Huffman tables"),
            (0xC0, "frame (SOF0)"),
        ):
            if [p for m, p in ours if m == marker] != [p for m, p in pillow if m == marker]:
                differences[label] += 1

    rows = [
        [label, f"{differences.get(label, 0)} of {len(indices)}"]
        for label in (
            "segment order",
            "JFIF (APP0)",
            "quantization tables",
            "Huffman tables",
            "frame (SOF0)",
        )
    ]

    return markdown(["header part", "files that differ"], rows)


def write_scores(count: int) -> None:
    """Tier 2 scores and tier 3b confidences, image by image."""
    rows = []

    for condition in ALL:
        indices = selection(condition.kind, count)

        for source in (condition.cover, condition.name):
            path = SCORES / f"attacks-{count}" / f"{source}.npz"

            if path.exists():
                values = np.load(path)["scores"]
                rows += [
                    ("attacks", source, name(i), *(f"{v:.6g}" for v in row))
                    for i, row in zip(indices, values, strict=True)
                ]

        for number, split in enumerate(ensemble_scores(condition.name, count)):
            test = np.array(indices)[split["test"]]
            rows += [
                ("ensemble", f"{condition.name}/{number}/cover", name(i), f"{s:.4g}")
                for i, s in zip(test, split["cover"], strict=True)
            ]
            rows += [
                ("ensemble", f"{condition.name}/{number}/embedded", name(i), f"{s:.4g}")
                for i, s in zip(test, split["embedded"], strict=True)
            ]

    unique = list(dict.fromkeys(rows))

    with atomic(RESULTS / "scores.csv.gz") as partial_path, gzip.open(partial_path, "wt") as file:
        writer = csv.writer(file)
        writer.writerow(["source", "condition", "image", "score..."])
        writer.writerows(unique)


def summary() -> Table:
    rows = []
    sources = {
        "tier 2": read(RESULTS / "attacks.csv"),
        "tier 3b": read(RESULTS / "ensemble.csv"),
        "tier 1 statistic": read(RESULTS / "scan-statistics.csv"),
    }

    for tier, table in sources.items():
        for row in table:
            condition = BY_NAME.get(row["condition"])
            rows.append(
                {
                    "tier": tier,
                    "condition": row["condition"],
                    "group": condition.group if condition else "",
                    "detector": row.get("statistic", "FLD ensemble (rich model)"),
                    "n": row.get("n") or row.get("test_pairs", ""),
                    "pe_mean": row["pe_mean"],
                    "pe_std": row["pe_std"],
                    "auc_mean": row["auc_mean"],
                    "detection_rate": "",
                    "false_alarm_rate": "",
                }
            )

    scan = read(RESULTS / "scan.csv")
    covers = {(r["tool"], r["condition"]): r for r in scan if r["is_cover"] == "True"}

    for row in (r for r in scan if r["is_cover"] != "True"):
        condition = BY_NAME[row["condition"]]
        cover = covers.get((row["tool"], condition.cover))
        rows.append(
            {
                "tier": "tier 1",
                "condition": row["condition"],
                "group": condition.group,
                "detector": row["tool"],
                "n": row["n"],
                "pe_mean": "",
                "pe_std": "",
                "auc_mean": "",
                "detection_rate": row["rate"],
                "false_alarm_rate": cover["rate"] if cover else "",
            }
        )

    return rows


def _truth(row: dict[str, str]) -> str:
    """What the attack estimates: SPA an embedding rate, WS and F5 a change rate (for DCT,
    over every component, while F5 reads luminance); chi-square gives a p-value."""
    return {
        "spa": row["true_rate"],
        "ws": row["true_change_rate"],
        "f5": row["true_change_rate"],
    }.get(row["statistic"], "–")


def tables(count: int) -> str:
    attacks = read(RESULTS / "attacks.csv")
    ensemble = read(RESULTS / "ensemble.csv")
    scan = read(RESULTS / "scan.csv")
    passed, gate_table = gate(attacks, ensemble)
    tier3 = [
        [r["condition"], r["group"], r["test_pairs"], fmt(r), f"{float(r['auc_mean']):.3f}"]
        for r in ensemble
    ]
    tier2 = [
        [
            r["condition"],
            r["statistic"],
            r["n"],
            fmt(r),
            f"{float(r['auc_mean']):.3f}",
            r["embedded_estimate"],
            r["cover_estimate"],
            _truth(r),
        ]
        for r in attacks
    ]
    tier1 = [
        [
            r["condition"],
            r["tool"],
            r["n"],
            r["positives"],
            f"{r['rate']} [{r['wilson_low']}, {r['wilson_high']}]",
        ]
        for r in scan
    ]
    statistics = [
        [r["condition"], r["statistic"], r["n"], fmt(r), f"{float(r['auc_mean']):.3f}"]
        for r in read(RESULTS / "scan-statistics.csv")
    ]
    kerckhoffs = [
        [r["condition"], r["statistic"], r["n"], fmt(r), f"{float(r['auc_mean']):.3f}"]
        for r in read(RESULTS / "kerckhoffs.csv")
    ]
    sections = [
        "# Detectability study: tables",
        "Generated by `report.py` from `results/*.csv`. P_E = (P_FA + P_MD) / 2 on the testing"
        " halves, mean ± std over 10 splits. 0.5 is a coin toss.",
        f"## Validation gate: {'passed' if passed else 'FAILED'}",
        gate_table,
        "## Checks on CipherStitch",
        checks(ensemble),
        "## Tier 3b: rich models with an FLD ensemble",
        markdown(["condition", "what", "test pairs", "P_E", "AUC"], tier3),
        "## Tier 2: classical statistical attacks",
        markdown(
            ["condition", "attack", "n", "P_E", "AUC", "mean estimate", "on covers", "truth"],
            tier2,
        ),
        "## Tier 1: popular tools (verdict per file, Wilson 95%)",
        markdown(["condition", "tool", "n", "flagged", "rate [95%]"], tier1),
        "### Tier 1 statistics",
        markdown(["condition", "statistic", "n", "P_E", "AUC"], statistics),
        "## E-K: public LSB positions",
        markdown(["condition", "statistic", "n", "P_E", "AUC"], kerckhoffs),
        "## E-C: headers of P-0 against Pillow's JPEG",
        header_comparison(count),
        "## P_E by camera (tier 3b, pooled testing predictions)",
        camera_table(count),
        "## P_E on covers the Detection row does not warn about",
        no_warning_table(count),
        "## Payloads and changes",
        payload_table(count),
    ]

    return "\n\n".join(sections) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, required=True)
    args = parser.parse_args()
    rows = summary()

    with (
        atomic(RESULTS / "summary.csv") as partial_path,
        partial_path.open("w", newline="") as file,
    ):
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    with atomic(RESULTS / "tables.md") as partial_path:
        partial_path.write_text(tables(args.count))

    write_scores(args.count)


if __name__ == "__main__":
    main()
