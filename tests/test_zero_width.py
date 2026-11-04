import time
import unicodedata
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from cipherstitch import container, kdf, service
from cipherstitch.catalog import CarrierKind, CipherId, MethodId
from cipherstitch.container import Candidate, UnitOrder
from cipherstitch.errors import CapacityError, NoHiddenDataError, UnsupportedFormatError
from cipherstitch.media import text as text_media
from cipherstitch.methods.zero_width import (
    SYMBOL_SETS,
    SYMBOLS,
    ZeroWidthChannel,
    ZeroWidthRules,
    insertion_points,
    relaxed_points,
    strip_symbols,
)
from cipherstitch.params.model import Value
from cipherstitch.params.settings import Settings
from cipherstitch.payload import HiddenFile
from cipherstitch.service import HideOptions, ResearchReveal

from .conftest import plain_method

PASSWORD = "quiet harbour 47 lanterns"  # noqa: S105
COVER = (
    "Рукопис — текст, написаний поверх змитого тексту.\r\n"
    "A manuscript keeps traces of what was there before 👍🏽, even café (café).\r\n"
) * 30
FILE = HiddenFile("note.txt", "Таємниця".encode() * 10)
ENCRYPTED = HideOptions(
    CarrierKind.TEXT, MethodId.ZERO_WIDTH, CipherId.AES_GCM, PASSWORD, is_research=True
)
PLAIN = HideOptions(CarrierKind.TEXT, MethodId.ZERO_WIDTH, CipherId.NONE, is_research=True)


@pytest.fixture
def cover(tmp_path: Path) -> Path:
    path = tmp_path / "cover.txt"
    path.write_bytes(COVER.encode())

    return path


def _without_symbols(text: str) -> str:
    return "".join(char for char in text if char not in SYMBOLS)


@pytest.mark.parametrize(
    ("text", "points"),
    [
        ("Hi, you.", [0, 4, 5]),  # never next to ASCII punctuation (Markdown syntax)
        ("éx", [2]),  # never between a letter and its combining mark
        ("👍🏽 ok", [1, 3, 4]),  # never inside an emoji sequence
        ("🇺🇦", [1]),  # nor between regional indicators
        ("  \n", []),
    ],
)
def test_insertion_points_keep_graphemes_whole(text: str, points: list[int]) -> None:
    assert insertion_points(text) == points


def test_insertion_points_are_the_same_on_every_python_version() -> None:
    """Changing these points makes text hidden on another Python version unreadable."""
    new_letters = chr(0x1C8A) + chr(0x430) + " " + chr(0x1E9E) + "ig e" + chr(0x301) + "x "
    new_letters += chr(0x2EBF0)
    sample = "Ґанок, ʼєднає " + new_letters + "中 👍🏽 🇺🇦!"  # U+1C8A: Unicode 16 (3.14)
    assert insertion_points(sample) == [0, 1, 2, 3, 7, 8, 9, 10, 11, 12, 15, 18, 19, 23, 26, 29]
    arabic = "".join(chr(code) for code in (0x645, 0x631, 0x62D, 0x628, 0x627))
    jamo = chr(0x1100) + chr(0x1161)
    fragile = f"See https://a.io/x, `ls -l` and <b>hi</b> &amp; {arabic} {jamo} x.txt"
    expected = [0, 1, 2, 28, 29, 30, 35, 52, 55, 76, 77]
    assert insertion_points(fragile + "\n```\ncode\n```\nok") == expected
    domains = "пошта@приклад.укр " + chr(0x1C8A) + ".укр नमस्ते.भारत ok"
    assert insertion_points(domains) == [20, 21, 22, 36, 37]


@pytest.mark.parametrize(
    "fragile",
    [
        "https://example.com/a_b?q=1",
        "(http://localhost:8080)",
        "www.example.com",
        "me@example.org",
        "mailto:me@example.org",
        "github.com/owner/repo",
        "report.txt",
        "приклад.укр",
        "пошта@приклад.укр",
        "звіт.docx",
        "नमस्ते.भारत",  # vowel signs (marks) on both sides of the dot
        "`inline code`",
        "``code with ` inside``",
        "<b>",
        '<img src="x.png" alt="two words">',
        "</div>",
        "<!-- a comment -->",
        "&amp;",
        "&#8212;",
    ],
)
def test_no_points_inside_text_a_symbol_would_break(fragile: str) -> None:
    text = f"Look at {fragile} now"
    start = text.index(fragile)

    assert not [i for i in insertion_points(text) if start <= i < start + len(fragile)]
    assert insertion_points(text)  # the prose around it still has points


@pytest.mark.parametrize(
    "sentence",
    ["我喜欢猫.我也喜欢狗", "猫が好き.犬も好き", "สวัสดีครับ.ไปไหนมา", "请查看报告.pdf文件"],
)
def test_a_dot_in_text_without_word_spaces_is_no_domain(sentence: str) -> None:
    """The whole sentence is one token, which a domain would leave without points."""
    assert insertion_points(sentence) == insertion_points(sentence.replace(".", ","))


@pytest.mark.parametrize(
    "block",
    [
        "```python\nprint(x)\n```\n",
        "~~~~\nkeep me\n~~~\nstill code\n~~~~\n",
        "   ```\nindented fence\n```\n",
        "```\nnever closed\n",
        "      ```sh\n      pip install x\n\n      ciphst hide\n      ```\n",
        "> ```\n> quoted\n> ```\n",
    ],
    ids=["backticks", "longer-tildes", "indented", "unclosed", "in-a-list-item", "quoted"],
)
def test_no_points_inside_fenced_code(block: str) -> None:
    text = "Before the block\n" + block
    start = text.index(block)

    assert not [i for i in insertion_points(text) if i >= start]
    assert insertion_points(text)


@pytest.mark.parametrize(
    ("text", "points"),
    [
        (
            "A ``` fence needs its own line",
            [0, *range(6, 11), *range(12, 17), 18, 19, 20, 22, 23, 24, *range(26, 30)],
        ),
        ("`no closing", [1, 2, *range(4, 11)]),
        ("`one\n\nparagraph`", [1, 2, 3, *range(6, 14)]),
        ("``one` two` three", [2, 3, *range(12, 17)]),  # only ` two` is code
    ],
    ids=["inline-backticks", "unmatched", "across-paragraphs", "different-lengths"],
)
def test_backticks_without_a_code_span_keep_their_words(text: str, points: list[int]) -> None:
    assert insertion_points(text) == points


def test_a_fence_closes_on_a_crlf_line() -> None:
    text = "Intro\r\n```\r\ncode\r\n```\r\nafter it"
    block = (text.index("```"), text.rindex("```") + 3)

    assert not [i for i in insertion_points(text) if block[0] <= i < block[1]]
    assert [i for i in insertion_points(text) if i >= text.index("after")]


def test_crafted_text_is_read_in_linear_time() -> None:
    """Revealing parses untrusted text: open comments, fences and backtick runs of many
    lengths must not make it rescan the text."""
    hostile = (
        "<!--" * 50_000
        + "~~~\nx\n~~~\n`a` " * 5_000
        + "".join("`" * length + " y " for length in range(1, 600))
    )
    started = time.perf_counter()
    insertion_points(hostile)

    assert time.perf_counter() - started < 2


@pytest.mark.parametrize(
    ("text", "points"),
    [
        ("مرحبا بك", [4, 7]),  # Arabic: joined letters, so only word ends
        (chr(0x1100) + chr(0x1161) + chr(0x11A8) + " x", [2, 4]),  # conjoining jamo
        ("한국어 문장", [0, 1, 2, 4, 5]),  # composed Hangul syllables
        ("नमस्ते दुनिया", [0, 1, 5, 12]),  # never next to a vowel sign or a virama
    ],
    ids=["arabic", "jamo", "hangul", "devanagari"],
)
def test_scripts_that_change_shape_get_word_ends_only(text: str, points: list[int]) -> None:
    assert insertion_points(text) == points


def test_strip_symbols_reverses_insertion() -> None:
    assert strip_symbols(f"a{SYMBOLS[2]}b{SYMBOLS[0]}{SYMBOLS[3]}") == ("ab", {0: 2, 1: 0})
    assert strip_symbols(f"{SYMBOLS[1]}a") == ("a", {})  # nothing to follow


@pytest.mark.parametrize("options", [ENCRYPTED, PLAIN], ids=["encrypted", "plain"])
def test_roundtrip_keeps_the_visible_text(
    cover: Path, tmp_path: Path, options: HideOptions
) -> None:
    embedded = tmp_path / "embedded.txt"
    service.hide(cover, FILE, embedded, options)
    text = embedded.read_bytes().decode()
    assert _without_symbols(text) == COVER
    assert any(symbol in text for symbol in SYMBOLS)

    out = tmp_path / "out"
    out.mkdir()
    result = service.reveal(embedded, out, options.password)
    assert result.path.read_bytes() == FILE.data
    assert (result.method, result.cipher) == (MethodId.ZERO_WIDTH, options.cipher)


def test_markdown_syntax_is_never_split(tmp_path: Path) -> None:
    cover = tmp_path / "cover.md"
    original = "# Title\n\n- item\n1. step\n\n[a link](x) and **bold** text.\n" * 40
    cover.write_text(original, encoding="utf-8")
    embedded = tmp_path / "embedded.md"
    service.hide(cover, FILE, embedded, PLAIN)
    text = embedded.read_text(encoding="utf-8")

    for marker in ("# ", "- ", "1. ", "](", "**"):
        assert text.count(marker) == original.count(marker), marker


def test_encrypted_symbols_are_spread_over_the_whole_text(cover: Path, tmp_path: Path) -> None:
    embedded = tmp_path / "embedded.txt"
    service.hide(cover, FILE, embedded, ENCRYPTED)
    text = embedded.read_bytes().decode()
    last_symbol = max(text.rfind(symbol) for symbol in SYMBOLS)
    assert last_symbol > len(text) * 0.9
    assert plain_method(embedded) is None


def test_plain_payload_is_detected(cover: Path, tmp_path: Path) -> None:
    embedded = tmp_path / "embedded.md"
    cover.rename(tmp_path / "cover.md")
    service.hide(tmp_path / "cover.md", FILE, embedded, PLAIN)
    assert plain_method(embedded) == MethodId.ZERO_WIDTH
    assert plain_method(tmp_path / "cover.md") is None


def test_output_keeps_the_carrier_format(cover: Path, tmp_path: Path) -> None:
    assert service.output_suffixes(MethodId.ZERO_WIDTH, Path("notes.MD")) == (".md",)

    with pytest.raises(UnsupportedFormatError, match=r"\.txt"):
        service.hide(cover, FILE, tmp_path / "embedded.md", ENCRYPTED)


def test_emoji_joined_by_zero_width_joiners_make_a_cover(tmp_path: Path) -> None:
    """U+200D is no symbol: it would change letters in cursive scripts even at a word end."""
    family = chr(0x200D).join(chr(code) for code in (0x1F468, 0x1F469, 0x1F467))
    original = f"Family: {family} at home. " * 50
    cover = tmp_path / "family.txt"
    cover.write_text(original, encoding="utf-8")
    embedded = tmp_path / "embedded.txt"
    service.hide(cover, HiddenFile("a.txt", b"family"), embedded, ENCRYPTED)

    assert _without_symbols(embedded.read_text(encoding="utf-8")) == original
    assert service.reveal(embedded, tmp_path, PASSWORD).path.read_bytes() == b"family"


def test_cover_with_zero_width_characters_is_rejected(tmp_path: Path) -> None:
    cover = tmp_path / "spaced.txt"
    cover.write_text(f"Hello{SYMBOLS[0]}world. " * 50, encoding="utf-8")

    with pytest.raises(UnsupportedFormatError, match="already contains zero-width"):
        service.hide(cover, HiddenFile("a", b"x"), tmp_path / "embedded.txt", ENCRYPTED)


def test_cover_with_a_leading_zero_width_character_is_rejected(tmp_path: Path) -> None:
    cover = tmp_path / "joined.txt"
    cover.write_text(SYMBOLS[3] + "Hello, world. " * 50, encoding="utf-8")

    with pytest.raises(UnsupportedFormatError, match="already contains zero-width"):
        service.hide(cover, HiddenFile("a", b"x"), tmp_path / "embedded.txt", PLAIN)


def test_non_utf8_text_is_rejected(tmp_path: Path) -> None:
    cover = tmp_path / "latin1.txt"
    cover.write_bytes("café".encode("latin-1"))

    with pytest.raises(UnsupportedFormatError, match="not UTF-8"):
        service.describe_carrier(cover, CarrierKind.TEXT)


def test_a_text_too_large_to_read_is_refused_before_reading(tmp_path: Path) -> None:
    """17 MB took 980 MB of memory to reveal from."""
    limit = 16 << 20
    cover = tmp_path / "long.txt"
    cover.write_bytes(b"a" * limit)
    assert len(text_media.read(cover)) == limit
    cover.write_bytes(b"a" * (limit + 1))

    for read in (
        lambda: service.describe_carrier(cover, CarrierKind.TEXT),
        lambda: ZeroWidthChannel(cover),
    ):
        with pytest.raises(UnsupportedFormatError, match=r"long\.txt is too large to read"):
            read()


def test_describe_and_capacity(cover: Path) -> None:
    details = service.describe_carrier(cover, CarrierKind.TEXT)
    assert details.description == f"{len(COVER):,} characters · TXT"
    points = len(insertion_points(COVER))
    assert details.capacity == service.capacity(cover, research=True) == points * 2 // 8


def test_estimate_reads_the_carrier_once_until_it_changes(
    cover: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[Path] = []
    original_init = ZeroWidthChannel.__init__

    def counting_init(self: ZeroWidthChannel, path: Path, *settings: Any) -> None:
        opened.append(path)
        original_init(self, path, *settings)

    monkeypatch.setattr(ZeroWidthChannel, "__init__", counting_init)
    first = service.estimate(cover, FILE.name, 10, ENCRYPTED)
    assert service.estimate(cover, FILE.name, 20, ENCRYPTED).available == first.available
    assert len(opened) == 1
    cover.write_bytes(COVER.encode() * 2)
    assert service.estimate(cover, FILE.name, 10, ENCRYPTED).available != first.available
    assert len(opened) == 2


@pytest.mark.parametrize("cipher_id", [CipherId.AES_GCM, CipherId.NONE])
def test_max_data_size_is_exact(cover: Path, cipher_id: CipherId) -> None:
    channel = ZeroWidthChannel(cover)
    limit = container.max_data_size(channel, FILE.name, cipher_id)
    password = PASSWORD if cipher_id != CipherId.NONE else None
    fitting = HiddenFile(FILE.name, bytes(limit))
    container.hide(
        channel,
        fitting,
        method=MethodId.ZERO_WIDTH,
        cipher_id=cipher_id,
        password=password,
        order=UnitOrder.PUBLIC if password is None else UnitOrder.KEYED,
    )

    with pytest.raises(CapacityError):
        container.hide(
            channel,
            HiddenFile(FILE.name, bytes(limit + 1)),
            method=MethodId.ZERO_WIDTH,
            cipher_id=cipher_id,
            password=password,
            order=UnitOrder.PUBLIC if password is None else UnitOrder.KEYED,
        )


# Research's symbol sets and insertion rules (zw.sym, zw.rules).
SET_CODES = {
    2: (0x200B, 0x200C),
    4: (0x200B, 0x200C, 0x2063, 0x2060),
    8: (0x200B, 0x200C, 0x2063, 0x2060, 0x2061, 0x2062, 0x2064, 0xFEFF),
}
VARIANTS = [(2, "strict"), (8, "strict"), (2, "relaxed"), (4, "relaxed"), (8, "relaxed")]
SHORT_FILE = HiddenFile("a.txt", b"traces")
ORDERS = pytest.mark.parametrize(
    ("cipher", "changes"),
    [
        (CipherId.AES_GCM, {}),
        (CipherId.AES_GCM, {"frame.order": "public"}),
        (CipherId.NONE, {}),
    ],
    ids=["keyed", "public", "plain"],
)


def _zw_settings(size: int, rules: str, changes: dict[str, Value] | None = None) -> Settings:
    return Settings.of({"zw.sym": size, "zw.rules": rules, **(changes or {})})


def _hide_research(
    cover: Path, output: Path, settings: Settings, cipher: CipherId, file: HiddenFile = FILE
) -> None:
    password = None if cipher == CipherId.NONE else PASSWORD
    options = HideOptions(
        CarrierKind.TEXT, MethodId.ZERO_WIDTH, cipher, password, settings=settings, is_research=True
    )
    service.hide(cover, file, output, options)


def _reveal_research(path: Path, out: Path, cipher: CipherId, settings: Settings) -> bytes:
    password = None if cipher == CipherId.NONE else PASSWORD
    research = ResearchReveal(MethodId.ZERO_WIDTH, cipher, settings)

    return service.reveal(path, out, password, research=research).path.read_bytes()


def test_the_symbol_sets_are_frozen() -> None:
    """Changing a symbol or its value makes text hidden with that set unreadable."""
    assert {size: tuple(map(ord, symbols)) for size, symbols in SYMBOL_SETS.items()} == SET_CODES
    assert SYMBOL_SETS[4] == SYMBOLS
    assert all(unicodedata.category(symbol) == "Cf" for symbol in SYMBOL_SETS[8])


@ORDERS
@pytest.mark.parametrize(("size", "rules"), VARIANTS)
def test_research_symbol_sets_and_rules_round_trip(
    cover: Path,
    tmp_path: Path,
    size: int,
    rules: str,
    cipher: CipherId,
    changes: dict[str, Value],
) -> None:
    settings = _zw_settings(size, rules, changes)
    embedded = tmp_path / "embedded.txt"
    _hide_research(cover, embedded, settings, cipher)
    text = embedded.read_bytes().decode()

    assert strip_symbols(text, SYMBOL_SETS[size])[0] == COVER
    assert set(text) - set(COVER) == set(SYMBOL_SETS[size])
    assert _reveal_research(embedded, tmp_path / "out", cipher, settings) == FILE.data


@pytest.mark.parametrize("size", [2, 4, 8])
@pytest.mark.parametrize("is_relaxed", [False, True], ids=["strict", "relaxed"])
def test_the_capacity_follows_the_bits_per_point(cover: Path, size: int, is_relaxed: bool) -> None:
    channel = ZeroWidthChannel(cover, rules=ZeroWidthRules(SYMBOL_SETS[size], is_relaxed))
    points = relaxed_points(COVER) if is_relaxed else insertion_points(COVER)
    bits = {2: 1, 4: 2, 8: 3}[size]

    assert channel.unit_count == len(points)
    assert channel.bits_per_unit == bits
    assert channel.capacity(0) == len(points) * bits // 8


@pytest.mark.parametrize("cipher", [CipherId.AES_GCM, CipherId.NONE])
@pytest.mark.parametrize(("size", "rules"), [(4, "strict"), *VARIANTS])
def test_a_payload_reads_only_with_its_symbol_set_and_rules(
    cover: Path, tmp_path: Path, size: int, rules: str, cipher: CipherId
) -> None:
    written = _zw_settings(size, rules)
    embedded = tmp_path / "embedded.txt"
    _hide_research(cover, embedded, written, cipher)
    readers = [_zw_settings(*variant) for variant in [(4, "strict"), *VARIANTS]]

    for index, settings in enumerate(reader for reader in readers if reader != written):
        with pytest.raises(NoHiddenDataError):
            _reveal_research(embedded, tmp_path / f"out-{index}", cipher, settings)


@pytest.mark.parametrize(
    ("text", "points"),
    [
        ("ab", [0, 1]),  # the last character takes one
        ("a\r\nb", [0, 2, 3]),  # none after CR, so CRLF stays whole, but one after LF
        ("a\rb", [0, 2]),
        ("\n\n", [0, 1]),
        ("e" + chr(0x301) + "x", [1, 2]),  # never before a combining mark
        ("a" + chr(0x20DD) + "b" + chr(0x903), [1, 3]),  # enclosing and spacing marks too
        ("a" + chr(0x200D) + "b", [0, 1, 2]),  # the cover's zero-width characters count
        (chr(0xFEFF) + "ab", [0, 1, 2]),  # so does its byte order mark
        ("<b>x.io</b>", list(range(11))),
        ("", []),
    ],
    ids=[
        "end",
        "crlf",
        "cr",
        "lf",
        "mark",
        "other-marks",
        "joiner",
        "byte-order-mark",
        "markup",
        "empty",
    ],
)
def test_relaxed_points(text: str, points: list[int]) -> None:
    assert relaxed_points(text) == points


def test_an_empty_text_holds_nothing(tmp_path: Path) -> None:
    """Reveal opens every channel that may fit a file, so an empty text must not break it."""
    empty = tmp_path / "empty.txt"
    empty.write_bytes(b"")

    assert insertion_points("") == relaxed_points("") == []
    assert service.describe_carrier(empty, CarrierKind.TEXT).capacity == 0

    with pytest.raises(NoHiddenDataError):
        service.reveal(empty, tmp_path / "out", PASSWORD)


def _relaxed_reference(text: str) -> list[int]:
    """The rule as worded: any character but CR, unless a Unicode 3.2 mark comes next."""

    def is_mark(index: int) -> bool:
        return index < len(text) and unicodedata.ucd_3_2_0.category(text[index]).startswith("M")

    return [index for index, char in enumerate(text) if char != "\r" and not is_mark(index + 1)]


_POOL = [
    "a",
    chr(0x44F),
    " ",
    "\r",
    "\n",
    "#",
    "`",
    ".",
    chr(0x301),
    chr(0x903),
    chr(0x20DD),
    chr(0x200D),
    chr(0xFEFF),
    chr(0x645),
    chr(0x1100),
    chr(0x1F44D),
    chr(0x1F3FD),
    chr(0x1AB0),
]


@pytest.mark.parametrize("seed", range(5))
def test_relaxed_points_match_a_direct_reference(seed: int) -> None:
    indices = np.random.default_rng(seed).integers(0, len(_POOL), 500)
    text = "".join(_POOL[index] for index in indices)

    assert relaxed_points(text) == _relaxed_reference(text)


@pytest.mark.parametrize(
    "text",
    [
        COVER,
        "# Title\n\n- item\n1. step\n\n[a link](x) and **bold** `code` text.\n",
        "See https://a.io/x, <b>hi</b> &amp; " + chr(0x645) + chr(0x631) + " x.txt\n```\nok\n```",
    ],
    ids=["prose", "markdown", "fragile"],
)
def test_relaxed_rules_only_add_points(text: str) -> None:
    assert relaxed_points(text) == _relaxed_reference(text)
    assert set(insertion_points(text)) < set(relaxed_points(text))


def test_relaxed_points_are_the_same_on_every_python_version() -> None:
    """Changing these points makes text hidden on another Python version unreadable. In
    Unicode 3.2, U+06DE was a mark and U+1885 a letter. Today it is the other way round, and
    U+1AB0 is a mark added since."""
    sample = "a" + chr(0x6DE) + "b" + chr(0x1885) + "c" + chr(0x1AB0) + "d" + chr(0x301) + "\r\n"
    sample += "e" + chr(0x20DD) + "f" + chr(0x903)

    assert relaxed_points(sample) == [1, 2, 3, 4, 5, 7, 9, 11, 13]


@pytest.mark.parametrize(
    ("size", "code"),
    [(2, 0x200C), (8, 0x2061), (8, 0x2064), (8, 0xFEFF)],
    ids=["2-zwnj", "8-function-application", "8-invisible-plus", "8-mid-text-feff"],
)
def test_a_cover_with_a_symbol_of_the_set_is_refused(tmp_path: Path, size: int, code: int) -> None:
    cover = tmp_path / "spaced.txt"
    cover.write_text(f"Hello{chr(code)}world. " * 50, encoding="utf-8")

    with pytest.raises(UnsupportedFormatError, match="already contains zero-width"):
        settings = _zw_settings(size, "strict")
        _hide_research(cover, tmp_path / "embedded.txt", settings, CipherId.NONE, SHORT_FILE)


def test_symbols_of_other_sets_stay_cover_text(tmp_path: Path) -> None:
    """Only the chosen set's symbols make a cover unusable."""
    original = f"Hello{chr(0x2063)}world, a manuscript keeps traces. " * 60
    cover, embedded = tmp_path / "cover.txt", tmp_path / "embedded.txt"
    cover.write_text(original, encoding="utf-8")
    settings = _zw_settings(2, "strict")
    _hide_research(cover, embedded, settings, CipherId.AES_GCM, SHORT_FILE)
    text = embedded.read_text(encoding="utf-8")

    assert strip_symbols(text, SYMBOL_SETS[2])[0] == original
    assert _reveal_research(embedded, tmp_path / "out", CipherId.AES_GCM, settings) == b"traces"


@pytest.mark.parametrize("rules", ["strict", "relaxed"])
def test_a_leading_byte_order_mark_stays_in_the_text(tmp_path: Path, rules: str) -> None:
    original = chr(0xFEFF) + "A manuscript keeps traces of older words.\n" * 60
    cover, embedded = tmp_path / "cover.txt", tmp_path / "embedded.txt"
    cover.write_bytes(original.encode())
    settings = _zw_settings(8, rules)
    _hide_research(cover, embedded, settings, CipherId.AES_GCM, SHORT_FILE)
    text = embedded.read_bytes().decode()

    assert strip_symbols(text, SYMBOL_SETS[8])[0] == original
    assert _reveal_research(embedded, tmp_path / "out", CipherId.AES_GCM, settings) == b"traces"


def test_strip_symbols_reads_only_the_chosen_set() -> None:
    bom, eight = chr(0xFEFF), SYMBOL_SETS[8]

    assert strip_symbols(f"a{eight[6]}b{eight[1]}", eight) == ("ab", {0: 6, 1: 1})
    assert strip_symbols(f"a{eight[6]}b{eight[1]}") == (f"a{eight[6]}b", {2: 1})
    assert strip_symbols(f"a{SYMBOLS[3]}b", SYMBOL_SETS[2]) == (f"a{SYMBOLS[3]}b", {})
    assert strip_symbols(bom + bom + "a", eight) == (bom + "a", {0: 7})
    assert strip_symbols(bom + "a", eight) == (bom + "a", {})


# Texts holding VECTOR_FILE under AES-256-GCM in the keyed order, with VECTOR_PASSWORD and the
# cheap VECTOR_ARGON2 profile: 2 and 8 symbols under strict rules, and 4 under relaxed ones.
# Each digit stands for the symbol of that value (SET_CODES). Never regenerate them: data
# hidden with these settings must stay readable.
VECTOR_FILE = HiddenFile("a.txt", b"cipherstitch")
VECTOR_PASSWORD = "пароль-йї"  # noqa: S105
VECTOR_ARGON2 = {"ARGON2_MEMORY_KIB": 32, "ARGON2_ITERATIONS": 1}
TWO_VECTOR = (
    "A0 m0a1n0u1s1c1r1i0p1t0 k1e1e1p0s1 t1r0a0c0e1s0 o1f0 w1h1a1t1 w0a0s1 w1r1i1t1t0e0n0 b1"
    "e1f1o0r1e1 i0t, a1n0d0 a0 c0a1r0e0f1u1l0 r1e0a1d0e1r1 c1a0n0 s1t1i1l1l1 f0i0n0d0 t0h1e"
    "0 o0l1d0e1r1 w1o1r1d0s1 u1n1d0e1r0 t0h0e0 n1e0w0e0r0 o1n0e0s. A1 m0a0n0u0s0c0r0i0p1t0 "
    "k0e1e0p1s0 t0r0a1c1e0s0 o1f1 w0h0a1t0 w1a0s1 w0r1i1t0t1e0n0 b0e0f1o1r1e1 i0t, a0n0d0 a"
    "1 c1a1r0e0f0u0l0 r0e0a0d0e0r0 c1a1n1 s1t0i0l1l0 f0i0n0d1 t1h1e0 o1l0d0e0r1 w1o0r1d0s1 "
    "u0n1d0e1r0 t1h1e0 n0e1w1e1r0 o0n0e0s. A1 m1a1n0u1s0c1r1i0p0t0 k0e0e0p0s0 t1r1a0c1e1s1 "
    "o0f0 w0h1a0t0 w1a1s0 w1r0i0t1t0e0n1 b0e1f0o0r0e1 i1t, a0n1d0 a0 c0a1r1e0f1u1l1 r0e0a0d"
    "0e1r1 c1a0n1 s1t0i1l1l1 f0i1n1d1 t1h0e0 o0l0d0e0r1 w1o0r1d1s0 u1n0d0e1r0 t0h1e0 n0e1w0"
    "e0r1 o1n1e1s. A0 m1a0n0u1s0c1r0i0p1t0 k0e1e1p1s0 t1r0a1c1e0s0 o1f1 w1h0a0t0 w1a1s0 w0r"
    "0i0t0t0e1n1 b1e0f0o0r1e0 i1t, a0n1d1 a0 c0a1r1e1f0u0l1 r1e1a1d0e0r1 c0a0n0 s1t0i1l0l0 "
    "f0i1n1d0 t0h0e0 o1l0d1e0r1 w1o0r1d0s0 u1n0d0e1r1 t1h0e0 n0e0w0e1r1 o1n0e0s. A1 m1a0n1u"
    "0s1c0r1i0p1t0 k0e1e0p0s0 t0r0a0c0e0s0 o1f1 w1h1a0t0 w0a0s0 w0r0i1t0t1e0n0 b1e0f1o0r0e1"
    " i1t, a0n1d1 a0 c0a0r1e0f0u1l0 r0e0a1d0e0r1 c1a0n1 s1t0i0l0l1 f0i1n0d0 t0h1e1 o0l1d1e0"
    "r0 w1o1r1d1s0 u1n0d0e1r0 t0h0e0 n1e1w0e1r1 o0n0e0s. A1 m0a0n1u0s1c1r1i1p0t1 k1e1e1p1s1"
    " t1r1a0c0e0s0 o1f1 w1h1a1t1 w1a1s0 w0r0i0t0t1e0n0 b0e1f1o0r1e0 i1t, a0n1d0 a1 c0a1r0e0"
    "f1u1l1 r1e0a1d0e1r1 c0a0n0 s1t1i0l0l1 f0i0n0d0 t0h0e1"
)

EIGHT_VECTOR = (
    "A2 m1a5n3u5s7c0r3i3p0t1 k4e6e6p2s0 t3r4a6c4e5s5 o2f0 w2h4a7t6 w5a0s2 w7r2i3t7t0e0n7 b3"
    "e5f6o7r2e6 i6t, a6n6d4 a3 c2a2r6e5f5u6l4 r7e0a2d5e7r2 c2a4n6 s5t6i1l4l7 f6i1n6d7 t4h1e"
    "2 o4l0d1e1r1 w5o0r6d4s3 u6n6d3e1r3 t3h3e0 n7e2w1e2r6 o1n2e1s. A1 m7a0n0u3s4c4r2i7p7t0 "
    "k6e2e3p5s1 t4r3a0c2e6s1 o4f4 w2h6a6t6 w7a4s0 w1r3i6t0t0e4n3 b3e5f4o5r0e5 i5t, a5n7d4 a"
    "1 c5a7r2e0f7u3l0 r6e0a7d7e6r7 c1a5n1 s1t7i4l2l1 f0i1n3d6 t7h7e3 o5l2d2e3r1 w6o7r3d7s0 "
    "u0n2d0e2r2 t4h2e6"
)

RELAXED_VECTOR = (
    "#2 2N1o0t2e1s1\r\n0\r\n2S3e3e1 3h0t2t1p2s3:0/2/3e2x0a2m1p0l1e2.2c2o2m3/1a0_0b0?0q1=1x3 1a2"
    "n3d0 0`3l0s3 1-2l2`1 0(1t0w3i1c2e0)0,1 3t2h2e3n1 2*1r2e0a2d2*3 1<2b3>2t1h3i0s2<0/3b2>1"
    " 2&2a1m2p0;3 0t1h2a0t3:3 2d0o3n1e3.1\r\n1#3 3N3o3t1e0s0\r\n3\r\n3S3e2e0 3h0t0t0p3s1:0/1/2e"
    "2x3a2m1p2l0e0.1c2o3m0/1a1_0b1?0q3=3x3 1a2n3d0 3`0l0s3 3-0l3`2 3(3t0w2i2c3e0)1,0 1t1h0e"
    "1n0 0*3r1e2a3d0*3 0<2b3>2t2h2i0s0<2/0b0>1 2&0a1m1p2;3 2t1h0a0t2:2 1d2o0n2e0.2\r\n2#0 1N1"
    "o3t0e2s2\r\n3\r\n3S0e2e1 3h0t2t2p0s3:0/0/2e0x3a1m1p3l1e1.3c3o0m2/1a2_3b0?0q0=0x1 1a1n0d0 1"
    "`1l0s3 1-3l3`2 3(2t2w2i3c2e1)0,2 2t2h0e2n3 0*1r3e0a1d2*3 1<1b2>3t2h0i3s3<2/1b2>2 2&1a0"
)


def _vector_text(vector: str, size: int) -> str:
    return "".join(chr(SET_CODES[size][int(char)]) if char.isdigit() else char for char in vector)


@pytest.mark.parametrize(
    ("vector", "size", "is_relaxed"),
    [(TWO_VECTOR, 2, False), (EIGHT_VECTOR, 8, False), (RELAXED_VECTOR, 4, True)],
    ids=["2-symbols", "8-symbols", "relaxed"],
)
def test_format_vectors_stay_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, vector: str, size: int, is_relaxed: bool
) -> None:
    for name, value in VECTOR_ARGON2.items():
        monkeypatch.setattr(kdf, name, value)

    path = tmp_path / "vector.txt"
    path.write_bytes(_vector_text(vector, size).encode())
    candidates = (Candidate(MethodId.ZERO_WIDTH, UnitOrder.KEYED),)
    channel = ZeroWidthChannel(path, rules=ZeroWidthRules(SYMBOL_SETS[size], is_relaxed))
    revealed = container.reveal([(channel, candidates)], VECTOR_PASSWORD)

    assert revealed == container.Revealed(VECTOR_FILE, MethodId.ZERO_WIDTH, CipherId.AES_GCM)

    with pytest.raises(NoHiddenDataError):
        container.reveal([(ZeroWidthChannel(path), candidates)], VECTOR_PASSWORD)


def test_a_text_with_too_few_insertion_points_refuses_the_prefix(tmp_path: Path) -> None:
    """A sentence holds a few bytes: the prefix's bits outnumbered the units (a ValueError)."""
    text = "A manuscript keeps traces of what was there before."
    cover = tmp_path / "short.txt"
    cover.write_text(text)
    channel = ZeroWidthChannel(cover)

    assert channel.capacity(0) == len(insertion_points(text)) * 2 // 8 < 16

    with pytest.raises(CapacityError, match="holds at most 0"):
        channel.write(bytes(16), b"", None)
