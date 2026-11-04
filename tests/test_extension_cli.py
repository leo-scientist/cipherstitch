"""The CLI with extensions: ``ciphst extensions``, Research mode's warnings and choices, an
extension's failure and profiles that name an extension's entries."""

import json
import os
from pathlib import Path

import pytest

from cipherstitch import cli, extension_loader

from .conftest import EXTENSION_FIXTURES, ImageFactory, use_extensions

STRONG = "Correct-Horse-42!"
MISSING = "cipherstitch_missing_dependency"
BROKEN = f"extension broken couldn't load: ModuleNotFoundError: No module named '{MISSING}'"


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    status = cli.main(list(args))
    captured = capsys.readouterr()

    return status, captured.out, captured.err


@pytest.fixture
def fxb(tmp_path: Path) -> tuple[Path, Path]:
    """A cover only the fixtures' byte-lsb method takes, and a file to hide."""
    cover, secret = tmp_path / "cover.fxb", tmp_path / "note.txt"
    cover.write_bytes(os.urandom(4096))
    secret.write_bytes(b"meet at noon")

    return cover, secret


def test_extensions_lists_what_loaded(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use_extensions(monkeypatch, "working")

    assert _run(capsys, "extensions") == (
        0,
        "byte_lsb: A method for the tests\n"
        "  method byte-lsb · wire id 200 · document: .fxb\n"
        "chacha_copy\n"
        "  cipher chacha-copy · wire id 201\n",
        "",
    )


def test_extensions_lists_cost_functions_with_the_parameter_they_join(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use_extensions(monkeypatch, "costs")

    assert _run(capsys, "extensions") == (
        0,
        "ones: Costs of one\n  cost ones · limg.cost\n  cost ones · dct.cost\n",
        "",
    )


def test_extensions_lists_failures_with_their_tracebacks(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use_extensions(monkeypatch, "mixed")
    status, out, err = _run(capsys, "extensions")

    assert status == 1
    assert out.splitlines() == [
        "faulty",
        "  method faulty · wire id 210 · text: .txt · writes .txt, .md",
        "good: Working fixtures",
        "  method byte-lsb · wire id 200 · document: .fxb",
        "  cipher chacha-copy · wire id 201",
    ]
    assert err.startswith(f"error: {BROKEN}\nTraceback (most recent call last):\n")
    assert "mixed/broken.py" in err


def test_extensions_lists_an_extension_that_exits_on_import_as_a_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(extension_loader, "PACKAGE", f"{EXTENSION_FIXTURES}.exits")
    status, out, err = _run(capsys, "extensions")

    assert status == 1
    assert out.splitlines() == ["stays", "  method stays · wire id 142 · text: .txt"]
    assert "error: extension quits couldn't load: SystemExit: 2\n" in err


def test_extensions_lists_every_problem_and_what_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use_extensions(monkeypatch, "rules")
    status, _out, err = _run(capsys, "extensions")
    lines = err.splitlines()
    heading = lines.index("error: extension many_problems couldn't load:")

    assert status == 1
    assert lines[heading + 1] == "  method 'rule-method': the wire id must be an int in 128–254"

    use_extensions(monkeypatch, "missing_dependency")
    status, out, _err = _run(capsys, "extensions")

    assert status == 0
    assert out.splitlines()[1:] == [
        "  method optional-method · wire id 190 · text: .txt · unavailable: Optional method "
        f"needs {MISSING}: pip install {MISSING}",
        "  cipher optional-cipher · wire id 190 · unavailable: Optional cipher needs "
        "missing-dependency: pip install missing-dependency",
    ]


def test_extensions_says_when_there_are_none(capsys: pytest.CaptureFixture[str]) -> None:
    status, out, _err = _run(capsys, "extensions")

    folder = Path(__file__).parent / "extension_fixtures" / "empty"  # conftest's

    assert (status, out) == (0, f"No extensions in {folder} (see docs/extensions.md)\n")


def test_research_hides_and_reveals_with_extensions_and_warns_about_failures(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    fxb: tuple[Path, Path],
) -> None:
    use_extensions(monkeypatch, "mixed")
    monkeypatch.setattr("getpass.getpass", lambda _prompt: STRONG)
    cover, secret = fxb
    output, out_dir = tmp_path / "out.fxb", tmp_path / "out"
    choices = ["--method", "byte-lsb", "--cipher", "chacha-copy"]
    status, _out, err = _run(
        capsys, "hide", str(cover), str(secret), "-o", str(output), "--research", *choices
    )

    assert status == 0
    assert err.splitlines() == [
        f"warning: {BROKEN}",
        "warning: Anyone who compares the file with its original sees the changed bytes.",
    ]

    reveal = ["reveal", str(output), "-d", str(out_dir), "--research", *choices]

    assert _run(capsys, *reveal) == (
        0,
        f"Revealed {out_dir / 'note.txt'}\n",
        f"warning: {BROKEN}\n",
    )
    assert (out_dir / "note.txt").read_bytes() == b"meet at noon"


def test_capacity_counts_an_extensions_method_only_with_research(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], fxb: tuple[Path, Path]
) -> None:
    use_extensions(monkeypatch, "mixed")
    cover = str(fxb[0])
    refusal = "Off in Standard mode: .fxb is not a supported carrier"

    assert _run(capsys, "capacity", cover) == (1, "", "error: .fxb is not a supported carrier\n")
    assert _run(capsys, "capacity", cover, "--research") == (
        0,
        f"Byte LSB: 512 bytes in Research mode. {refusal}\n",
        f"warning: {BROKEN}\n",
    )


@pytest.mark.parametrize(
    ("option", "message"),
    [
        ("--method", "error: Unknown method unknown. Choose from lsb, dct, "),
        ("--cipher", "error: Unknown cipher unknown. Choose from aes-256-gcm, "),
    ],
)
def test_research_names_its_choices_for_an_unknown_id(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fxb: tuple[Path, Path],
    tmp_path: Path,
    option: str,
    message: str,
) -> None:
    use_extensions(monkeypatch, "working")
    cover, secret = fxb
    args = ["hide", str(cover), str(secret), "-o", str(tmp_path / "o.fxb"), "--research"]
    status, _out, err = _run(capsys, *args, option, "unknown")

    assert status == 1
    assert err.startswith(message)
    assert err.rstrip().endswith("byte-lsb" if option == "--method" else "chacha-copy")


def test_an_extension_error_shows_its_cause(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    use_extensions(monkeypatch, "mixed")
    cover = tmp_path / "cover.txt"
    cover.write_text("lanterns " * 100, encoding="utf-8")
    args = ["hide", str(cover), str(cover), "-o", str(tmp_path / "o.txt"), "--research"]
    status, _out, err = _run(capsys, *args, "--method", "faulty")
    lines = err.splitlines()

    assert status == 1
    assert lines[1] == "error: The extension faulty failed: ValueError: can't open cover.txt"
    assert lines[2] == "Traceback (most recent call last):"
    assert "in broken_opener" in err
    assert lines[-1] == "ValueError: can't open cover.txt"


def _profile(path: Path, method: str, cipher: str) -> Path:
    document = {"format": "cipherstitch-profile", "version": 1, "defaults": 1}
    path.write_text(json.dumps({**document, "method": method, "cipher": cipher}), encoding="utf-8")

    return path


def test_a_profile_names_extension_entries(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fxb: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    use_extensions(monkeypatch, "working")
    cover, secret = fxb
    output, out_dir = tmp_path / "out.fxb", tmp_path / "out"
    profile = str(_profile(tmp_path / "p.csprofile", "byte-lsb", "none"))

    assert (
        _run(capsys, "hide", str(cover), str(secret), "-o", str(output), "--profile", profile)[0]
        == 0
    )
    assert _run(capsys, "reveal", str(output), "-d", str(out_dir), "--profile", profile)[0] == 0
    assert (out_dir / "note.txt").read_bytes() == b"meet at noon"

    unknown = str(_profile(tmp_path / "u.csprofile", "line-spaces", "none"))
    status, _out, err = _run(capsys, "reveal", str(output), "--profile", unknown)

    assert status == 1
    assert err == (
        "error: u.csprofile names a method this CipherStitch doesn't know, 'line-spaces' "
        "(an extension that isn't installed?)\n"
    )


def test_a_cost_function_nobody_here_has_stops_a_hide_but_not_a_reveal(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, make_image: ImageFactory
) -> None:
    """Hiding would otherwise go on with HILL's costs, which isn't what the profile asked.
    Revealing reads the same values whatever the costs were."""
    document = {"format": "cipherstitch-profile", "version": 1, "defaults": 1}
    params = {"limg.cost": "ones", "stc.h": 4}  # stc.h: a Format value the reveal needs
    profile = tmp_path / "p.csprofile"
    profile.write_text(
        json.dumps({**document, "method": "lsb", "cipher": "none", "params": params}),
        encoding="utf-8",
    )
    cover, secret, output = make_image(), tmp_path / "note.txt", tmp_path / "o.png"
    secret.write_bytes(b"meet at noon")
    hide = ["hide", str(cover), str(secret), "-o", str(output)]
    status, _out, err = _run(capsys, *hide, "--profile", str(profile))

    assert status == 1
    assert err == (
        "error: p.csprofile sets limg.cost to a cost function this CipherStitch doesn't know, "
        "'ones' (an extension that isn't installed?)\n"
    )

    research = ["--research", "--method", "lsb", "--cipher", "none"]
    stored = tmp_path / "stc4.csprofile"
    stored.write_text(json.dumps({**document, "params": {"stc.h": 4}}), encoding="utf-8")

    assert _run(capsys, *hide, *research, "--profile", str(stored))[0] == 0

    reveal = ["reveal", str(output), "-d", str(tmp_path / "out"), "--profile", str(profile)]

    assert _run(capsys, *reveal)[0] == 0
    assert (tmp_path / "out" / "note.txt").read_bytes() == b"meet at noon"
