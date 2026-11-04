"""A text method whose opener raises a foreign exception, and which gives no risk line."""

from pathlib import Path

from cipherstitch.extensions import CarrierKind, Channel, Embedded, Extension, MethodSpec, Tuning


def broken_opener(path: Path, tuning: Tuning) -> Channel[Embedded]:
    raise ValueError(f"can't open {path.name}")


EXTENSION = Extension(
    methods=(
        MethodSpec(
            "faulty",
            210,
            "Faulty",
            CarrierKind.TEXT,
            (".txt",),
            broken_opener,
            outputs=(".txt", ".md"),
        ),
    ),
)
