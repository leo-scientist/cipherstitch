"""The values a parameter allows: its type, its options and its range. Settings check every
value with them, and the extension rules check an extension's defaults."""

import math
from decimal import ROUND_HALF_UP, Decimal

from cipherstitch.errors import InvalidParameterError
from cipherstitch.params.model import Param, ParamType, Value

# A double holds about 15 significant digits, so a finer step can't move a value reliably.
MAX_DECIMALS = 15


def checked(param: Param, name: str, value: object) -> Value:
    """``value`` for ``param``, named ``name`` in messages, in its canonical type, if it is
    allowed. A number is moved to the nearest step from the minimum, so that the editor, the
    Parameters row and the engine all see the value a profile gives."""
    title = f"{param.label} ({name})"

    if param.type is ParamType.BOOL:
        if not isinstance(value, bool):
            raise InvalidParameterError(f"{title} must be on or off")

        return value

    if isinstance(value, bool):
        raise InvalidParameterError(f"{title} can't be on or off")

    if param.type is ParamType.ENUM:
        return _option_value(param, title, value)

    if param.type is ParamType.INT and not isinstance(value, int):
        raise InvalidParameterError(f"{title} must be a whole number")

    if not isinstance(value, int | float):
        raise InvalidParameterError(f"{title} must be a number")

    number = value if param.type is ParamType.INT else _finite(title, value)

    if param.minimum is not None and number < param.minimum:
        raise InvalidParameterError(f"{title} must be at least {_number(param.minimum)}")

    if param.maximum is not None and number > param.maximum:
        raise InvalidParameterError(f"{title} must be at most {_number(param.maximum)}")

    return number if param.step is None else _on_step(param, number)


def decimals(step: float) -> int:
    """The decimals a step needs, as the editor shows its values: 1 for 0.1, 2 for 0.05,
    5 for 1e-05 and 0 for 2.0. From the step's shortest repr, which is what ``_on_step``
    snaps with (``f"{step:g}"`` kept 6 significant digits), normalized so that a trailing
    zero or an exponent adds none."""
    exponent = Decimal(str(step)).normalize().as_tuple().exponent

    return max(0, -exponent) if isinstance(exponent, int) else 0


def _on_step(param: Param, number: int | float) -> int | float:
    """In decimal arithmetic: 0.1 steps in floats would give 2.3000000000000003."""
    step = Decimal(str(param.step))
    start = Decimal(str(param.minimum or 0))
    count = ((Decimal(str(number)) - start) / step).to_integral_value(ROUND_HALF_UP)
    snapped = start + count * step

    if param.maximum is not None and snapped > Decimal(str(param.maximum)):
        snapped -= step  # a maximum off the steps: the last step below it

    return int(snapped) if param.type is ParamType.INT else float(snapped)


def _finite(title: str, value: int | float) -> float:
    try:
        number = float(value)
    except OverflowError:
        raise InvalidParameterError(f"{title} is far too large") from None

    if not math.isfinite(number):
        raise InvalidParameterError(f"{title} must be a finite number")

    return number


def _option_value(param: Param, title: str, value: object) -> Value:
    option = next((option for option in param.options if option.value == value), None)

    if option is None:
        allowed = ", ".join(str(option.value) for option in param.options)
        raise InvalidParameterError(f"{title} must be one of: {allowed}")

    return option.value


def _number(value: float) -> str:
    return str(int(value)) if value == int(value) else str(value)
