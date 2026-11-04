"""XML read from a carrier, which may be crafted (docs/security.md#untrusted-carriers)."""

from xml.etree import ElementTree
from xml.parsers import expat


class _DoctypeError(Exception):
    pass


class _RootReachedError(Exception):
    pass


def parsed(xml: bytes) -> ElementTree.Element:
    """The tree of ``xml``. Raises :class:`ElementTree.ParseError` if it is not well-formed,
    names an encoding expat cannot read, or has a document type declaration.

    A DTD can declare attribute defaults that expat copies into every element they apply to,
    which grows memory far beyond the input. Expat refuses entity bombs, but not that. OPC
    parts must not have a DTD (ECMA-376 Part 2), and XMP has none.
    """
    _check_prolog(xml)

    # The stdlib parser never fetches external entities.
    return ElementTree.fromstring(xml)  # noqa: S314


def _check_prolog(xml: bytes) -> None:
    """Refuse a document type declaration. A first pass stops at the root's start tag, which
    a declaration must come before, so it costs no more than the prolog. Expat reads the
    encoding as the tree's parser does."""
    scanner = expat.ParserCreate()
    scanner.StartDoctypeDeclHandler = _refuse_doctype
    scanner.StartElementHandler = _stop_at_root

    try:
        scanner.Parse(xml, True)
    except _RootReachedError:
        return
    except _DoctypeError as exc:
        raise ElementTree.ParseError("a document type declaration is refused") from exc
    # An encoding expat cannot read (multi-byte, unknown, or not a text codec) raises one of
    # these, UnicodeError included.
    except (expat.ExpatError, LookupError, ValueError) as exc:
        raise ElementTree.ParseError(str(exc)) from exc


def _refuse_doctype(*_declaration: object) -> None:
    raise _DoctypeError


def _stop_at_root(*_element: object) -> None:
    raise _RootReachedError
