# User guide

CipherStitch can be used three ways: the `ciphst` command, the `ciphst-gui` desktop
app, and the `cipherstitch` Python package. All three share the same options and produce
the same files.

## Command line

```text
ciphst hide CARRIER FILE -o OUTPUT [--research] [-e] [--carrier KIND] [--method METHOD]
            [--cipher CIPHER] [--profile PROFILE]
ciphst reveal CARRIER [-d DIR] [-e] [--research --method METHOD --cipher CIPHER]
              [--profile PROFILE]
ciphst capacity CARRIER [--research]
ciphst extensions
ciphst --version
```

### `hide`

Hides `FILE` in `CARRIER` and saves the result to `OUTPUT`, in
[Standard mode](#standard-and-research-mode) unless `--research` is given.

| Option                 | Meaning                                                                 |
| ---------------------- | ----------------------------------------------------------------------- |
| `-o`, `--output`       | where to save the result (required), replacing any existing file        |
| `--research`           | [Research mode](#standard-and-research-mode): every carrier type, method and cipher |
| `-e`, `--encrypt`      | with `--research`, ask for a password and encrypt with AES-256-GCM. Standard mode always does |
| `--cipher CIPHER`      | the cipher (any cipher but `none` implies `--encrypt`)                  |
| `--method METHOD`      | the embedding method                                                    |
| `--carrier KIND`       | `image`, `audio`, `document` or `text`, checked against the extension   |
| `--profile PROFILE`    | Research parameters from a [profile](#profiles), and its method and cipher unless given. Implies `--research` |

Ciphers: `aes-256-gcm`, `chacha20-poly1305`, `xchacha20-poly1305`, `aes-256-cbc-hmac`,
`fernet`, `none`. Methods: `lsb`, `dct`, `audio-lsb`, `phase-coding`, `echo-hiding`,
`object-stream`, `whitespace`, `zero-width`, and in Research mode the Experimental
`lsb-replacement` (see [Embedding methods](methods.md)). Research mode also takes the ids of
the [extensions](extensions.md) it loads, which `ciphst extensions` lists. Without
`--research`, any other id fails with
`Unknown method x. Extension methods run only in Research mode (--research)`, and an unknown
one in Research mode names the choices.

- **Default method:** the carrier type's first available method that the mode allows for
  the cover and that can write the output's format. A PNG or TIFF with `-o out.jpg` picks
  *DCT coefficients*, which compresses it to JPEG first, and a camera RAW (DNG, CR2, NEF,
  ARW and others, with the `raw` extra) always does. A JPEG cover gets *DCT coefficients*
  and must be saved as `.jpg` or `.jpeg`, because both modes refuse it for LSB.
- **Output format:** image LSB and sequential LSB replacement write `.png`, `.bmp`, `.tif`
  or `.tiff`, and DCT writes `.jpg` or `.jpeg`. Every other method keeps the carrier's
  extension.
- **Checks first:** the options, the output format and, in Standard mode, the advised size
  are checked before you are asked for the password. You type the password twice, and it
  must not be empty. Its strength is checked once you have typed it.
- **Without encryption** (`--research` without `-e`) the command prints
  `warning: without --encrypt anyone who finds the payload can read it`.
- **An option Standard mode turns off** fails with `Off in Standard mode …`. When Research
  mode's default parameters allow it, a hint names the flags to add: `--research`, and
  `-e` to keep the encryption Standard mode would have used.
- **Detection risks**, such as a payload larger than the size advised for the carrier or
  LSB on a JPEG cover, are printed as `warning: …` lines before you are asked for the
  password. They never stop hiding (see
  [Staying inconspicuous](security.md#staying-inconspicuous)).
- **DOCX printer settings:** `object-stream` stores the payload as the document's
  printer settings, in place of the ones it had, or in a custom property with Research
  mode's `obj.loc`. A write with either document method removes what an earlier one left,
  except Research mode's space and tab padding (see
  [Embedding methods](methods.md#embedded-object-stream-pdf-docx)).

```bash
ciphst hide cover.png report.pdf -o cover-2.png
ciphst hide IMG_0042.CR2 report.pdf -o photo.jpg
ciphst hide scan.png report.pdf -o scan.jpg
ciphst hide essay.md key.txt -o essay-2.md --research -e
ciphst hide song.wav key.txt -o song-2.wav --research --method echo-hiding --cipher chacha20-poly1305
ciphst hide cover.png notes.txt -o out.png --research --method lsb --cipher none
```

### `reveal`

Extracts the hidden file from `CARRIER` into a directory, under its original name.

| Option               | Meaning                                                  |
| -------------------- | -------------------------------------------------------- |
| `-d`, `--output-dir` | where to save the file (default: the current directory)  |
| `-e`, `--encrypted`  | ask for the password used to hide                        |
| `--research`         | try only `--method` and `--cipher`, which it needs        |
| `--method METHOD`    | with `--research`, the method used to hide               |
| `--cipher CIPHER`    | with `--research`, the cipher used to hide. Any but `none` asks for the password |
| `--profile PROFILE`  | the [profile](#profiles) used to hide: its parameters, and its method and cipher unless given. Implies `--research` |

- Without `--research`, the method and the cipher are detected, so only the password is
  needed (see [Standard and Research mode](#standard-and-research-mode)).
- When nothing is revealed, the error is the same whatever the cause. A hint follows: add
  `-e` if the payload was encrypted, or reveal with `--research` and the same `--method`
  and `--cipher` if it was hidden in Research mode. After a Research reveal, the hint says
  that the method, cipher and parameters must be the ones used for hiding, and that
  `--profile` gives parameters other than the defaults. A method the file type may hold
  that needs a missing extra adds its install hint to the error.
- The directory given with `-d` is made if it does not exist.
- Revealing never overwrites a file. It fails with `… already exists in …` instead.
- If you give a password but the payload was not encrypted, a warning says that the
  password was not used and that nothing proves who hid the file or that it is unchanged.

```bash
ciphst reveal cover-2.png -d ./out -e
ciphst reveal song-2.wav --research --method echo-hiding --cipher chacha20-poly1305
ciphst reveal photo-2.png --profile fast.csprofile
```

### `capacity`

Prints how many bytes the carrier has room for in each mode, on one line. The 16-byte
nonce or salt, the frame, the file name and any encryption overhead come out of that
room, so the file itself must be a little smaller:

- **Standard mode:** the advised size, which is Standard mode's limit, with its own
  default method for the carrier (*DCT* for a JPEG or a camera RAW).
- **Research mode:** the capacity, with its default method and parameters.

When the two modes pick different methods, each number names its own. When Standard mode
can't use the carrier, its reason takes the place of its number. Research mode's number
counts the built-in methods only. `--research` lets an extension's method count too, for a
file only an extension takes, and loads the extensions to find it.

```text
$ ciphst capacity cover.png
LSB: 76 bytes in Standard mode (the advised size), 633 bytes in Research mode
$ ciphst capacity notes.md
Zero-width characters: 2700 bytes in Research mode. Off in Standard mode: no recommended method for this carrier
```

The first is a 60 × 40 picture, and another picture of that size gives nearly the same
numbers (about 630 bytes in Research mode). A 512 × 512 photo is advised about 8 400
bytes of its 76 000 or so.

### `extensions`

Lists the [extensions](extensions.md) in the package's `extensions` folder
(`src/cipherstitch/extensions/` in a checkout), which only Research mode loads: each module
with its summary, then each method's id, wire id, carrier type and suffixes, each cipher's
id and wire id, each cost function's id and the parameter it is an option of, and the keys
of its parameters, which a [profile](#profiles) sets. An entry whose dependency is missing
says so, with the install hint. Every extension that couldn't load follows on stderr, with
every problem found and the traceback of an exception, and the exit status is then 1.

```text
$ ciphst extensions
line_spaces: An example method: one bit per line of a text file.
  method line-spaces · wire id 128 · text: .txt, .md
  parameters lines.mark
ocb_cipher: An example cipher: AES-256-OCB3 from cryptography.
  cipher aes-256-ocb3 · wire id 128
variance_costs: An example cost function: image LSB by the local variance.
  cost local-variance · limg.cost
```

A Research hide or reveal prints one `warning: extension x couldn't load: …` line for each
extension that failed, then goes on without it.

### Exit status

| Code | Meaning                                                          |
| ---- | ---------------------------------------------------------------- |
| 0    | success                                                          |
| 1    | the operation failed, and the reason is printed as `error: …`. For `extensions`, an extension couldn't load |
| 2    | invalid arguments                                                |
| 130  | interrupted with Ctrl+C                                          |

## Desktop app

Start it with `ciphst-gui` (needs the `gui` extra).

**Hide**

1. **Carrier:** choose the carrier type, then the file. A camera RAW, or a PNG or TIFF
   for *DCT coefficients*, is first compressed to JPEG to measure its capacity. On a large
   picture that takes a few seconds in the background. The footer says so meanwhile, and
   **Hide payload** waits for it.
2. **Method:** the picker shows each method's tier, pros, cons and capacity. If the
   method shown cannot use the chosen file, the first one that can takes its place, so a
   camera RAW gets *DCT coefficients*.
3. **Payload:** a file, or text typed in place (hidden as `message.txt`). The capacity
   meter shows how much room the payload takes, overhead included.
4. **Encryption** and **Password:** the strength meter is a rough guide. Repeat the
   password to confirm it.
5. **Save to:** by default the cover's own name in your Pictures, Music or Documents
   folder, with `-2`, `-3` and so on when that name is taken. The format next to it is the
   cover's own when the method can write it. Image LSB and LSB replacement can also write
   PNG, BMP or TIFF, so they offer that choice. The other methods write one format only,
   which is shown but cannot be changed. A file name typed in the save dialog with another
   extension, or none, gets the format's own.

In the Method and Encryption pickers, options that cannot work with the chosen carrier
file or the other choices are greyed out and cannot be picked. Hover over one to see why.

A **Detection** row appears under the payload when a choice makes the payload easier to
detect. That happens, for example, with a payload larger than the size advised for the
carrier (the capacity bar marks that size and turns lighter past it), with LSB on a JPEG
cover or on one so flat that some changes may land in flat colour, or with a document
method that anyone can see by inspecting the file. The row is only advice, and hiding
still works (see [Staying inconspicuous](security.md#staying-inconspicuous)).

The **Hide payload** button stays disabled, with a note in the footer, while the options
cannot work: an extra is missing, the method cannot use the carrier, the payload is too
large or the passwords do not match.

**Reveal**

1. **File:** choose a file produced by Hide.
2. **Password:** leave it empty if the file was hidden without encryption.
3. **Save to:** your Downloads folder by default.

Reveal tries every method and cipher the file type can hold, at Standard's values, except
the Experimental methods. When nothing is revealed, the error is the same whatever the
cause. A hint follows. It asks for the password if none was typed, and says that a file
hidden in Research mode needs Research mode and the same method, encryption and
parameters.

The result card shows the revealed file, *integrity verified* for encrypted payloads, and
a button that shows it in Finder (or opens its folder on other systems).

**Drag and drop.** Drop a file on the window and choose a zone: *Use as carrier* or *Hide
this file*.

**Research mode.** The app starts in [Standard mode](#standard-and-research-mode): the
Carrier, Method and Encryption fields list only what it offers, the capacity bar marks
the advised size as the limit, and Hide stays disabled until the payload and the password
meet the rules. A note under the password says from the start that it must be strong.

The *Research mode* switch at the top right, on both pages, lists every carrier type,
method and cipher. The password note then shows only once a password is typed. The window
title says *CipherStitch — Research* while it is on. The app doesn't remember the switch:
every launch starts in Standard mode (see
[What the app keeps](security.md#what-the-app-keeps)).
Switching it off moves the carrier, method and cipher to ones Standard mode offers, and
switching it on keeps them.

In Research mode, a **Parameters** row before *Save to* sums up the parameters that differ
from Standard's for the chosen method, or says *Standard values*. When a parameter that
changes the stored format differs, a warning says so: nothing in the file records it, so
revealing needs the same values. *Load profile…* takes the parameters of a
[profile](#profiles) and shows its name until you change a value. *Clear* then puts
Standard's values back.

**Edit…** opens the parameter editor over the window:

- The groups the chosen method and encryption read are listed on the left, each with its
  number of parameters or of changes.
- Each parameter shows its kind, the constant it sets, what changing it does and, where
  there is one, a measured result. *Format* means revealing needs the same value.
  *Embedder* affects hiding only. *Guidance* is a rule, not part of the file.
- A row's reset button, or *Reset group*, puts Standard's values back. The menu at the top
  offers presets: *Standard values*, *Fast STC (h = 4)* and *P-0 · no side information*,
  and the last eight profiles you loaded or exported since the app started, each where the
  listed groups have
  its parameters. A preset sets only the parameters listed, so changes made for another
  method stay.
- *Export…* saves the listed parameters, the method and the encryption as a
  [profile](#profiles).
- Values that cannot work together are refused with the reason, and the row shows its
  last value again.
- *Done* or Esc closes the editor and hands the values to the page that opened it. They
  last until the app quits, also while Research mode is off, which ignores them. The app
  does not save them.

In Research mode, Reveal asks for what the hiding used, since files carry no settings:
**Method** (the methods that can hide in the chosen file's type, each with its tier),
**Encryption** and **Parameters**. The Parameters row and its editor work as on Hide, with
values of their own, so revealing someone's file does not change what you hide with. With
*No encryption* the password is not needed. *Load profile…* also sets the method and the
encryption the profile names. Reveal then tries only those choices, and its error hint says
to check them.

Opening a profile with the app (`ciphst-gui fast.csprofile`) shows Reveal in Research mode
with the profile loaded.

**Extensions.** Research mode also lists the methods and ciphers of the
[extensions](extensions.md), after the built-in ones and tagged *Experimental*. They are in
the Method and Encryption pickers, whose detail ends with *From the extension* and the
extension's name, and in Reveal's Method and Encryption lists. An extension's method may
have parameters of its own, which the parameter editor shows with the built-in ones that
apply to it. An extension's cost functions follow the built-in ones in the *Cost function*
choices of the *LSB image* and *DCT* groups. A file whose suffix only an extension takes
can be chosen or dropped as a carrier, and the file dialogs and the drop zone list that
suffix. Standard mode drops such a file when you switch to it.

The app loads the extensions the first time you turn Research mode on. A slow extension holds the window for that long, and a changed one needs a
restart. Each extension that couldn't load gets a line under the Research mode note on
both pages, for example *Extension x couldn't load: ModuleNotFoundError: No module named
'scipy'*. Every problem and the traceback go once to stderr, the terminal you started the
app from. When an extension's code fails while hiding or revealing, the footer shows *The
extension x failed: …* and the traceback goes to stderr too.

**No traces.** The app saves no settings and no history. It starts in Standard mode every
time, and it forgets the parameters and the profiles used lately when it quits. Its file
dialogs leave the folder history Qt keeps as it was. [What the app
keeps](security.md#what-the-app-keeps) has the details and what the operating system may
still record.

**Progress and Cancel.** Each step is shown while an operation runs. Cancel returns to the
form at once. The operation stops at its next step and saves nothing.

## Standard and Research mode

**Standard mode** is the default on the command line and in the Python API. It keeps
only the safest choices and runs every method with its tested defaults. It lowers the risk
of detection, but it does not make hiding undetectable: statistical steganalysis can still
find a payload, only less often.

- **Recommended only:** carriers, methods and ciphers of the *Recommended* tier. Documents
  and text are off. So is *No encryption*, which means a password is required. That
  leaves the methods `lsb`, `dct` and `audio-lsb`, and the ciphers
  `aes-256-gcm`, `chacha20-poly1305` and `xchacha20-poly1305`.
- **Covers that change by the hidden bits alone:**
  - Image LSB needs an 8-bit RGB or RGBA PNG, BMP or TIFF that it does not have to
    convert. That rules out JPEG, WebP and JPEG-compressed TIFF covers, greyscale,
    palette, CMYK and 1-bit images, 16 bits per channel or 15–16 bits per pixel, and any
    colour that stands for transparency.
  - Image LSB also refuses a cover so flat that even a payload of the advised size could
    change some of its flat colour, meaning runs of 16 or more equal values in a row, as
    in blown-out sky, screenshots or graphics. LSB changes stand out there. Only a cover
    that is nearly all flat is refused: a screenshot that is three-quarters flat passes.
  - Audio LSB needs 16- or 24-bit samples, and refuses a recording so silent that even a
    payload of the advised size could change some of its digital silence.
  - DCT accepts every JPEG it can read, because it leaves flat areas untouched. It also
    accepts a RAW, PNG or TIFF that it compresses itself, except a JPEG-compressed TIFF.
- **No more than the advised size,** the one the Detection row gives for the carrier (see
  [Staying inconspicuous](security.md#staying-inconspicuous)).
- **A strong password:** "Strong" or better on the app's strength meter, which
  [estimates](security.md#choosing-a-password) at least 10⁸ guesses. Common passwords
  with a digit and a symbol added, such as `Password1!`, stay "Weak". So does
  `пароль123`, because the meter counts each word in another script, such as Ukrainian,
  as a common English word. A few uncommon words pass.

The format rules are read from the file's header. Finding flat colour or digital silence
decodes the whole carrier, once per version of the file.

**Research mode** (`--research`) is for studying the methods. It offers every carrier,
method and cipher, payloads up to the capacity, and parameters that change how the
methods work. Its output may be easier to detect, so it is not for real secrets.

- It offers the methods and ciphers of the [extensions](extensions.md) too, after the
  built-in ones and tagged *Experimental*. Standard mode never loads them, and its reveal
  never tries them, so a file hidden with one reveals only in Research mode, naming its
  method and cipher, and only while the extension is there.
- Its cover rules and password minimum are parameters that start at Standard's:
  `limg.lossy` (JPEG, WebP, greyscale and palette covers for image LSB, off), `laud.depth`
  (the lowest audio depth, 16 bits) and `crypto.minpw` (Strong, checked only when
  encrypting). A nearly all-flat cover and a JPEG-compressed TIFF for DCT are only
  warnings in Research mode.
- The parameters are not stored in the file. Some of them change the stored format, and a
  payload hidden with them reveals only with the same values.

**Revealing.** Standard mode tries every method and cipher the file type can hold, at the
default parameters, except the Experimental methods. It reveals anything hidden in
Standard mode, and anything hidden in Research mode with a method that is not Experimental
and without changing a parameter. Research mode tries only the method, cipher and
parameters you give, and every unit order, so `frame.order` never has to match. Either
way, a failure gives one error that does not say why, because without the right password
CipherStitch can't tell a wrong password from a changed file or one with nothing hidden.

```bash
ciphst hide photo.png report.pdf -o photo-2.png
ciphst hide photo.png notes.txt -o photo-3.png --research --cipher none
ciphst reveal photo-3.png --research --method lsb --cipher none
```

## Profiles

A hidden file never carries its settings, since anything in it would show that a payload is
there. A payload hidden with changed parameters that change the stored format reveals only
with the same values. A profile keeps them: a `.csprofile` file, UTF-8 JSON, that the app
exports from the parameter editor and that `--profile` reads.

```json
{
  "format": "cipherstitch-profile",
  "version": 1,
  "name": "Fast STC",
  "created": "2026-11-02T15:40:00Z",
  "app": "CipherStitch 0.1.0",
  "defaults": 1,
  "method": "lsb",
  "cipher": "aes-256-gcm",
  "params": { "stc.h": 4, "stc.kept": 6 }
}
```

- `params` holds only the values that differ from Standard's, by their `group.param` key
  as the editor shows it. `defaults` numbers the built-in defaults they differ from. If a
  later release changes a default, it keeps the old ones under their number, so an old
  profile still gives the same settings.
- `method` and `cipher` are optional. Reveal starts from them, and `--method` and
  `--cipher` override them. They may name an extension's entries. A profile that names one
  this CipherStitch doesn't know is refused with
  `… doesn't know, 'x' (an extension that isn't installed?)`.
- `name` and `note` are optional, and the app shows them as they are: a name of at most 80
  characters, a note of at most 2000, neither with a control or format character. A
  profile that repeats a key is refused rather than read with the last value.
- A profile never holds a password, a key, a salt or a nonce. It does name the method,
  cipher and parameters a file was hidden with, so keep it apart from the files if that
  matters.
- A profile from a newer CipherStitch is refused with the advice to update. Parameters this
  version doesn't know are left out when hiding, with a warning. Revealing refuses them,
  because it would find nothing without them. An extension's parameters are known while it
  is loaded. For a key in a group no built-in has, the warning and the refusal add that an
  extension may be missing. Hiding refuses a cost function no loaded extension has, with
  `… to a cost function this CipherStitch doesn't know, 'x' (an extension that isn't
  installed?)`. Revealing doesn't need it and goes on.

## Python API

```python
from pathlib import Path

from cipherstitch import capacity, hide_file, reveal_file

print(capacity("cover.png"), capacity("cover.png", research=True))
password = "quiet harbour 47 lanterns"
hide_file("cover.png", "report.pdf", Path("cover-2.png"), password=password)
revealed = reveal_file("cover-2.png", Path("out"), password=password)
```

Paths may be strings or `pathlib.Path` objects.

- `hide_file(carrier_path, secret_path, output_path, password=None, *, research=False)`
  hides in Standard mode, which encrypts with AES-256-GCM and needs a strong password.
  With `research=True`, it encrypts when a password is given and stores a masked plain
  frame otherwise (see [Stored format](format.md#unencrypted)). The password must still
  be strong, as Research mode's `crypto.minpw` parameter asks by default. Pass other
  settings to `service.hide_options` to lower it. The method is chosen as on
  the command line.
- `reveal_file(carrier_path, output_dir, password=None)` saves the file in `output_dir`,
  made if missing, and returns its path.
- `capacity(carrier_path, *, research=False)` is a number `ciphst capacity` prints:
  Standard mode's advised size, or with `research=True` the capacity. The nonce or salt,
  the frame, the file name and the encryption overhead come out of it, so the file must
  be a little smaller (see [`capacity`](#capacity)). When Standard mode can't use the
  carrier, it raises `NotAvailableError` with the reason.

A carrier, payload or folder that can't be read or written raises the usual `OSError`,
such as `FileNotFoundError` or `PermissionError`. Every other error derives from
`cipherstitch.CipherStitchError`. The package also exports
`CapacityError`, `NoHiddenDataError`, `NotAvailableError` (an option, carrier or cover the
mode turns off, or a missing extra), `UnsupportedFormatError` and `InvalidFileNameError`.

For full control (method, cipher, progress, detection, mode, parameters), use
`cipherstitch.service`, the API the CLI and the app are built on. `service.hide_options`
and `service.HideOptions` default to [Standard mode](#standard-and-research-mode), and
`service.hide` then enforces every rule. Pass `is_research=True` for Research mode, and
`settings` (a `cipherstitch.params.settings.Settings`) to change its parameters, which
`service.hide_options` then checks the method and the cover against.
`service.reveal` takes a `service.ResearchReveal(method, cipher, settings)` to reveal in
Research mode.

```python
from dataclasses import replace
from pathlib import Path

from cipherstitch import service
from cipherstitch.catalog import CipherId, MethodId
from cipherstitch.params.settings import Settings
from cipherstitch.payload import HiddenFile

carrier, output = Path("song.wav"), Path("song-2.wav")
method, cipher = MethodId.ECHO_HIDING, CipherId.CHACHA20
settings = Settings.of({"echo.seg": 40})  # it changes the stored format: reveal needs it too
options = service.hide_options(
    carrier, output, cipher, method=method, is_research=True, settings=settings
)
options = replace(options, password="quiet harbour 47 lanterns")
service.hide(carrier, HiddenFile("key.txt", b"..."), output, options)

research = service.ResearchReveal(method, cipher, settings)
result = service.reveal(output, Path("out"), "quiet harbour 47 lanterns", research=research)
print(result.path, result.method, result.cipher)
```

## Troubleshooting

| Message                                                       | What to do                                                      |
| ------------------------------------------------------------- | --------------------------------------------------------------- |
| `… needs the X extra, see Installation in README.md`          | install the extra (see [Installation](../README.md#installation)) |
| `Couldn't reveal anything. …`                                 | check the password and `-e`, and for Research mode the method, cipher and parameters. The carrier may also have been re-compressed, converted or edited |
| `Payload is N bytes, this carrier can hide at most M`         | use a larger carrier, a higher-capacity method or a smaller payload |
| `… is too small to hide a file with …`                        | use a larger carrier or a higher-capacity method                |
| `… can't hold this payload reliably with …`                   | try another method or carrier. For phase coding and echo hiding, try a louder recording |
| `Output must be one of: …`                                    | give the output one of the listed extensions                    |
| `Off in Standard mode …`                                      | choose an allowed option or cover, or use Research mode        |
| `Off in the Research parameters …`                            | choose another cover, or allow it with the `limg.lossy` or `laud.depth` parameter |
| `Payload is … over the advised … for this carrier`            | use a larger carrier or a smaller payload                       |
| `Standard mode needs a strong password …`                     | use a few uncommon words or a longer random string              |
| `… already contains zero-width characters`                    | use another text as the carrier                                 |
| `… uses an unsupported package layout`                        | save the DOCX again from a word processor, or use `--method whitespace` |
| `… already exists in …`                                       | reveal into another directory, or move the existing file        |
| `… is too large to read`, `… is too long to read`             | use a smaller carrier. Pictures are read up to about 89 megapixels and recordings up to about 3 hours of stereo |
| `… decodes to far more than a recording would`                | the FLAC is nearly all silence, which holds nothing. Use a louder recording |
| `Hidden data is truncated`                                    | the carrier lost the end of the payload, for example when it was cut or edited |
| `Unknown method x. Extension methods run only in Research mode` | add `--research` to use an extension's method or cipher      |
| `warning: extension x couldn't load: …`, in the app `Extension x couldn't load: …` | run `ciphst extensions` for every problem and the traceback, then see [Load errors](extensions.md#load-errors) |
| `The extension x failed: …`                                   | the extension's code failed. The traceback follows on stderr for its author |
