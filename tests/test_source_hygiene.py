"""Rules over the source files themselves, for tools that read them before Python does."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# CodeQL's Python extractor takes any ``coding:`` or ``coding=`` in a file's first two lines
# for a PEP 263 encoding declaration, comment or not (CPython wants the ``#``). "Phase
# coding: bits" made it read phase.py in the encoding "bits" and report a syntax error.
_ENCODING_COOKIE = re.compile(r"coding[:=]")


@pytest.mark.parametrize(
    "path",
    sorted((ROOT / "src").rglob("*.py")) + sorted((ROOT / "tests").rglob("*.py")),
    ids=lambda path: str(path.relative_to(ROOT)),
)
def test_the_first_two_lines_declare_no_encoding(path: Path) -> None:
    with path.open(encoding="utf-8") as file:
        head = [file.readline(), file.readline()]

    assert not any(_ENCODING_COOKIE.search(line) for line in head), (
        f"{path.relative_to(ROOT)} starts with text CodeQL reads as an encoding declaration"
    )
