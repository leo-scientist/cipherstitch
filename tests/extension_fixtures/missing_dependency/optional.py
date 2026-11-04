from cipherstitch.extensions import CarrierKind, CipherSpec, Extension, MethodSpec, Requirement
from tests.extension_fixtures.parts import never_opened

MISSING = "cipherstitch_missing_dependency"


class NeedsItsDependency:
    """No self-test runs without the dependency: this would fail it."""

    key_size = 32

    def sealed_size(self, size: int) -> int:
        return size

    def seal(self, key: bytes, counter: int, data: bytes) -> bytes:
        return data

    def unseal(self, key: bytes, counter: int, data: bytes) -> bytes:
        return data


EXTENSION = Extension(
    methods=(
        MethodSpec(
            "optional-method",
            190,
            "Optional method",
            CarrierKind.TEXT,
            (".txt",),
            never_opened,
            requires=Requirement(MISSING),
        ),
    ),
    ciphers=(
        CipherSpec(
            "optional-cipher",
            190,
            "Optional cipher",
            NeedsItsDependency(),
            requires=Requirement(MISSING, package="missing-dependency"),
        ),
    ),
)
