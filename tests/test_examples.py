"""The examples in examples/extensions/, loaded from there through the loader so that they
never fall behind the contract (docs/extensions.md)."""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from examples.extensions import line_spaces, ocb_cipher, variance_costs
from PIL import Image

from cipherstitch import cli, extension_loader, extension_testing, service
from cipherstitch.catalog import CarrierKind, CipherId
from cipherstitch.errors import NoHiddenDataError, UnsupportedFormatError
from cipherstitch.extensions import Domain
from cipherstitch.methods import Tuning
from cipherstitch.params.settings import Settings
from cipherstitch.payload import HiddenFile
from cipherstitch.service import HideOptions, ResearchReveal

STRONG = "Correct-Horse-42!"
POEM = "".join(f"Line {index} of the poem\n" for index in range(1200))
SPACE = Tuning(extension_values=(("lines.mark", "space"),))  # the default


@pytest.fixture
def examples(monkeypatch: pytest.MonkeyPatch) -> extension_loader.Loaded:
    monkeypatch.setattr(extension_loader, "PACKAGE", "examples.extensions")
    extension_loader.reset()

    return extension_loader.load()


@pytest.fixture
def poem(tmp_path: Path) -> Path:
    path = tmp_path / "poem.txt"
    path.write_text(POEM, encoding="utf-8")

    return path


def test_the_examples_load(examples: extension_loader.Loaded) -> None:
    assert examples.failures == ()
    assert [extension.name for extension in examples.extensions] == [
        "line_spaces",
        "ocb_cipher",
        "variance_costs",
    ]
    assert (examples.methods[0].entry.id, examples.methods[0].wire_id) == ("line-spaces", 128)
    assert (examples.ciphers[0].entry.id, examples.ciphers[0].wire_id) == ("aes-256-ocb3", 128)
    assert [cost.id for cost in examples.costs(Domain.SPATIAL)] == ["local-variance"]


def test_the_method_passes_its_checks(poem: Path) -> None:
    extension = line_spaces.EXTENSION
    extension_testing.check_method(extension.methods[0], poem, extension)


def test_the_cipher_passes_its_checks() -> None:
    extension_testing.check_cipher(ocb_cipher.EXTENSION.ciphers[0])


def test_the_cost_function_passes_its_checks(tmp_path: Path) -> None:
    rng = np.random.default_rng(seed=3)
    pixels = rng.integers(0, 256, size=(96, 128, 3), dtype=np.uint8)
    pixels[:16] = 120  # flat colour, where the costs are the highest
    cover = tmp_path / "cover.png"
    Image.fromarray(pixels).save(cover)
    extension_testing.check_cost(variance_costs.EXTENSION.costs[0], cover)


def test_the_cost_is_the_reciprocal_of_the_local_variance() -> None:
    """Each colour plane apart, the edges mirrored: a corner's window repeats its own row and
    column, never the value beyond them."""
    colors = np.zeros((3, 4, 3), dtype=np.uint8)
    colors[..., 1] = [[0, 0, 0, 0], [0, 9, 0, 0], [0, 0, 0, 0]]
    costs = variance_costs.variance_costs(colors, 0.1)
    floor = variance_costs.FLOOR
    once = 1 / (np.var([0] * 8 + [9]) + floor)  # a window that holds the 9 once

    assert costs.dtype == np.float32
    assert np.allclose(costs[..., 0], 1 / floor)
    assert np.allclose(costs[1, 1, 1], once)
    assert np.allclose(costs[0, 0, 1], once)
    assert np.allclose(costs[0, 3, 1], 1 / floor)  # the 9 is two columns away


@pytest.mark.usefixtures("examples")
def test_the_cli_runs_the_examples(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    poem: Path,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("getpass.getpass", lambda _prompt: STRONG)

    assert cli.main(["extensions"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "line_spaces: An example method: one bit per line of a text file.",
        "  method line-spaces · wire id 128 · text: .txt, .md",
        "  parameters lines.mark",
        "ocb_cipher: An example cipher: AES-256-OCB3 from cryptography.",
        "  cipher aes-256-ocb3 · wire id 128",
        "variance_costs: An example cost function: image LSB by the local variance.",
        "  cost local-variance · limg.cost",
    ]

    secret = tmp_path / "key.txt"
    secret.write_bytes(b"open sesame")

    for cipher in ("none", "aes-256-ocb3"):
        output, out_dir = tmp_path / f"{cipher}.txt", tmp_path / cipher
        choices = ["--research", "--method", "line-spaces", "--cipher", cipher]

        assert cli.main(["hide", str(poem), str(secret), "-o", str(output), *choices]) == 0
        assert cli.main(["reveal", str(output), "-d", str(out_dir), *choices]) == 0
        assert (out_dir / "key.txt").read_bytes() == b"open sesame"

    assert "Trailing spaces show in a diff" in capsys.readouterr().err


# Eight lines: a byte order mark, CRLF and LF endings, an empty line, no final newline.
COVER = chr(0xFEFF) + "a\r\nb\nc\n\nd\ne\nf\ng"
EMBEDDED = chr(0xFEFF) + "a \r\nb\nc \n \nd\ne\nf\ng "


def test_line_spaces_keep_the_line_endings(tmp_path: Path) -> None:
    path = tmp_path / "cover.txt"
    path.write_bytes(COVER.encode("utf-8"))
    body = bytes([0b10110001])
    embedded = line_spaces.LineSpaceChannel(path, SPACE).write(b"", body, None)

    assert embedded.text == EMBEDDED

    embedded.save(path)
    channel = line_spaces.LineSpaceChannel(path, SPACE)

    assert (channel.unit_count, channel.read(0, 1, None)) == (8, body)


def test_a_final_newline_starts_no_line(tmp_path: Path) -> None:
    path = tmp_path / "cover.txt"
    path.write_text("a\nb\n", encoding="utf-8")

    assert line_spaces.LineSpaceChannel(path, SPACE).unit_count == 2


@pytest.mark.usefixtures("examples")
def test_the_mark_is_a_format_parameter(poem: Path, tmp_path: Path) -> None:
    """A tab marks a 1, and only a reveal that asks for tabs finds the payload."""
    tab = Settings.of({"lines.mark": "tab"})
    output, secret = tmp_path / "tabs.txt", HiddenFile("key.txt", b"open sesame")
    options = HideOptions(CarrierKind.TEXT, "line-spaces", CipherId.NONE, is_research=True)
    service.hide(poem, secret, output, replace(options, settings=tab))
    lines = output.read_text(encoding="utf-8").splitlines()

    assert any(line.endswith("\t") for line in lines)
    assert not any(line.endswith(" ") for line in lines)

    revealed = service.reveal(output, tmp_path / "out", research=_research(tab))

    assert revealed.path.read_bytes() == b"open sesame"

    with pytest.raises(NoHiddenDataError):
        service.reveal(output, tmp_path / "other", research=_research(Settings()))


def _research(settings: Settings) -> ResearchReveal:
    return ResearchReveal("line-spaces", CipherId.NONE, settings)


@pytest.mark.parametrize("ending", [" ", "\t", "  \r"])
def test_line_spaces_refuse_a_cover_with_trailing_whitespace(tmp_path: Path, ending: str) -> None:
    path = tmp_path / "cover.md"
    path.write_text(f"first{ending}\nsecond\n", encoding="utf-8")
    channel = line_spaces.LineSpaceChannel(path, SPACE)

    with pytest.raises(UnsupportedFormatError, match="has spaces or tabs at the end of lines"):
        channel.write(b"", b"", None)


def test_line_spaces_refuse_what_they_cant_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    latin = tmp_path / "latin.txt"
    latin.write_bytes("café\n".encode("latin-1"))

    with pytest.raises(UnsupportedFormatError, match=r"latin\.txt is not UTF-8 text"):
        line_spaces.LineSpaceChannel(latin, SPACE)

    newlines = tmp_path / "newlines.txt"
    newlines.write_bytes(b"\n" * (line_spaces.MAX_LINES + 1))  # before a string per line

    with pytest.raises(UnsupportedFormatError, match=r"newlines\.txt has too many lines"):
        line_spaces.LineSpaceChannel(newlines, SPACE)

    monkeypatch.setattr(line_spaces, "MAX_SIZE", 4)

    with pytest.raises(UnsupportedFormatError, match=r"latin\.txt is too large for line-end"):
        line_spaces.LineSpaceChannel(latin, SPACE)
