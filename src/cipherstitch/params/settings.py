"""Resolved Research-mode settings: the defaults with the user's changes, always valid."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from cipherstitch.errors import InvalidParameterError
from cipherstitch.params import registry, values
from cipherstitch.params.model import Group, Kind, Value, key


@dataclass(frozen=True, slots=True)
class Settings:
    """Only the values that differ from the defaults (Standard's), sorted by key. Every
    instance is valid: unknown keys, wrong types, values out of range or outside a
    parameter's options, and groups whose values cannot work together raise
    ``InvalidParameterError``.
    ``Settings()`` is Standard."""

    changes: tuple[tuple[str, Value], ...] = ()

    def __post_init__(self) -> None:
        names = [name for name, _value in self.changes]

        if names != sorted(set(names)):
            raise InvalidParameterError("Settings must name each parameter once, sorted")

        for name, value in self.changes:
            _group, param = registry.lookup(name)
            canonical = checked(name, value)

            if canonical != value or type(canonical) is not type(value):
                raise InvalidParameterError(f"{name} must be stored as {canonical!r}")

            if canonical == param.default:
                raise InvalidParameterError(f"{name} is at its default and must be left out")

        for group in dict.fromkeys(registry.lookup(name)[0] for name in names):
            _check_group(group, self)

    @classmethod
    def of(cls, values: Mapping[str, object]) -> "Settings":
        """From any values, defaults included: they are dropped."""
        changes = {}

        for name, value in values.items():
            _group, param = registry.lookup(name)
            value = checked(name, value)

            if value != param.default:
                changes[name] = value

        return cls(tuple(sorted(changes.items())))

    def __getitem__(self, name: str) -> Value:
        _group, param = registry.lookup(name)

        return self.as_dict().get(name, param.default)

    def as_dict(self) -> Mapping[str, Value]:
        return MappingProxyType(dict(self.changes))

    def replaced(self, name: str, value: object) -> "Settings":
        return Settings.of({**self.as_dict(), name: value})

    def reset(self, names: Iterable[str]) -> "Settings":
        dropped = set(names)

        return Settings(tuple(item for item in self.changes if item[0] not in dropped))

    def format_changes(self) -> tuple[str, ...]:
        """The changed values Reveal needs too: nothing in the file records them."""
        return tuple(
            name for name, _value in self.changes if registry.lookup(name)[1].kind is Kind.FORMAT
        )


STANDARD = Settings()


def checked(name: str, value: object) -> Value:
    """``value`` for the parameter ``name`` in its canonical type, if it is allowed: an
    extension's cost function only if it is loaded."""
    return values.checked(registry.param_for(name, value), name, value)


def _check_group(group: Group, settings: Settings) -> None:
    if group.check is None:
        return

    by_id = {param.id: settings[key(group, param)] for param in group.params}
    problem = group.check(by_id)

    if problem is not None:
        raise InvalidParameterError(f"{group.label}: {problem}")
