"""Profiles: ``.csprofile`` files with the Research settings a payload was hidden with, which
the hidden file never carries. See docs/user-guide.md#profiles."""

import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cipherstitch import catalog
from cipherstitch.catalog import CipherKey, MethodKey
from cipherstitch.errors import InvalidParameterError, InvalidProfileError, NotAvailableError
from cipherstitch.hidden_text import has_hidden
from cipherstitch.params import registry
from cipherstitch.params.groups import GROUP_IDS
from cipherstitch.params.settings import Settings

SUFFIX = ".csprofile"
FORMAT = "cipherstitch-profile"
VERSION = 1
# The built-in defaults' version. A release that changes a default keeps the old table under
# its number, so a profile, which stores only changes, still rebuilds the same settings.
DEFAULTS = 1
MAX_SIZE = 64 * 1024  # a profile takes a few hundred bytes
# The name titles the Parameters row and names the export, the note is a few sentences.
MAX_NAME_LENGTH = 80
MAX_NOTE_LENGTH = 2000
# What a parameter key looks like, known or not: anything else, shown in a warning, could
# carry terminal escapes or markup.
_KEY_PART = r"[a-z][a-z0-9]{0,31}"
_KEY = re.compile(rf"{_KEY_PART}\.{_KEY_PART}")
_SHOWN_KEY = re.compile(rf"{_KEY_PART}(\.{_KEY_PART})?")  # a key or a field, as a message shows it


@dataclass(frozen=True, slots=True)
class Profile:
    """``settings`` holds the parameters this version knows, ``unknown`` names the others.
    ``missing_costs`` holds the cost functions no loaded extension has, by key with their
    values, left out of ``settings``: only hiding needs them. ``method`` and ``cipher`` are
    what revealing starts from. No secrets, ever."""

    settings: Settings
    name: str = ""
    note: str = ""
    method: MethodKey | None = None
    cipher: CipherKey | None = None
    unknown: tuple[str, ...] = ()
    missing_costs: tuple[tuple[str, str], ...] = ()


def read(path: Path) -> Profile:
    with path.open("rb") as file:
        data = file.read(MAX_SIZE + 1)

    if len(data) > MAX_SIZE:
        raise InvalidProfileError(f"{path.name} is too large to be a profile")

    return parse(data, path.name)


def parse(data: bytes, source: str) -> Profile:
    """The profile in ``data``, read from ``source`` (named in errors)."""
    try:
        # Notepad saves a BOM. A repeated key is refused, not taken as its last value.
        document = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_object(source))
    except (ValueError, RecursionError):  # not UTF-8, not JSON, or nested past the stack
        raise _not_a_profile(source) from None

    if not isinstance(document, dict) or document.get("format") != FORMAT:
        raise _not_a_profile(source)

    _check_versions(document, source)
    params = document.get("params", {})

    if not isinstance(params, dict) or not all(_KEY.fullmatch(name) for name in params):
        raise _not_a_profile(source)

    # A key in a group no built-in has loads the extensions, which may know it.
    known = {name: value for name, value in params.items() if registry.knows(name)}
    # So may a cost function that isn't a built-in's (docs/extensions.md#cost-functions).
    missing = {
        name: value
        for name, value in known.items()
        if isinstance(value, str) and registry.is_unknown_cost(name, value)
    }

    try:
        settings = Settings.of({k: v for k, v in known.items() if k not in missing})
    except InvalidParameterError as exc:
        raise InvalidProfileError(f"{exc} in {source}") from None

    return Profile(
        settings,
        _text(document, "name", MAX_NAME_LENGTH, source),
        _text(document, "note", MAX_NOTE_LENGTH, source),
        _choice(document, "method", catalog.method, source),
        _choice(document, "cipher", catalog.cipher, source),
        tuple(sorted(name for name in params if name not in known)),
        tuple(sorted(missing.items())),
    )


def dumps(profile: Profile, *, app: str, created: datetime | None = None) -> str:
    """The profile as written to a file: only the changed values."""
    moment = created or datetime.now(UTC)
    document: dict[str, object] = {
        "format": FORMAT,
        "version": VERSION,
        "name": profile.name,
        **({"note": profile.note} if profile.note else {}),
        "created": moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "app": app,
        "defaults": DEFAULTS,
        **({"method": str(profile.method)} if profile.method else {}),
        **({"cipher": str(profile.cipher)} if profile.cipher else {}),
        "params": dict(profile.settings.as_dict()),
    }

    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def write(path: Path, profile: Profile, *, app: str) -> None:
    path.write_text(dumps(profile, app=app), encoding="utf-8")


def refuse_unknown(profile: Profile, source: str) -> None:
    """Revealing without some of the values the hiding used would find nothing."""
    if not profile.unknown:
        return

    advice = (
        "Install the extension that adds them or update CipherStitch"
        if _may_come_from_an_extension(profile.unknown)
        else "Update CipherStitch"
    )

    raise InvalidProfileError(
        f"{source} sets parameters this version of CipherStitch doesn't know "
        f"({', '.join(profile.unknown)}). {advice} to reveal with it"
    )


def refuse_missing_costs(profile: Profile, source: str) -> None:
    """Hiding without a cost function the profile names would choose other changes than it
    asks for. Revealing reads the same values whatever the costs were, so it goes on."""
    if not profile.missing_costs:
        return

    name, value = profile.missing_costs[0]

    raise InvalidProfileError(
        f"{source} sets {name} to a cost function this CipherStitch doesn't know, "
        f"{value!r} (an extension that isn't installed?)"
    )


def unknown_warning(profile: Profile) -> str | None:
    """Hiding leaves the unknown parameters out: what to tell the user."""
    if not profile.unknown:
        return None

    warning = (
        "Left out the parameters this version of CipherStitch doesn't know "
        f"({', '.join(profile.unknown)})"
    )

    if _may_come_from_an_extension(profile.unknown):
        return f"{warning}. An extension that isn't installed may add them"

    return warning


def _may_come_from_an_extension(keys: tuple[str, ...]) -> bool:
    """Whether a key is in a group no built-in has. Only an extension or a newer version
    adds such a group, while a new key in a built-in group comes from a newer version."""
    return any(name.partition(".")[0] not in GROUP_IDS for name in keys)


def _check_versions(document: dict[str, object], source: str) -> None:
    for field, newest in (("version", VERSION), ("defaults", DEFAULTS)):
        number = document.get(field)

        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            raise _not_a_profile(source)

        if number > newest:
            raise InvalidProfileError(
                f"{source} was made by a newer CipherStitch. Update CipherStitch to use it"
            )


def _object(source: str) -> Callable[[list[tuple[str, object]]], dict[str, object]]:
    """The hook that builds each JSON object of a profile read from ``source``."""

    def build(pairs: list[tuple[str, object]]) -> dict[str, object]:
        document = dict(pairs)

        if len(document) < len(pairs):
            counts = Counter(key for key, _value in pairs)
            repeated = next(key for key, count in counts.items() if count > 1)
            shown = f"the key {repeated}" if _SHOWN_KEY.fullmatch(repeated) else "a key"

            raise InvalidProfileError(f"{source} repeats {shown}")

        return document

    return build


def _text(document: dict[str, object], field: str, limit: int, source: str) -> str:
    """A text the app shows as it is, so no control or format character."""
    text = document.get(field, "")

    if not isinstance(text, str):
        raise _not_a_profile(source)

    if len(text) > limit:
        raise InvalidProfileError(f"{source} has a {field} longer than {limit} characters")

    if has_hidden(text):
        raise InvalidProfileError(f"{source} has a {field} with a control or format character")

    return text


def _choice(
    document: dict[str, object],
    field: str,
    lookup: Callable[[str], catalog.Method | catalog.Cipher],
    source: str,
) -> str | None:
    value = document.get(field)

    if value is None:
        return None

    if not isinstance(value, str):
        raise _not_a_profile(source)

    try:
        return lookup(value).id  # an id that isn't a built-in's loads the extensions
    except NotAvailableError:
        raise InvalidProfileError(
            f"{source} names a {field} this CipherStitch doesn't know, {value!r} "
            "(an extension that isn't installed?)"
        ) from None


def _not_a_profile(source: str) -> InvalidProfileError:
    return InvalidProfileError(f"{source} is not a CipherStitch profile")
