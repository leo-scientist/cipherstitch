# Stored format, version 1

This is the specification of what CipherStitch writes into a carrier. Every value on this
page is a compatibility contract: changing one makes data hidden by earlier releases
unreadable (see [Compatibility](#compatibility)).

Integers are big-endian unless stated otherwise. Bytes become bits most significant bit
first, and bits become bytes the same way.

## Hidden file record

The hidden file is packed into a record first:

| Field       | Size  | Content                    |
| ----------- | ----- | -------------------------- |
| name length | 2     | length of the name, bytes  |
| name        | 1–255 | file name, UTF-8           |
| data        | rest  | the file's bytes           |

The name is checked when hiding and again when revealing, by one rule on every platform.
It is rejected if any of these holds:

- it is empty, made of blanks alone, or longer than 255 bytes
- it contains `/`, `\`, `<`, `>`, `:`, `"`, `|`, `?` or `*`
- it contains a control or format character (Unicode categories Cc and Cf, as of
  Unicode 16), such as NUL, a tab, U+200B or the bidi override U+202E
- it starts with a dot, as `.zshenv` does: revealed into a home folder, such a file could
  be one a shell runs at startup. `.` and `..` are rejected by this rule too
- it ends with a dot or a blank: a space, or another space separator (Unicode category
  Zs, as of Unicode 16) such as the no-break space U+00A0 or the ideographic space U+3000
- before its first dot, without trailing spaces and ignoring case, it is a Windows device
  name: `CON`, `PRN`, `AUX`, `NUL`, `CONIN$`, `CONOUT$`, or `COM` or `LPT` followed by a
  digit from 1 to 9, `¹`, `²` or `³`

## Stream layouts

Each embedding hands a *prefix* and a *body* to a [channel](#channels), which stores them
in the carrier. Slot and stream channels store the prefix in the first units and the body
after it, in sequence or in a key-derived order (see [Slot channels](#slot-channels)).
Trellis channels keep the prefix in a lead of their own (see
[Trellis channels](#trellis-channels)).

### Unencrypted

| Part   | Content                                                                    |
| ------ | -------------------------------------------------------------------------- |
| prefix | `nonce`, 16 random bytes                                                   |
| body   | the plain frame below, XOR `SHAKE-256("cipherstitch plain frame" ‖ nonce)` |

The plain frame, before masking:

| Field         | Size | Content                    |
| ------------- | ---- | -------------------------- |
| magic         | 4    | `CPST` (ASCII)             |
| version       | 1    | `1`                        |
| method id     | 1    | see [Ids](#ids)            |
| record length | 4    | length of the record       |
| record        | n    | the [record](#hidden-file-record) |

The mask is a public construction, not encryption. Tools that do not know the format see
random bytes, and two payloads never look alike, but anyone who knows it can detect and
read the frame.

Example: `a.txt` holding `cipherstitch`, stored by the object-stream method. The frame:

```text
43505354 01 07 00000013 0005 612e747874 636970686572737469746368
CPST     v1 m7 len 19   5    "a.txt"    "cipherstitch"
```

With the nonce `6a09e667f3bcc908b2fb1366ea957d3e`, the stored stream is the nonce followed
by the masked frame:

```text
6a09e667f3bcc908b2fb1366ea957d3e ee8c34ca43e8c5cddd2f80fdfc4cf437f2d5ff72eb07810e541a95bb6b
```

Research mode's `frame.mask` parameter can turn the mask off. The nonce is still written,
and the frame follows it as it is, so anyone who reads the bits sees `CPST` and the record.
Revealing such a payload needs the same setting.

### Encrypted

| Part   | Content                                                     |
| ------ | ----------------------------------------------------------- |
| prefix | `salt`, 16 random bytes                                     |
| body   | `seal(header, counter 0) ‖ seal(record, counter 1)`         |

The header, before sealing, is 7 bytes:

| Field         | Size | Content                                |
| ------------- | ---- | -------------------------------------- |
| version       | 1    | `1`                                    |
| cipher id     | 1    | see [Ids](#ids)                        |
| method id     | 1    | see [Ids](#ids)                        |
| record length | 4    | length of the record before sealing    |

Nothing here is readable without the password. The salt is random, and the sealed
blocks look like random bytes. The header names the cipher and the method, so revealing
needs only the password. One Research setting is the exception: with `frame.order` public
or sequential, image LSB, DCT and audio LSB store the body's length in the clear (see
[Trellis channels](#trellis-channels)), which gives away the payload's exact size, though
not its content.

### Ids

Ids are never renumbered or reused. Method id 2 is reserved. The method id and the cipher id
share these ranges:

| Ids     | Taken by                                                                       |
| ------- | ------------------------------------------------------------------------------ |
| 1–127   | CipherStitch's own methods and ciphers, below                                  |
| 128–254 | extensions, which declare their own ([Extensions](architecture.md#extensions)) |
| 0, 255  | reserved (255 for a longer id, should the format ever need one)                |

| Method                     | Name (CLI)        | Id |
| -------------------------- | ----------------- | -- |
| LSB                        | `lsb`             | 1  |
| DCT coefficients (JPEG)    | `dct`             | 3  |
| Audio LSB                  | `audio-lsb`       | 4  |
| Phase coding               | `phase-coding`    | 5  |
| Echo hiding                | `echo-hiding`     | 6  |
| Embedded object stream     | `object-stream`   | 7  |
| Whitespace padding         | `whitespace`      | 8  |
| Zero-width characters      | `zero-width`      | 9  |
| Sequential LSB replacement | `lsb-replacement` | 10 |

| Cipher                    | Name (CLI)           | Id |
| ------------------------- | -------------------- | -- |
| AES-256-GCM               | `aes-256-gcm`        | 1  |
| ChaCha20-Poly1305         | `chacha20-poly1305`  | 2  |
| XChaCha20-Poly1305        | `xchacha20-poly1305` | 3  |
| AES-256-CBC + HMAC-SHA256 | `aes-256-cbc-hmac`   | 4  |
| Fernet                    | `fernet`             | 5  |

A file hidden with an extension's method or cipher stores its wire ids, so revealing it needs
that extension, loaded with the same ids. Without it the reveal finds nothing, as with any
other file. A method or cipher moved from an extension into CipherStitch keeps its wire id,
so its files stay readable.

## Keys

One Argon2id run per embedding gives the master key:

| Parameter   | Value                                         |
| ----------- | --------------------------------------------- |
| password    | NFC-normalized (RFC 8265), then UTF-8         |
| salt        | the 16-byte salt of the layout                |
| memory      | 262 144 KiB (256 MiB)                         |
| iterations  | 6                                             |
| lanes       | 4                                             |
| output      | 32 bytes                                      |

Research mode's `crypto.kdf` parameter can derive it with scrypt or PBKDF2-HMAC-SHA256
instead, from the same normalized password and salt, at fixed costs. Both are far cheaper
to brute-force than Argon2id.

| Function           | Costs                       | Output   |
| ------------------ | --------------------------- | -------- |
| scrypt             | N = 2¹⁷, r = 8, p = 1       | 32 bytes |
| PBKDF2-HMAC-SHA256 | 600 000 iterations          | 32 bytes |

Research mode's `crypto.mem`, `crypto.iter` and `crypto.lanes` parameters set Argon2id's
memory, iterations and lanes instead of the values above, and `crypto.nfc` off derives the
key from the password's UTF-8 bytes as typed, without normalizing it. Nothing in the
carrier records these settings or `crypto.kdf`, so revealing such a payload needs the same
values.

Every other key is an HKDF-SHA256 subkey of the master key, with no salt and the purpose
as the info string:

| Info (UTF-8)         | Length | Used as                                   |
| -------------------- | ------ | ----------------------------------------- |
| `aes-256-gcm`        | 32     | AES-256-GCM key                           |
| `chacha20-poly1305`  | 32     | ChaCha20-Poly1305 key                     |
| `xchacha20-poly1305` | 32     | XChaCha20-Poly1305 key                    |
| `aes-256-cbc-hmac`   | 64     | AES-256 key (first half), HMAC key (second half) |
| `fernet`             | 32     | Fernet key (signing key, then encryption key)    |
| `order`              | 32     | seed of the [keyed order](#keyed-order)   |

An extension's cipher takes its key from the info `cipherstitch extension cipher <wire id>`,
for example `cipherstitch extension cipher 128`, at the key size it declares. Its files then
depend on its wire id alone, and renaming the cipher breaks nothing.

## Ciphers

`seal(key, counter, data)` is called twice per embedding: counter 0 for the header and
counter 1 for the record.

| Cipher                    | Sealed block                                                         | Overhead |
| ------------------------- | -------------------------------------------------------------------- | -------- |
| AES-256-GCM               | `ciphertext ‖ tag(16)`, with the counter as a 12-byte nonce and no associated data | 16 |
| ChaCha20-Poly1305         | same as AES-256-GCM                                                  | 16       |
| XChaCha20-Poly1305        | `nonce(24, random) ‖ ciphertext ‖ tag(16)`, with the counter as 8 bytes of associated data | 40 |
| AES-256-CBC + HMAC-SHA256 | `IV(16, random) ‖ ciphertext ‖ tag(32)`, with PKCS#7 padding and the HMAC-SHA256 of `counter(8) ‖ IV ‖ ciphertext` as the tag | 49–64 |
| Fernet                    | a Fernet token made at timestamp 0, base64-decoded, without its first 9 bytes (`0x80` and the zero timestamp): `IV(16) ‖ ciphertext ‖ HMAC(32)` | 49–64 |

Counter nonces are safe because every embedding draws a fresh salt, so each key seals
exactly these two blocks. The HMAC is checked, in constant time, before anything is
decrypted. Fernet does not bind the counter. A Fernet header is told apart from a record
by its fixed 7-byte length.

## Channels

A channel stores the prefix and the body in one carrier. There are three kinds.

### Slot channels

Zero-width text is a sequence of `N` units, the insertion points listed in
[Embedding methods](methods.md#zero-width-characters). Unit `u` is insertion point `u`, and
it carries `b` bits, 2 by default: the symbol after it.

- `n` bytes need `⌈8n / b⌉` units, so `4n` with 2 bits.
- The prefix fills units `0 … P − 1`, where `P` is the number of units the prefix needs.
- The body fills *positions* `0, 1, 2, …`, `b` bits each. With encryption, position `i` is
  unit `P + π(i)` in the [keyed order](#keyed-order) on `N − P` positions, keyed by the
  `order` subkey. Without it, and in Research's public and sequential orders, position `i`
  is unit `P + i`.

A symbol's value holds its bits, the first one highest, so with 2 bits it is
`2 × first bit + second bit`. Research mode's `zw.sym` picks one of three symbol sets
(`SYMBOL_SETS`):

| Set           | `b` | Symbols and values                                                    |
| ------------- | --- | --------------------------------------------------------------------- |
| 4 (default)   | 2   | U+200B = 0, U+200C = 1, U+2063 = 2, U+2060 = 3                        |
| 2 (Research)  | 1   | U+200B = 0, U+200C = 1                                                |
| 8 (Research)  | 3   | the four above, then U+2061 = 4, U+2062 = 5, U+2064 = 6, U+FEFF = 7   |

Readers remove the set's symbols and find the insertion points on what is left, so they
find the same points as the writer. Symbols of the other sets stay in the text. So does a
U+FEFF at the very start, the text's byte order mark. A symbol always follows a character,
so it never starts a text.

Research mode's relaxed rules (`zw.rules`) give other insertion points. A symbol may
follow any character except CR (U+000D), unless the next character is a combining mark
(category M in Unicode 3.2). The last character and LF count, and so do the zero-width
characters a text already holds.

#### LSB replacement

Sequential LSB replacement, Research mode's Experimental baseline, is a slot channel over
an image's colour values, numbered as for [image LSB](#trellis-channels): the red, green
and blue of every pixel, row by row. Unit `u` is value `u`, and it carries 1 bit, the
value's lowest.

- `n` bytes need `8n` units.
- The prefix fills units `0 … 127`, and the body follows in sequence, with or without
  encryption: position `i` is unit `128 + i`. The body is never keyed.
- The writer sets each unit's lowest bit to the stream's bit and leaves the others.

### Keyed order

π is a keyed bijection on `[0, count)`: a 6-round Feistel network over the smallest domain
of `b ≥ 2` bits with `2^b ≥ count`, and cycle-walking into `[0, count)`. Zero-width and
the [trellis channels](#trellis-channels) key it by the `order` subkey, and the trellis
channels by `UNKEYED_SEED` for a public order.

1. A value `x` is split into a high half `A` of `⌊b / 2⌋` bits and a low half `B` of
   `⌈b / 2⌉` bits. The halves swap widths every round, as in NIST FF1.
2. The round tables are `SHAKE-256(seed ‖ "cipherstitch order")`, read as 6 tables of
   `2^⌈b/2⌉` little-endian 32-bit integers, round after round.
3. Round `r`: `(A, B) ← (B, A ⊕ (T_r[B] mod 2^|A|))`.
4. `π(i)` applies the network to `i`, then again to the result while it is `count` or more.

A Python reference implementation:

```python
import hashlib
import struct


def keyed_order(seed: bytes, count: int) -> list[int]:
    bits = max(2, (count - 1).bit_length())
    widths = (bits // 2, bits - bits // 2)  # of the high half A and the low half B
    size = 1 << widths[1]
    stream = hashlib.shake_256(seed + b"cipherstitch order").digest(24 * size)
    tables = struct.unpack(f"<{6 * size}I", stream)

    def permute(x: int) -> int:
        while True:  # cycle-walking: repeat until the value is below count
            a, b = x >> widths[1], x & (size - 1)
            for r in range(6):
                a, b = b, a ^ (tables[r * size + b] & ((1 << widths[r % 2]) - 1))
            x = a << widths[1] | b
            if x < count:
                return x

    return [permute(i) for i in range(count)]
```

Any position can be computed on its own, so readers never need the whole order.

An encrypted body goes in the keyed order unless Research mode's `frame.order` picks the
public or the sequential one. The sequential order has no π: position `i` is unit `P + i`
in a slot channel, and stream position `i` is unit `R + i` in a
[trellis channel](#trellis-channels). In zero-width that is also the public order.

### Trellis channels

DCT, image LSB and audio LSB store data as the syndrome of their units' bits under
[syndrome-trellis codes](#syndrome-trellis-codes), so the reader needs neither the changed
units nor their number.

**Units.** A carrier has `N` values, in this order:

| Method    | Values                                                                                                                                                |
| --------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| DCT       | the AC coefficients of the Y, then Cb, then Cr components, block by block, row-major inside each 8×8 block (as jpeglib returns them), zeros included |
| Image LSB | the R, G and B values of each pixel, pixels in row-major order (alpha is not a value)                                                                 |
| Audio LSB | every channel's sample of a frame, frame after frame                                                                                                  |

A unit has two bits, the two lowest bits of its value `v` in two's complement: its
*parity* `v mod 2` and its *second bit* `⌊v / 2⌋ mod 2`. A coefficient of −1 has parity 1
and second bit 1, one of −2 parity 0 and second bit 1.

Unit `u` is value `a × u mod N`, where `a` is the first integer from
`⌊(⌊√(5N²)⌋ − N) / 2⌋` on (about 0.618 × N) that is coprime to `N`. Runs of consecutive
units therefore cover the whole carrier, and in a JPEG every frequency. A writer that
compresses its own JPEG must give each component the block grid jpeglib returns for the
declared size, or readers count another `N`. Y has ⌈H/8⌉ × ⌈W/8⌉ blocks, and the chroma
⌈H/16⌉ × ⌈W/16⌉ in 4:2:0, ⌈H/8⌉ × ⌈W/16⌉ in 4:2:2 and ⌈H/8⌉ × ⌈W/8⌉ in 4:4:4.

**Regions.** With `R = min(16 384, ⌊N / 8⌋)` and `H = min(65 536, ⌊(N − R) / 4⌋)`:

| Region | Units or positions              | Content                                               |
| ------ | ------------------------------- | ----------------------------------------------------- |
| lead   | units `0 … R − 1`               | 16 bytes: a [code](#syndrome-trellis-codes) of 128 bits |
| stream | units `R … N − 1`, in the [keyed order](#keyed-order) of a seed | `u32 length ‖ u16 split ‖ data` |
| head   | stream positions `0 … H − 1`    | the stream's first 70 bytes: a code of 560 bits       |
| tail   | stream positions `H … H + S − 1` | the rest, `8 × (length − 64)` bits when `length > 64` |

- Stream position `i` is unit `R + π(i)`, where π is the keyed order on `N − R` positions
  keyed by the `order` subkey, or by 32 zero bytes (`UNKEYED_SEED`) without encryption and
  in Research's public order. In Research's sequential order, stream position `i` is unit
  `R + i`.
- The tail's code spans `S = min(N − R − H, 512 × tail bits)` positions.
- The lead and the head are padded with random bytes when there is less to store.
- When `H < 560` the carrier holds nothing: its capacity is 0 and every read is empty.

**Codes.** A region's code of `m` bits over its `n` units is two
[syndrome-trellis codes](#syndrome-trellis-codes) over the same `n` units: bits `0 … f − 1`
are the syndrome of the units' second bits, and bits `f … m − 1` that of their parities,
with

```text
f = min(max(⌊(m × s + 32 768) / 65 536⌋, 1), m − 1)
```

where `s` is the stream's `split` for its tail and 32 768 for the lead and the head, so
`f = m / 2` there. `m` is always a whole number of bytes. The writer picks the split, and
the reader only applies it.

With Research mode's binary coding (`stc.code` binary), each region's code of `m` bits is
one syndrome-trellis code over the units' parities, and the second bits hold nothing. The
stream keeps its `split` field, which the writer sets to 0 and readers ignore.

**Prefix and body.**

- With a seed (encrypted, keyed): the lead holds the prefix (the salt), and the stream in
  the seed's order holds the body.
- In Research's sequential order: the lead holds the salt, and the stream in sequence holds
  the body.
- Without one: the lead holds the first 16 bytes of `prefix ‖ body`, and the unkeyed stream
  holds the rest, if there is any. For an unencrypted payload that is the nonce in the
  lead and the masked frame in the stream.

**Reading.** The lead is read from its code. A stream is read from its head. A length above
`64 + ⌊(N − R − H) / 8⌋` means the carrier holds no stream in this order. Only a stream
longer than 64 bytes needs its tail. In an encrypted carrier the lead holds only the
random salt and the stream's data only sealed bytes. In the keyed order nothing is
readable without the password. In Research's public or sequential order the stream's
positions are public, so anyone who knows the format can read its `length` and `split`:
the exact size of the sealed body, and so of the payload, though not its content.

**Capacity.** With `D` the counted values among units `R … N − 1` and `T = N − R − H`,
the body bytes that fit after a `p`-byte prefix are:

```text
capacity(p)     = max(16 − p, 0) + max(⌊min(⌊80 × D / 100⌋, 560 + T) / 8⌋ − 6, 0)
stored_limit(p) = max(16 − p, 0) + 64 + ⌊T / 8⌋
```

DCT counts the non-zero coefficients. Image and audio LSB count the values that are
neither flat nor at an end of their range. A flat value is in a run of 16 or more equal
values along a row of one colour of a picture, or along one channel of a recording in
stretches of 4096 samples. The 80% cap and the use of `D` are the writer's policy.
Embedding changes which values are counted, so readers check stored lengths against
`stored_limit`, which depends on `N` only (see [Revealing](#revealing)).

Which values change, which way, and at what cost is the writer's policy too, since
readers only see the units' bits. CipherStitch moves a value up or down by one, never
beyond the ends of its range. Either step flips the parity, and the second bit flips too
on a step down from an even value or up from an odd one. In a JPEG cover a step towards
an estimate of the picture before compression costs less
([methods](methods.md#dct-coefficients-jpeg)). When CipherStitch compresses a RAW, PNG or
TIFF itself, a step towards the value the picture had before rounding costs less, and a
step away from it more ([methods](methods.md#from-a-raw-png-or-tiff)).

#### Syndrome-trellis codes

A code stores `m` message bits in `n ≥ m` units (constraint height 6):

- Block `j` (`0 ≤ j < m`) is units `⌈j × n / m⌉ … ⌈(j + 1) × n / m⌉ − 1`.
- Column `k` of the check matrix is byte `k` of
  `SHAKE-256("cipherstitch stc columns 297")` `& 0x3F | 0x21`: 6 bits, the lowest and the
  highest always set. Of the 4096 numbered labels tried, this one gave image LSB and DCT
  the cheapest codes.
- The code has `s = min(max(⌊n / 32 768⌋, 1), 4096)` segments. Segment `t` holds blocks
  `⌊t × m / s⌋ … ⌊(t + 1) × m / s⌋ − 1`.
- The `k`-th unit of block `j` carries column `k`, cut to its lowest `min(6, e − j)` bits,
  where `e` is the first block after the segment.
- Message bit `r` is the XOR of bit `r − j` of the columns of every unit whose bit is 1.

A Python reference implementation:

```python
import hashlib


def syndrome(bits: list[int], m: int) -> list[int]:
    n = len(bits)
    segments = min(max(n // 32768, 1), 4096)
    columns = hashlib.shake_256(b"cipherstitch stc columns 297").digest(-(-n // m))
    message = [0] * m
    for unit, bit in enumerate(bits):
        block = unit * m // n
        segment = ((block + 1) * segments + m - 1) // m - 1
        end = (segment + 1) * m // segments
        column = columns[unit - (block * n + m - 1) // m] & 0x3F | 0x21
        for k in range(min(6, end - block)):
            message[block + k] ^= bit & column >> k
    return message
```

The reader depends on nothing else. Which units change, and how, is the writer's choice.
CipherStitch steps a value by ±1 at its cost, J-UNIWARD for DCT and HILL for LSB (see
[DCT coefficients](methods.md#dct-coefficients-jpeg) and
[Trellis coding](methods.md#trellis-coding)). It codes the second bits first, then the
parities around the steps already taken, each with the Viterbi algorithm. Each search
steps only over the cheapest units, ten per message bit, and the cheapest unit of every
block left without one. The other units keep their bits. A value at an end of its range
cannot flip its second bit, and a value the first search stepped keeps its parity, so a
block may have no unit the search can use. If its neighbours cannot make up for it, which
happens on the first try to one nearly full LSB cover in four to ten, the writer tries
again with up to 30% more of the tail's bits in the second bits, six more times. If none
of that works, hiding stops with an error instead of writing an unreadable carrier.

With binary coding there is one search, over the parities. Either step flips a parity, so
each value costs as much as its cheaper step that stays in range, and a changed value takes
that step. Where both cost the same, it goes either way at random, as in LSB matching.
Binary coding has no split to move, so the writer tries once.

### Stream channels

The carrier stores one byte stream, `prefix ‖ body`, so there is no keyed order:

| Method                  | Where the stream lives                                                 |
| ----------------------- | ---------------------------------------------------------------------- |
| Object stream, PDF      | an application's private data in the page-piece dictionary (see below) |
| Object stream, DOCX     | the printer-settings part (see below)                                  |
| Object stream, Research | a property of the document's metadata with `obj.loc` (see below)       |
| Whitespace padding      | trailing whitespace of DOCX paragraphs (see below)                     |
| Phase coding            | Reed–Solomon-coded bits in the first audio channel                     |
| Echo hiding             | Reed–Solomon-coded bits as echoes in every audio channel               |

**Padding.** An object stream is followed by random bytes, up to a total length drawn
uniformly from `len(stream) … 65 536` bytes by default. A whitespace stream is padded the
same way, up to the capacity (8 bytes per paragraph by default). So neither the object's
length nor the amount of whitespace shows the payload's size. Readers read at most
1 048 576 bytes of an object, the highest limit a writer may use, and ignore what follows
the payload. In metadata the limit counts base64 characters (see below).

**PDF.** The document catalog's page-piece dictionary `/PieceInfo` (PDF 1.4) gets the
entry `/Editor << /LastModified (D:…Z) /Private stream >>`, where `stream` holds the
object and `/LastModified` is the time of hiding in UTC. Entries of other applications
are kept, and the file is saved as PDF 1.4 or later.

**DOCX part.** The object is the part `word/printerSettings/printerSettings1.bin`. It is
linked from `word/_rels/document.xml.rels` by a relationship of type
`http://schemas.openxmlformats.org/officeDocument/2006/relationships/printerSettings`,
with the first free Id `rIdN`, and declared in `[Content_Types].xml` by an `Override`
with the content type
`application/vnd.openxmlformats-officedocument.wordprocessingml.printerSettings`. Readers
follow the first printer-settings relationship of the main document whose target is in
`word/printerSettings/`. Word drops parts that are not linked.

The cover's own printer settings are replaced. Both document methods first remove:

- every printer-settings relationship of the main document
- the `w:printerSettings` references of its section properties
- the parts those relationships link in `word/printerSettings/`, with their content
  types

Whitespace padding and the metadata stream do this only when such a part exists. Either
way, no payload object is left behind.

**Metadata stream.** Research's `obj.loc` can put the object in the document's metadata
instead, as base64 text (RFC 4648, with `=` padding and no line breaks) in one property
named `DocumentState`. The limit counts characters, so the stream and its padding take at
most three quarters of it, 49 152 bytes by default. Readers ignore spaces, tabs and line
breaks in the text, decode at most 786 432 bytes, and read text that is not base64 as no
payload. Only Research's reveal with the same setting reads this location, and it reads no
other.

- **PDF.** The property is `DocumentState` in the namespace
  `urn:uuid:489bd2b8-2e41-442c-a303-bb107208f89c`, in the XMP packet of the catalog's
  `/Metadata` stream. The writer adds
  `<rdf:Description rdf:about="…" xmlns:doc="urn:uuid:…"><doc:DocumentState>…</doc:DocumentState></rdf:Description>`
  just before the packet's last `</rdf:RDF>`, with the `rdf:about` of the packet's first
  description, and leaves every other byte of the packet as it was. A PDF without metadata
  gets a new packet in a stream with `/Type /Metadata /Subtype /XML`. It holds the
  `xpacket` processing instructions with the id `W5M0MpCehiHzreSzNTczkc9d`, an `x:xmpmeta`
  element and an `rdf:RDF` element with only that description. The file is saved as PDF
  1.4 or later. Readers take the first value of the property in the packet, the text of an
  element or an attribute of a description, since XMP allows both. The PDF is unreadable
  in this location if its metadata is not a stream, uses a filter other than FlateDecode
  without parameters, holds more than 8 MiB, has a document type declaration or is not
  XMP. A PDF/A file no longer conforms, because PDF/A wants an extension schema for every
  property outside its predefined ones.
- **DOCX.** The property is a custom property,
  `<property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" pid="N" name="DocumentState"><vt:blob>…</vt:blob></property>`,
  added as the last child of `Properties`. N is one more than the highest `pid` there, or
  2 if there is none. The part is the one the package links by a relationship of type
  `http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties`.
  A package without one gets the part `docProps/custom.xml`, with the main document's XML
  declaration, date and compression, linked from `_rels/.rels` with the first free Id
  `rIdN` and declared by an `Override` with the content type
  `application/vnd.openxmlformats-officedocument.custom-properties+xml`. Readers follow
  the first such relationship and take the first property named `DocumentState`. A value
  of a type other than `vt:blob` is no payload. The DOCX is unreadable in this location if
  the part holds more than 8 MiB, has a document type declaration or is not custom
  properties.

Every object or whitespace write removes the property in the form a metadata write stores
it, the whole description in a PDF. A metadata write removes the page-piece entry
`/Editor`, and `/PieceInfo` if nothing else is left in it. In a DOCX it removes the printer
settings and the whitespace padding, as a write to the default location does. Writers
refuse a cover where the metadata would then read as anything but the new value, such as
one holding the property as an attribute or with another type.

**Whitespace.** The paragraphs are the `w:p` elements of the body, tables included, that
hold no other `w:p` (text boxes do), in document order. With `S` bits of the padded stream
and `K` paragraphs, paragraph `k` gets bits `⌊k·S/K⌋ … ⌊(k+1)·S/K⌋ − 1`: a space for 0,
a no-break space (U+00A0) for 1. They are appended as one new run
`<w:r><w:t xml:space="preserve">…</w:t></w:r>`, after the paragraph's own trailing
spaces and no-break spaces are removed. Reading collects the trailing spaces and
no-break spaces after each paragraph's last tab, break, drawing, symbol, note reference,
field or equation, and keeps whole bytes.

Research's space and tab (`ws.sym`) writes a tab for 1 instead. In the new run each tab is
a `<w:tab/>`, and each stretch of spaces a `<w:t xml:space="preserve">`. Reading then
collects the trailing spaces and tabs after each paragraph's last break, drawing, symbol,
note reference, field or equation. A tab there is a `w:tab` in a run, as Word and
python-docx write one, or a tab character in a `w:t`. A tab stop in the paragraph's
properties is not one. The cover's trailing spaces and no-break spaces are removed before
writing, as with the default symbols, so an earlier space-and-no-break-space payload goes.
Writers refuse a cover where a tab would then be left at the end of a paragraph, because
it would read as a 1 bit and removing it could break a form. That includes a tab followed
by no-break spaces and a cover holding a space-and-tab payload.

**Reed–Solomon.** The stream is padded with random bytes to a multiple of 223 bytes. Each
223-byte block becomes a 255-byte codeword (reedsolo `RSCodec(32)`: GF(2⁸), primitive
polynomial `0x11d`, generator 2, first consecutive root 0). The codewords are embedded
bit by bit from the start of the recording.

- **Phase coding.** Segment `s` is samples `4096·s … 4096·s + 4095` of the first
  channel. Bit `64·s + j` is the phase of bin `32 + j` of the segment's real FFT:
  +π/2 for 0, −π/2 for 1. A bit is 1 if the phase is negative. Readers look at the first
  channel only. What writers do to the other channels and to the segments after the data
  is their choice.
- **Echo hiding.** Segments are `L = max(round(0.025 × rate), 512)` samples long, with
  halves rounded to even (1 102 samples at 44.1 kHz), and carry one bit each. The echo
  delays are `d₀ = max(⌊rate / 1000⌋, 2)` samples for 0 and
  `d₁ = max(⌊3 × rate / 2000⌋, 3)` for 1. To read a bit, the channels of the segment
  are summed and windowed (Hann), and its real cepstrum
  `c = irfft(log(|rfft(x)| + 10⁻⁹))` is taken. The bit is 1 if `c[d₁] > c[d₀]`.

## Revealing

1. **Group.** Every method that can write a file with this suffix is a candidate.
   Candidates that share a channel type form one group, so the carrier is opened once
   per group.
2. **Plain frames.** In every group, the 16-byte nonce and the next 10 bytes are read
   without a key, and the 10 bytes are unmasked. They are a frame if the magic and version
   match, the method id is one of the group's unkeyed candidates, and the record is
   within what the carrier can have stored after the nonce (`stored_limit`).
3. **Encrypted payloads** (only with a password). For each group that holds at least 16
   bytes:
   1. The first 16 bytes are the salt, and one Argon2id run gives the master key.
   2. For each candidate order (keyed, then public, then sequential in Research mode) and
      each installed built-in cipher (in the order of the [cipher ids](#ids)), the sealed
      header after the salt is read and unsealed with counter 0.
   3. The header is accepted if it is 7 bytes long, names version 1, this cipher and
      this candidate's method, and the sealed record is within what the carrier can have
      stored (`stored_limit`).
   4. The record is then unsealed with counter 1. If that fails, the carrier was modified
      after hiding, and revealing stops with "no hidden data".
4. Otherwise there is no hidden data.

Plain frames are tried before any key derivation, so a password typed for an unencrypted
payload still works. "No hidden data" and "wrong password" are the same error.

This is Standard mode's reveal, at the default parameters. It leaves out the Experimental
methods (extensions included), the extensions' ciphers and the sequential order, which only
Research mode hides with. Research mode's follows the same steps with its own parameters,
its method's channel only and its cipher only, or only the plain frame when the cipher is
`none`. It tries all three orders whatever `frame.order` says, so that parameter never has
to match.

## Compatibility

Fixed for version 1:

- `MAGIC`, `VERSION`, `NONCE_SIZE`, the mask label `cipherstitch plain frame` and the id
  tables in `container.py`
- the id ranges, and the key info of an extension's cipher,
  `cipherstitch extension cipher <wire id>`
- the `CipherId` string values and `ORDER_SEED`, which are HKDF info strings
- the Argon2id profile, Research's scrypt and PBKDF2 costs and the NFC normalization of
  passwords in `kdf.py`
- the zero-width units, the symbol sets and their values, and the strict and relaxed
  insertion rules
- the whitespace symbols (a space for 0, and a no-break space or Research's tab for 1) and
  the elements that end a paragraph's tail
- in `methods/feistel.py`, the keyed order (label, 6 rounds, the split of the halves),
  `UNKEYED_SEED` and the unit multiplier (`golden_multiplier`)
- the trellis channels: the values and their numbering for DCT, image LSB and audio LSB,
  the stream's fields, the split rule (`SPLIT_SCALE`, `EVEN_SPLIT` and the clamp),
  Research's binary coding over the parities alone, `LEAD_BYTES`, `HEAD_BYTES`,
  `LEAD_UNITS`, `HEAD_UNITS` and `TAIL_UNITS_PER_BIT` in `methods/trellis.py`, and the
  codes in `methods/stc.py`
- the units, bits and stream locations of every method on this page, and the metadata
  stream's property name, XMP namespace, `vt:blob` type and base64 text

Test vectors pin these values. Never regenerate the vectors:

- `tests/test_kdf.py::test_production_profile_is_frozen` and
  `test_the_alternatives_are_frozen`
- `tests/test_container.py::test_format_v1_wire_ids_are_frozen`,
  `test_format_v1_vectors_stay_readable`, `test_kdf_vectors_stay_readable`,
  `test_the_unmasked_vector_stays_readable` and `test_the_extension_vectors_stay_readable`
- `tests/test_slots.py::test_format_vectors_stay_readable`,
  `test_the_sequential_vector_stays_readable` and `test_the_binary_vector_stays_readable`,
  and `tests/test_feistel.py` (`GOLDEN_ORDER`)
- `tests/test_lsb_replacement.py::test_format_vectors_stay_readable`
- `tests/test_dct.py::test_format_vectors_stay_readable` and `tests/test_stc.py`
  (`GOLDEN_*`)
- the numbering of the values read from a file:
  `tests/test_dct.py::test_values_are_the_ac_coefficients_of_y_then_cb_then_cr`,
  `tests/test_lsb_image.py::test_values_are_each_pixels_red_green_and_blue_row_by_row`
  and `tests/test_audio.py::test_values_are_every_channels_sample_frame_after_frame`
- `tests/test_zero_width.py::test_insertion_points_are_the_same_on_every_python_version`,
  `test_relaxed_points_are_the_same_on_every_python_version`,
  `test_the_symbol_sets_are_frozen` and `test_format_vectors_stay_readable`
- `tests/test_whitespace.py::test_the_space_and_tab_vector_stays_readable`
- `tests/test_metadata_stream.py::test_the_metadata_vector_stays_readable`

A new built-in method or cipher takes the next free id below 128. Any other change needs a
new `VERSION`, and readers keep reading version 1.
