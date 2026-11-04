import pytest

from cipherstitch.sizes import format_size


@pytest.mark.parametrize(
    ("size", "text"),
    [
        (0, "0 B"),
        (1023, "1023 B"),
        (1024, "1.0 KiB"),
        (1536, "1.5 KiB"),
        (1_048_524, "1023.9 KiB"),
        (1_048_575, "1.0 MiB"),  # 1023.999 KiB rounds up to the next unit
        (3 << 20, "3.0 MiB"),
        ((1 << 30) - 1, "1.0 GiB"),
        (5 << 30, "5.0 GiB"),
    ],
)
def test_format_size(size: int, text: str) -> None:
    assert format_size(size) == text
