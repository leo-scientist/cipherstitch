"""Reference embeddings at CipherStitch's payloads (bench venv).

    uv run --project benchmarks/detectability python benchmarks/detectability/references.py \
        --count 3000

The references of the study's reduced variant (``docs/detectability.md``), and what its
validation gate and implementation check need:

- Simulators (conseal), at the bound. On the PNG covers, with the bits per colour value
  CipherStitch's image LSB stores at 5% and 20%: LSB replacement and LSB matching (permuted),
  LSB matching with the efficiency of Hamming codes, and HILL per colour. On the QF 75
  covers, nsF5 on every component at CipherStitch's DCT rate in bits per non-zero AC
  coefficient, and at 0.2 for the gate.
- Tools, with CipherStitch's payload bytes at 5% and 20%: OpenStego 0.8.6 RandomLSB with
  AES128 on the PNG covers and steghide 0.5.1 on the QF 75 covers (the tools container),
  HStego 0.6.1 on both, with its 5% limit lifted (the HStego container, ``hstego/embed.py``).
  A payload that does not fit is recorded as such, never shrunk.

Each output gets a JSON record in ``work/manifests/<condition>/``; done images are skipped.
"""

import argparse
import json
import re
import subprocess
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing import Pool
from pathlib import Path

import conseal as cl
import jpeglib
import numpy as np
import numpy.typing as npt
from common import (
    COVERS,
    EMBEDDED,
    HSTEGO_IMAGE,
    MANIFESTS,
    MAX_WORKERS,
    SEED,
    TOOLS_IMAGE,
    WORK,
    atomic,
    fail,
    logger,
    mark_done,
    name,
    payload,
    selection,
)
from conditions import REFERENCE_FILLS, at_fill
from PIL import Image

GATE_BPNZAC = 0.2
TOOL_PASSWORD = "detectability-study"  # noqa: S105 - fixed, synthetic payloads; no spaces
TOOL_PARALLEL = 8
NO_ROOM = ("too short", "too long", "insufficient", "not enough", "exceeds", "capacity")
PNG_TOOLS = ("openstego", "hstego-png")  # the others embed in the QF 75 covers
RELEASED_CAPACITY = re.compile(r"released capacity: (\d+) bytes")  # printed by hstego/embed.py

log = logger("references")

Pixels = npt.NDArray[np.uint8]


def _seed(index: int, condition: str) -> int:
    return int(np.random.default_rng([SEED, index, zlib.crc32(condition.encode())]).integers(2**31))


def _cipherstitch(family: str, fill: int, index: int) -> dict[str, float] | None:
    """CipherStitch's record at that fill, or ``None`` if its payload did not fit: the
    references get no payload there either."""
    path = MANIFESTS / at_fill(family, fill) / f"{name(index)}.json"

    if not path.exists():
        fail(f"{path} is missing: run embed.py --family {family} first")

    row = json.loads(path.read_text())

    return row if row.get("status", "ok") == "ok" else None


def _lsb_matching(hamming: bool) -> Callable[[np.ndarray, float, int], np.ndarray]:
    def simulate(cover: np.ndarray, alpha: float, seed: int) -> np.ndarray:
        efficiency = {"e": cl.coding.hamming.efficiency(alpha)} if hamming else {}

        return cl.lsb.simulate(
            cover, alpha, modify=cl.LSB_MATCHING, permute=True, seed=seed, **efficiency
        )

    return simulate


def _lsb_replacement(cover: np.ndarray, alpha: float, seed: int) -> np.ndarray:
    return cl.lsb.simulate(cover, alpha, modify=cl.LSB_REPLACEMENT, permute=True, seed=seed)


def _hill(cover: np.ndarray, alpha: float, seed: int) -> np.ndarray:
    planes = [
        cl.hill.simulate_single_channel(
            np.ascontiguousarray(cover[..., c], dtype=np.uint8), alpha, seed=seed + c
        )
        for c in range(3)
    ]

    return np.stack(planes, axis=-1)


LSB_SIMULATORS = {
    "lsbr": _lsb_replacement,
    "lsbm": _lsb_matching(hamming=False),
    "lsbmh": _lsb_matching(hamming=True),
    "hill": _hill,
}


@dataclass(frozen=True, slots=True)
class Job:
    kind: str  # a simulator, "nsf5" or a tool
    condition: str
    index: int
    rate: float  # bits per colour value, or per non-zero AC coefficient
    payload_bytes: int = 0


def _job(kind: str, condition: str, index: int, cipherstitch: dict[str, float] | None) -> Job:
    """A job at CipherStitch's payload; with none (it did not fit), a job that only records so."""
    if cipherstitch is None:
        return Job(kind, condition, index, rate=float("nan"))

    return Job(kind, condition, index, cipherstitch["rate"], int(cipherstitch["payload_bytes"]))


def _record(job: Job, **fields: object) -> None:
    row = {"index": job.index, "condition": job.condition, "rate": job.rate, **fields}

    with atomic(MANIFESTS / job.condition / f"{name(job.index)}.json") as partial:
        partial.write_text(json.dumps(row))


def _read_rgb(path: Path) -> Pixels:
    with Image.open(path) as opened:
        return np.asarray(opened.convert("RGB"))


def simulate_lsb(job: Job) -> None:
    cover = _read_rgb(COVERS / "png" / f"{name(job.index)}.png")
    embedded = LSB_SIMULATORS[job.kind](cover.copy(), job.rate, _seed(job.index, job.condition))
    embedded = embedded.astype(np.uint8)
    output = EMBEDDED / job.condition / f"{name(job.index)}.png"

    with atomic(output) as partial:
        Image.fromarray(embedded).save(partial, format="PNG")

    _record(job, status="ok", values=cover.size, changed=int(np.count_nonzero(cover != embedded)))


def _components(jpeg: object) -> list[str]:
    return [c for c in ("Y", "Cb", "Cr") if getattr(jpeg, c) is not None]


def simulate_nsf5(job: Job) -> None:
    cover_path = COVERS / "q75" / f"{name(job.index)}.jpg"
    jpeg = jpeglib.read_dct(str(cover_path))
    seed = _seed(job.index, job.condition)
    changed = nonzero = 0

    for number, component in enumerate(_components(jpeg)):
        y0 = getattr(jpeg, component)
        ac = y0.copy()
        ac[:, :, 0, 0] = 0
        component_nonzero = int(np.count_nonzero(ac))

        if not component_nonzero:  # at a rate per non-zero AC coefficient it holds nothing
            continue

        y1 = cl.nsF5.simulate_single_channel(y0, job.rate, seed=seed + number)
        changed += int(np.count_nonzero(y0 != y1))
        nonzero += component_nonzero
        setattr(jpeg, component, y1)

    if not nonzero:
        _record(job, status="does not fit")

        return

    output = EMBEDDED / job.condition / f"{name(job.index)}.jpg"

    with atomic(output) as partial:
        jpeg.write_dct(str(partial))

    _record(job, status="ok", nonzero_ac=nonzero, changed=changed)


def simulated(job: Job) -> str:
    if (MANIFESTS / job.condition / f"{name(job.index)}.json").exists():
        return "skipped"

    if np.isnan(job.rate):
        _record(job, status="no payload")

        return "no payload"

    (simulate_nsf5 if job.kind == "nsf5" else simulate_lsb)(job)

    return "embedded"


def simulator_jobs(lsb: list[int], jpeg: list[int]) -> list[Job]:
    """LSB simulators on the ``lsb`` set, nsF5 on the ``jpeg`` set."""
    jobs = []

    for fill in REFERENCE_FILLS:
        for index in lsb:
            row = _cipherstitch("lsb", fill, index)
            jobs += [_job(k, at_fill(k, fill), index, row) for k in LSB_SIMULATORS]

        for index in jpeg:
            jobs.append(
                _job("nsf5", at_fill("nsf5", fill), index, _cipherstitch("dct-q75", fill, index))
            )

    gate = f"nsf5-bpnzac{round(GATE_BPNZAC * 100):03d}"

    return jobs + [Job("nsf5", gate, index, GATE_BPNZAC) for index in jpeg]


def _suffix(job: Job) -> str:
    return ".png" if job.kind in PNG_TOOLS else ".jpg"


def _tool_command(job: Job) -> str:
    payload_path = f"/data/payloads/{job.condition}/{name(job.index)}.bin"
    output = f"/data/embedded/{job.condition}/{name(job.index)}"

    if job.kind == "openstego":
        cover = f"/data/covers/png/{name(job.index)}.png"
        command = (
            "java -jar /opt/openstego/lib/openstego.jar embed -a RandomLSB"
            f" -mf {payload_path} -cf {cover} -sf {output}.png -e -A AES128 -p '{TOOL_PASSWORD}'"
        )
    elif job.kind == "steghide":
        cover = f"/data/covers/q75/{name(job.index)}.jpg"
        command = (
            f"steghide embed -cf {cover} -ef {payload_path} -sf {output}.jpg"
            f" -p '{TOOL_PASSWORD}' -Z -f -q"
        )
    else:
        suffix = _suffix(job)
        cover = f"/data/covers/{'png' if suffix == '.png' else 'q75'}/{name(job.index)}{suffix}"
        command = (
            f"/opt/hstego/bin/python /opt/hstego/embed.py {cover} {payload_path}"
            f" {output}{suffix} '{TOOL_PASSWORD}'"
        )

    log_path = f"/data/manifests/{job.condition}/{name(job.index)}"

    return f"{command} > {log_path}.log 2>&1; echo $? > {log_path}.status"


def tool_jobs(lsb: list[int], jpeg: list[int]) -> list[Job]:
    """OpenStego and HStego on the ``lsb`` set, steghide and HStego on the ``jpeg`` set."""
    jobs = []
    tools = (
        ("openstego", "lsb", lsb),
        ("hstego-png", "lsb", lsb),
        ("steghide", "dct-q75", jpeg),
        ("hstego-q75", "dct-q75", jpeg),
    )

    for fill in REFERENCE_FILLS:
        for tool, family, indices in tools:
            for index in indices:
                jobs.append(
                    _job(tool, at_fill(tool, fill), index, _cipherstitch(family, fill, index))
                )

    return jobs


def _run_container(image: str, jobs: list[Job]) -> None:
    """Run the jobs' commands in one container of ``image``, ``TOOL_PARALLEL`` at a time."""
    script = WORK / "jobs" / f"{image}.txt"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("".join(f"{_tool_command(job)}\n" for job in jobs))
    started = time.time()
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{WORK}:/data",
            image,
            "sh",
            "-c",
            f"xargs -d '\\n' -P {TOOL_PARALLEL} -I CMD sh -c CMD < /data/jobs/{image}.txt",
        ],
        check=True,
    )
    log.info("%d runs in %s in %.0f s", len(jobs), image, time.time() - started)


def run_tools(jobs: list[Job]) -> None:
    """Write each payload, run the pending tool commands, one container per image, then
    record."""
    pending = [j for j in jobs if not (MANIFESTS / j.condition / f"{name(j.index)}.json").exists()]

    for job in (j for j in pending if np.isnan(j.rate)):
        (MANIFESTS / job.condition).mkdir(parents=True, exist_ok=True)
        _record(job, status="no payload")

    pending = [j for j in pending if not np.isnan(j.rate)]

    for job in pending:
        target = WORK / "payloads" / job.condition / f"{name(job.index)}.bin"
        fill = int(job.condition.rsplit("-f", 1)[1])
        target.parent.mkdir(parents=True, exist_ok=True)
        (EMBEDDED / job.condition).mkdir(parents=True, exist_ok=True)
        (MANIFESTS / job.condition).mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload(job.index, fill, job.payload_bytes))

    if not pending:
        return

    for image, in_image in ((TOOLS_IMAGE, False), (HSTEGO_IMAGE, True)):
        image_jobs = [j for j in pending if j.kind.startswith("hstego") == in_image]

        if image_jobs:
            _run_container(image, image_jobs)

    with Pool(MAX_WORKERS) as pool:
        pool.map(record_tool, pending, chunksize=8)


def record_tool(job: Job) -> None:
    base = MANIFESTS / job.condition / name(job.index)
    status = int(Path(f"{base}.status").read_text())
    message = Path(f"{base}.log").read_text().strip()

    suffix = _suffix(job)
    output = EMBEDDED / job.condition / f"{name(job.index)}{suffix}"
    released = RELEASED_CAPACITY.search(message)
    extra = {"released_capacity_bytes": int(released[1])} if released else {}

    if status != 0 or not output.exists():  # OpenStego exits with 0 after printing its help
        reason = "does not fit" if any(s in message.lower() for s in NO_ROOM) else "error"
        _record(
            job, payload_bytes=job.payload_bytes, status=reason, message=message[-300:], **extra
        )

        return

    if suffix == ".png":
        cover = _read_rgb(COVERS / "png" / f"{name(job.index)}.png")
        embedded = _read_rgb(output)
        changes = {"values": cover.size, "changed": int(np.count_nonzero(cover != embedded))}
    else:
        before = jpeglib.read_dct(str(COVERS / "q75" / f"{name(job.index)}.jpg"))
        after = jpeglib.read_dct(str(output))
        changed = sum(
            int(np.count_nonzero(getattr(before, c) != getattr(after, c)))
            for c in _components(before)
        )
        changes = {"changed": changed}

    _record(job, payload_bytes=job.payload_bytes, status="ok", **changes, **extra)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--part", choices=("simulators", "tools"), action="append")
    args = parser.parse_args()
    parts = args.part or ["simulators", "tools"]
    lsb, jpeg_set = selection("lsb", args.count), selection("jpeg", args.count)

    if "simulators" in parts:
        jobs = simulator_jobs(lsb, jpeg_set)
        started = time.time()

        with Pool(MAX_WORKERS) as pool:
            outcomes = pool.map(simulated, jobs, chunksize=4)

        log.info(
            "simulators: %d embedded, %d without a payload, %d skipped in %.0f s",
            outcomes.count("embedded"),
            outcomes.count("no payload"),
            outcomes.count("skipped"),
            time.time() - started,
        )
        mark_done(f"references-simulators-{args.count}")

    if "tools" in parts:
        run_tools(tool_jobs(lsb, jpeg_set))
        mark_done(f"references-tools-{args.count}")


if __name__ == "__main__":
    main()
