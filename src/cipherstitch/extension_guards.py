"""Guards around an extension's code (docs/architecture.md#extensions): every call into it
turns a foreign exception into :class:`ExtensionError`, and cheap checks catch results that
break the contract before the container trusts them.

``CipherStitchError`` (``CapacityError``, ``UnsupportedFormatError``…), ``MemoryError`` and
every ``BaseException`` pass through. Nothing here stops code that hangs or crashes the
interpreter.
"""

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import numpy as np
import numpy.typing as npt

from cipherstitch.catalog import CipherKey
from cipherstitch.ciphers.base import Sealer
from cipherstitch.container import Channel
from cipherstitch.errors import CipherStitchError, DecryptionError, ExtensionError
from cipherstitch.media.jpeg import Coefficients
from cipherstitch.methods import Embedded, Opener, Tuning, Value, lsb, uerd
from cipherstitch.params.model import Check

_PASSES: tuple[type[Exception], ...] = (CipherStitchError, MemoryError)
_COST_TYPES = (np.float32, np.float64)


@contextmanager
def _guard(extension: str, passes: tuple[type[Exception], ...] = _PASSES) -> Iterator[None]:
    try:
        yield
    except passes:
        raise
    except Exception as exc:
        raise ExtensionError(extension, described(exc)) from exc


def described(exc: BaseException) -> str:
    """E.g. ``TypeError: unsupported operand``: the type and the message folded onto one line,
    without the notes ``format_exception_only`` would add (its last line can be a note)."""
    kind = type(exc)
    name = kind.__qualname__

    if kind.__module__ not in ("builtins", "__main__"):
        name = f"{kind.__module__}.{name}"

    try:
        message = str(exc)
    except Exception:  # an extension's own exception class may break even this
        message = "<exception str() failed>"

    folded = " ".join(line.strip() for line in message.splitlines() if line.strip())

    return f"{name}: {folded}" if folded else name


def _size(extension: str, call: str, value: object) -> int:
    """``value``, the result of ``call``, if it is a size: an ``int`` of 0 or more."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ExtensionError(extension, f"{call} returned {value!r}, not a size")

    return value


def guarded_opener(extension: str, opener: Opener) -> Opener:
    def open_channel(path: Path, tuning: Tuning) -> Channel[Embedded]:
        with _guard(extension):
            channel = opener(path, tuning)

        return GuardedChannel(extension, channel)

    return open_channel


def guarded_check(extension: str, check: Check) -> Check:
    """A parameter group's check. Settings refuse the values with its problem, so an
    exception, or a result that is neither text nor ``None``, becomes the problem: the
    editor and the profiles show it instead of failing."""

    def checked(values: Mapping[str, Value]) -> str | None:
        try:
            problem = check(values)
        except MemoryError:
            raise
        except Exception as exc:
            return f"the extension {extension} failed: {described(exc)}"

        if problem is not None and not isinstance(problem, str):
            kind = type(problem).__name__
            return f"the extension {extension} failed: its check returned a {kind}"

        return problem

    return checked


class GuardedChannel:
    """An extension's channel: the container's checks rely on these results."""

    def __init__(self, extension: str, channel: Channel[Embedded]) -> None:
        self._extension = extension
        self._channel = channel

    def capacity(self, prefix_size: int) -> int:
        with _guard(self._extension):
            capacity = self._channel.capacity(prefix_size)

        return _size(self._extension, "capacity", capacity)

    def stored_limit(self, prefix_size: int) -> int:
        with _guard(self._extension):
            limit = self._channel.stored_limit(prefix_size)

        limit = _size(self._extension, "stored_limit", limit)
        capacity = self.capacity(prefix_size)

        if limit < capacity:
            problem = f"stored_limit returned {limit}, less than the capacity {capacity}"
            raise ExtensionError(self._extension, problem)

        return limit

    def read(self, prefix_size: int, size: int, seed: bytes | None) -> bytes:
        with _guard(self._extension):
            data = self._channel.read(prefix_size, size, seed)

        if not isinstance(data, bytes) or len(data) > size:
            problem = f"read returned {value_kind(data)} for at most {size} bytes"
            raise ExtensionError(self._extension, problem)

        return data

    def write(self, prefix: bytes, body: bytes, seed: bytes | None) -> Embedded:
        with _guard(self._extension):
            embedded = self._channel.write(prefix, body, seed)

        if not callable(getattr(embedded, "save", None)):
            problem = f"write returned {type(embedded).__name__}, which has no save method"
            raise ExtensionError(self._extension, problem)

        return GuardedEmbedded(self._extension, embedded)


class GuardedEmbedded:
    def __init__(self, extension: str, embedded: Embedded) -> None:
        self._extension = extension
        self._embedded = embedded

    def save(self, path: Path) -> None:
        with _guard(self._extension):
            self._embedded.save(path)

        if not path.is_file():
            raise ExtensionError(self._extension, "save wrote no file")


class GuardedCipher:
    """An extension's cipher, known by its spec's id, with the key size the loader checked.
    ``unseal`` may raise only :class:`DecryptionError`: anything else is the extension's
    failure, not a wrong key."""

    def __init__(self, extension: str, cipher_id: CipherKey, key_size: int, cipher: Sealer) -> None:
        self._extension = extension
        self._id = cipher_id
        self._key_size = key_size
        self._cipher = cipher

    @property
    def id(self) -> CipherKey:
        return self._id

    @property
    def key_size(self) -> int:
        return self._key_size

    def sealed_size(self, size: int) -> int:
        with _guard(self._extension):
            sealed = self._cipher.sealed_size(size)

        sealed = _size(self._extension, "sealed_size", sealed)

        if sealed < size:
            problem = f"sealed_size({size}) returned {sealed}, less than the input"
            raise ExtensionError(self._extension, problem)

        return sealed

    def seal(self, key: bytes, counter: int, data: bytes) -> bytes:
        with _guard(self._extension):
            sealed = self._cipher.seal(key, counter, data)

        expected = self.sealed_size(len(data))

        if not isinstance(sealed, bytes) or len(sealed) != expected:
            problem = f"seal returned {value_kind(sealed)}, not the {expected} bytes of sealed_size"
            raise ExtensionError(self._extension, problem)

        return sealed

    def unseal(self, key: bytes, counter: int, data: bytes) -> bytes:
        with _guard(self._extension, passes=(DecryptionError, MemoryError)):
            opened = self._cipher.unseal(key, counter, data)

        if not isinstance(opened, bytes):
            raise ExtensionError(self._extension, f"unseal returned {value_kind(opened)}")

        return opened


class GuardedCosts:
    """An extension's cost function (docs/extensions.md#cost-functions). It gets read-only
    views of the channel's values, and its result must be an array of their shape, float32 or
    float64, with no NaN and nothing negative. Values above the channel's wet cost, infinity
    included, become that cost, as the built-in costs never exceed it. The channel gets a
    fresh float32 array, which it may write over."""

    def __init__(self, extension: str, costs: Callable[..., object]) -> None:
        self._extension = extension
        self._costs = costs

    def spatial(self, colors: npt.NDArray[np.uint8], rate: float) -> npt.NDArray[np.float32]:
        """Image LSB's costs of ``colors`` (rows × columns × 3) for a payload of ``rate``."""
        with _guard(self._extension):
            result = self._costs(_read_only(colors), rate)

        return self._checked(result, colors.shape, lsb.WET_COST)

    def jpeg(self, cover: Coefficients) -> npt.NDArray[np.float32]:
        """DCT's costs of ``cover.ac``."""
        components = tuple(replace(c, steps=_read_only(c.steps)) for c in cover.components)
        viewed = Coefficients(_read_only(cover.ac), components, _read_only(cover.dc))

        with _guard(self._extension):
            result = self._costs(viewed)

        return self._checked(result, cover.ac.shape, uerd.WET)

    def _checked(
        self, result: object, shape: tuple[int, ...], wet: float
    ) -> npt.NDArray[np.float32]:
        if not isinstance(result, np.ndarray):
            problem = f"its costs must be an array of shape {shape}, not {value_kind(result)}"
        elif result.shape != shape:
            problem = f"its costs must be an array of shape {shape}, not {result.shape}"
        elif result.dtype.type not in _COST_TYPES:
            problem = f"its costs must be float32 or float64, not {result.dtype}"
        elif np.isnan(result).any():
            problem = "its costs hold NaN"
        elif (result < 0).any():
            problem = "its costs hold a negative value"
        else:
            costs = np.empty(shape, dtype=np.float32)
            np.minimum(result, wet, out=costs)  # a float64 result is cast once clipped

            return costs

        raise ExtensionError(self._extension, problem)


def _read_only[T: np.generic](values: npt.NDArray[T]) -> npt.NDArray[T]:
    view = values.view()
    view.flags.writeable = False

    return view


def value_kind(value: object) -> str:
    """E.g. ``20 bytes`` or ``str``."""
    return f"{len(value)} bytes" if isinstance(value, bytes) else type(value).__name__
