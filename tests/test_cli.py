import io
import json
import sys
from pathlib import Path

import pytest

from cipherstitch import cli, service
from cipherstitch.catalog import CipherId, MethodId
from cipherstitch.errors import NoHiddenDataError

from .conftest import ImageFactory

STRONG = "Correct-Horse-42!"


def _answer_getpass(monkeypatch: pytest.MonkeyPatch, *answers: str) -> None:
    replies = iter(answers)
    monkeypatch.setattr("getpass.getpass", lambda _prompt: next(replies))


def test_hide_and_reveal(
    make_image: ImageFactory,
    secret_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    embedded = str(tmp_path / "embedded.png")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    args = ["hide", str(make_image()), str(secret_file), "-o", embedded, "--research"]

    assert cli.main(args) == 0
    assert "warning: without --encrypt" in capsys.readouterr().err
    assert cli.main(["reveal", embedded, "-d", str(out_dir)]) == 0
    assert (out_dir / secret_file.name).read_bytes() == secret_file.read_bytes()


def test_hide_warns_about_detection_risks(
    make_image: ImageFactory,
    secret_file: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    large = tmp_path / "large.bin"
    large.write_bytes(bytes(10_000))  # the 256 × 256 cover is advised 2.0 KiB of 16.6 KiB
    cover = make_image(size=(256, 256))
    args = ["hide", str(cover), str(large), "-o", str(tmp_path / "s.png"), "--research"]

    assert cli.main(args) == 0

    warnings = capsys.readouterr().err.splitlines()

    assert len(warnings) == 2
    assert warnings[0].startswith("warning: The payload takes 9.8 KiB of the carrier")
    assert warnings[0].endswith("Keep it to 2.0 KiB or less.")
    assert warnings[1].startswith("warning: without --encrypt")


def test_encrypted_hide_and_reveal(
    make_image: ImageFactory,
    secret_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embedded = str(tmp_path / "embedded.png")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    _answer_getpass(monkeypatch, STRONG, STRONG, STRONG)
    args = ["hide", str(make_image()), str(secret_file), "-o", embedded, "--encrypt", "--research"]

    assert cli.main(args) == 0
    assert cli.main(["reveal", embedded, "-d", str(out_dir)]) == 1
    assert cli.main(["reveal", embedded, "-d", str(out_dir), "--encrypted"]) == 0
    assert (out_dir / secret_file.name).read_bytes() == secret_file.read_bytes()


@pytest.mark.parametrize("is_encrypted", [True, False])
def test_reveal_warns_when_the_password_was_not_used(
    make_image: ImageFactory,
    secret_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    is_encrypted: bool,
) -> None:
    embedded = str(tmp_path / "embedded.png")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    _answer_getpass(monkeypatch, STRONG, STRONG, STRONG)
    hide_args = ["hide", str(make_image()), str(secret_file), "-o", embedded, "--research"]
    assert cli.main([*hide_args, *(["--encrypt"] if is_encrypted else [])]) == 0
    capsys.readouterr()

    assert cli.main(["reveal", embedded, "-d", str(out_dir), "--encrypted"]) == 0
    assert ("hidden without encryption" in capsys.readouterr().err) is not is_encrypted


@pytest.mark.parametrize("answers", [("",), ("pw", "other")], ids=["empty", "mismatch"])
def test_hide_rejects_bad_password_input(
    make_image: ImageFactory,
    secret_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    answers: tuple[str, ...],
) -> None:
    _answer_getpass(monkeypatch, *answers)
    args = ["hide", str(make_image()), str(secret_file), "-o", str(tmp_path / "s.png"), "-e"]
    assert cli.main(args) == 1
    assert "error:" in capsys.readouterr().err


def test_capacity(make_image: ImageFactory, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["capacity", str(make_image(size=(64, 48)))]) == 0
    assert capsys.readouterr().out.strip() == (
        "LSB: 97 bytes in Standard mode (the advised size), 808 bytes in Research mode"
    )


def test_capacity_says_why_standard_mode_cant_use_the_carrier(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cover = tmp_path / "cover.txt"
    cover.write_text("Every word here can carry two bits. " * 400, encoding="utf-8")

    assert cli.main(["capacity", str(cover)]) == 0
    assert capsys.readouterr().out.strip() == (
        "Zero-width characters: 2700 bytes in Research mode. "
        "Off in Standard mode: no recommended method for this carrier"
    )


def test_capacity_gives_standards_error_when_neither_mode_can_use_the_carrier(
    make_image: ImageFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    cover = make_image("grey.bmp", size=(160, 120), mode="L")

    assert cli.main(["capacity", str(cover)]) == 1
    assert capsys.readouterr().err.startswith("error: Off in Standard mode: LSB would save")


@pytest.mark.parametrize(
    ("name", "message"),
    [
        (".", ". is a folder, not a file"),
        ("notes", "notes is not a supported carrier"),
        ("a.zip", ".zip is not a supported carrier"),
    ],
)
def test_an_unsupported_carrier_is_named(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    name: str,
    message: str,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert cli.main(["capacity", name]) == 1
    assert capsys.readouterr().err.strip() == f"error: {message}"


def test_missing_image_reports_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["capacity", str(tmp_path / "missing.png")]) == 1
    assert "error:" in capsys.readouterr().err


def test_hide_with_explicit_carrier_method_and_cipher(
    make_image: ImageFactory,
    secret_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embedded = tmp_path / "embedded.bmp"
    _answer_getpass(monkeypatch, STRONG, STRONG)
    args = [str(make_image()), str(secret_file), "-o", str(embedded), "--research"]
    options = ["--carrier", "image", "--method", "lsb", "--cipher", "fernet"]
    assert cli.main(["hide", *args, *options]) == 0
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    result = service.reveal(embedded, out_dir, STRONG)
    assert (result.method, result.cipher) == (MethodId.LSB, CipherId.FERNET)


@pytest.mark.parametrize(
    ("options", "message"),
    [
        (["--method", "zero-width", "--research"], "can't use .png files"),
        (["--carrier", "audio"], "Audio carriers are .wav, .flac files"),
        (["-e", "--cipher", "none"], "--encrypt needs a cipher other than none"),
    ],
)
def test_hide_options_are_checked_before_asking_for_a_password(
    make_image: ImageFactory,
    secret_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    options: list[str],
    message: str,
) -> None:
    _answer_getpass(monkeypatch)  # any prompt fails the test
    args = ["hide", str(make_image()), str(secret_file), "-o", str(tmp_path / "s.png"), *options]
    assert cli.main(args) == 1
    assert message in capsys.readouterr().err


def test_a_file_name_with_a_leading_dot_is_refused_before_asking_for_a_password(
    make_image: ImageFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Revealed into a home folder, .zshenv would be a file the shell runs."""
    _no_prompt(monkeypatch)
    secret = tmp_path / ".zshenv"
    secret.write_text("echo hello\n", encoding="utf-8")
    args = ["hide", str(make_image()), str(secret), "-o", str(tmp_path / "s.png"), "-e"]

    assert cli.main([*args, "--research"]) == 1
    assert capsys.readouterr().err.strip() == (
        "error: Unsafe or invalid file name: '.zshenv'. Rename the file and hide it again"
    )


@pytest.mark.parametrize(
    ("cover", "output", "message"),
    [
        ("archive.zip", "out.png", "is not a supported carrier"),
        ("cover.png", "out.jpg", "Output must be one of: .png, .bmp, .tif, .tiff"),
    ],
)
def test_an_explicit_method_must_fit_the_carrier_and_the_output(
    make_image: ImageFactory,
    secret_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    cover: str,
    output: str,
    message: str,
) -> None:
    _answer_getpass(monkeypatch)  # any prompt fails the test

    if cover.endswith(".zip"):
        (tmp_path / cover).write_bytes(b"data")
    else:
        make_image(cover)

    args = [str(tmp_path / cover), str(secret_file), "-o", str(tmp_path / output)]
    assert cli.main(["hide", *args, "--method", "lsb"]) == 1
    assert message in capsys.readouterr().err


def _no_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_prompt: str) -> str:
        raise AssertionError("prompted for a password")

    monkeypatch.setattr("getpass.getpass", fail)


def test_standard_hide_always_encrypts(
    make_image: ImageFactory, secret_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    embedded = str(tmp_path / "embedded.png")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    cover = make_image(size=(320, 240))  # advised 1.0 KiB
    _answer_getpass(monkeypatch, STRONG, STRONG, STRONG)

    assert cli.main(["hide", str(cover), str(secret_file), "-o", embedded]) == 0
    assert cli.main(["reveal", embedded, "-d", str(out_dir), "-e"]) == 0
    assert (out_dir / secret_file.name).read_bytes() == secret_file.read_bytes()


@pytest.mark.parametrize(
    ("cover_name", "mode", "extra", "hints"),
    [
        (
            "cover.png",
            "RGB",
            ["--cipher", "fernet"],
            ["hint: Research mode allows this. Add --research"],
        ),
        ("grey.bmp", "L", [], []),  # Research's default parameters refuse it too
    ],
    ids=["off-cipher", "greyscale-cover"],
)
def test_standard_hide_refuses_off_options_before_any_prompt(
    make_image: ImageFactory,
    secret_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    cover_name: str,
    mode: str,
    extra: list[str],
    hints: list[str],
) -> None:
    _no_prompt(monkeypatch)
    cover = make_image(cover_name, size=(160, 120), mode=mode)
    args = ["hide", str(cover), str(secret_file), "-o", str(tmp_path / "s.png")]

    assert cli.main([*args, *extra]) == 1

    lines = capsys.readouterr().err.splitlines()

    assert lines[0].startswith("error: Off in Standard mode")
    assert lines[1:] == hints


def test_the_research_hint_keeps_the_encryption(
    secret_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Standard mode encrypts by default, Research mode only with -e."""
    _no_prompt(monkeypatch)
    cover = tmp_path / "cover.txt"
    cover.write_text("Every word here can carry two bits. " * 400, encoding="utf-8")

    assert cli.main(["hide", str(cover), str(secret_file), "-o", str(tmp_path / "s.txt")]) == 1
    assert capsys.readouterr().err.splitlines()[1:] == [
        "hint: Research mode allows this. Add --research -e"
    ]


def test_standard_hide_refuses_more_than_advised_before_any_prompt(
    make_image: ImageFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _no_prompt(monkeypatch)
    large = tmp_path / "large.bin"
    large.write_bytes(bytes(10_000))  # the 256 × 256 cover is advised 2.0 KiB of 16.6 KiB
    cover = make_image(size=(256, 256))
    args = ["hide", str(cover), str(large), "-o", str(tmp_path / "s.png")]

    assert cli.main(args) == 1
    assert capsys.readouterr().err.strip() == (
        "error: Payload is 7977 bytes over the advised 2.0 KiB for this carrier"
    )


def test_a_research_hide_refuses_a_payload_that_does_not_fit_before_any_prompt(
    make_image: ImageFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _no_prompt(monkeypatch)
    large = tmp_path / "large.bin"
    large.write_bytes(bytes(10_000))  # the 64 × 48 cover holds 808 bytes, the framing takes 66
    cover = make_image(size=(64, 48))
    args = ["hide", str(cover), str(large), "-o", str(tmp_path / "s.png"), "--research", "-e"]

    assert cli.main(args) == 1
    assert capsys.readouterr().err.strip() == (
        "error: Payload is 9258 bytes too large for this carrier"
    )


def test_a_folder_as_the_file_to_hide_is_refused_before_any_prompt(
    make_image: ImageFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _no_prompt(monkeypatch)
    folder = tmp_path / "notes"
    folder.mkdir()
    args = ["hide", str(make_image()), str(folder), "-o", str(tmp_path / "s.png"), "-e"]

    assert cli.main(args) == 1
    assert capsys.readouterr().err.strip() == "error: notes is a folder, not a file"


def test_reveal_refuses_an_empty_password(
    make_image: ImageFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """As hide does: with an empty one, only plain frames would be tried, in silence."""
    _answer_getpass(monkeypatch, "")

    assert cli.main(["reveal", str(make_image()), "-d", str(tmp_path), "-e"]) == 1
    assert capsys.readouterr().err.strip() == "error: Password must not be empty"


def test_names_the_console_cannot_encode_are_printed_escaped(
    make_image: ImageFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ASCII console (a non-UTF-8 locale, a redirected Windows console) and a Cyrillic
    file name: the file is hidden, and the line says so with escapes."""
    secret = tmp_path / "звіт.txt"
    secret.write_bytes(b"report")
    strict = io.TextIOWrapper(io.BytesIO(), encoding="ascii", errors="strict")
    monkeypatch.setattr(sys, "stdout", strict)
    args = ["hide", str(make_image()), str(secret), "-o", str(tmp_path / "s.png"), "--research"]

    assert cli.main(args) == 0

    strict.flush()
    printed = strict.buffer.getvalue().decode("ascii")

    assert printed.startswith("Hidden \\u0437\\u0432\\u0456\\u0442.txt in ")


@pytest.mark.parametrize("research", [[], ["--research", "-e"]], ids=["standard", "research"])
def test_hide_refuses_a_weak_password(
    make_image: ImageFactory,
    secret_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    research: list[str],
) -> None:
    """Research mode's minimum is a parameter, strong by default."""
    _answer_getpass(monkeypatch, "pw", "pw")
    cover = make_image(size=(320, 240))
    args = ["hide", str(cover), str(secret_file), "-o", str(tmp_path / "s.png"), *research]

    assert cli.main(args) == 1
    assert "a strong password. Try" in capsys.readouterr().err
    assert not (tmp_path / "s.png").exists()


def test_research_reveal_tries_the_given_method_and_cipher(
    make_image: ImageFactory,
    secret_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    embedded = str(tmp_path / "embedded.png")
    _answer_getpass(monkeypatch, STRONG, STRONG, STRONG, STRONG)
    hide = ["hide", str(make_image(size=(320, 240))), str(secret_file), "-o", embedded]
    research = ["--research", "--method", "lsb"]

    assert cli.main([*hide, "--cipher", "chacha20-poly1305", "--research"]) == 0
    assert cli.main(["reveal", embedded, "-d", str(tmp_path / "a"), *research, "--cipher", "none"])
    assert capsys.readouterr().err.splitlines()[1:] == [
        "hint: the method, cipher and parameters must be the ones used for hiding. Give "
        "parameters other than the defaults with --profile"
    ]

    reveal = ["reveal", embedded, "-d", str(tmp_path / "b"), *research]

    assert cli.main([*reveal, "--cipher", "chacha20-poly1305"]) == 0
    assert (tmp_path / "b" / secret_file.name).read_bytes() == secret_file.read_bytes()


def _profile(path: Path, **fields: object) -> Path:
    document = {"format": "cipherstitch-profile", "version": 1, "defaults": 1, **fields}
    path.write_text(json.dumps(document), encoding="utf-8")

    return path


def test_a_profile_carries_the_research_settings_from_hide_to_reveal(
    make_image: ImageFactory,
    secret_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fields: dict[str, object] = {"method": "lsb", "cipher": "chacha20-poly1305"}
    # stc.h is Format: nothing in the file records it.
    fast = _profile(tmp_path / "fast.csprofile", **{**fields, "params": {"stc.h": 4}})
    embedded = str(tmp_path / "embedded.png")
    _answer_getpass(monkeypatch, *[STRONG] * 5)  # hide twice, then three reveals
    hide = ["hide", str(make_image(size=(320, 240))), str(secret_file), "-o", embedded]

    assert cli.main([*hide, "--profile", str(fast)]) == 0  # Research, ChaCha20, stc.h 4

    research = ["--research", "--method", "lsb", "--cipher", "chacha20-poly1305"]

    assert cli.main(["reveal", embedded, "-d", str(tmp_path / "a"), *research]) == 1

    wrong = _profile(tmp_path / "wrong.csprofile", **{**fields, "params": {"stc.h": 5}})
    capsys.readouterr()

    assert cli.main(["reveal", embedded, "-d", str(tmp_path / "a"), "--profile", str(wrong)]) == 1
    assert capsys.readouterr().err.splitlines()[-1] == (
        "hint: the method, cipher and profile must be the ones used for hiding"
    )
    assert cli.main(["reveal", embedded, "-d", str(tmp_path / "b"), "--profile", str(fast)]) == 0
    assert (tmp_path / "b" / secret_file.name).read_bytes() == secret_file.read_bytes()


def test_encrypt_overrides_a_profile_without_encryption(
    make_image: ImageFactory, secret_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = _profile(tmp_path / "plain.csprofile", method="lsb", cipher="none")
    embedded = str(tmp_path / "embedded.png")
    _answer_getpass(monkeypatch, STRONG, STRONG, STRONG)
    hide = ["hide", str(make_image()), str(secret_file), "-o", embedded]

    assert cli.main([*hide, "--profile", str(plain), "-e"]) == 0  # the default cipher
    assert cli.main(["reveal", embedded, "-d", str(tmp_path / "out"), "-e"]) == 0


def test_unknown_profile_parameters_are_left_out_of_a_hide_and_refused_by_a_reveal(
    make_image: ImageFactory,
    secret_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    newer = _profile(tmp_path / "newer.csprofile", method="lsb", params={"zz.new": 1})
    embedded = str(tmp_path / "embedded.png")
    hide = ["hide", str(make_image()), str(secret_file), "-o", embedded, "--profile", str(newer)]

    assert cli.main(hide) == 0  # plain: Research encrypts only when asked
    assert capsys.readouterr().err.splitlines()[0] == (
        "warning: Left out the parameters this version of CipherStitch doesn't know (zz.new). "
        "An extension that isn't installed may add them"
    )

    reveal = ["reveal", embedded, "-d", str(tmp_path), "--profile", str(newer)]

    assert cli.main([*reveal, "--cipher", "none"]) == 1
    assert capsys.readouterr().err.startswith("error: newer.csprofile sets parameters")

    plain = _profile(tmp_path / "plain.csprofile")

    assert cli.main(["reveal", embedded, "-d", str(tmp_path), "--profile", str(plain)]) == 1
    assert capsys.readouterr().err.strip() == (
        "error: plain.csprofile names no method or cipher. Add --method and --cipher"
    )

    with_method = _profile(tmp_path / "lsb.csprofile", method="lsb")

    assert cli.main(["reveal", embedded, "-d", str(tmp_path), "--profile", str(with_method)]) == 1
    assert capsys.readouterr().err.strip() == "error: lsb.csprofile names no cipher. Add --cipher"


@pytest.mark.parametrize(
    ("options", "message"),
    [
        (["--method", "lsb"], "error: --method and --cipher need --research"),
        (["--research", "--cipher", "none"], "error: --research needs --method and --cipher"),
    ],
)
def test_research_reveal_options_go_together(
    make_image: ImageFactory,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    options: list[str],
    message: str,
) -> None:
    assert cli.main(["reveal", str(make_image()), "-d", str(tmp_path), *options]) == 1
    assert capsys.readouterr().err.strip() == message


@pytest.mark.parametrize(
    ("options", "hint"),
    [
        ([], "hint: encrypted? Add -e to type the password. Hidden in Research mode? "),
        (["-e"], "hint: hidden in Research mode? "),
    ],
    ids=["without-password", "with-password"],
)
def test_a_failed_standard_reveal_hints_at_the_password_and_research_mode(
    make_image: ImageFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    options: list[str],
    hint: str,
) -> None:
    _answer_getpass(monkeypatch, STRONG)

    assert cli.main(["reveal", str(make_image()), "-d", str(tmp_path), *options]) == 1

    lines = capsys.readouterr().err.splitlines()

    assert lines[0].startswith("error: Couldn't reveal anything.")
    assert lines[1] == f"{hint}Reveal with --research and the same --method and --cipher"


@pytest.mark.parametrize(
    ("error", "code", "message"),
    [(EOFError, 1, "error: no password given"), (KeyboardInterrupt, 130, "")],
    ids=["input-closed", "ctrl-c"],
)
def test_an_abandoned_password_prompt_ends_without_a_traceback(
    make_image: ImageFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: type[BaseException],
    code: int,
    message: str,
) -> None:
    def abandon(_prompt: str) -> str:
        raise error

    monkeypatch.setattr("getpass.getpass", abandon)

    assert cli.main(["reveal", str(make_image()), "-d", str(tmp_path), "-e"]) == code
    assert capsys.readouterr().err.strip() == message


def test_a_truncated_payload_gets_no_reveal_hint(
    make_image: ImageFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A payload found damaged: neither -e nor --research can help."""

    def truncated(*_args: object, **_kwargs: object) -> None:
        raise NoHiddenDataError("Hidden data is truncated")

    monkeypatch.setattr("cipherstitch.service.reveal", truncated)

    assert cli.main(["reveal", str(make_image()), "-d", str(tmp_path)]) == 1
    assert capsys.readouterr().err.splitlines() == ["error: Hidden data is truncated"]


def test_commands_run_from_a_deleted_folder(
    make_image: ImageFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The current folder is read only when reveal needs it, not when the parser is built."""
    cover = make_image()
    gone = tmp_path / "gone"
    gone.mkdir()
    monkeypatch.chdir(gone)
    gone.rmdir()

    assert cli.main(["capacity", str(cover)]) == 0
    assert cli.main(["reveal", str(cover)]) == 1
    assert capsys.readouterr().err.strip() == (
        "error: The current folder no longer exists. Choose one with -d"
    )


def test_reveal_saves_in_the_current_folder_by_default(
    make_image: ImageFactory, secret_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    embedded = tmp_path / "embedded.png"
    service.hide_file(make_image(), secret_file, embedded, research=True)
    here = tmp_path / "here"
    here.mkdir()
    monkeypatch.chdir(here)

    assert cli.main(["reveal", str(embedded)]) == 0
    assert (here / secret_file.name).read_bytes() == secret_file.read_bytes()


def test_a_missing_output_folder_is_refused_before_asking_for_a_password(
    make_image: ImageFactory,
    secret_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _no_prompt(monkeypatch)
    output = tmp_path / "nodir" / "out.png"
    args = ["hide", str(make_image()), str(secret_file), "-o", str(output), "--research", "-e"]

    assert cli.main(args) == 1
    assert capsys.readouterr().err.strip() == (
        f"error: Can't save in {output.parent}: there is no such folder"
    )


def test_an_unknown_carrier_type_lists_the_choices(
    make_image: ImageFactory, secret_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["hide", str(make_image()), str(secret_file), "-o", str(tmp_path / "s.png")]

    with pytest.raises(SystemExit) as stopped:
        cli.main([*args, "--carrier", "foo"])

    # Python 3.12's argparse lists the choices unquoted, 3.13 and later quote them.
    message = capsys.readouterr().err.splitlines()[-1].replace("'", "")

    assert stopped.value.code == 2
    assert message.endswith(
        "argument --carrier: invalid choice: foo (choose from image, audio, document, text)"
    )


def test_the_help_describes_the_command_for_its_user(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--help"])

    help_text = capsys.readouterr().out

    assert "Hide a file inside an image, a recording, a document or a text file" in help_text
    assert "``" not in help_text
