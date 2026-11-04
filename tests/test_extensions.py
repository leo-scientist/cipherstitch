"""The extension loader: discovery, the load-time rules, clashes and lookups. The fixtures are
in tests/extension_fixtures/, one scenario per package."""

import importlib
import math
from dataclasses import replace
from pathlib import Path, PurePath
from typing import Any

import pytest

from cipherstitch import (
    availability,
    catalog,
    ciphers,
    container,
    extension_checks,
    extension_loader,
    methods,
)
from cipherstitch.catalog import CipherId, MethodId, Tier
from cipherstitch.container import Candidate, UnitOrder
from cipherstitch.errors import NotAvailableError, UnsupportedFormatError
from cipherstitch.extension_guards import GuardedCipher
from cipherstitch.extensions import Extension, Option, ParamType, Scope
from tests.extension_fixtures.parts import CIPHER, METHOD, ChaChaCopy
from tests.extension_fixtures.parts import GROUP as PARAMS_GROUP
from tests.extension_fixtures.parts import LEVEL as PARAM

from .conftest import EXTENSION_FIXTURES, skip_unless_installed, use_extensions

SHAPE = "the id must be lower-case letters and digits, in words joined by hyphens, at most 32"
KEY = "the id must be lower-case letters and digits, starting with a letter, at most 32"
WIRE = "the wire id must be an int in 128–254"
SUFFIXES = "the inputs must be a non-empty tuple of suffixes such as .txt"
GROUP = "parameter group 'rule'"
WHOLE = (
    "a whole number's minimum, maximum and step must be whole numbers from -2147483648 to "
    "2147483647, as the editor holds them"
)
LEVEL = "parameter 'rule.level'"
ENTRY_RULES = {
    "carrier_type": "method 'rule-method': the carrier must be a CarrierKind",
    "cipher_id_builtin": "cipher 'fernet': the id fernet is taken by CipherStitch",
    "cipher_id_repeated": "the cipher id rule-cipher is declared 2 times",
    "extension_type": "EXTENSION.methods must be a tuple of MethodSpec",
    "extension_wrong_type": "EXTENSION must be an Extension, not a dict",
    "fixed_nonce": (
        "cipher 'rule-cipher': seal gives the same output for counters 0 and 1, so the header "
        "and the record would share a keystream"
    ),
    "hidden_character": "method 'rule-method': the summary has a control or format character",
    "id_builtin": "method 'zero-width': the id zero-width is taken by CipherStitch",
    "id_length": f"method '{'a' * 33}': {SHAPE} characters",
    "id_pattern": f"method 'Rule_Method': {SHAPE} characters",
    "id_repeated": "the method id rule-method is declared 2 times",
    "id_reserved": "method 'none': the id none is taken by CipherStitch",
    "identity_mac": "cipher 'rule-cipher': seal leaves the plaintext readable in its output",
    "key_size": "cipher 'rule-cipher': key_size must be an int in 16–64, not 8",
    "name_blank": "method 'rule-method': the name must be 1–40 characters",
    "name_length": "method 'rule-method': the name must be 1–40 characters",
    "no_extension": "it has no EXTENSION",
    "open_not_callable": "method 'rule-method': open must be callable",
    "order_type": "method 'rule-method': the order must be an Order",
    "outputs_empty": (
        "method 'rule-method': the outputs must be None or a non-empty tuple of suffixes "
        "such as .txt"
    ),
    "requires_module": "method 'rule-method': requires must name a top-level module, such as scipy",
    "requires_module_hidden": (
        "method 'rule-method': requires must name a top-level module, such as scipy"
    ),
    "requires_type": "method 'rule-method': requires must be a Requirement",
    "sealed_size": "cipher 'rule-cipher': seal of 0 bytes returned 16 bytes, not 15 bytes",
    "stream_cipher": (
        "cipher 'rule-cipher': unseal of a flipped bit returned data instead of raising "
        "DecryptionError"
    ),
    "suffix_kind": "method 'rule-method': .wav belongs to audio carriers, not text ones",
    "suffix_pattern": f"method 'rule-method': {SUFFIXES}",
    "suffix_two_kinds": "the suffix .two joins more than one carrier kind: image, text",
    "summary_blank": "method 'rule-method': the summary is only blanks",
    "text_type": "cipher 'rule-cipher': the pros must be a tuple of str",
    "trellis_type": "method 'rule-method': codes_with_trellis must be a bool",
    "value_error": (
        "cipher 'rule-cipher': unseal of a flipped bit raised ValueError: bad tag instead of "
        "DecryptionError"
    ),
    "wire_id_range": f"method 'rule-method': {WIRE}",
    "wire_id_repeated": "the cipher wire id 150 is declared 2 times",
    "wire_id_type": f"method 'rule-method': {WIRE}",
}
PARAM_RULES = {
    "check_defaults": f"{GROUP}: the check refuses the defaults: level 1 never works",
    "check_type": f"{GROUP}: check must be callable or None",
    "default_range": f"{LEVEL}: the default is refused: Level (rule.level) must be at most 7",
    "default_type": f"{LEVEL}: the default must be stored as 1.0",
    "float_step": f"{LEVEL}: the step needs more than 15 decimals",
    "group_id_builtin": "parameter group 'stc': the id stc is taken by CipherStitch",
    "group_id_pattern": f"parameter group 'rule-group': {KEY} characters",
    "group_id_repeated": "the parameter group id rule is declared 2 times",
    "group_label_length": f"{GROUP}: the label must be 1–40 characters",
    "icon_unknown": (
        f"{GROUP}: the icon must name one of the app's, such as flask (gui/resources/icons)"
    ),
    "int_range": f"{LEVEL}: {WHOLE}",
    "int_step": f"{LEVEL}: {WHOLE}",
    "option_bool": f"{LEVEL}: an option's value can't be a bool: use ParamType.BOOL for a switch",
    "options_empty": f"{LEVEL}: a choice needs a non-empty tuple of Option",
    "param_hidden_character": f"{LEVEL}: the note has a control or format character",
    "param_id_pattern": f"parameter 'rule.Level': {KEY} characters",
    "param_repeated": "the parameter rule.level is declared 2 times",
    "params_type": "EXTENSION.params must be a tuple of Group",
    "range_inverted": (
        f"{LEVEL}: the minimum must not pass the maximum, and the step must be above 0"
    ),
    "scope_cover": f"{LEVEL}: the scope may name only methods and encryption",
    "scope_no_method": f"{GROUP}: the scope must name one or more of the extension's methods",
    "scope_other_method": (
        f"{GROUP}: the scope names lsb, which isn't one of the extension's methods"
    ),
}
COST = "cost function 'rule-cost'"
COST_RULES = {
    "cost_domain": f"{COST}: the domain must be a Domain",
    "cost_id_builtin": "cost function 'hill': the id hill is taken by CipherStitch",
    "cost_id_pattern": f"cost function 'Rule_Cost': {SHAPE} characters",
    "cost_id_repeated": "the spatial cost function id rule-cost is declared 2 times",
    "cost_label_hidden": f"{COST}: the label has a control or format character",
    "cost_label_length": f"{COST}: the label must be 1–40 characters",
    "cost_not_callable": f"{COST}: costs must be callable",
    "costs_type": "EXTENSION.costs must be a tuple of CostSpec",
}
RULES = ENTRY_RULES | PARAM_RULES | COST_RULES


def _rules() -> extension_loader.Loaded:
    return extension_loader.load_package(f"{EXTENSION_FIXTURES}.rules")


def _failure(loaded: extension_loader.Loaded, name: str) -> extension_loader.Failure:
    return next(failure for failure in loaded.failures if failure.extension == name)


def _refuse_loading() -> extension_loader.Loaded:
    raise AssertionError("The extensions were loaded")


def test_modules_and_packages_load_in_name_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """discovery/ also holds _private.py, which raises if anything imports it."""
    loaded = use_extensions(monkeypatch, "discovery")

    assert [(e.name, e.summary) for e in loaded.extensions] == [
        ("alpha", "A package"),
        ("beta", "A module"),
    ]
    assert [method.entry.id for method in loaded.methods] == ["alpha", "beta"]
    assert loaded.failures == ()


def test_the_extensions_load_once(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded = use_extensions(monkeypatch, "discovery")

    assert extension_loader.load() is loaded

    extension_loader.reset()

    reloaded = extension_loader.load()

    assert reloaded is not loaded
    assert [e.name for e in reloaded.extensions] == [e.name for e in loaded.extensions]


def test_an_extension_that_looks_itself_up_while_loading_sees_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loaded = use_extensions(monkeypatch, "reentrant")
    module = importlib.import_module(f"{EXTENSION_FIXTURES}.reentrant.looks_itself_up")

    assert extension_loader.Loaded() == module.SEEN_WHILE_LOADING
    assert module.FOUND_WHILE_LOADING is None
    assert loaded.failures == ()
    assert extension_loader.load() is loaded
    assert extension_loader.find_method("looks-itself-up") is loaded.methods[0]


def test_an_interrupted_load_keeps_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ctrl+C while importing an extension stops the load."""
    monkeypatch.setattr(extension_loader, "PACKAGE", f"{EXTENSION_FIXTURES}.interrupted")

    for _attempt in range(2):
        with pytest.raises(KeyboardInterrupt):
            extension_loader.load()


def test_an_extension_that_exits_on_import_fails_alone(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    loaded = use_extensions(monkeypatch, "exits")
    failure = _failure(loaded, "quits")

    assert failure.problems == ("SystemExit: 2",)
    assert failure.traceback is not None
    assert "quits.py" in failure.traceback
    assert [extension.name for extension in loaded.extensions] == ["stays"]
    assert extension_loader.load() is loaded
    assert "unrecognized arguments: --bogus" in capsys.readouterr().err


def test_git_ignores_the_folder_but_its_contract() -> None:
    gitignore = (Path(__file__).parents[1] / ".gitignore").read_text().splitlines()

    assert "src/cipherstitch/extensions/*" in gitignore
    assert "!src/cipherstitch/extensions/__init__.py" in gitignore


@pytest.mark.parametrize("name", sorted(RULES))
def test_a_broken_rule_fails_the_whole_module(name: str) -> None:
    assert _failure(_rules(), name).problems == (RULES[name],)


def test_every_rule_has_its_fixture() -> None:
    loaded = _rules()

    assert loaded.extensions == ()
    expected = sorted({*RULES, "import_error", "many_problems"})

    assert [failure.extension for failure in loaded.failures] == expected


def test_a_module_lists_every_problem() -> None:
    assert _failure(_rules(), "many_problems").problems == (
        f"method 'rule-method': {WIRE}",
        "method 'rule-method': the name must be 1–40 characters",
        f"method 'rule-method': {SUFFIXES}",
        f"cipher 'Rule Cipher': {SHAPE} characters",
    )


def test_an_import_error_keeps_its_traceback() -> None:
    failure = _failure(_rules(), "import_error")
    missing = "No module named 'cipherstitch_missing_dependency'"

    assert failure.problems == (f"ModuleNotFoundError: {missing}",)
    assert failure.traceback is not None
    assert "import_error.py" in failure.traceback
    assert failure.traceback.rstrip().endswith(missing)


@pytest.mark.parametrize("field", ["name", "summary", "capacity", "risk", "pros", "cons"])
@pytest.mark.parametrize("hidden", [chr(10), chr(0x200B)])
def test_every_shown_text_refuses_control_and_format_characters(field: str, hidden: str) -> None:
    text = f"Bits{hidden}"
    changes: dict[str, Any] = {field: (text,) if field in ("pros", "cons") else text}
    problems = extension_checks.problems(Extension(methods=(replace(METHOD, **changes),)))
    verb = "have" if field in ("pros", "cons") else "has"

    assert problems == (f"method 'rule-method': the {field} {verb} a control or format character",)


def test_texts_and_entry_lists_must_have_their_types() -> None:
    changes: dict[str, Any] = {"summary": None}
    ciphers: Any = [CIPHER]
    extension = Extension(methods=(replace(METHOD, **changes),), ciphers=ciphers)

    assert extension_checks.problems(extension) == (
        "method 'rule-method': the summary must be a str",
        "EXTENSION.ciphers must be a tuple of CipherSpec",
    )


def test_the_extension_summary_is_checked_too() -> None:
    problems = extension_checks.problems(Extension(summary=chr(0x202E)))

    assert problems == ("EXTENSION: the summary has a control or format character",)
    assert extension_checks.problems(Extension(summary=chr(9))) == (
        "EXTENSION: the summary has a control or format character",
    )
    assert extension_checks.problems(Extension(summary=" ")) == (
        "EXTENSION: the summary is only blanks",
    )


@pytest.mark.parametrize(
    "name",
    ["   ", chr(0x3000), " " + "n" * 40, "n" * 41],
    ids=["spaces", "ideographic", "lead", "long"],
)
def test_a_name_needs_something_visible_in_at_most_40_characters(name: str) -> None:
    """A name is never empty, so blanks alone fail its length rule. An optional text may be
    empty, but not only blanks."""
    problems = extension_checks.problems(Extension(methods=(replace(METHOD, name=name),)))

    assert problems == ("method 'rule-method': the name must be 1–40 characters",)
    assert extension_checks.problems(Extension(methods=(replace(METHOD, name=" n "),))) == ()
    assert extension_checks.problems(Extension(methods=(replace(METHOD, summary=""),))) == ()


@pytest.mark.parametrize("cipher_id", [c for c in CipherId if c != CipherId.NONE])
def test_every_built_in_cipher_passes_the_self_test(cipher_id: CipherId) -> None:
    """Fernet draws a random IV and ignores the counter, which the self-test allows."""
    skip_unless_installed(cipher_id)

    assert extension_checks.self_test(ciphers.get(cipher_id)) is None


class _Shrinking(ChaChaCopy):
    def sealed_size(self, size: int) -> int:
        return 16 if size > 10 else size + 16


class _Forgetful(ChaChaCopy):
    def unseal(self, key: bytes, counter: int, data: bytes) -> bytes:
        return super().unseal(key, counter, data) + b"x"


class _CutBlind(ChaChaCopy):
    """Opens a sealed block cut by one byte as nothing."""

    def __init__(self) -> None:
        self._sealed: set[bytes] = set()

    def seal(self, key: bytes, counter: int, data: bytes) -> bytes:
        sealed = super().seal(key, counter, data)
        self._sealed.add(sealed)

        return sealed

    def unseal(self, key: bytes, counter: int, data: bytes) -> bytes:
        if any(len(s) == len(data) + 1 and s.startswith(data) for s in self._sealed):
            return b""

        return super().unseal(key, counter, data)


class _KeyBlind(ChaChaCopy):
    """Opens a block under any key it never sealed with as nothing."""

    def __init__(self) -> None:
        self._keys: set[bytes] = set()

    def seal(self, key: bytes, counter: int, data: bytes) -> bytes:
        self._keys.add(key)

        return super().seal(key, counter, data)

    def unseal(self, key: bytes, counter: int, data: bytes) -> bytes:
        return super().unseal(key, counter, data) if key in self._keys else b""


class _Broken(ChaChaCopy):
    def seal(self, key: bytes, counter: int, data: bytes) -> bytes:
        raise TypeError("no")


class _NoKeySize(ChaChaCopy):
    @property
    def key_size(self) -> int:  # type: ignore[override]
        raise AttributeError("key_size")


@pytest.mark.parametrize(
    ("cipher", "problem"),
    [
        (_Shrinking(), "sealed_size(11) returned 16, not an int of at least 26"),
        (_Forgetful(), "unseal didn't give back the 0 bytes seal took"),
        (_CutBlind(), "unseal of a cut block returned data instead of raising DecryptionError"),
        (_KeyBlind(), "unseal with a wrong key returned data instead of raising DecryptionError"),
        (_Broken(), "the self-test raised TypeError: no"),
        (_NoKeySize(), "key_size raised AttributeError: key_size"),
    ],
)
def test_the_self_test_names_what_it_caught(cipher: ChaChaCopy, problem: str) -> None:
    assert extension_checks.self_test(cipher) == problem


def test_modules_that_claim_the_same_thing_all_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded = use_extensions(monkeypatch, "clash")
    problems = {failure.extension: failure.problems for failure in loaded.failures}

    assert [extension.name for extension in loaded.extensions] == ["shared_one", "shared_two"]
    assert problems == {
        "cost_one": ("the spatial cost function id shared-cost is also claimed by cost_two",),
        "cost_two": ("the spatial cost function id shared-cost is also claimed by cost_one",),
        "first": (
            "the method wire id 160 is also claimed by second",
            "the cipher id clash-cipher is also claimed by second",
        ),
        "group_one": ("the parameter group id shared is also claimed by group_two",),
        "group_two": ("the parameter group id shared is also claimed by group_one",),
        "id_one": (
            "the method id clash-method is also claimed by id_two",
            "the cipher wire id 200 is also claimed by id_two",
        ),
        "id_two": (
            "the method id clash-method is also claimed by id_one",
            "the cipher wire id 200 is also claimed by id_one",
        ),
        "image_kind": (
            "the suffix .clash joins image carriers here, but text carriers in text_kind",
        ),
        "second": (
            "the method wire id 160 is also claimed by first",
            "the cipher id clash-cipher is also claimed by first",
        ),
        "text_kind": (
            "the suffix .clash joins text carriers here, but image carriers in image_kind",
        ),
    }


def test_entries_become_experimental_catalog_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded = use_extensions(monkeypatch, "working")
    method, cipher = catalog.method("byte-lsb"), catalog.cipher("chacha-copy")

    assert (method.name, method.tier, method.extension) == (
        "Byte LSB",
        Tier.EXPERIMENTAL,
        "byte_lsb",
    )
    assert method.is_available
    assert (cipher.name, cipher.tier, cipher.extension) == (
        "ChaCha20 copy",
        Tier.EXPERIMENTAL,
        "chacha_copy",
    )
    assert loaded.method("byte-lsb") == extension_loader.LoadedMethod(
        method,
        catalog.CarrierKind.DOCUMENT,
        (".fxb",),
        loaded.methods[0].embedding,
        200,
        "Anyone who compares the file with its original sees the changed bytes.",
    )
    assert catalog.method(MethodId.LSB).extension is None


def test_lookups_fall_back_to_the_extensions(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded = use_extensions(monkeypatch, "working")
    embedding = methods.get("byte-lsb")
    cipher = ciphers.get("chacha-copy")

    assert embedding is not None
    assert embedding == loaded.methods[0].embedding
    assert (embedding.inputs, embedding.outputs) == ((".fxb",), None)
    assert isinstance(cipher, GuardedCipher)
    assert (cipher.id, cipher.key_size) == ("chacha-copy", 32)
    assert container.method_wire_id("byte-lsb") == 200
    assert container.cipher_wire_id("chacha-copy") == 201
    assert container.cipher_key_info("chacha-copy") == "cipherstitch extension cipher 201"
    assert ciphers.overhead_label("chacha-copy") == "+16 B"


def test_built_in_lookups_never_load_the_extensions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extension_loader, "load", _refuse_loading)
    monkeypatch.delitem(methods._EMBEDDINGS, MethodId.LSB_REPLACEMENT)

    assert catalog.method(MethodId.DCT).name == "DCT coefficients (JPEG)"
    assert catalog.cipher(CipherId.NONE).name == "No encryption"
    assert methods.get(MethodId.LSB_REPLACEMENT) is None  # a built-in without a channel
    assert ciphers.get(CipherId.FERNET).id == CipherId.FERNET
    assert container.method_wire_id(MethodId.ZERO_WIDTH) == 9
    assert container.cipher_wire_id(CipherId.FERNET) == 5
    assert container.cipher_key_info(CipherId.AES_GCM) == "aes-256-gcm"
    assert catalog.is_builtin_method("lsb")
    assert not catalog.is_builtin_method("byte-lsb")
    assert catalog.is_builtin_cipher("none")
    assert not catalog.is_builtin_cipher("chacha-copy")


def test_unknown_ids_stay_unknown_once_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    use_extensions(monkeypatch, "working")

    assert methods.get("nsf5") is None

    for lookup in (catalog.method, container.method_wire_id):
        with pytest.raises(NotAvailableError, match=r"^Unknown method nsf5$"):
            lookup("nsf5")

    for cipher_lookup in (catalog.cipher, ciphers.get, container.cipher_wire_id):
        with pytest.raises(NotAvailableError, match=r"^Unknown cipher nsf5$"):
            cipher_lookup("nsf5")

    with pytest.raises(NotAvailableError, match=r"^Unknown cipher nsf5$"):
        container.cipher_key_info("nsf5")


def test_standard_reveal_tries_only_built_in_ciphers(monkeypatch: pytest.MonkeyPatch) -> None:
    use_extensions(monkeypatch, "working")

    assert all(catalog.is_builtin_cipher(cipher.id) for cipher in ciphers.encrypting())


def test_reveal_opens_an_extension_method_only_when_named(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded = use_extensions(monkeypatch, "working")
    carrier = PurePath("cover.fxb")
    opener = loaded.methods[0].embedding.open
    orders = (UnitOrder.KEYED, UnitOrder.PUBLIC, UnitOrder.SEQUENTIAL)

    with pytest.raises(UnsupportedFormatError, match="can't carry a payload"):
        methods.for_reveal(carrier)

    assert methods.for_reveal(carrier, "byte-lsb") == [
        (opener, tuple(Candidate("byte-lsb", order) for order in orders))
    ]
    assert methods.for_reveal(PurePath("cover.txt"), "byte-lsb") == methods.for_reveal(
        PurePath("cover.txt")
    )


def test_a_missing_dependency_leaves_the_entry_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Its cipher would fail the self-test, which can't run without the dependency."""
    loaded = use_extensions(monkeypatch, "missing_dependency")
    module = "cipherstitch_missing_dependency"

    assert loaded.failures == ()
    assert availability.method_reason("optional-method", None) == (
        f"Optional method needs {module}: pip install {module}"
    )
    assert availability.cipher_reason("optional-cipher") == (
        "Optional cipher needs missing-dependency: pip install missing-dependency"
    )


def test_built_in_install_hints_are_unchanged() -> None:
    assert catalog.Requirement("nacl", "crypto-extra").install_hint("XChaCha20-Poly1305") == (
        "XChaCha20-Poly1305 needs the crypto-extra extra, see Installation in README.md"
    )


@pytest.mark.parametrize(
    "module", ["scipy.signal", "sci" + chr(0x200D) + "py", "caf" + chr(0xE9), "2fa", ""]
)
def test_a_requirement_names_an_ascii_module(module: str) -> None:
    """``str.isidentifier`` takes format characters and letters beyond ASCII, which the
    install hint would then show."""
    requirement = catalog.Requirement(module)
    extension = Extension(methods=(replace(METHOD, requires=requirement),))

    assert extension_checks.problems(extension) == (
        "method 'rule-method': requires must name a top-level module, such as scipy",
    )


def test_a_requirement_names_an_extra_or_a_package() -> None:
    both = catalog.Requirement("scipy", "audio", "scipy")
    odd = catalog.Requirement("scipy", package="no spaces")
    problems = extension_checks.problems(
        Extension(methods=(replace(METHOD, requires=both), replace(METHOD, requires=odd)))
    )

    assert problems == (
        "method 'rule-method': requires names an extra or a package, not both",
        "method 'rule-method': requires names an extra or a package pip can't install",
        "the method id rule-method is declared 2 times",
        "the method wire id 150 is declared 2 times",
    )


def test_a_cipher_spec_is_checked_like_a_method() -> None:
    cipher = replace(CIPHER, wire_id=True, name="", pros=("Fine", chr(0x7F)))
    problems = extension_checks.problems(Extension(ciphers=(cipher,)))

    assert problems == (
        f"cipher 'rule-cipher': {WIRE}",
        "cipher 'rule-cipher': the name must be 1–40 characters",
        "cipher 'rule-cipher': the pros have a control or format character",
    )


def _raises(values: object) -> str | None:
    raise ValueError("no")


_CHOICE = replace(PARAM, type=ParamType.ENUM, minimum=None, maximum=None, step=None)


@pytest.mark.parametrize(
    ("changes", "param_changes", "problem"),
    [
        ({"params": ()}, {}, f"{GROUP}: the params must be a non-empty tuple of Param"),
        ({}, {"kind": "format"}, f"{LEVEL}: the kind must be a Kind"),
        ({}, {"type": "int"}, f"{LEVEL}: the type must be a ParamType"),
        ({}, {"scopes": [Scope()]}, f"{LEVEL}: the scopes must be a tuple of Scope"),
        (
            {},
            {"options": (Option(1, "One"),)},
            f"{LEVEL}: only a choice (ParamType.ENUM) has options",
        ),
        ({}, {"maximum": math.inf}, f"{LEVEL}: a number needs a finite minimum, maximum and step"),
        ({}, {"step": 2}, f"{LEVEL}: the default must be stored as 2"),  # off its step
        ({"check": _raises}, {}, f"{GROUP}: the check raised ValueError: no"),
        ({"scope": "rule-method"}, {}, f"{GROUP}: a scope must be a Scope"),
        (
            {"scope": Scope(("rule-method",))},  # type: ignore[arg-type]
            {},
            f"{GROUP}: the scope's methods must be a non-empty frozenset of method ids",
        ),
        (
            {"scope": Scope(frozenset({"rule-method"}), "on")},  # type: ignore[arg-type]
            {},
            f"{GROUP}: the scope's encryption must be an Encryption or None",
        ),
        (
            {"label": f"Rule{chr(0x200B)}"},
            {},
            f"{GROUP}: the label has a control or format character",
        ),
        ({"note": f"Rules{chr(10)}"}, {}, f"{GROUP}: the note has a control or format character"),
        ({}, {"label": "n" * 41}, f"{LEVEL}: the label must be 1–40 characters"),
        (
            {},
            {"code": f"LEVEL{chr(0x202E)}"},
            f"{LEVEL}: the code has a control or format character",
        ),
        ({}, {"unit": f"dB{chr(10)}"}, f"{LEVEL}: the unit has a control or format character"),
        ({}, {"known": f"0.48{chr(0)}"}, f"{LEVEL}: the known has a control or format character"),
    ],
)
def test_every_parameter_rule_names_what_it_caught(
    changes: dict[str, Any], param_changes: dict[str, Any], problem: str
) -> None:
    group = replace(PARAMS_GROUP, **{"params": (replace(PARAM, **param_changes),), **changes})

    assert extension_checks.problems(Extension(methods=(METHOD,), params=(group,))) == (problem,)


@pytest.mark.parametrize(
    ("options", "problem"),
    [
        ((Option([1], "One"),), "an option's value must be an int, float or str"),  # type: ignore[arg-type]
        ((Option(1, "One"), Option(1.0, "One again")), "two options have the same value"),
        ((Option(1, ""),), "the option label must be 1–40 characters"),
    ],
)
def test_a_choices_options_are_checked(options: tuple[Option, ...], problem: str) -> None:
    group = replace(PARAMS_GROUP, params=(replace(_CHOICE, default=1, options=options),))

    assert extension_checks.problems(Extension(methods=(METHOD,), params=(group,))) == (
        f"{LEVEL}: {problem}",
    )


def test_a_cipher_without_its_dependency_still_needs_a_key_size_in_range() -> None:
    """It skips the self-test, which may need the dependency, but not the key size."""
    short_key = importlib.import_module(f"{EXTENSION_FIXTURES}.rules.key_size").ShortKey()
    missing = catalog.Requirement("cipherstitch_missing_dependency")
    spec = replace(CIPHER, cipher=short_key, requires=missing)

    assert extension_checks.problems(Extension(ciphers=(spec,))) == (
        "cipher 'rule-cipher': key_size must be an int in 16–64, not 8",
    )
