# Extensions

Research mode can run embedding methods, ciphers and cost functions that aren't part of
CipherStitch. Each one is a Python module in the package's `extensions` folder,
`src/cipherstitch/extensions/` in a checkout. The CLI and the app pick it up the next time
they run in Research mode, and list it with the built-ins.

Extensions are for experiments. Their code runs with your rights, and nobody has measured
how detectable their output is. Read [Trust and limits](#trust-and-limits) before you copy
one in.

## What you can add

- **Embedding methods** for the four carrier types: image, audio, document and text. A
  method may also hide in a suffix CipherStitch doesn't know, such as `.pgm`, which then
  joins its carrier type.
- **Ciphers**: authenticated encryption with a key of 16 to 64 bytes.
- **Parameters** of your methods, which the app's parameter editor and profiles handle like
  the built-in ones (see [Parameters](#parameters)).
- **Cost functions** for image LSB and DCT, which say what changing each value costs (see
  [Cost functions](#cost-functions)).

Every entry is tagged *Experimental*, and only Research mode offers it. Standard mode never
imports an extension, so its output doesn't change by a byte whatever the folder holds.

An extension can't add a carrier type or a key derivation function, and it can't change
Standard mode.

## Quick start

The repository has three examples in `examples/extensions/`: a method that hides one bit
per line of a text file, AES-256-OCB3, and a cost function for image LSB. Copy the method
into the folder and check that it loads:

```bash
cp examples/extensions/line_spaces.py src/cipherstitch/extensions/
uv run ciphst extensions
```

```text
line_spaces: An example method: one bit per line of a text file.
  method line-spaces · wire id 128 · text: .txt, .md
  parameters lines.mark
```

Hide a file in a text of a few hundred lines, then reveal it. *Line-end spaces* holds one
bit per line, so a short file takes about 500 lines, and more when encrypted.

```bash
uv run ciphst hide poem.txt key.txt -o poem-2.txt --research --method line-spaces
uv run ciphst reveal poem-2.txt -d out --research --method line-spaces --cipher none
```

In the app (`uv run ciphst-gui`), switch *Research mode* on and choose *Text* as the
carrier type. *Line-end spaces* comes last in the Method picker, tagged *Experimental*, and
its detail ends with *From the extension line_spaces*. Its Parameters row's *Edit…* shows
the method's one parameter, the mark at the end of a line, a space or a tab. To reveal the
file hidden above, choose it on the Reveal page, then *Line-end spaces* as the method and
*No encryption*. The app loads the extensions the first time Research mode is on, so start
it again after you change one.

The cipher works the same way. Copy `examples/extensions/ocb_cipher.py` in, then hide with
`--research --cipher aes-256-ocb3`, or choose *AES-256-OCB3* in the app. The cost function
in `examples/extensions/variance_costs.py` shows as *Local variance* in the parameter
editor's *LSB image* group (see [Cost functions](#cost-functions)). To remove an extension,
delete its file.

`.gitignore` leaves everything in the folder but its `__init__.py` out of git, so a local
experiment is never committed or packaged by accident. A fork that wants to commit its
extensions removes those two lines.

## An extension module

An extension is a module (`line_spaces.py`) or a package (`my_method/` with an
`__init__.py`) whose name doesn't start with `_`. It exports `EXTENSION`:

```python
EXTENSION = Extension(
    methods=(...),  # MethodSpec entries
    ciphers=(...),  # CipherSpec entries
    params=(...),  # Group entries: the methods' Research parameters
    costs=(...),  # CostSpec entries: cost functions for image LSB and DCT
    summary="An example method: one bit per line of a text file.",
)
```

Import everything you build on from `cipherstitch.extensions`: `Extension`, `MethodSpec`,
`CipherSpec`, the channel bases (`SlotChannel`, `StreamChannel`, `TrellisChannel` and the
`Channel` protocol), `CounterNonceAead`, `Order`, `Tuning`, `CarrierKind`, `Requirement`,
`SEQUENTIAL_SEED`, `Bits`, `Units`, `Embedded`, `Sealer`, what parameters are made of
(`Group`, `Param`, `Option`, `ParamType`, `Kind`, `Scope`, `Encryption` and `experimental`),
what cost functions are made of (`CostSpec`, `Domain`, `SpatialCosts`, `JpegCosts`,
`Coefficients` and `Component`) and the errors (`CapacityError`, `UnsupportedFormatError`,
`NotAvailableError`, `DecryptionError`). Keep helpers inside your package, or in a module
whose name starts with `_`. The loader skips those.

- **Ids** are the names the CLI and profiles use: lower-case letters and digits, in words
  joined by hyphens, at most 32 characters. An id must be unique among the methods (or the
  ciphers), and can't be a built-in's, `none` or `order`.
- **Names, summaries, pros, cons, capacity and risk** are shown as plain text. A name is 1
  to 40 characters with something visible in it, no text may hold a control or format
  character, and an optional text is either empty or has something visible too.
- **Wire ids** are stored in every file the entry writes. Pick a free one in 128–254.
  Methods and ciphers count apart, so a method and a cipher may share one.
  `ciphst extensions` lists the wire ids taken by what loaded. Never change a wire id
  once you have hidden anything with it, and write it in the module's docstring, as the
  examples do. Two extensions that claim the same id or wire id both fail to load.
- **Order of the lists:** the built-ins come first, then the extensions in name order, each
  with its entries in the order it declares them. The default method for a file is always a
  built-in one when a built-in method can hide in it, so name an extension's method with
  `--method`.

The extensions load once per process, on the first Research command that needs them, so
a change shows the next time a command runs.

## Writing a method

A `MethodSpec` names the method and gives its opener, a callable that takes the carrier's
path and the channels' `Tuning` and returns a channel:

```python
MethodSpec(
    "line-spaces",
    128,
    "Line-end spaces",
    CarrierKind.TEXT,
    (".txt", ".md"),
    open=LineSpaceChannel,
    order=Order.KEYED_IF_ENCRYPTED,
    summary="A space at the end of a line for 1, none for 0.",
    pros=("The text reads the same",),
    cons=("Editors that trim trailing spaces destroy it", "Very low capacity"),
    capacity="1 bit per line",
    risk="Trailing spaces show in a diff and in editors that mark them.",
)
```

`inputs` are the suffixes the method hides in. `outputs` are those it writes, `None` for the
carrier's own. A suffix a built-in carrier type has belongs to that type, and a new one
joins the method's. Only a suffix in `inputs` is offered as a carrier.

### The channel

The container hands a channel two parts. The prefix is 16 bytes, a nonce or a salt, and the
body follows it. A channel provides:

| Member                             | What it does                                                        |
| ---------------------------------- | ------------------------------------------------------------------- |
| `capacity(prefix_size)`            | the body bytes that fit after a prefix of `prefix_size` bytes (0 or 16) |
| `stored_limit(prefix_size)`        | the most body bytes a write can have stored. Reveal ignores longer lengths. Usually the capacity |
| `read(prefix_size, size, seed)`    | up to `size` bytes after a prefix of `prefix_size` bytes, in the order of `seed`. The prefix itself is read as `read(0, 16, None)`. Callers read at most the first 64 bytes, or the whole body |
| `write(prefix, body, seed)`        | the carrier with both stored, as an object with `save(path)`. Raise `CapacityError` when they don't fit |

The capacity must be exact: `extension_testing.check_method` hides a payload of exactly the
most it allows.

### Orders and seeds

The prefix is always in the channel's own order. The body's order comes from `seed`:

| Seed              | Order                                         | When                                    |
| ----------------- | --------------------------------------------- | --------------------------------------- |
| `None`            | the channel's own, which anyone can follow    | a plain payload, and every payload of an `Order.SEQUENTIAL` method |
| a 32-byte key     | a keyed order that only the password gives    | an encrypted payload of an `Order.KEYED_IF_ENCRYPTED` method |
| `SEQUENTIAL_SEED` | in sequence                                   | Research's `frame.order` set to sequential |

Declare `Order.KEYED_IF_ENCRYPTED` only if the channel orders its units by the seed.
`SlotChannel` and `TrellisChannel` do that for you. A Research reveal tries the keyed, the
public and the sequential order, so `frame.order` never has to match.

### Choosing a base

- **`SlotChannel`**: fixed-width units, such as one bit per line. Give it the unit count and
  the bits per unit, then implement `_read_bits(units)` and `_written(writes)`. It handles
  the capacity, the prefix and the keyed order.
- **`StreamChannel`**: one byte stream stored as a whole, such as an object in a document.
  Give it the limit in bytes, then implement `_read_stream()` and `_written(stream)`. Its
  methods are `Order.SEQUENTIAL`.
- **`TrellisChannel`**: values that a write steps by ±1 at a cost, coded with
  syndrome-trellis codes, as image LSB and DCT are. Give it the path, the value count and
  `tuning.coding`, a `_method_name`, and implement `_values`, `_counted`, `_costs(rate)`,
  `_value_range` and `_rebuilt(values)`. `methods/lsb.py` is the reference. Set
  `codes_with_trellis=True` in the `MethodSpec`, so that Research mode shows the STC
  parameters for your method (see [Parameters](#parameters)).
- **The `Channel` protocol** itself, for anything else.

The example is a `SlotChannel` with one unit per line, and `_text` reads the file within
its limits (see [Untrusted files](#untrusted-files)):

```python
class LineSpaceChannel(SlotChannel[EmbeddedLines]):
    """Unit i is line i, and its bit is whether the line ends with the mark, before its LF or
    CR LF. The text after the last LF is a line only if it isn't empty, so a write never adds
    a line."""

    def __init__(self, path: Path, tuning: Tuning) -> None:
        self._name = path.name
        self._mark = MARKS[str(tuning.extension_value("lines.mark"))]
        self._pieces = _text(path).split("\n")  # the lines, each with the CR of a CR LF
        count = len(self._pieces) if self._pieces[-1] else len(self._pieces) - 1
        ends = (_line(piece).endswith(self._mark) for piece in self._pieces[:count])
        self._bits = np.fromiter(ends, dtype=np.uint8, count=count)
        super().__init__(count, bits_per_unit=1)

    def _read_bits(self, units: Units) -> Bits:
        return self._bits[units]

    def _written(self, writes: Sequence[tuple[Units, Bits]]) -> EmbeddedLines:
        # A space or tab already there could read as a 1, and removing it could change the text.
        if any(_line(piece).endswith(_TRAILING) for piece in self._pieces):
            raise UnsupportedFormatError(f"{self._name} has spaces or tabs at the end of lines")

        bits = np.zeros(len(self._pieces), dtype=np.uint8)  # 0 for an empty piece at the end

        for units, chunk in writes:
            bits[units] = chunk  # one bit per unit: as many units as bits

        pieces = zip(self._pieces, bits, strict=True)

        return EmbeddedLines("\n".join(_marked(p, self._mark) if bit else p for p, bit in pieces))


def _text(path: Path) -> str:
    """The file's text, refused before reading or splitting it would pass the limits."""
    if path.stat().st_size > MAX_SIZE:
        raise UnsupportedFormatError(f"{path.name} is too large for line-end spaces")

    data = path.read_bytes()

    if data.count(b"\n") > MAX_LINES:
        raise UnsupportedFormatError(f"{path.name} has too many lines for line-end spaces")

    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnsupportedFormatError(f"{path.name} is not UTF-8 text") from exc
```

### Untrusted files

A file to reveal from may be crafted. Make the opener fail with `UnsupportedFormatError` on
anything it can't read, and bound what opening a file can allocate before you allocate it.
The example checks the file's size against `MAX_SIZE` before reading it, and counts its
newlines in the raw bytes against `MAX_LINES` before it decodes the text or makes a string
per line. Without the second limit, a file of 16 MiB of newlines took 1.7 GB to open. With
both, opening takes at most about four times `MAX_SIZE`. Any other exception reaches the
user as `The extension x failed: …`.

Refuse a cover the method can't use cleanly when writing, not when opening, since reveal
opens files that already hold a payload. The example refuses a text whose lines already end
in spaces or tabs.

### Determinism

Reveal must find exactly the units that write used, on any machine. Derive them from the
file alone. Avoid `set` order and `hash()`, which change between runs, and float sums whose
rounding can change between processors. A write must never add or remove a unit: the
example counts no line after a final newline, so a space never creates one.

### Optional dependencies

Import a library that CipherStitch doesn't install inside the functions that need it, and
declare it:

```python
MethodSpec(..., requires=Requirement("scipy"))
```

Without the module, the entry still loads and shows as unavailable, with the hint
`Name needs scipy: pip install scipy`. Name the top-level module you import, in ASCII
letters, digits and underscores. Give `package="..."` when the pip package's name isn't
the module's. An import at the top of the module would fail the whole extension instead.

### The Detection row

`risk` is what a detector or a curious person would notice, in one sentence. `ciphst hide`
prints it as a `warning:` line. Without it the line reads "This method comes from an
extension. CipherStitch hasn't measured how detectable it is."

## Writing a cipher

A cipher provides `key_size`, `sealed_size(size)`, `seal(key, counter, data)` and
`unseal(key, counter, data)`:

- **Authenticated only.** `unseal` must raise `DecryptionError` for anything `seal` didn't
  produce under that key and counter, and must raise nothing else.
- **Exact sizes.** `sealed_size` gives the exact length of `seal`'s output, because the
  capacity math relies on it.
- **Keys.** Every hide derives a master key with Argon2id from the password and a fresh
  16-byte salt. Your key is its HKDF-SHA256 subkey with the info
  `cipherstitch extension cipher <wire id>`, `key_size` bytes long. Renaming the cipher
  breaks nothing, changing its wire id breaks every file.
- **Counters.** `seal` runs twice per hide, with counter 0 for the header and 1 for the
  record. Every hide has new keys, so the counter can serve as the nonce.

The loader runs a self-test on every cipher, which takes microseconds. It checks sizes from
0 to 64 bytes, 255 and 4096, round trips, a flipped bit, a cut block and a wrong key
(`DecryptionError` alone), that no 16 bytes of the input show in the output, and that
counters 0 and 1 give different output.

For an AEAD from `cryptography` with a 12-byte nonce and a 16-byte tag, `CounterNonceAead`
does all of this. The whole example:

```python
from cryptography.hazmat.primitives.ciphers.aead import AESOCB3

from cipherstitch.extensions import CipherSpec, CounterNonceAead, Extension


class AesOcb3(CounterNonceAead):
    """A 32-byte key, the block counter as its 12-byte nonce and a 16-byte tag."""

    _algorithm = AESOCB3


EXTENSION = Extension(
    ciphers=(
        CipherSpec(
            "aes-256-ocb3",
            128,
            "AES-256-OCB3",
            AesOcb3(),
            summary="AES in OCB mode: one pass encrypts and authenticates.",
            pros=("One pass, no separate MAC",),
            cons=("Rarely used outside research",),
        ),
    ),
    summary="An example cipher: AES-256-OCB3 from cryptography.",
)
```

Only a Research reveal that names the cipher tries it. Standard mode's reveal tries the
built-in ciphers alone.

## Parameters

A method can have Research parameters, which the app's parameter editor shows and profiles
keep, as the built-in methods do. They go in `Extension.params`, as groups. The example's
mark is one:

```python
MARK = Group(
    "lines",
    "Line-end spaces",
    "paragraph",
    "How line-end spaces marks a 1.",
    (
        Param(
            "mark",
            "Mark",
            "MARKS",
            ParamType.ENUM,
            "space",
            Kind.FORMAT,
            options=(Option("space", "Space"), Option("tab", "Tab")),
            note="The character at the end of a line that stands for 1.",
        ),
    ),
    scope=Scope(frozenset({"line-spaces"})),
)
```

A group has an id, a label, an icon, a note, its parameters, a scope and an optional check.
A parameter has an id, a label, the code it sets (the editor shows it under the label), a
type, a default and a kind. Some types need options, or a minimum, a maximum and a step.
A unit, a note, a measured result (`known`) and scopes are optional.

- **Keys.** A parameter's key is `group.param`, here `lines.mark`, and profiles store it.
  Both ids are lower-case letters and digits that start with a letter, at most 32
  characters and without hyphens. A group's id can't be a built-in group's (`crypto`,
  `frame`, `stc`, `limg`, `laud`, `dct`, `pre`, `phase`, `echo`, `obj`, `ws`, `zw`), and two
  extensions that declare the same group id both fail to load.
- **Types.** `ParamType.ENUM` offers its `options`, each with an `int`, `float` or `str`
  value, and `experimental(value, label)` makes one show as experimental. `INT` and
  `FLOAT` need a `minimum`, a `maximum` and a `step`. The editor's spin boxes hold whole
  numbers from −2147483648 to 2147483647, so an `INT` needs whole bounds and a whole step
  in that range, and a `FLOAT` step needs at most 15 decimals (`1e-15` at the finest). A
  number moves to the nearest step from the minimum, so with a minimum of `0.5` and a step
  of `0.1` a profile's `2.25` becomes `2.3`. `BOOL` is a switch. The default must be a
  value the editor allows, on a step and of the parameter's type: `1.0` for a `FLOAT`,
  never `1`.
- **Kinds.** `Kind.FORMAT` is for a value a reveal needs again, as the mark is. Nothing in
  the file records it, so the app warns when a payload is hidden with a changed one.
  `Kind.EMBEDDER` changes only how a payload is hidden, and `Kind.GUIDANCE` is a rule or a
  warning.
- **Scope.** The group's scope names the methods of your extension it applies to, one or
  more. A parameter's `scopes` may narrow it further, for example to encrypted payloads
  with `Scope(encryption=Encryption.ON)`. Neither may name another method.
- **Check.** A group's `check` gets the group's values by parameter id and returns why they
  can't work together, or `None`. It must accept the defaults. If it raises, its exception
  is the problem the editor shows.
- **Texts and icon.** A label is 1 to 40 characters with something visible in it, no
  text may hold a control or format character, and an optional text such as a note is
  either empty or has something visible too. The icon is one of the app's, from
  `src/cipherstitch/gui/resources/icons/`, such as `flask` or `paragraph`.

The opener reads a value with `tuning.extension_value(key)`, as the example does with
`MARKS[str(tuning.extension_value("lines.mark"))]`. It gets the user's value in Research
mode, or the default. The tuning holds every parameter of the extension whose method opens
the carrier, and any other key raises `LookupError`, which the user sees as
`The extension x failed: …`. Never change a Format parameter's default once you have
hidden anything with it: files hidden at the old default would need the old value.

Some built-in parameters apply to an extension's methods too. Every method gets the key
derivation group when it encrypts, and the frame mask when it doesn't. An
`Order.KEYED_IF_ENCRYPTED` method gets `frame.order`. A method with
`codes_with_trellis=True` gets the STC group and `frame.rounds`, which reach its channel in
`tuning.coding`, so pass that on to `TrellisChannel`.

`ciphst extensions` lists each extension's keys. The CLI sets them through a profile
(`--profile`), and the app's *Export…* writes one. A profile with keys that no loaded
extension has still opens: hiding leaves them out and says an extension may be missing, and
revealing refuses it.

## Cost functions

Image LSB and DCT store a payload by moving values up or down by one, and a cost function
says what each move costs. The trellis codes then choose the cheapest moves that spell the
payload. HILL, WOW, MiPOD, J-UNIWARD and UERD are all cost functions, and most papers on
the subject contribute one. Yours becomes one more option of Research's *Cost function*:
`limg.cost` for image LSB, `dct.cost` for DCT, after the built-in ones and tagged
experimental. The rest stays the core's: the codes and their keyed order, wet costs on flat
colour, the ends of the range, a JPEG's polarization, a precover's side information and the
format.

A cost function only steers where the changes go, and a reveal reads the same values
whatever they cost. A payload hidden with one reveals without the extension, in Standard
mode too.

The example in `examples/extensions/variance_costs.py` costs a step of a colour value the
reciprocal of the variance around it. It is a toy that shows the shape of a cost function,
not a way to hide better than HILL:

```python
WINDOW = 3  # values across and down
FLOOR = 0.01  # added to the variance, so that flat colour costs 100 rather than infinity


def variance_costs(colors: npt.NDArray[np.uint8], rate: float) -> npt.NDArray[np.float32]:
    """The cost of a step of each value of ``colors`` (rows × columns × 3). The payload's
    ``rate`` doesn't change it."""
    values = colors.astype(np.int32)
    count = WINDOW * WINDOW
    sums = _window_sums(values)
    # count² times the variance, exact in integers, so it is never below zero
    spread = count * _window_sums(values * values) - sums * sums
    variance = spread.astype(np.float32) / (count * count)

    return np.reciprocal(variance + np.float32(FLOOR))


EXTENSION = Extension(
    costs=(CostSpec("local-variance", "Local variance", Domain.SPATIAL, variance_costs),),
    summary="An example cost function: image LSB by the local variance.",
)
```

`_window_sums` adds up each value's 3 × 3 window in its colour plane, the edges mirrored by
`np.pad(..., mode="symmetric")`. A `CostSpec` has an id, a label, a domain and the function:

| Domain           | Option of               | Called as             | Gets                                           | Returns                                   |
| ---------------- | ----------------------- | --------------------- | ---------------------------------------------- | ----------------------------------------- |
| `Domain.SPATIAL` | `limg.cost` (image LSB) | `costs(colors, rate)` | the picture's red, green and blue as `uint8`, rows × columns × 3, alpha left out | that shape: `result[y, x, c]` is what a step of `colors[y, x, c]` costs |
| `Domain.JPEG`    | `dct.cost` (DCT)        | `costs(cover)`        | the cover's `Coefficients`                     | the shape of `cover.ac`: `result[i]` is what a step of `cover.ac[i]` costs |

- **The coefficients.** `cover.ac` holds every AC coefficient: component after component
  (Y, then Cb and Cr), each component's blocks row by row, and in each block the 63 AC
  terms in row-major order, the DC term left out. `cover.components` gives each component's
  grid (`rows`, `columns`) and its 64 quantization steps in row-major order, the DC's
  first, and `cover.dc` each block's DC term in block order. For a RAW, PNG or TIFF cover
  they are those of the JPEG that DCT compresses it to.
- **The rate.** `rate` is the payload's message bits per value. Only a cost that depends on
  the payload's size reads it, as MiPOD's does.
- **Both ways alike.** Your cost holds for a step up and a step down alike. DCT tilts them
  itself, towards the Wiener estimate in a JPEG (`dct.pol`) and towards the uncompressed
  picture in a precover (`pre.si`).
- **What the core checks.** The inputs are read-only. Return float32, or float64, which the
  channel casts. A wrong shape, another type, NaN or a negative value fails the hide with
  `The extension x failed: its costs …`. Without that check, a NaN would end in a
  misleading capacity error or a crash deep in the codes. Values above the wet cost of
  10¹⁰, infinity included, become that cost, and such a value changes only where nothing
  else can.
- **Flat colour.** While `limg.wet` is on, as it is by default, flat colour stays wet
  whatever your costs. With it off, the Detection row warns about flat colour for your cost
  function, as for every built-in one but HILL.
- **Ids and labels.** An id follows the rule of method ids, and can't be a built-in option
  of its parameter: `hill`, `uniform`, `wow`, `s-uniward` and `mipod` for `limg.cost`,
  `j-uniward`, `uerd` and `uniform` for `dct.cost`. It must be unique among the cost
  functions of its domain, so one id may serve both. The label is what the editor shows, 1
  to 40 characters with something visible in it.
- **Speed.** The function runs over every value of the cover at each hide, and again when
  the codes retry near the capacity. A 12-megapixel photo has 36 million colour values,
  144 MB as float32, so keep it to vectorized numpy.

In the app, choose your cost function in the parameter editor's *LSB image* or *DCT* group.
The CLI sets it through a profile, such as `{"params": {"limg.cost": "local-variance"}}`
with `--profile`. `ciphst extensions` lists it as `cost local-variance · limg.cost`. Hiding
with a profile that names a cost function no loaded extension has is refused with
`… sets limg.cost to a cost function this CipherStitch doesn't know, 'x' (an extension that
isn't installed?)`, since it would otherwise go on with other costs than the profile asks
for. Revealing with that profile goes on without it, as a reveal reads the same values
whatever the costs were.

## Testing

`cipherstitch.extension_testing` checks an extension the way the loader and the CLI use it:

- `check_method(spec, cover_path, extension)` applies the loader's rules to `extension`,
  the `EXTENSION` that declares `spec`, then hides and reveals with `spec` through the
  service in Research mode, its parameters at their defaults: a plain payload, a payload
  encrypted with AES-256-GCM in every order the method allows, and a payload of exactly the
  most the cover holds, while one byte more must raise `CapacityError`. Revealing the
  untouched cover, and the payload with another method that can read such files, must find
  nothing. Without `extension`, the spec is checked alone, which suits a method without
  parameters.
- `check_cipher(spec)` runs the self-test and the rules, then hides and reveals with zero-width
  characters in a generated text: in every order, at exactly the most the text holds, and
  with a wrong password, which must find nothing.
- `check_cost(spec, cover_path)` applies the rules, then computes the costs of the cover's
  values and checks them as the channel does. Then it hides a payload encrypted with
  AES-256-GCM with the cost function, through image LSB in a PNG, BMP or TIFF cover or
  through DCT in a JPEG, RAW, PNG or TIFF one. The payload must reveal with the same
  settings, and in Standard mode, which never loads an extension.

They raise `AssertionError` with what failed, and load what they check on its own whatever
the folder holds. Put them in a pytest file next to the project's tests:

```python
from pathlib import Path

from cipherstitch import extension_testing
from cipherstitch.extensions.line_spaces import EXTENSION


def test_line_spaces(tmp_path: Path) -> None:
    cover = tmp_path / "poem.txt"
    lines = (f"Line {index} of the poem\n" for index in range(1200))
    cover.write_text("".join(lines), encoding="utf-8")
    extension_testing.check_method(EXTENSION.methods[0], cover, EXTENSION)
```

`tests/conftest.py` lowers the Argon2id cost for the tests. Elsewhere each encrypted round
trip takes about half a second.

`uv run mypy` checks the folder with the rest of `src/`. ruff follows `.gitignore`, which
leaves the folder out, so name it and turn that off:

```bash
uv run ruff check --no-respect-gitignore src/cipherstitch/extensions/
uv run ruff format --check --no-respect-gitignore src/cipherstitch/extensions/
```

If the files your extension writes must stay readable, keep one hidden file as a vector and
reveal it in a test that never regenerates it, as
`tests/test_container.py::test_the_extension_vectors_stay_readable` does for a fixture.

## Load errors

`ciphst extensions` prints each failure with every problem found and, for an exception,
its traceback, and exits with status 1. A Research hide or reveal prints one
`warning: extension x couldn't load: …` line for each. In Research mode the app shows
`Extension x couldn't load: …` under the Research mode note, and prints every problem and
the traceback on stderr once.

| Message                                                     | What to do                                                      |
| ----------------------------------------------------------- | --------------------------------------------------------------- |
| `it has no EXTENSION`                                       | export `EXTENSION = Extension(...)`                             |
| `EXTENSION must be an Extension, not a …`                   | build it with `Extension` from `cipherstitch.extensions`        |
| `ModuleNotFoundError: No module named 'x'`                  | install `x`, or import it inside functions and declare `requires` |
| `SystemExit: 2`                                             | the module exits while it is imported, as an argument parser does: run that code only under `if __name__ == "__main__":` |
| `… the id must be lower-case letters and digits, …`         | rename the entry, for example `my-method`                       |
| `… the id x is taken by CipherStitch`                       | choose an id no built-in has                                    |
| `… the wire id must be an int in 128–254`                   | choose a free wire id in that range                             |
| `… is also claimed by y`                                    | two extensions claim one id, wire id, parameter group id or cost function id: change one of them |
| `the … is declared N times`                                 | the module declares one id, wire id, group or parameter more than once: change all but one |
| `… .wav belongs to audio carriers, not text ones`           | use the suffix's own carrier type                               |
| `the suffix .x joins … carriers here, but … in y`           | two extensions put one new suffix in different carrier types   |
| `… the name must be 1–40 characters`                        | shorten the name, or write one: blanks alone don't count        |
| `… has a control or format character`                       | remove it from the text                                         |
| `… is only blanks`                                          | write the text, or leave it empty                               |
| `… requires must name a top-level module, such as scipy`    | name the module you import, not a submodule, in ASCII letters, digits and underscores |
| `… key_size must be an int in 16–64, …`                     | use a key of 16 to 64 bytes                                     |
| `… seal of N bytes returned …, not M bytes`                 | make `sealed_size` match `seal`                                 |
| `… unseal of a flipped bit returned data instead of raising DecryptionError` | use authenticated encryption                   |
| `… raised ValueError: … instead of DecryptionError`         | catch the library's error in `unseal` and raise `DecryptionError` |
| `… seal leaves the plaintext readable in its output`        | encrypt the data                                                |
| `… seal gives the same output for counters 0 and 1, …`      | put the counter in the nonce                                    |
| `parameter … the id must be lower-case letters and digits, starting with a letter, …` | name the group or parameter without hyphens or capitals |
| `… the scope names x, which isn't one of the extension's methods` | scope the group to your own methods                       |
| `… the scope must name one or more of the extension's methods` | name them, for example `Scope(frozenset({"my-method"}))`      |
| `… an option's value can't be a bool: …`                   | make the parameter a `ParamType.BOOL` switch, or give the options text values |
| `… the default is refused: …`, `… the default must be stored as …` | give a default the editor allows, on a step and in the parameter's type |
| `… a whole number's minimum, maximum and step must be whole numbers …` | keep an `INT`'s bounds and step whole and within the editor's range |
| `… the step needs more than 15 decimals`                    | use a coarser step, or rescale the parameter                    |
| `… the icon must name one of the app's, …`                 | choose an icon from `src/cipherstitch/gui/resources/icons/`     |
| `… the check refuses the defaults: …`                       | make the group's check accept its defaults                      |
| `cost function … the domain must be a Domain`               | use `Domain.SPATIAL` or `Domain.JPEG`                            |
| `cost function … costs must be callable`                    | pass the function itself, not its result                        |
| `The extension x failed: its costs …`                       | not a load error: the cost function returned a wrong shape, another type, NaN or a negative value while hiding |
| `The extension x failed: TypeError: …`                      | not a load error: the extension's code failed while hiding or revealing. The traceback follows on stderr |

## Trust and limits

- **Your rights.** An extension is ordinary Python code that runs with your rights, with
  no sandbox. Read one before you copy it in.
- **One folder.** The loader reads only the package's `extensions` folder, which
  `ciphst extensions` names. It never looks in your home folder, in environment variables
  or in settings.
- **Standard mode never imports it.** A broken or slow extension can't touch Standard mode,
  and its output stays the same.
- **The guards' reach.** Every call into an extension is guarded: another exception becomes
  `The extension x failed: …`, and results that break the contract are refused. Nothing can
  stop code that hangs, runs out of memory, crashes the interpreter or patches CipherStitch
  when it is imported. Don't patch core modules.
- **Loading time.** A slow import delays the first Research command by that long, and holds
  the app's window when Research mode first turns on. Import heavy libraries inside the
  functions that need them.
- **No measurements.** CipherStitch hasn't measured how detectable any extension is, and an
  extension's cipher has had no review.
- **An installed copy.** An installed CipherStitch loads from the `cipherstitch/extensions`
  folder of its installation, and an upgrade deletes what you put there. Use a clone with
  `uv sync`.
- **No stability promise.** The contract is experimental and may change in any release.
  A hidden file stays readable as long as the extension keeps its wire ids and its channel.

## Moving an extension into the core

- The method or cipher keeps its wire id, even above 127, so the files it wrote stay
  readable. A cipher also keeps its key info, `cipherstitch extension cipher <wire id>`,
  which `container.cipher_key_info` then has to return for it.
- Follow the built-in steps in [Architecture](architecture.md#extending) for everything
  else: the catalog entry, the registry, `risks.py`, the docs and a format vector.
- A tier above Experimental needs a detectability round, as the built-in methods had (see
  [Detectability](detectability.md)).
