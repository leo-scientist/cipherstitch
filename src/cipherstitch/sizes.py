"""Sizes as the app and the advice show them."""

_STEP = 1024
_UNITS = ("KiB", "MiB", "GiB")


def format_size(size: int) -> str:
    if size < _STEP:
        return f"{size} B"

    # The unit follows the rounded number: 1 048 575 bytes is 1.0 MiB, not 1024.0 KiB.
    for power, unit in enumerate(_UNITS[:-1], start=1):
        shown = f"{size / _STEP**power:.1f}"

        if float(shown) < _STEP:
            return f"{shown} {unit}"

    return f"{size / _STEP ** len(_UNITS):.1f} {_UNITS[-1]}"
