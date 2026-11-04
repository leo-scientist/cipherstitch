"""The domain is a plain string."""

from dataclasses import replace

from cipherstitch.extensions import Extension
from tests.extension_fixtures.parts import COST

EXTENSION = Extension(costs=(replace(COST, domain="spatial"),))  # type: ignore[arg-type]
