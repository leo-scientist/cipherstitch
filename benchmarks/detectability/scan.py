"""Tier 1: what popular tools say about the files (bench venv, tools container).

    uv run --project benchmarks/detectability python benchmarks/detectability/scan.py [--round 2]

The first :data:`SAMPLE` images of each set, covers and embedded files, go through the tools
an analyst or Aperi'Solve would run. Each tool's verdict per file is binary (the rules are
in :data:`VERDICTS`); StegExpose's fused score and binwalk's mean entropy are also scored
as statistics. Raw outputs go to ``work/scan/<tool>/<condition>/``, the summary to
``results/scan.csv`` and ``results/scan-statistics.csv`` (``results/round-2/`` for round 2,
which reuses round 1's raw outputs for the covers).
"""

import argparse
import csv
import json
import os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from common import (
    RESULTS,
    STEGDETECT_IMAGE,
    TOOLS_IMAGE,
    WORK,
    atomic,
    is_usable,
    logger,
    mark_done,
    name,
    selection,
)
from conditions import ROUND_FOLDERS
from features import image_path
from metrics import evaluate, scalar_trainer, wilson

SAMPLE = 500
STEGSEEK_SAMPLE = 30  # 36 s per file without steghide data: a full search of the seeds
PARALLEL = 10
SCAN = WORK / "scan"
Conditions = dict[str, list[tuple[str, str]]]  # format: (condition, the cover it is compared with)
ROUND_1: Conditions = {
    "png": [
        ("cover", "cover"),
        ("lsb-f05", "cover"),
        ("lsb-f50", "cover"),
        ("openstego-f05", "cover"),
        ("openstego-f20", "cover"),
    ],
    "jpeg": [
        ("q75", "q75"),
        ("dct-q75-f05", "q75"),
        ("dct-q75-f50", "q75"),
        ("steghide-f05", "q75"),
        ("steghide-f20", "q75"),
        ("p0", "p0"),
        ("psi-f05", "p0"),
        ("psi-f50", "p0"),
    ],
}
ROUND_2: Conditions = {
    "png": [("cover", "cover"), ("lsb2-f05", "cover"), ("lsb2-f20", "cover")],
    "jpeg": [
        ("q75", "q75"),
        ("dct2-q75-f05", "q75"),
        ("dct2-q75-f20", "q75"),
    ],
}
ROUND_3: Conditions = {
    "png": [("cover", "cover"), ("lsb3-f05", "cover"), ("lsb3-f20", "cover")],
    "jpeg": [
        ("q75", "q75"),
        ("dct3-q75-f05", "q75"),
        ("dct3-q75-f20", "q75"),
    ],
}
ROUND_4: Conditions = {
    "png": [("cover", "cover"), ("lsb4-f05", "cover"), ("lsb4-f20", "cover")],
    "jpeg": [
        ("q75", "q75"),
        ("dct4-q75-f05", "q75"),
        ("dct4-q75-f20", "q75"),
    ],
}
ROUNDS = {1: ROUND_1, 2: ROUND_2, 3: ROUND_3, 4: ROUND_4}
SET_OF = {"png": "lsb", "jpeg": "jpeg"}
MARKERS = re.compile(rb"(?i:steghide|openstego|cipherstitch)|CPST")  # CPST: the plain frame's magic
ZSTEG_TEXT = 20  # characters of readable text that count as a find
ZSTEG_DISTINCT = 10  # fewer distinct characters is a run, e.g. "UUUU…" from a flat bit plane
ZSTEG_LETTERS = 0.8  # share of letters and spaces in text a person wrote
ZSTEG_SPACES = 3  # words, not a row of pixel values that happen to be letters
ZSTEG_MAX_PLANE = 4  # bit planes above b4 are the picture itself
ZSTEG_HIT = re.compile(  # "b1,rgb,lsb,xy .. text: "…"" or "extradata:0 .. file: …"
    r"(b\dp?,\w+,[lm]sb,\w+|extradata:\d+|imagedata)\s+\.\.\s+(text|file): "
    r'("(?:[^"\\]|\\.)*"|[^\n\r]*)'
)
ZSTEG_TYPES = re.compile(
    r"Zip archive|gzip compressed|PNG image|JPEG image|PDF document|ELF |7-zip|RAR archive"
    r"|bzip2 compressed|XZ compressed|ASCII text|UTF-8 Unicode text"
)
FILE_TAGS = re.compile(r"^\[(System|ExifTool)\]")  # file system and exiftool, not the file
STANDARD_CHUNKS = {"IHDR", "PLTE", "IDAT", "IEND", "tRNS", "gAMA", "cHRM", "sRGB", "iCCP"}
STANDARD_CHUNKS |= {"pHYs", "tEXt", "zTXt", "iTXt", "bKGD", "tIME", "eXIf", "sBIT", "hIST"}

log = logger("scan")


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    formats: tuple[str, ...]
    command: str  # {path}: the file in the container; {out}: a scratch folder for it
    sample: int = SAMPLE
    parallel: int = PARALLEL
    image: str = TOOLS_IMAGE


TOOLS = [
    Tool("zsteg", ("png",), "zsteg -a {path}"),
    Tool(  # -E computes the entropy only, so the signatures are scanned first, retried when
        "binwalk",  # binwalk analyses 0 files under load
        ("png", "jpeg"),
        "cd {out} && for try in 1 2 3; do binwalk -l {out}/signatures.json {path};"
        " grep -q Analysis {out}/signatures.json 2>/dev/null && break; done;"
        " binwalk -E -l {out}/entropy.json {path}",
        parallel=4,
    ),
    Tool("pngcheck", ("png",), "pngcheck -v {path}"),
    Tool(
        "foremost",
        ("png", "jpeg"),
        "foremost -q -o {out}/carved -i {path}; echo @@files; ls -R {out}/carved",
    ),
    Tool("strings", ("png", "jpeg"), "strings -n 8 {path}"),
    Tool("exiftool", ("png", "jpeg"), "exiftool -a -G1 -s -n {path}"),
    Tool(
        "openstego",
        ("png",),
        "java -jar /opt/openstego/lib/openstego.jar extract -a RandomLSB -sf {path}"
        " -xd {out}/data -p ''; echo @@files; ls {out}/data",
        parallel=8,
    ),
    Tool("steghide", ("jpeg",), "steghide extract -sf {path} -p '' -xf {out}/data -f"),
    Tool("outguess", ("jpeg",), "outguess -r {path} {out}/data"),
    Tool("stegseek", ("jpeg",), "stegseek --seed {path}", STEGSEEK_SAMPLE, parallel=1),
    Tool("stegdetect", ("jpeg",), "stegdetect -t jopifa -s 1.0 {path}", image=STEGDETECT_IMAGE),
]


def scanned(tool: Tool, kind: str, condition: str) -> list[int]:
    """The images the tool scans in a condition: its sample, less those that did not fit."""
    return [i for i in selection(SET_OF[kind], tool.sample) if is_usable(condition, i)]


def raw_path(tool: Tool, condition: str, index: int) -> Path:
    return SCAN / tool.name / condition / f"{name(index)}.txt"


def _jobs(tool: Tool, kind: str, conditions: Conditions) -> list[tuple[str, int, Path]]:
    """(condition, index, raw output) for each file the tool still has to scan."""
    return [
        (condition, index, raw_path(tool, condition, index))
        for condition, _cover in conditions[kind]
        for index in scanned(tool, kind, condition)
        if not raw_path(tool, condition, index).exists()
    ]


def _container_path(path: Path) -> str:
    return f"/data/{path.relative_to(WORK)}"


def run_tool(tool: Tool, conditions: Conditions) -> None:
    """Run the tool on every pending file, ``parallel`` at a time, in one container."""
    lines = []

    for kind in tool.formats:
        for condition, index, raw in _jobs(tool, kind, conditions):
            raw.parent.mkdir(parents=True, exist_ok=True)
            source = _container_path(image_path(SET_OF[kind], condition, index))
            target = _container_path(raw)
            scratch = f"/tmp/{tool.name}-{condition}-{name(index)}"  # noqa: S108 - container
            command = tool.command.format(path=source, out=scratch)
            keep = (
                f"cp {scratch}/signatures.json {target}.signatures.json 2>/dev/null; "
                f"cp {scratch}/entropy.json {target}.entropy.json 2>/dev/null; "
                if tool.name == "binwalk"
                else ""
            )
            lines.append(
                f"mkdir -p {scratch} && ({command}) > {target}.partial 2>&1; "
                f"echo $? > {target}.status; {keep}mv {target}.partial {target}; rm -rf {scratch}"
            )

    if not lines:
        return

    jobs = WORK / "jobs" / f"scan-{tool.name}.txt"
    jobs.parent.mkdir(parents=True, exist_ok=True)
    jobs.write_text("".join(f"{line}\n" for line in lines))
    started = time.time()
    script = f"xargs -d '\\n' -P {tool.parallel} -I CMD sh -c CMD < {_container_path(jobs)}"
    docker = ["docker", "run", "--rm", "-v", f"{WORK}:/data", tool.image, "sh", "-c", script]
    subprocess.run(docker, check=True)
    log.info("%s: %d files in %.0f s", tool.name, len(lines), time.time() - started)


def run_stegexpose(conditions: Conditions) -> None:
    """StegExpose reads a folder: one of hard links per condition, one JVM per folder."""
    commands = []

    for condition, _cover in conditions["png"]:
        report = SCAN / "stegexpose" / f"{condition}.csv"

        if report.exists():
            continue

        stage = SCAN / "stage" / condition
        stage.mkdir(parents=True, exist_ok=True)

        for index in selection("lsb", SAMPLE):
            link = stage / f"{name(index)}.png"

            if is_usable(condition, index) and not link.exists():
                os.link(image_path("lsb", condition, index), link)

        report.parent.mkdir(parents=True, exist_ok=True)
        jar = "java -jar /opt/stegexpose/StegExpose.jar"
        commands.append(
            f"{jar} {_container_path(stage)} default default {_container_path(report)}.partial"
            f" > /dev/null 2>&1 && mv {_container_path(report)}.partial {_container_path(report)}"
        )

    if commands:
        script = " & ".join(f"({c})" for c in commands) + " & wait"
        subprocess.run(
            ["docker", "run", "--rm", "-v", f"{WORK}:/data", TOOLS_IMAGE, "sh", "-c", script],
            check=True,
        )


def _is_prose(text: str) -> bool:
    letters = sum(c.isalpha() or c == " " for c in text)

    return (
        len(text) >= ZSTEG_TEXT
        and len(set(text)) >= ZSTEG_DISTINCT
        and text.count(" ") >= ZSTEG_SPACES
        and letters >= ZSTEG_LETTERS * len(text)
    )


def _zsteg(text: str, _status: int) -> bool:
    """A readable text or a known file type in the low bit planes (b1–b4) or after the image.
    Higher planes are the picture itself, whose bytes often fall among the letters."""
    for source, kind, found in ZSTEG_HIT.findall(text):
        plane = re.match(r"b(\d)", source)

        if plane and int(plane[1]) > ZSTEG_MAX_PLANE:
            continue

        if not plane and not source.startswith("extradata"):
            continue

        value = found.encode().decode("unicode_escape", errors="ignore")

        if (kind == "text" and _is_prose(value.strip('"'))) or (
            kind == "file" and ZSTEG_TYPES.search(value)
        ):
            return True

    return False


def _pngcheck(text: str, status: int) -> bool:
    chunks = set(re.findall(r"chunk (\w{4}) at offset", text))

    return status != 0 or bool(chunks - STANDARD_CHUNKS)


def _listed(text: str) -> list[str]:
    """The file names the command listed after its ``@@files`` marker."""
    listing = text.split("@@files", 1)[1] if "@@files" in text else ""

    return [line for line in listing.splitlines() if re.search(r"\.\w{2,4}$", line)]


def _foremost(text: str, _status: int) -> bool:
    carved = [f for f in _listed(text) if f != "audit.txt"]

    return len(carved) > 1  # the image itself is carved too


def _strings(text: str, _status: int) -> bool:
    return bool(MARKERS.search(text.encode()))


def _openstego(text: str, _status: int) -> bool:
    listing = text.split("@@files", 1)[1] if "@@files" in text else ""
    files = [line for line in listing.splitlines() if line.strip() and not line.startswith("ls:")]

    return bool(files)  # something was extracted


def _steghide(text: str, _status: int) -> bool:
    return "wrote extracted data" in text


def _outguess(text: str, status: int) -> bool:
    return status == 0 and "too long" not in text and "Extracting usable bits" in text


def _stegseek(text: str, _status: int) -> bool:
    return "Found (possible) seed" in text


def _stegdetect(text: str, _status: int) -> bool:
    """A test that fired prints its name with stars, e.g. ``outguess(old)(***)``."""
    return "*" in text


VERDICTS: dict[str, Callable[[str, int], bool]] = {
    "zsteg": _zsteg,
    "pngcheck": _pngcheck,
    "foremost": _foremost,
    "strings": _strings,
    "openstego": _openstego,
    "steghide": _steghide,
    "outguess": _outguess,
    "stegseek": _stegseek,
    "stegdetect": _stegdetect,
}


def _binwalk_signatures(raw: Path) -> bool:
    """Any signature besides the image itself at offset 0."""
    entries = json.loads(Path(f"{raw}.signatures.json").read_text())
    analyses = [e["Analysis"] for e in entries if "Analysis" in e]
    found = [m for a in analyses for m in a.get("file_map", [])]

    return len(found) > 1 or any(m["offset"] > 0 for m in found)


def _binwalk_entropy(raw: Path) -> float:
    entries = json.loads(Path(f"{raw}.entropy.json").read_text())
    blocks = [b["entropy"] for e in entries if "Entropy" in e for b in e["Entropy"]["blocks"]]

    return float(np.mean(blocks))


def _exif_tags(raw: Path) -> dict[str, str]:
    lines = raw.read_text().splitlines()
    tags = (line.split(":", 1) for line in lines if ":" in line and not FILE_TAGS.match(line))

    return {key.strip(): value.strip() for key, value in tags}


def _stegexpose(condition: str) -> dict[int, tuple[bool, float]]:
    with (SCAN / "stegexpose" / f"{condition}.csv").open() as file:
        rows = [r for r in csv.reader(file) if r and r[0].endswith(".png")]  # a blank line first

    return {int(r[0].split(".")[0]): (r[1] == "true", float(r[7])) for r in rows if len(r) > 7}


def verdicts(kind: str, condition: str, cover: str) -> dict[str, list[bool]]:
    """Each tool's verdict per scanned file of the condition."""
    found: dict[str, list[bool]] = {}

    for tool in (t for t in TOOLS if kind in t.formats):
        indices = scanned(tool, kind, condition)
        raws = [raw_path(tool, condition, i) for i in indices]

        if not all(r.exists() for r in raws):  # not run yet
            continue

        if tool.name == "binwalk":
            found["binwalk"] = [_binwalk_signatures(r) for r in raws]
        elif tool.name == "exiftool":
            covers = [raw_path(tool, cover, i) for i in indices]
            found["exiftool"] = [
                _exif_tags(r) != _exif_tags(c) for r, c in zip(raws, covers, strict=True)
            ]
        else:
            statuses = [int(Path(f"{r}.status").read_text()) for r in raws]
            rule = VERDICTS[tool.name]
            found[tool.name] = [
                rule(r.read_text(errors="replace"), s) for r, s in zip(raws, statuses, strict=True)
            ]

    if kind == "png" and (SCAN / "stegexpose" / f"{condition}.csv").exists():
        stegexpose = _stegexpose(condition)
        found["stegexpose"] = [verdict for verdict, _score in stegexpose.values()]

    return found


def statistics(kind: str, condition: str, cover: str) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Scalar scores of the condition and its cover, image by image (those already run)."""
    binwalk = next(t for t in TOOLS if t.name == "binwalk")
    sample = scanned(binwalk, kind, condition)
    raws = [[raw_path(binwalk, c, i) for i in sample] for c in (cover, condition)]
    scores = {}

    if all(Path(f"{r}.entropy.json").exists() for group in raws for r in group):
        entropy = [np.array([_binwalk_entropy(r) for r in group]) for group in raws]
        scores["binwalk entropy"] = (entropy[0], entropy[1])

    reports = [SCAN / "stegexpose" / f"{c}.csv" for c in (cover, condition)]

    if kind == "png" and all(r.exists() for r in reports):
        covers, embedded = _stegexpose(cover), _stegexpose(condition)
        both = [i for i in sample if i in covers and i in embedded]
        fused = [np.array([table[i][1] for i in both]) for table in (covers, embedded)]
        scores["StegExpose fusion"] = (np.nan_to_num(fused[0]), np.nan_to_num(fused[1]))

    return scores


def metadata_differences(conditions: Conditions) -> list[dict[str, object]]:
    """Which exiftool tags differ between each embedded file and its own cover."""
    exiftool = next(t for t in TOOLS if t.name == "exiftool")
    rows: list[dict[str, object]] = []

    for kind, pairs in conditions.items():
        for condition, cover in (pair for pair in pairs if pair[0] != pair[1]):
            counts: dict[str, int] = {}
            indices = scanned(exiftool, kind, condition)

            for index in indices:
                ours = _exif_tags(raw_path(exiftool, condition, index))
                theirs = _exif_tags(raw_path(exiftool, cover, index))

                for tag in sorted(
                    k for k in ours.keys() | theirs.keys() if ours.get(k) != theirs.get(k)
                ):
                    counts[tag] = counts.get(tag, 0) + 1

            rows += [
                {"condition": condition, "tag": tag, "files": n, "of": len(indices)}
                for tag, n in counts.items()
            ]

            if not counts:
                rows.append(
                    {"condition": condition, "tag": "(none)", "files": 0, "of": len(indices)}
                )

    return rows


def write_results(round_number: int) -> None:
    rows, scored = [], []
    conditions = ROUNDS[round_number]

    for kind, pairs in conditions.items():
        for condition, cover in pairs:
            for tool, flags in verdicts(kind, condition, cover).items():
                if tool == "exiftool" and condition == cover:
                    continue  # a paired diff with the file's own cover: no false alarms to count

                low, high = wilson(sum(flags), len(flags))
                rows.append(
                    {
                        "condition": condition,
                        "is_cover": condition == cover,
                        "tool": tool,
                        "n": len(flags),
                        "positives": sum(flags),
                        "rate": f"{sum(flags) / len(flags):.4f}" if flags else "",
                        "wilson_low": f"{low:.4f}",
                        "wilson_high": f"{high:.4f}",
                    }
                )

            if condition == cover:
                continue

            for statistic, (cover_scores, embedded) in statistics(kind, condition, cover).items():
                result = evaluate(cover_scores, embedded, scalar_trainer)
                scored.append(
                    {
                        "condition": condition,
                        "statistic": statistic,
                        "n": len(embedded),
                        "pe_mean": f"{result.pe_mean:.4f}",
                        "pe_std": f"{result.pe_std:.4f}",
                        "auc_mean": f"{result.auc_mean:.4f}",
                    }
                )

    folder = RESULTS / ROUND_FOLDERS[round_number]
    outputs = (
        (folder / "scan.csv", rows),
        (folder / "scan-statistics.csv", scored),
        (folder / "scan-metadata.csv", metadata_differences(conditions)),
    )

    for target, table in outputs:
        with atomic(target) as partial_path, partial_path.open("w", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(table[0]))
            writer.writeheader()
            writer.writerows(table)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tool", action="append", help="default: every tool")
    parser.add_argument("--round", type=int, choices=ROUNDS, default=1)
    args = parser.parse_args()
    conditions = ROUNDS[args.round]

    for tool in TOOLS:
        if args.tool is None or tool.name in args.tool:
            run_tool(tool, conditions)

    if args.tool is None or "stegexpose" in args.tool:
        run_stegexpose(conditions)

    write_results(args.round)
    mark_done("scan" if args.round == 1 else f"scan-round{args.round}")


if __name__ == "__main__":
    main()
