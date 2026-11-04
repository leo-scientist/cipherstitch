"""Command-line interface: ``ciphst hide|reveal|capacity|extensions``."""

import argparse
import getpass
import importlib
import sys
import traceback
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from cipherstitch import __version__, catalog, extension_loader, payload, service, standard
from cipherstitch.catalog import CarrierKind, CipherId, CipherKey, MethodId
from cipherstitch.errors import (
    CapacityError,
    CipherStitchError,
    ExtensionError,
    NoHiddenDataError,
    NotAvailableError,
    UnsupportedFormatError,
)
from cipherstitch.extension_loader import Failure, LoadedCipher, LoadedMethod
from cipherstitch.params import profile, registry
from cipherstitch.params.model import key
from cipherstitch.params.settings import STANDARD as STANDARD_SETTINGS
from cipherstitch.params.settings import Settings

_DESCRIPTION = (
    "Hide a file inside an image, a recording, a document or a text file, encrypted with a "
    "password, and reveal it again."
)
_INTERRUPTED = 130  # the shell's code for a process stopped by Ctrl+C
_METHODS = ", ".join(MethodId)
_CIPHERS = ", ".join(CipherId)


class _ResearchAllowsError(CipherStitchError):
    """A Standard mode refusal of choices that ``--research`` allows, with the flags to add."""

    def __init__(self, message: str, flags: str) -> None:
        super().__init__(message)
        self.flags = flags


def _prompt_password() -> str:
    password = getpass.getpass("Password: ")

    if not password:
        raise CipherStitchError("Password must not be empty")

    return password


def _prompt_new_password() -> str:
    password = _prompt_password()

    if getpass.getpass("Repeat password: ") != password:
        raise CipherStitchError("Passwords do not match")

    return password


def _cipher(args: argparse.Namespace) -> CipherKey:
    if args.cipher is None:
        return catalog.DEFAULT_CIPHER if args.encrypt or not args.research else CipherId.NONE

    if args.encrypt and args.cipher == CipherId.NONE:
        raise CipherStitchError("--encrypt needs a cipher other than none")

    cipher: CipherKey = args.cipher

    return cipher


def _warn_about_failures(args: argparse.Namespace) -> None:
    """Research mode (``--research`` or ``--profile``) loads the extensions: one warning for
    each that couldn't, before a profile or a choice names what it would have added."""
    if not args.research and args.profile is None:
        return

    for failure in service.extension_failures():
        message = f"extension {failure.extension} couldn't load: {failure.brief}"
        print(f"warning: {message}", file=sys.stderr)


def _check_choices(args: argparse.Namespace) -> None:
    """``--method`` and ``--cipher`` by mode. Standard mode knows only the built-ins, and
    never loads the extensions to look an id up. Research mode knows the extensions' too."""
    if not args.research:
        for reason in (
            None if args.method is None else standard.unknown_method_reason(args.method),
            None if args.cipher is None else standard.unknown_cipher_reason(args.cipher),
        ):
            if reason is not None:
                raise NotAvailableError(f"{reason} (--research)")

        return

    _check_choice("method", args.method, service.reveal_methods(is_research=True))
    _check_choice("cipher", args.cipher, service.mode_ciphers(is_research=True))


def _check_choice(kind: str, value: str | None, known: tuple[str, ...]) -> None:
    if value is not None and value not in known:
        raise NotAvailableError(f"Unknown {kind} {value}. Choose from {', '.join(known)}")


def _with_profile(
    args: argparse.Namespace, *, is_reveal: bool
) -> tuple[argparse.Namespace, Settings]:
    """``--profile`` implies ``--research``, and gives the parameters and the method and
    cipher that ``--method`` and ``--cipher`` leave open. Parameters this version doesn't know
    are left out of a hide and refused by a reveal, which would find nothing without them. A
    cost function no loaded extension has is refused by a hide and doesn't matter to a
    reveal."""
    if args.profile is None:
        return args, STANDARD_SETTINGS

    loaded = profile.read(args.profile)

    if is_reveal:
        profile.refuse_unknown(loaded, args.profile.name)
    else:
        profile.refuse_missing_costs(loaded, args.profile.name)

        if (warning := profile.unknown_warning(loaded)) is not None:
            print(f"warning: {warning}", file=sys.stderr)

    # -e asks to encrypt: a profile's "none" gives way to the default cipher.
    wants_encryption = not is_reveal and args.encrypt and loaded.cipher == CipherId.NONE
    cipher = args.cipher or (None if wants_encryption else loaded.cipher)
    chosen = {"method": args.method or loaded.method, "cipher": cipher}

    return argparse.Namespace(**{**vars(args), **chosen, "research": True}), loaded.settings


def _hide_options(args: argparse.Namespace, settings: Settings) -> service.HideOptions:
    """A Standard refusal hints at ``--research`` only if Research mode's default parameters
    allow the same choices: they keep some of Standard's cover rules."""
    cipher = _cipher(args)

    def options(*, is_research: bool) -> service.HideOptions:
        return service.hide_options(
            args.image,
            args.output,
            cipher,
            method=args.method,
            carrier=None if args.carrier is None else CarrierKind(args.carrier),
            is_research=is_research,
            settings=settings,
        )

    try:
        return options(is_research=args.research)
    except CipherStitchError as exc:
        if args.research or not str(exc).startswith(standard.OFF):
            raise

        try:
            options(is_research=True)
        except CipherStitchError:
            raise exc from None

        # Standard encrypts by default, Research only with -e.
        flags = "--research" if args.cipher is not None else "--research -e"
        raise _ResearchAllowsError(str(exc), flags) from exc


def _hide(args: argparse.Namespace) -> None:
    _warn_about_failures(args)
    args, settings = _with_profile(args, is_reveal=False)
    _check_choices(args)
    options = _hide_options(args, settings)
    hidden = service.read_payload(args.file)
    estimate = service.estimate(args.image, hidden.name, len(hidden.data), options)

    if estimate.excess:
        raise CapacityError(_excess_message(estimate, is_research=options.is_research))

    for risk in service.detection_risks(options, args.image, estimate):
        print(f"warning: {risk}", file=sys.stderr)

    if options.is_encrypted:
        options = replace(options, password=_prompt_new_password())
    else:
        print(
            "warning: without --encrypt anyone who finds the payload can read it", file=sys.stderr
        )

    service.hide(args.image, hidden, args.output, options)
    print(f"Hidden {args.file.name} in {args.output}")


def _excess_message(estimate: service.Estimate, *, is_research: bool) -> str:
    """Research mode measures the payload against the capacity, Standard mode against the
    advised size. Either refusal comes before the password prompt."""
    excess = f"{estimate.excess} bytes"

    if is_research:
        return f"Payload is {excess} too large for this carrier"

    return standard.over_limit(excess, estimate.limit)


def _reveal(args: argparse.Namespace) -> None:
    _warn_about_failures(args)
    args, settings = _with_profile(args, is_reveal=True)
    research = _research_reveal(args, settings)
    is_encrypted = args.encrypted or (research is not None and research.cipher != CipherId.NONE)
    output_dir = _output_dir(args)
    password = _prompt_password() if is_encrypted else None
    result = service.reveal(args.image, output_dir, password, research=research)

    if password and result.cipher == CipherId.NONE:
        print(
            "warning: the file was hidden without encryption, so the password was not used "
            "and nothing proves who hid it or that it is unchanged",
            file=sys.stderr,
        )

    print(f"Revealed {result.path}")


def _output_dir(args: argparse.Namespace) -> Path:
    """``-d``, or the current folder, read only now: a parser default would fail every
    command run from a deleted folder before any error handling."""
    if args.output_dir is not None:
        return Path(args.output_dir)

    try:
        return Path.cwd()
    except FileNotFoundError as exc:
        raise CipherStitchError("The current folder no longer exists. Choose one with -d") from exc


def _reveal_hint(args: argparse.Namespace) -> str:
    if args.profile is not None:
        return "the method, cipher and profile must be the ones used for hiding"

    if args.research:
        return (
            "the method, cipher and parameters must be the ones used for hiding. Give "
            "parameters other than the defaults with --profile"
        )

    research = "Reveal with --research and the same --method and --cipher"

    if not args.encrypted:
        return f"encrypted? Add -e to type the password. Hidden in Research mode? {research}"

    return f"hidden in Research mode? {research}"


def _research_reveal(args: argparse.Namespace, settings: Settings) -> service.ResearchReveal | None:
    """Research mode's Reveal tries only the given method, cipher and settings; Standard's
    tries all."""
    if not args.research:
        if args.method is not None or args.cipher is not None:
            raise CipherStitchError("--method and --cipher need --research")

        return None

    missing = [name for name in ("method", "cipher") if getattr(args, name) is None]

    if missing and args.profile is not None:
        flags = " and ".join(f"--{name}" for name in missing)
        names = " or ".join(missing)
        raise CipherStitchError(f"{args.profile.name} names no {names}. Add {flags}")

    if missing:
        raise CipherStitchError("--research needs --method and --cipher")

    _check_choices(args)

    return service.ResearchReveal(args.method, args.cipher, settings)


def _capacity(args: argparse.Namespace) -> None:
    """Both modes on one line, e.g. ``LSB: 8424 bytes in Standard mode (the advised size),
    76895 bytes in Research mode``, or Research's number and why Standard mode can't use
    the carrier. When neither can, Standard's error, as for a hide. Only ``--research`` lets
    the extensions' methods count, which loads them."""
    _warn_about_failures(args)

    def research_capacity() -> service.ModeCapacity:
        return service.mode_capacity(args.image, research=True, with_extensions=args.research)

    try:
        in_standard = service.mode_capacity(args.image)
    except (NotAvailableError, UnsupportedFormatError) as exc:
        try:
            in_research = research_capacity()
        except CipherStitchError:
            raise exc from None

        # Research mode knows the suffixes the extensions' methods add, Standard mode doesn't.
        reason = str(exc) if isinstance(exc, NotAvailableError) else f"{standard.OFF}: {exc}"
        print(f"{_method_name(in_research)}: {in_research.size} bytes in Research mode. {reason}")

        return

    in_research = research_capacity()
    standard_size = f"{in_standard.size} bytes in Standard mode"
    research_size = f"{in_research.size} bytes in Research mode"

    if in_standard.method == in_research.method:
        name = _method_name(in_standard)
        print(f"{name}: {standard_size} (the advised size), {research_size}")
    else:
        standard_with = f"{standard_size} with {_method_name(in_standard)} (the advised size)"
        print(f"{standard_with}, {research_size} with {_method_name(in_research)}")


def _method_name(found: service.ModeCapacity) -> str:
    return catalog.method(found.method).name


def _extensions(_args: argparse.Namespace) -> int:
    """What loaded on stdout, what failed on stderr with its traceback: 1 if anything did."""
    loaded = service.loaded_extensions()

    for extension in loaded.extensions:
        print(f"{extension.name}: {extension.summary}" if extension.summary else extension.name)

        for method in extension.methods:
            print(f"  {_method_line(method)}")

        for cipher in extension.ciphers:
            print(f"  {_cipher_line(cipher)}")

        for cost in extension.costs:
            print(f"  cost {cost.id} · {registry.COST_KEYS[cost.domain]}")

        keys = [key(group, param) for group in extension.params for param in group.params]

        if keys:
            print(f"  parameters {', '.join(keys)}")

    if not loaded.extensions and not loaded.failures:
        print(f"No extensions in {_extension_folder()} (see docs/extensions.md)")

    for failure in loaded.failures:
        _print_failure(failure)

    return 1 if loaded.failures else 0


def _extension_folder() -> str:
    """The folder Research mode loads the extensions from: an installed copy's is in its own
    package, not in a clone's ``src/cipherstitch/extensions/``."""
    package = importlib.import_module(extension_loader.PACKAGE)

    return next(iter(package.__path__))


def _method_line(method: LoadedMethod) -> str:
    """E.g. ``method line-spaces · wire id 128 · text: .txt, .md``."""
    embedding = method.embedding
    parts = [
        f"method {method.entry.id}",
        f"wire id {method.wire_id}",
        f"{method.carrier}: {', '.join(embedding.inputs)}",
    ]

    if embedding.outputs is not None:
        parts.append(f"writes {', '.join(embedding.outputs)}")

    reason = service.unavailable_methods(method.carrier, is_research=True).get(method.entry.id)

    return " · ".join(parts if reason is None else [*parts, f"unavailable: {reason}"])


def _cipher_line(cipher: LoadedCipher) -> str:
    """E.g. ``cipher aes-256-ocb3 · wire id 128``."""
    parts = [f"cipher {cipher.entry.id}", f"wire id {cipher.wire_id}"]
    reason = service.unavailable_ciphers(is_research=True).get(cipher.entry.id)

    return " · ".join(parts if reason is None else [*parts, f"unavailable: {reason}"])


def _print_failure(failure: Failure) -> None:
    heading = f"error: extension {failure.extension} couldn't load:"

    if len(failure.problems) == 1:
        print(f"{heading} {failure.problems[0]}", file=sys.stderr)
    else:
        print(heading, *(f"  {problem}" for problem in failure.problems), sep="\n", file=sys.stderr)

    if failure.traceback is not None:
        print(failure.traceback, end="", file=sys.stderr)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ciphst", description=_DESCRIPTION)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(required=True)

    hide = commands.add_parser("hide", help="hide a file inside an image, audio, document or text")
    _add_hide_arguments(hide)
    hide.set_defaults(handler=_hide)

    reveal = commands.add_parser("reveal", help="extract a hidden file from a carrier")
    reveal.add_argument("image", type=Path, metavar="carrier", help="file with hidden data")
    reveal.add_argument("-d", "--output-dir", type=Path, help="default: current directory")
    reveal.add_argument(
        "-e", "--encrypted", action="store_true", help="prompt for the password used to hide"
    )
    reveal.add_argument(
        "--research",
        action="store_true",
        help="try only --method and --cipher, for a file hidden in Research mode (default: try "
        "every method and cipher at Standard's settings)",
    )
    reveal.add_argument("--method", help=f"the method used to hide: {_METHODS}, or an extension's")
    reveal.add_argument(
        "--cipher",
        help=f"the cipher used to hide: {_CIPHERS}, or an extension's. Anything but none "
        "prompts for the password",
    )
    reveal.add_argument(
        "--profile",
        type=Path,
        help=f"a {profile.SUFFIX} file with the Research parameters used to hide, and the "
        "method and cipher unless --method or --cipher is given (implies --research)",
    )
    reveal.set_defaults(handler=_reveal)

    capacity = commands.add_parser(
        "capacity",
        help="show how many bytes a payload may take in a carrier, in Standard mode (the advised "
        "size) and in Research mode (the capacity). The framing comes out of that room",
    )
    capacity.add_argument(
        "image",
        type=Path,
        metavar="carrier",
        help="image or camera RAW, WAV/FLAC audio, PDF/DOCX document or UTF-8 text",
    )
    capacity.add_argument(
        "--research",
        action="store_true",
        help="let the extensions' methods count for Research mode, for a file only they take",
    )
    capacity.set_defaults(handler=_capacity, profile=None)

    extensions = commands.add_parser(
        "extensions",
        help="list the extensions Research mode loads, with their ids and wire ids, and those "
        "that couldn't load (exit status 1 if any)",
    )
    extensions.set_defaults(handler=_extensions)

    return parser


def _add_hide_arguments(hide: argparse.ArgumentParser) -> None:
    hide.add_argument(
        "image",
        type=Path,
        metavar="carrier",
        help="cover image or camera RAW, WAV/FLAC audio, PDF/DOCX document or UTF-8 text",
    )
    hide.add_argument("file", type=Path, help="file to hide")
    hide.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="where to save the result: .png, .bmp or .tiff for image LSB and LSB replacement, "
        ".jpg for DCT (from a JPEG, or from a RAW, PNG or TIFF it compresses), and the "
        "carrier's own format for the other methods",
    )
    hide.add_argument(
        "-e",
        "--encrypt",
        action="store_true",
        help=f"with --research, encrypt with a password "
        f"({catalog.cipher(catalog.DEFAULT_CIPHER).name} unless --cipher picks another), so "
        "nothing in the carrier can be read without it. Standard mode always encrypts",
    )
    hide.add_argument(
        "--carrier",
        choices=[kind.value for kind in CarrierKind],  # plain strings: argparse lists them
        help="carrier type (default: taken from the carrier's extension)",
    )
    hide.add_argument(
        "--method",
        help=f"embedding method: {_METHODS}, or with --research an extension's (ciphst "
        "extensions lists them). Default: the first one for the carrier type that the mode "
        "allows and that can write the output format",
    )
    hide.add_argument(
        "--cipher",
        help=f"cipher: {_CIPHERS}, or with --research an extension's. Anything but none turns "
        "on --encrypt",
    )
    hide.add_argument(
        "--research",
        action="store_true",
        help="Research mode: every carrier type, method and cipher, the extensions' too, and "
        "payloads up to the capacity. Its default parameters still refuse lossy covers for LSB "
        "and audio below 16 bits. The output may be easier to detect, so it is not for real "
        "secrets. Without it, Standard mode allows only recommended methods, ciphers and "
        "covers, payloads up to the advised size and a strong password",
    )
    hide.add_argument(
        "--profile",
        type=Path,
        help=f"Research parameters from a {profile.SUFFIX} file, and its method and cipher "
        "unless --method or --cipher is given (implies --research). Parameters this version "
        "doesn't know are left out with a warning",
    )


def main(argv: Sequence[str] | None = None) -> int:
    # A file or folder name may hold characters the console's encoding lacks (a non-UTF-8
    # name on Linux, a cp1252 console on Windows): print it escaped, not fail after the work.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")

    args = _build_parser().parse_args(argv)

    try:
        status: int | None = args.handler(args)
    except NoHiddenDataError as exc:
        print(f"error: {exc}", file=sys.stderr)
        if str(exc).startswith(payload.NOT_FOUND_MESSAGE):  # not a payload found damaged
            print(f"hint: {_reveal_hint(args)}", file=sys.stderr)

        return 1
    except (CipherStitchError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)

        if isinstance(exc, _ResearchAllowsError):
            print(f"hint: Research mode allows this. Add {exc.flags}", file=sys.stderr)

        if isinstance(exc, ExtensionError) and exc.__cause__ is not None:
            traceback.print_exception(exc.__cause__, file=sys.stderr)  # for the extension's author

        return 1
    except EOFError:  # input closed at a password prompt
        print("error: no password given", file=sys.stderr)

        return 1
    except KeyboardInterrupt:
        print(file=sys.stderr)

        return _INTERRUPTED

    return status or 0
