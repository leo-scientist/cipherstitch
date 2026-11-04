from dataclasses import replace

from cipherstitch.extensions import Extension, Scope
from tests.extension_fixtures.parts import GROUP, METHOD

SPEC = replace(METHOD, id="group-two", wire_id=191)
EXTENSION = Extension(
    methods=(SPEC,), params=(replace(GROUP, id="shared", scope=Scope(frozenset({SPEC.id}))),)
)
