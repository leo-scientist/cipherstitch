"""The default object location leaves a cover's own /Metadata as it was, whatever it holds."""

from pathlib import Path

import pytest

from cipherstitch import service
from cipherstitch.catalog import CarrierKind, CipherId, MethodId
from cipherstitch.payload import HiddenFile

FILE = HiddenFile("n.txt", b"field notes")
XMP = (
    f'<?xpacket begin="{chr(0xFEFF)}" id="W5M0MpCehiHzreSzNTczkc9d"?>'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
    '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    '<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">'
    "<dc:format>application/pdf</dc:format></rdf:Description></rdf:RDF></x:xmpmeta>"
    '<?xpacket end="w"?>'
).encode()


def _cover(path: Path, metadata: bytes | None) -> Path:
    """A PDF whose /Metadata is ``metadata`` as a stream, or a dictionary for ``None``."""
    pikepdf = pytest.importorskip("pikepdf")
    pdf = pikepdf.new()
    pdf.add_blank_page()

    if metadata is None:
        pdf.Root.Metadata = pikepdf.Dictionary(Type=pikepdf.Name.Metadata)
    else:
        pdf.Root.Metadata = pdf.make_stream(metadata)

    pdf.save(path, fix_metadata_version=False)

    return path


def _hidden(cover: Path, tmp_path: Path) -> Path:
    embedded = tmp_path / "embedded.pdf"
    options = service.HideOptions(
        CarrierKind.DOCUMENT, MethodId.OBJECT_STREAM, CipherId.NONE, None, is_research=True
    )
    service.hide(cover, FILE, embedded, options)

    assert service.reveal(embedded, tmp_path / "out").path.read_bytes() == FILE.data

    return embedded


def _metadata(path: Path) -> bytes:
    import pikepdf

    with pikepdf.open(path) as pdf:
        return bytes(pdf.Root.Metadata.read_bytes())


@pytest.mark.parametrize("xmp", [XMP, b"not xml at all"], ids=["xmp", "not-xml"])
def test_the_cover_s_metadata_stays_byte_for_byte(tmp_path: Path, xmp: bytes) -> None:
    """pikepdf's default save would rewrite it, and replace what it can't parse with an
    empty packet that names pikepdf."""
    embedded = _hidden(_cover(tmp_path / "cover.pdf", xmp), tmp_path)

    assert _metadata(embedded) == xmp
    assert b"pikepdf" not in embedded.read_bytes().lower()


def test_a_metadata_dictionary_does_not_stop_hiding(tmp_path: Path) -> None:
    """pikepdf's default save raises its own error on it."""
    _hidden(_cover(tmp_path / "cover.pdf", None), tmp_path)
