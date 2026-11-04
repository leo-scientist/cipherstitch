# Security model

CipherStitch combines two things with different guarantees: **encryption**, which protects
what is hidden, and **steganography**, which makes it less obvious *that* something is
hidden. Only the first one is a cryptographic guarantee.

## What encryption protects

With a password, the hidden file is sealed with an authenticated cipher under keys
derived from the password:

- **Confidentiality.** The file's name, size and contents are encrypted. Only the salt is
  stored in the clear, and it is random. Research mode's public or sequential order shows
  the size (see [What it does not protect](#what-it-does-not-protect)).
- **No readable marker.** An encrypted payload has no magic bytes or header in the clear.
  With image and audio LSB, DCT, the padded document object and padded whitespace, the
  stored bits look random and do not show where the payload ends, unless Research mode
  puts the payload in a public or sequential order. Zero-width characters look random
  too, but their number shows the payload's size. The header names the cipher and the
  method, so revealing needs only the password.
- **Integrity.** Any change to the sealed blocks fails authentication. A payload changed
  after hiding is reported as "no hidden data" rather than revealed with damage.
- **No oracle.** "No hidden data" and "wrong password" are the same error, with the same
  message: "Couldn't reveal anything". In Research mode, so is a method, cipher or
  parameter other than the ones used for hiding.

## What it does not protect

- **The fact of hiding.** Statistical steganalysis can detect LSB, DCT and audio
  embedding, especially when a large part of the capacity is used. We measured it on
  512 × 512 photos (see [Detectability](detectability.md)). Popular tools found nothing in
  either image method. A rich-model detector trained on CipherStitch's own output had an
  error rate of 0.47 and 0.37 for image LSB with payloads of about 6% and 25% of the
  capacity, and 0.46 and 0.39 for DCT on a JPEG at 5% and 20%. An error rate of 0.5 is a
  coin toss. Larger photos at the same fill are easier to detect. Keep payloads to the size
  the app advises (see
  [Staying inconspicuous](#staying-inconspicuous)).
- **Document and text methods.** An extra object in a PDF or DOCX, unusual trailing
  whitespace or zero-width characters are visible to anyone who inspects the file. The
  object sits where applications keep their own data (a PDF's page-piece dictionary, a
  DOCX's printer settings) and is padded to a random length, but it holds no real
  application data. Whitespace is padded to a random length too, but the number of
  zero-width characters shows the payload's size. Research mode's `obj.loc` parameter can
  put the object in the document's metadata instead, where anyone can see it without
  inspecting the file. Word lists custom properties with the document's properties, and
  PDF readers such as Acrobat show XMP metadata.
- **Error-corrected audio.** Phase coding and echo hiding frame the payload in
  Reed–Solomon codewords. Anyone who knows the method can extract the bits and check
  whether they decode, which shows the payload and its size to within 223 bytes.
- **Comparison with the original.** Anyone who has the original carrier sees which bits
  changed.
- **Unencrypted payloads.** Without a password the payload is masked with a public
  keystream and a random nonce. Tools that do not know the format see random bytes, but
  anyone who knows it can detect and read the payload, and nothing proves that it is
  unchanged. The CLI warns about this, and the app shows *integrity verified* only for
  encrypted payloads. Research mode's `frame.mask` parameter can turn the mask off. Then
  anyone who reads the carrier's bits sees the `CPST` signature and the payload as it is.
- **A public or sequential order.** With a password, the values that hold the payload
  follow from the key, so a detector cannot tell which ones to look at. Research mode's
  `frame.order` parameter can put an encrypted payload in the public order or in sequence
  instead. Then anyone can work out which values hold it and test exactly those. With
  image LSB, DCT and audio LSB, anyone who knows the format can also read the length
  stored in front of the sealed blocks, which gives the payload's exact size. With
  `stc.spread` sequential too, the payload fills the carrier from its start, so where the
  changes stop shows its size as well.
- **Lossy processing.** Re-compressing, resizing or converting the carrier destroys the
  payload (see [Embedding methods](methods.md)). This is a loss of data, not of secrecy.

## Staying inconspicuous

Some choices make a payload much easier to detect. CipherStitch names each one. The app
lists them in a *Detection* row on the Hide page, and `ciphst hide` prints each as a
`warning: …` line before it asks for the password.
[Standard mode](user-guide.md#standard-and-research-mode), the default, applies these
rules for you. It refuses everything below except one: it lets DCT rewrite progressive
JPEGs and JPEGs with restart markers. A payload hidden in Standard mode is harder to
find, but not impossible to find. Research mode refuses only what its cover parameters
rule out, and warns about the rest.

- **A payload larger than advised** (image LSB, DCT, audio LSB). More changed values give
  statistical steganalysis more to measure. By the square-root law, the payload a cover
  hides equally well grows with the square root of the cover's size (its non-flat values,
  or a JPEG's non-zero coefficients), so a larger cover is advised a smaller share of its
  capacity. For image LSB that is about 11% of a 512 × 512 PNG's capacity and 1.6% of a
  12-megapixel one's, for DCT about 7% of a typical 512 × 512 JPEG's at quality 75, and
  for DCT from a camera RAW, PNG or TIFF about 17%, since its side information hides the
  changes better. At that size a detector trained on CipherStitch's output is wrong about
  45% of the time ([round 3](../benchmarks/detectability/results/round-3/protocol.md)).
  Smaller covers were easier to detect than the law predicts, so a cover smaller than
  512 × 512 is advised the same share as one of that size. Audio LSB borrows image LSB's
  rate, which nothing measured. The Detection row and `ciphst capacity` give the advised
  size in bytes for the carrier, and the app's capacity bar marks it and turns lighter past
  it.
- **Image LSB on a JPEG or WebP cover.** Pixels decoded from a lossy format keep the
  traces of its compression, and LSB changes break them. Use DCT coefficients on the
  JPEG, or a cover that was never lossy-compressed.
- **Image LSB on a greyscale or palette cover.** The result is saved in full colour, and
  the changes tint grey pixels. Use a colour cover.
- **Flat colour or digital silence** (image and audio LSB): runs of 16 or more equal
  values, as in blown-out sky, screenshots, graphics or silent passages. A ±1 change
  stands out where nothing else varies, so LSB changes other values instead wherever it
  can. With a large payload or a nearly flat cover it cannot always, and the Detection row
  says so. Use a smaller payload, a noisier cover, or
  DCT on a JPEG, which leaves flat areas untouched. Finding these runs decodes the whole
  carrier, once per version of the file.
- **DCT on a progressive JPEG or a JPEG with restart markers.** The result is always a
  baseline JPEG without restart markers, because jpeglib can write neither. Next to other
  pictures from the same camera, app or website, it stands out. The rest of the header,
  from the EXIF data to the kind of Huffman tables, stays as it was (see
  [DCT coefficients](methods.md#dct-coefficients-jpeg)).
- **DCT on a JPEG-compressed TIFF.** DCT compresses a RAW, PNG or TIFF to JPEG itself, and
  a TIFF that was JPEG-compressed before keeps the traces of that first compression next
  to the second. Use a RAW, or a PNG or TIFF that was never lossy-compressed. A PNG that
  was once a JPEG cannot be told from its header, so it is not flagged. The result has no
  EXIF, so it looks like a picture saved by an editor or a website, not one straight from
  a camera. Before anything is hidden in it, the JPEG has exactly the coefficients of
  Pillow's JPEG of the same picture, and the
  [side information](methods.md#from-a-raw-png-or-tiff) makes a payload harder to detect
  than the same share of any JPEG cover measured (see
  [Detectability](detectability.md#dct-from-a-precover)). DCT on an existing JPEG keeps
  that JPEG's own compression.
- **Document and text methods, phase coding and echo hiding** are found by inspection,
  as described above.

## Cryptography

| Part            | Choice                                                                 |
| --------------- | ---------------------------------------------------------------------- |
| Key derivation  | Argon2id, 256 MiB, 6 iterations, 4 lanes (about 0.2 s on an Apple M3 Pro) |
| Subkeys         | HKDF-SHA256, one per cipher and one for the keyed order                |
| Salt            | 16 random bytes, fresh for every embedding                             |
| Default cipher  | AES-256-GCM                                                            |
| Other ciphers   | ChaCha20-Poly1305, XChaCha20-Poly1305, AES-256-CBC + HMAC-SHA256, Fernet |

- Every embedding draws a fresh salt, so its keys are never reused. That makes block
  counters safe as nonces and removes the need to store them.
- AES-256-CBC + HMAC is encrypt-then-MAC: the tag covers the block counter, the IV and the
  ciphertext, and is checked in constant time before anything is decrypted.
- Fernet uses AES-128. The other ciphers use 256-bit keys.
- Passwords are NFC-normalized (RFC 8265), so the same password typed on different
  systems gives the same key. NFKC is not used, so a no-break space stays distinct from a
  space.
- Research mode's `crypto.mem`, `crypto.iter` and `crypto.lanes` parameters change
  Argon2id's costs, and `crypto.nfc` turns the normalization off. The carrier records
  none of them, so revealing needs the same values. Lower costs make password guessing
  cheaper.

Research mode's `crypto.kdf` parameter can derive the key with scrypt (N = 2¹⁷, r = 8,
p = 1, so 128 MiB) or PBKDF2-HMAC-SHA256 (600 000 iterations) instead, at the costs OWASP
recommends for each. Both make password guessing cheaper than Argon2id at 256 MiB.
PBKDF2 needs almost no memory, so graphics cards and dedicated hardware run it far
faster, and scrypt takes half the memory per guess. They are there to study, not to
protect real secrets.

The exact constructions are in [Stored format](format.md#ciphers).

### Choosing a password

Every guess costs an attacker one Argon2id run, but that does not make a short password
safe. Use a long passphrase of uncommon words.

The app's strength meter uses [zxcvbn](https://github.com/dwolfhub/zxcvbn-python). It
estimates how many guesses an attacker who tries common passwords, words, names, dates
and patterns first would need. "Fair" starts at 10⁶ guesses, "Strong" at 10⁸ and
"Very strong" at 10¹⁰. [Standard mode](user-guide.md#standard-and-research-mode)
requires "Strong", and so does Research mode unless its `crypto.minpw` parameter is
lowered.

zxcvbn has English dictionaries only. CipherStitch therefore counts each word in another
script, such as Ukrainian or any other Cyrillic, as one of the thousand or so most common
English words. So `пароль123` is "Weak", as is `Password1!`, and a phrase of four uncommon
Ukrainian words is "Very strong". Chinese, Japanese, Korean and Thai are often written
without spaces, so the meter leaves their letters as they are: zxcvbn rates each one as a
random character, about three to a common word, and finds a part typed again in a row. In
other scripts, the same word typed again counts as a repeat. The meter cannot tell a common
word from a rare one, so choose rare words yourself. It rates the password in the
normalized form the key is derived from. Its rating is only an estimate.

## Handling files

- **Revealing never overwrites.** The revealed file is created exclusively in the output
  directory. If a file with that name already exists, revealing fails.
- **Safe file names.** A hidden file name must be valid on every platform, on hiding and
  on revealing (see [Hidden file record](format.md#hidden-file-record)). So a name that
  points outside the output directory, holds `:` or another character Windows refuses,
  names a Windows device such as `NUL.txt`, or ends with a dot or a blank (a space, or
  another Unicode space separator such as U+00A0) is refused, as is a name of blanks
  alone. So is a name with control or format characters, such as the bidi override U+202E, which
  turns `x`, U+202E, `gpj.exe` into a name shown as `xexe.jpg`. A name that starts with a
  dot is refused too: `.zshenv` revealed into a home folder would be a file the shell runs
  at startup. A macOS or Linux user can therefore not hide a file named `a:b.txt` or
  `.env` under that name, and has to rename it first. Revealing also refuses a name that
  would not stay one plain file name in the output directory.
- **Atomic output.** Hiding writes a hidden temporary file next to the destination and
  moves it into place. A cancelled or failed operation leaves the destination as it was.

## What the app keeps

CipherStitch keeps no record of its use. The command line, the Python API and the app write
only the files you ask for: the carrier with its payload, a revealed file or an exported
profile.

- **No settings.** The app starts in Standard mode every time. Research mode's parameters
  and the profiles used lately last until it quits.
- **No folder history.** Qt's file dialogs record the folder they last showed, and their
  history, in settings that every Qt app shares, and Qt has no option to turn that off.
  These are `~/Library/Preferences/com.qtproject.plist` on macOS, `~/.config/QtProject.conf`
  on Linux and the registry key `HKEY_CURRENT_USER\Software\QtProject` on Windows.
  CipherStitch puts them back as they were after each dialog. If no Qt app had used a file
  dialog before, macOS may leave that file empty.
- **No caches or logs.** Capacities, costs and compressed precovers are cached in memory
  only. Errors go to the terminal, never to a file.
- **No leftover files.** Hiding deletes its temporary file whether it succeeds, fails or is
  cancelled (see [Handling files](#handling-files)), and a failed reveal deletes what it
  wrote. Only a crash or a power cut can leave the temporary file behind: a hidden
  `.xxxxxxxx.partial` file next to the destination.

The operating system keeps records of its own, which CipherStitch can't change:

- The native file dialogs of Windows keep recently used files and folders per program in
  the registry. GTK and KDE dialogs on Linux add chosen files to the desktop's recent
  files. On macOS, the open and save panels left nothing in the preferences in a check.
- File managers, search indexes and backups see the files you hide in and reveal, like
  any other file.

## Untrusted carriers

A carrier to reveal from may be crafted. CipherStitch bounds what parsing it can cost:

- DOCX relationships, content types, custom properties and XMP metadata are parsed with
  the standard library, which never fetches external entities. A part with a document
  type declaration is refused before parsing, so it cannot declare entities or attribute
  defaults that grow far beyond its size. DOCX parts must not have one, and XMP never does.
  Custom properties and XMP metadata over 8 MiB are refused before parsing.
- A PDF payload stream is decompressed only up to 1 MiB, the largest object a writer may
  store, and XMP metadata only up to 8 MiB.
- JPEG dimensions are checked against Pillow's decompression-bomb limit before any
  coefficient array is allocated. Other images use Pillow's own check.
- A WAV header that claims more frames than the file holds is refused, and so is a FLAC
  file that does not record its length.
- Audio methods with error correction decode codeword by codeword and stop at the first
  one that fails, so a recording without a payload is not decoded in full.

## Extensions

Research mode can run methods, ciphers and cost functions from [extensions](extensions.md),
Python modules in the package's `extensions` folder (`src/cipherstitch/extensions/` in a
checkout, and `ciphst extensions` names it). They change what you have to trust:

- **Extension code runs with your rights.** It is ordinary Python with no sandbox. It can
  read and change your files, and it can patch CipherStitch itself when it is imported.
  Read an extension before you copy it in. The loader reads only that folder, never your
  home folder, environment variables or settings.
- **Extension ciphers are unvetted.** The self-test that runs when one loads catches broken
  constructions: wrong sizes, no authentication, plaintext in the output, one keystream for
  the header and the record. It can't prove a cipher secure. Nobody has reviewed an
  extension's cipher, and nobody has measured how detectable an extension's method or cost
  function is.
- **Standard mode runs none of them.** It never imports the folder, refuses any id that
  isn't a built-in's, and its reveal tries only the built-in methods and ciphers. Its output
  is the same whatever the folder holds.
- **Failures are contained, not isolated.** An exception in an extension stops the
  operation with `The extension x failed: …`, and a result that breaks the contract is
  refused before the container uses it. Code that hangs, exhausts memory or crashes the
  interpreter still takes CipherStitch down with it.
