"""Research's space and tab for DOCX whitespace padding: a 1 bit is a trailing tab (w:tab)
instead of a no-break space (docs/format.md#stream-channels)."""

import re
import time
import zipfile
from collections.abc import Sequence
from pathlib import Path

import pytest

from cipherstitch import container, kdf, service
from cipherstitch.catalog import CarrierKind, CipherId, MethodId
from cipherstitch.container import Candidate, UnitOrder
from cipherstitch.errors import CapacityError, NoHiddenDataError, UnsupportedFormatError
from cipherstitch.methods.whitespace import NO_BREAK_SPACE, TAB, WhitespaceChannel, WhitespaceRules
from cipherstitch.params import engine
from cipherstitch.params.settings import Settings
from cipherstitch.payload import HiddenFile
from cipherstitch.service import ResearchReveal

PASSWORD = "quiet harbour 47 lanterns"  # noqa: S105
FILE = HiddenFile("note.txt", b"a manuscript keeps traces " * 4)
PARAGRAPHS = 40
TABS = Settings.of({"ws.sym": "space-tab"})
TAB_RULES = WhitespaceRules(has_tabs=True)
MAIN_PART = "word/document.xml"
REFUSED = "already has tabs at the end of paragraphs"
BIT_RUN = re.compile(r'<w:r>((?:<w:t xml:space="preserve"> +</w:t>|<w:tab/>)+)</w:r></w:p>')
SYMBOL = re.compile(r'(<w:tab/>)|<w:t xml:space="preserve">( +)</w:t>')
# Tails as Word writes them: runs split by revision ids and properties, proofing and
# bookmark marks between them, a tab in a run of its own, tabs and spaces in one run, and
# a paragraph's own tab stop, which is no tab. They spell 0x65, 0xC3 and 0xB9.
WORD_PARAGRAPHS = (
    '<w:r w:rsidR="00A1B2C3"><w:rPr><w:lang w:val="en-GB"/></w:rPr><w:t>Hello.</w:t>'
    '<w:t xml:space="preserve"> </w:t></w:r><w:r><w:tab/></w:r><w:proofErr w:type="spellEnd"/>'
    '<w:r w:rsidRPr="00D4E5F6"><w:tab/><w:t xml:space="preserve">  </w:t><w:tab/></w:r>'
    '<w:bookmarkEnd w:id="0"/><w:r><w:t xml:space="preserve"> </w:t><w:lastRenderedPageBreak/>'
    "<w:tab/></w:r>",
    "<w:r><w:t>Total:</w:t><w:tab/><w:t>42</w:t></w:r><w:r><w:rPr><w:b/></w:rPr><w:tab/>"
    '<w:tab/><w:t xml:space="preserve"> </w:t></w:r><w:r><w:t xml:space="preserve">   </w:t>'
    "<w:tab/><w:tab/></w:r>",
    '<w:pPr><w:tabs><w:tab w:val="right" w:leader="dot" w:pos="8640"/></w:tabs></w:pPr>'
    '<w:r><w:tab/><w:t xml:space="preserve"> </w:t><w:tab/><w:tab/><w:tab/>'
    '<w:t xml:space="preserve">  </w:t><w:tab/></w:r>',
)

# The tails of ten paragraphs holding VECTOR_FILE under AES-256-GCM, with VECTOR_PASSWORD
# and the cheap VECTOR_ARGON2 profile: "1" for a tab, "0" for a space. Never regenerate
# them: data hidden with space and tab must stay readable.
VECTOR_FILE = HiddenFile("a.txt", b"cipherstitch")
VECTOR_PASSWORD = "пароль-йї"  # noqa: S105
VECTOR_ARGON2 = {"ARGON2_MEMORY_KIB": 32, "ARGON2_ITERATIONS": 1}
TAB_VECTOR = (
    "10000101010110101101011101000011011100111101000001011110111",
    "00101111000110010000000110101111111011101110010001100100000",
    "00010110001100111110010111101100000011000001000010100111111",
    "00011111110110000100101110101100010111111010110011011101011",
    "011101001110111100000000100011011010010011111011111110111111",
    "11101011101100000000110110011110001001011111000111110101001",
    "01101001001010110010001110000001100101101110111001101101111",
    "00010001100001000010010101101100010111100111011000101011111",
    "01001110101010010010000101101100100110100011100010111100000",
    "111010011111010011111000010101111001001100000010100100110101",
)


def _cover(path: Path, lines: Sequence[str] = ()) -> Path:
    """``lines``, then PARAGRAPHS paragraphs of text. python-docx writes a tab as a w:tab and
    a line break as a w:br."""
    python_docx = pytest.importorskip("docx")
    document = python_docx.Document()

    for line in lines:
        document.add_paragraph(line)

    for index in range(PARAGRAPHS):
        document.add_paragraph(f"Paragraph {index}: a manuscript keeps traces.")

    document.save(str(path))

    return path


def _word_document(path: Path, paragraphs: Sequence[str]) -> Path:
    """A DOCX whose body holds ``paragraphs``, each the XML inside a w:p."""
    python_docx = pytest.importorskip("docx")
    template = path.with_name(f"template-{path.name}")
    python_docx.Document().save(str(template))
    body = "".join(f"<w:p>{paragraph}</w:p>" for paragraph in paragraphs).encode()

    with zipfile.ZipFile(template) as source, zipfile.ZipFile(path, "w") as output:
        for info in source.infolist():
            content = source.read(info)

            if info.filename == MAIN_PART:
                content = content.replace(b"<w:body>", b"<w:body>" + body, 1)

            output.writestr(info, content)

    return path


def _texts(path: Path) -> list[str]:
    python_docx = pytest.importorskip("docx")

    return [paragraph.text for paragraph in python_docx.Document(str(path)).paragraphs]


def _options(settings: Settings, cipher: CipherId) -> service.HideOptions:
    password = None if cipher == CipherId.NONE else PASSWORD

    return service.HideOptions(
        CarrierKind.DOCUMENT,
        MethodId.WHITESPACE,
        cipher,
        password,
        settings=settings,
        is_research=True,
    )


def _reveal(path: Path, tmp_path: Path, cipher: CipherId, settings: Settings | None) -> bytes:
    """Research's reveal with ``settings``, Standard's with ``None``."""
    password = None if cipher == CipherId.NONE else PASSWORD
    research = None if settings is None else ResearchReveal(MethodId.WHITESPACE, cipher, settings)
    result = service.reveal(path, tmp_path / "out", password, research=research)
    data = result.path.read_bytes()
    result.path.unlink()

    return data


@pytest.mark.parametrize("cipher", [CipherId.AES_GCM, CipherId.NONE], ids=["encrypted", "plain"])
def test_a_space_and_tab_payload_reveals_only_with_its_setting(
    tmp_path: Path, cipher: CipherId
) -> None:
    cover, embedded = _cover(tmp_path / "cover.docx"), tmp_path / "embedded.docx"
    service.hide(cover, FILE, embedded, _options(TABS, cipher))

    assert _reveal(embedded, tmp_path, cipher, TABS) == FILE.data
    assert [text.rstrip(" " + TAB) for text in _texts(embedded)] == _texts(cover)

    for settings in (Settings(), None):  # space and no-break space, in Research and Standard
        with pytest.raises(NoHiddenDataError):
            _reveal(embedded, tmp_path, cipher, settings)


@pytest.mark.parametrize("cipher", [CipherId.AES_GCM, CipherId.NONE], ids=["encrypted", "plain"])
def test_a_space_and_no_break_space_payload_is_not_read_with_tabs(
    tmp_path: Path, cipher: CipherId
) -> None:
    cover, embedded = _cover(tmp_path / "cover.docx"), tmp_path / "embedded.docx"
    service.hide(cover, FILE, embedded, _options(Settings(), cipher))

    assert _reveal(embedded, tmp_path, cipher, None) == FILE.data

    with pytest.raises(NoHiddenDataError):
        _reveal(embedded, tmp_path, cipher, TABS)


def test_ones_are_tabs_at_the_end_of_paragraphs(tmp_path: Path) -> None:
    cover, embedded = _cover(tmp_path / "cover.docx"), tmp_path / "embedded.docx"
    stream = bytes(range(0, 256, 3))
    WhitespaceChannel(cover, TAB_RULES).write(stream[:4], stream[4:], None).save(embedded)

    with zipfile.ZipFile(embedded) as package:
        main = package.read(MAIN_PART).decode()

    runs = BIT_RUN.findall(main)
    bits = "".join(
        "1" if tab else "0" * len(spaces) for run in runs for tab, spaces in SYMBOL.findall(run)
    )

    assert len(runs) == PARAGRAPHS
    assert bits.startswith("".join(f"{byte:08b}" for byte in stream))
    assert NO_BREAK_SPACE not in main
    assert WhitespaceChannel(embedded, TAB_RULES).read(0, len(stream), None) == stream


def test_tabs_read_back_from_words_xml(tmp_path: Path) -> None:
    cover = _word_document(tmp_path / "word.docx", WORD_PARAGRAPHS)

    assert WhitespaceChannel(cover, TAB_RULES).read(0, 4, None) == bytes([0x65, 0xC3, 0xB9])
    assert WhitespaceChannel(cover).read(0, 4, None) == b""  # tabs end its tails


def test_a_tab_character_in_the_text_is_a_tab(tmp_path: Path) -> None:
    tail = f"{TAB}{TAB}  {TAB} {TAB}{TAB}"
    paragraph = f'<w:r><w:t xml:space="preserve">Ada{tail}</w:t></w:r>'
    cover = _word_document(tmp_path / "typed.docx", [paragraph] * PARAGRAPHS)

    assert WhitespaceChannel(cover, TAB_RULES).read(0, 1, None) == bytes([0xCB])

    with pytest.raises(UnsupportedFormatError, match=REFUSED):
        service.hide(cover, FILE, tmp_path / "embedded.docx", _options(TABS, CipherId.AES_GCM))


@pytest.mark.parametrize(
    "line",
    ["Name: \t", "Signature:\t\t", "Date:\t  ", f"Place:\t{NO_BREAK_SPACE} "],
    ids=["tab", "tabs", "tab-then-spaces", "tab-then-no-break-space"],
)
def test_a_cover_whose_paragraphs_end_in_tabs_is_refused(tmp_path: Path, line: str) -> None:
    """A reader could not tell the tabs from 1 bits, and removing them could break a form.
    Writing strips spaces and no-break spaces, so a tab before them counts too."""
    cover, embedded = _cover(tmp_path / "form.docx", [line]), tmp_path / "embedded.docx"

    with pytest.raises(UnsupportedFormatError, match=REFUSED):
        service.hide(cover, FILE, embedded, _options(TABS, CipherId.AES_GCM))

    assert not embedded.exists()

    service.hide(cover, FILE, embedded, _options(Settings(), CipherId.AES_GCM))

    assert _texts(embedded)[0].startswith(line.rstrip(" " + NO_BREAK_SPACE))
    assert _reveal(embedded, tmp_path, CipherId.AES_GCM, None) == FILE.data


def test_tabs_before_text_or_a_break_and_tab_stops_are_kept(tmp_path: Path) -> None:
    python_docx = pytest.importorskip("docx")
    shared = pytest.importorskip("docx.shared")
    document = python_docx.Document()
    lines = ["Name:\tAda", "Signature:\t\n"]

    for line in lines:
        document.add_paragraph(line)

    document.add_paragraph().paragraph_format.tab_stops.add_tab_stop(shared.Inches(3))

    for index in range(PARAGRAPHS):
        document.add_paragraph(f"Paragraph {index}: a manuscript keeps traces.")

    cover, embedded = tmp_path / "form.docx", tmp_path / "embedded.docx"
    document.save(str(cover))
    service.hide(cover, FILE, embedded, _options(TABS, CipherId.AES_GCM))
    paragraphs = python_docx.Document(str(embedded)).paragraphs

    assert paragraphs[0].text.startswith(lines[0])
    assert paragraphs[1].text.startswith(lines[1])
    assert len(paragraphs[2].paragraph_format.tab_stops) == 1
    assert _reveal(embedded, tmp_path, CipherId.AES_GCM, TABS) == FILE.data


def test_hiding_again_with_tabs_is_refused(tmp_path: Path) -> None:
    cover, embedded = _cover(tmp_path / "cover.docx"), tmp_path / "embedded.docx"
    service.hide(cover, FILE, embedded, _options(TABS, CipherId.AES_GCM))

    with pytest.raises(UnsupportedFormatError, match=REFUSED):
        service.hide(embedded, FILE, embedded, _options(TABS, CipherId.AES_GCM))

    assert _reveal(embedded, tmp_path, CipherId.AES_GCM, TABS) == FILE.data


def test_tabs_over_a_no_break_space_payload_replace_it(tmp_path: Path) -> None:
    cover, first = _cover(tmp_path / "cover.docx", ["Name:\tAda"]), tmp_path / "first.docx"
    service.hide(cover, FILE, first, _options(Settings(), CipherId.NONE))
    second = tmp_path / "second.docx"
    service.hide(first, FILE, second, _options(TABS, CipherId.AES_GCM))

    with zipfile.ZipFile(second) as package:
        assert NO_BREAK_SPACE not in package.read(MAIN_PART).decode()

    assert _reveal(second, tmp_path, CipherId.AES_GCM, TABS) == FILE.data

    with pytest.raises(NoHiddenDataError):
        _reveal(second, tmp_path, CipherId.NONE, None)


def test_tabs_over_a_no_break_space_payload_after_a_final_tab_are_refused(
    tmp_path: Path,
) -> None:
    """Writing would strip the earlier payload and leave the cover's tab before the bits."""
    cover, first = _cover(tmp_path / "form.docx", ["Signature:\t"]), tmp_path / "first.docx"
    service.hide(cover, FILE, first, _options(Settings(), CipherId.NONE))

    with pytest.raises(UnsupportedFormatError, match=REFUSED):
        service.hide(first, FILE, tmp_path / "second.docx", _options(TABS, CipherId.AES_GCM))


def test_a_paragraph_of_many_tabs_is_read_in_linear_time(tmp_path: Path) -> None:
    paragraph = "<w:r><w:tab/><w:t>x</w:t></w:r>" * 100_000 + "<w:r><w:tab/></w:r>"
    cover = _word_document(tmp_path / "long.docx", [paragraph])
    start = time.perf_counter()
    channel = WhitespaceChannel(cover, TAB_RULES)

    assert time.perf_counter() - start < 2
    assert channel.read(0, 1, None) == b""  # one bit, no whole byte


def test_whitespace_settings_reach_the_channel() -> None:
    settings = Settings.of({"ws.sym": "space-tab", "ws.bpp": 128})

    assert engine.tuning(settings).whitespace == WhitespaceRules(128, has_tabs=True)
    assert engine.tuning(Settings.of({"ws.bpp": 16})).whitespace == WhitespaceRules(16)
    assert engine.tuning(Settings()).whitespace == WhitespaceRules()


def test_the_space_and_tab_vector_stays_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    python_docx = pytest.importorskip("docx")

    for name, value in VECTOR_ARGON2.items():
        monkeypatch.setattr(kdf, name, value)

    document = python_docx.Document()

    for index, tail in enumerate(TAB_VECTOR):
        paragraph = document.add_paragraph(f"Paragraph {index}: a manuscript keeps traces.")
        paragraph.add_run(tail.replace("0", " ").replace("1", TAB))

    path = tmp_path / "vector.docx"
    document.save(str(path))
    candidates = (Candidate(MethodId.WHITESPACE, UnitOrder.PUBLIC),)
    revealed = container.reveal([(WhitespaceChannel(path, TAB_RULES), candidates)], VECTOR_PASSWORD)

    assert revealed == container.Revealed(VECTOR_FILE, MethodId.WHITESPACE, CipherId.AES_GCM)

    with pytest.raises(NoHiddenDataError):
        container.reveal([(WhitespaceChannel(path), candidates)], VECTOR_PASSWORD)


def test_a_document_without_paragraphs_refuses_the_prefix(tmp_path: Path) -> None:
    """Zero paragraphs hold zero bits: the padding's random length raised a ValueError."""
    cover = _word_document(tmp_path / "empty.docx", [])
    channel = WhitespaceChannel(cover)

    assert channel.capacity(0) == 0

    with pytest.raises(CapacityError, match="holds at most 0"):
        channel.write(bytes(16), b"", None)
