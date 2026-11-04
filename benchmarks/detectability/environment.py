"""Record the versions the study ran with in ``results/environment.txt`` (any venv).

uv run python benchmarks/detectability/environment.py
"""

import platform
import subprocess
from pathlib import Path

from common import RESULTS, ROOT, STEGDETECT_IMAGE, TOOLS_IMAGE, atomic

REPOSITORY = ROOT.parent.parent
VENV_PYTHON = ".venv/bin/python"
DEBIAN_PACKAGES = "steghide outguess libimage-exiftool-perl foremost pngcheck default-jre-headless"
DEBIAN_PACKAGES += " ruby file binutils"
TOOL_VERSIONS = (
    "steghide --version | head -1; outguess -h 2>&1 | head -1; exiftool -ver;"
    " zsteg --version 2>&1 | head -1; binwalk --version; stegseek --version 2>&1 | head -1;"
    " java -version 2>&1 | head -1; gem list zsteg | tail -1"
)


def _run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, check=False)

    return (result.stdout + result.stderr).strip()


def _freeze(project: Path) -> str:
    """The venv's packages: CipherStitch itself, editable, is the commit above."""
    return _run(
        ["uv", "pip", "freeze", "--exclude-editable", "--python", str(project / VENV_PYTHON)]
    )


def _docker(image: str, command: str) -> str:
    return _run(["docker", "run", "--rm", image, "sh", "-c", command])


def sections() -> list[tuple[str, str]]:
    cpu = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
    packages = f"cat /etc/debian_version; dpkg-query -W {DEBIAN_PACKAGES}"

    return [
        ("machine", f"{platform.platform()} · {cpu}"),
        ("cipherstitch", _run(["git", "-C", str(REPOSITORY), "log", "-1", "--format=%h %s"])),
        ("main venv", _freeze(REPOSITORY)),
        ("bench venv", _freeze(ROOT)),
        ("tools container: Debian packages", _docker(TOOLS_IMAGE, packages)),
        ("tools container: tools", _docker(TOOLS_IMAGE, TOOL_VERSIONS)),
        ("stegdetect", _docker(STEGDETECT_IMAGE, "stegdetect -V 2>&1")),
    ]


def main() -> None:
    text = "\n\n".join(f"## {title}\n{body}" for title, body in sections()) + "\n"

    with atomic(RESULTS / "environment.txt") as partial:
        Path(partial).write_text(text)


if __name__ == "__main__":
    main()
