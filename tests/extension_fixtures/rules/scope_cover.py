"""A parameter's scope names a kind of cover, which is DCT's."""

from dataclasses import replace

from cipherstitch.extensions import Extension, Scope
from cipherstitch.params.model import Cover
from tests.extension_fixtures.parts import GROUP, LEVEL, METHOD

FOR_JPEGS = replace(LEVEL, scopes=(Scope(cover=Cover.JPEG),))
EXTENSION = Extension(methods=(METHOD,), params=(replace(GROUP, params=(FOR_JPEGS,)),))
