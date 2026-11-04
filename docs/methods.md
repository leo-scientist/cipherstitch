# Embedding methods

CipherStitch offers nine methods across four carrier types. This page explains how each
one works, what it can hold and what destroys it. The exact bit layout is in
[Stored format](format.md).

Standard mode offers the first three in the table below: LSB, DCT coefficients and audio
LSB. The other six, and every parameter that changes how a method works, are for
[Research mode](user-guide.md#standard-and-research-mode). Sequential LSB replacement is
an Experimental baseline that any detector should find.

| Method                 | Carrier in           | Output          | Capacity                            | Extra       |
| ---------------------- | -------------------- | --------------- | ----------------------------------- | ----------- |
| LSB                    | image                | PNG, BMP, TIFF  | up to 2.4 bits per pixel            | —           |
| DCT coefficients       | JPEG, RAW, PNG, TIFF | JPEG            | 0.8 bit per non-zero AC coefficient | `jpeg`³     |
| Audio LSB              | WAV, FLAC            | same as carrier | up to 0.8 bit per sample            | —¹          |
| Phase coding           | WAV, FLAC            | same as carrier | ~690 bit/s at 44.1 kHz              | `ecc`¹      |
| Echo hiding            | WAV, FLAC            | same as carrier | ~40 bit/s at 44.1 kHz               | `ecc`¹      |
| Embedded object stream | PDF, DOCX            | same as carrier | up to 64 KiB                        | —²          |
| Whitespace padding     | DOCX                 | DOCX            | 8 bytes per paragraph               | `documents` |
| Zero-width characters  | TXT, MD              | same as carrier | 2 bits per insertion point          | —           |
| LSB replacement        | image                | PNG, BMP, TIFF  | 3 bits per pixel                    | —           |

¹ FLAC carriers need the `audio` extra.
² PDF carriers need the `documents` extra.
³ Camera RAW carriers also need the `raw` extra.

The capacity is the raw room in the carrier. The payload also carries its file name, a
small frame and 16 random bytes (a nonce, or the salt when it is encrypted), plus the
cipher overhead when it is encrypted. The app's capacity meter and `ciphst hide`
account for all of it.

## Keyed and public order

Image LSB, audio LSB and DCT spread the payload over the whole carrier, so the changes do
not cluster. With a password the order is derived from it (see
[Keyed order](format.md#keyed-order)), and only someone who knows the password can tell
which values hold the payload. Without one they use a fixed public order, which anyone
who knows the format can follow. The 16 random bytes stored before the payload (the
nonce, or the salt) are spread over the carrier too, at fixed positions.

Zero-width characters use the keyed order when the payload is encrypted. Without
encryption they store it in sequence, from the first insertion point on. The other
methods store one stream in sequence.

In Research mode `frame.order` can put an encrypted payload in the public order, or in
sequence (*sequential*, Experimental), and `stc.spread` sequential numbers the values one
after another instead of by the golden-ratio step. With both, the payload fills the
carrier from its first values. Either order lets an attacker test exactly the values that
hold data (see [Security model](security.md#what-it-does-not-protect)). Standard mode's
reveal tries the keyed and public orders, Research mode's all three.

## LSB matching

Image and audio LSB change a value by LSB matching (±1 embedding): it moves one step up
or down, never beyond the edges of its range (a pixel at 0 or 255, a sample at the lowest
or highest level). Overwriting the lowest bit instead would only ever swap a value 2k with
2k + 1, and that pairing is what chi-square, RS and sample-pair analysis measure. Both
ways carry data, and the code picks the way ([trellis coding](#trellis-coding)).

## Trellis coding

Image and audio LSB store the payload over syndrome-trellis codes, as DCT does (Filler,
Judas and Fridrich, 2011). The payload is the *syndrome* of the values' two lowest bits
under fixed codes, so readers compute it without knowing which values changed, and the
writer may change any set of values that gives the right syndrome:

- Each change has a cost (HILL, Li, Wang, Huang and Li, 2014). The picture's high-pass
  residual is averaged over 3 × 3 values, turned into its reciprocal and averaged again
  over 15 × 15, colour by colour. A change in a smooth area costs more than one in
  texture or noise, so the changes go to busy areas. A recording is treated the same way
  along each channel, over 3 and 15 samples. Nothing was measured on audio yet.
- A step up and a step down both carry data. Part of the payload goes into the values'
  second bits, and the rest into their parities. A step down from an even value or up from
  an odd one flips the second bit, the other step only the parity. The code stores the
  first part, then the rest around the steps already taken, each time picking the set of
  changes with the lowest total cost (the multi-layered construction of the same paper).
  Each search looks only at the cheapest values, ten per hidden bit.
- The second bits take the share of the payload an ideal code would give them, from the
  costs, a little over half in a photo. The file records it, so hiding is free to give
  them more when a nearly full cover leaves the parities no solution, up to seven splits in
  turn. On a cover of many megapixels a payload at the very top of the capacity may still
  find none and is refused, and one a few percent smaller fits. With an even split the
  parities would take a few more changes, and steps that keep a value in its pair (2k and
  2k + 1) would outnumber the others, as with LSB replacement, which Weighted Stego picks
  up ([detectability](detectability.md#popular-tools-and-classical-attacks)).
- On 512 × 512 photos this changes about 0.18 values per hidden bit at 5% of the capacity
  and 0.2 at 20%, against 0.5 for plain LSB matching. The total cost of the changes is 10%
  lower at 5% and 18% lower at 20% than with the parities alone.
- **Binary coding in Research mode.** `stc.code` binary codes all of a region's bits in
  the parities alone. Either step flips a parity, so each value takes its cheaper step that
  stays in range, either way at random when both cost the same. It takes more changes per hidden bit, and the writer tries once, since
  there is no share between bit planes to move.
- A change in flat colour or digital silence stands out, because nothing else varies
  there. A value in a run of 16 or more equal values (along a row of one colour, or along
  one channel of a recording) is *flat*. The code changes a flat value only where a block
  of the code holds nothing else, and avoids even most of those. A small payload leaves
  flat areas untouched unless nearly all of the carrier is flat. Flat values do not count
  towards the capacity, which is 0.8 bit per value that is not flat. Nor do values at the
  edges of the range, which cannot flip their second bit. A carrier whose lead or header
  would sit mostly on such values (a white band with Research's sequential units, say)
  holds nothing, since no write could succeed.
- The nonce or salt takes a *lead* of up to 16 384 values at fixed positions spread over
  the carrier. The first 70 bytes of the rest, which hold the header, take up to 65 536
  more, so a header reads the same whatever follows it (see
  [Trellis channels](format.md#trellis-channels)).
- **Speed and memory** on a 12-megapixel PNG (Apple M3 Pro): hiding 0.7% of the capacity
  takes about 6 s and 1 GB, and 5% about 11 s and 1.4 GB. A 25-megapixel picture takes
  about twice that. Revealing takes under 2 s.

## Images

### LSB

The payload is stored in the two lowest bits of the red, green and blue channels, up to
0.8 bit per value that is not flat. The changes go where the picture is busy
([trellis coding](#trellis-coding)), and no channel value moves by more than one
([LSB matching](#lsb-matching)). Alpha is left untouched, so transparency is preserved.

- **Carrier:** PNG, BMP, TIFF, JPEG or WebP. The pixels are converted to RGB first, or
  to RGBA if the image has transparency. 16-bit greyscale is rounded to 8 bits, and other
  pictures with values beyond 8 bits are refused rather than clipped. Only the first
  frame of an animation and the first page of a TIFF are read, and the output holds only
  that picture.
- **Output:** always lossless PNG, BMP or TIFF, even for a JPEG carrier. It keeps this
  much of the cover:
  - PNG and TIFF keep the resolution, and the ICC profile if it is an RGB one. BMP keeps
    the resolution.
  - The EXIF data of a JPEG, PNG or WebP cover goes into PNG whole. TIFF takes only its
    descriptive tags and its Exif and GPS sections, because the other tags describe how a
    file is stored and would break the TIFF. The maker's notes are left out too, as their
    offsets would no longer match. A photo turned by its EXIF orientation stays turned as
    PNG but not as TIFF or BMP. CipherStitch turns a TIFF by its orientation while
    reading, and that would move the hidden bits. For the same reason TIFF output leaves
    out XMP data found in the EXIF if it holds an orientation.
  - PNG keeps the XMP data of a JPEG, WebP or TIFF cover. A PNG cover saved as PNG
    keeps its text (XMP included), gamma, sRGB and chromaticity chunks.
  - A TIFF cover is also turned by an orientation in its XMP while reading. Its XMP is
    then kept without that orientation, or left out when the orientation is written in a
    form that cannot be taken out. XMP stored as text, which the XMP specification does
    not allow, is read as well, and TIFF output stores it as bytes.
  - A TIFF cover saved as TIFF keeps its descriptive tags: description, document and page
    name, make, model, software, date, artist, host computer, copyright, XMP and Photoshop
    data. It keeps its Exif and GPS sections too, without the maker's notes and the
    interoperability section. It also keeps LZW, Deflate or PackBits compression and the
    predictor, so its size stays close to the cover's.
    Other compressions, such as lossy JPEG, are not kept. Nor is any compression when the
    cover stores one of these tags in a type libtiff cannot write, such as a text tag
    stored as bytes.
  - A TIFF cover saved as PNG gives it the same EXIF data: the descriptive tags, without
    XMP and Photoshop data, and the Exif and GPS sections.
- **Order:** keyed with a password, public without one (see
  [Keyed and public order](#keyed-and-public-order)).
- **[Standard mode](user-guide.md#standard-and-research-mode)** accepts only 8-bit RGB or
  RGBA PNG, BMP or TIFF covers that need no conversion, hold a single picture and are not
  so flat that a payload of the advised size would change some of their flat colour
  ([trellis coding](#trellis-coding)). Research mode warns about a cover of several frames
  or pages.
- **Other costs in Research mode.** `limg.cost` weighs the changes with another cost
  function, to compare where each one puts them. HILL is the default and the only one
  Standard mode uses. The choice moves only the writer's changes, so revealing is the same
  whatever it was.
  - *WOW* (Holub and Fridrich, 2012) filters each colour plane with the three Daubechies 8
    high-pass filters J-UNIWARD uses, and costs a change by how little texture the filters
    see where it lands, so smooth areas and clean edges cost the most.
  - *S-UNIWARD* (Holub, Fridrich and Denemark, 2014) costs a change the relative change it
    makes to those filters' output, as J-UNIWARD does in a JPEG.
  - *MiPOD* (Sedighi, Cogranne and Fridrich, 2016) estimates each value's noise from its
    neighbourhood and spreads the changes so that a detector which knows that model learns
    the least. Its costs depend on the payload's size.
  - *Uniform* costs every change the same, as plain LSB matching does.
  - Flat colour stays out of reach under each of them while `limg.wet` is on, as it is by
    default. With it off, only HILL still keeps the changes out of flat colour, and the
    others get the flat-colour warning. None of them was measured against detectors.
  - An [extension](extensions.md#cost-functions) can add its own cost functions, which
    follow these as experimental options and get the same wet costs and warning.
- **Destroyed by:** any lossy re-compression (saving as JPEG, most messengers and social
  networks), resizing, colour conversion, editing.

### Sequential LSB replacement (Experimental)

The textbook method, kept in Research mode as a baseline for detector tests: it is what
Weighted Stego, Sample Pairs and chi-square were built to catch, and the Detection row says
so.

- The stream's bits overwrite the lowest bit of each colour value, red, green and blue of
  every pixel, row by row, numbered as for [LSB](#lsb). The prefix takes the first 128
  values and the body follows in sequence.
- The order is never keyed, with or without a password, and nothing weighs where the
  changes go. Half of the values it writes change, and each change moves a value within
  its pair (2k and 2k + 1).
- **Capacity:** 3 bits per pixel. **Output:** PNG, BMP or TIFF, as for LSB. Of the LSB
  image parameters, only `limg.lossy` and `limg.meta` apply to it.
- **Revealing:** Standard mode does not try it, since every image reveal would then cost
  one more decode and, with a password, one more Argon2id run. Reveal it with
  `--research --method lsb-replacement --cipher none`, or with the cipher it was encrypted
  with (see [Stored format](format.md#lsb-replacement)).

### DCT coefficients (JPEG)

The payload is stored in the quantized DCT coefficients of the JPEG itself, so no pixel is
decoded or re-compressed and the result is still a JPEG. CipherStitch changes coefficients
by one over syndrome-trellis codes (Filler, Judas and Fridrich, 2011):

- Every AC coefficient, zero or not, carries its two lowest bits, and the payload is the
  *syndrome* of these bits under fixed codes, shared between the bit planes as for
  [LSB](#trellis-coding). The reader computes it without knowing which coefficients
  changed.
- A JPEG cover's picture before compression is unknown, so CipherStitch estimates it: it
  decompresses each colour component, smooths it with a 3 × 3 Wiener filter and
  compresses it again without rounding. A step towards that estimate costs 0.65 times as
  much as a step away from it, so about three changes in four go that way (cost
  polarization, as HStego does). From a RAW, PNG or TIFF the true rounding errors take its
  place, and nearly every change goes the way the picture lies (see
  [From a RAW, PNG or TIFF](#from-a-raw-png-or-tiff)).
- Each change has a cost (J-UNIWARD, Holub, Fridrich and Denemark, 2014): how much it
  changes the picture's fine detail, as three wavelet filters see it, relative to the
  detail already there. A change in a smooth area or along a clean edge costs more, so
  the changes go to textured areas. The colour components are coarser and calmer than the
  brightness, so they take only a few percent of the changes.
- The code picks a set of changes with a low total cost: about 0.22 changed coefficients
  per hidden bit at 5% of the capacity and 0.24 at 20%, against 0.5 for plain parity
  embedding. Coding both bits, not the parities alone as CipherStitch did before, lowers
  the total J-UNIWARD cost by 14% at 5% and 19% at 20%. The polarization gives some of
  that back (7% and 14% lower), for changes that mostly go the picture's way. Its search
  looks only at the cheapest coefficients, ten per hidden bit, which adds less than 0.1%
  to the total cost and makes it several times faster.
- **Other costs in Research mode.** `dct.cost` weighs the changes with another cost
  function. J-UNIWARD is the default and the only one Standard mode uses, and revealing is
  the same whatever it was. Polarization and a precover's rounding errors tilt each of
  them the same way.
  - *UERD* (Guo, Ni, Su, Tang and Shi, 2015) costs a change the coefficient's quantization
    step over the energy of its block and, at a quarter of the weight, of the eight blocks
    around it. Busy blocks and fine steps are cheap. A coefficient whose neighbourhood has
    no energy at all is left alone. It is about thirty times faster than J-UNIWARD.
  - *Uniform* costs every change the same.
  - Neither was measured against detectors.
  - An [extension](extensions.md#cost-functions) can add its own cost functions, which
    follow these as experimental options. Polarization and rounding errors tilt them too.
- **Capacity:** 0.8 bit per non-zero AC coefficient outside the *lead*. The lead holds a
  fixed 16 bytes in up to 16 384 coefficients, an eighth of a small JPEG (see
  [Trellis channels](format.md#trellis-channels)).
- **Speed and memory** on a 12-megapixel JPEG (Apple M3 Pro): hiding 5% of the capacity
  takes about 3 s and 290 MB of arrays for a typical photo, and about 4.5 s for a noisy
  high-quality one. At half the capacity or more it takes up to 18 s and 850 MB.
  Revealing takes under 1 s.
- **Carrier and output:** JPEG (`.jpg`, `.jpeg`), or a RAW, PNG or TIFF that is
  compressed to JPEG first. The output is always a JPEG. CMYK JPEGs are not supported.
- **What the output keeps:** the cover's quantization tables, chroma sampling, component
  ids, and its APPn and COM segments (JFIF, EXIF, ICC profile, XMP, comments) byte for
  byte and in the same order. The Huffman tables are of the cover's kind: the standard
  ones if the cover uses them, tables optimized for the image otherwise. So the output
  has about the cover's size. It is always baseline without restart markers, because
  jpeglib can write neither progressive JPEGs nor restart intervals (see
  [Staying inconspicuous](security.md#staying-inconspicuous)). It holds only the main
  picture: images a JPEG stores after it, such as the other frames of an MPO, an HDR gain
  map or a motion photo's video, are not kept, and neither is the index that points to
  them.
- **Needs:** the `jpeg` extra (jpeglib, which has no Linux aarch64 wheel), and the `raw`
  extra (rawpy) for camera RAW files.
- **Destroyed by:** re-compression, resizing or editing.

#### From a RAW, PNG or TIFF

A camera RAW (DNG, CR2, CR3, NEF, ARW, RAF, ORF, RW2, PEF, SRW), or a PNG or TIFF that was
never a JPEG, can be the cover too. CipherStitch then compresses it to JPEG itself, once,
and hides the payload while it compresses:

- A RAW is developed by LibRaw with the camera's white balance into 16-bit sRGB, turned
  upright. A PNG or TIFF is read as 8-bit colour or greyscale (16-bit greyscale is
  rounded to 8 bits, not clipped) and turned upright by its EXIF orientation. Pictures
  with transparent pixels, and CMYK or Lab ones, are refused. Only the first frame of an
  animated PNG or the first page of a TIFF is compressed: Standard mode refuses such a
  cover, and Research mode warns about it.
- The compression is libjpeg-turbo's own at quality 90 with 4:2:0 chroma, computed here
  in numpy: its 8-bit colour conversion and chroma downsampling, its integer DCT and its
  quantization. Before anything is hidden, the JPEG therefore has exactly the
  coefficients Pillow writes for the same picture, and without an ICC profile the same
  bytes. A developed RAW is rounded to 8 bits first. Before rounding, each coefficient is
  known to an eighth of a step or better, and so is its *rounding error*: how far, and
  which way, the picture lies from the rounded coefficient.
- That error is side information, as in SI-UNIWARD (Denemark and Fridrich, 2015). A step
  towards the picture's value costs the J-UNIWARD cost times 1 − 2 × |error|, and a step
  away from it 1 + 2 × |error| times. So a coefficient that was nearly halfway between two
  values is cheap to change, and nearly every change moves towards the picture, even if
  that raises its magnitude. A coefficient without side information (an exact value, or a
  tie at halfway) is changed as in a JPEG cover.
- The coefficients that can change and the capacity are the same as for a JPEG cover.
  Readers neither know nor need to know where the JPEG came from.
- **Other chroma sampling in Research mode.** `pre.sub` compresses with 4:2:2 or 4:4:4
  chroma instead of 4:2:0, which stays the default and the only one Standard mode uses.
  The coefficients are still the ones Pillow writes with the same setting. Finer chroma
  gives the colour components more coefficients, so more of the payload can go there. It
  was not measured against detectors.
- **What the output keeps:** the picture, and an RGB ICC profile if the PNG or TIFF has
  one. It is a baseline JPEG with a JFIF segment, component ids 1 to 3 and Huffman tables
  optimized for the image. It has no EXIF: a camera model next to libjpeg's tables would
  not add up, and a serial number would identify the camera.
- **Speed:** developing a 24-megapixel RAW takes a few seconds, and compressing it about a
  second, before embedding starts. Neither can be cancelled. The command line and the app
  do both once to estimate the capacity and again to hide.

### Detectability

Statistical steganalysis can detect LSB and DCT embedding, especially when a large part of
the capacity is used. Both put their changes where the picture is busy, and a small payload
changes few values. Encryption protects the content and hides the structure, and a small
payload keeps the image inconspicuous. DCT leaves flat areas untouched, and so does LSB
unless the payload is large or nearly all of the image is flat. Against a detector trained
on their output, both are harder to detect than OpenStego, steghide or nsF5 at the same
payload (see [Detectability](detectability.md)).
LSB on a JPEG, WebP, greyscale or palette cover is easier still to detect (see
[Staying inconspicuous](security.md#staying-inconspicuous)).
For DCT, a RAW, PNG or TIFF makes the better cover when there is one. Hiding while
compressing it was the hardest to detect of everything measured at 5% and 20% of the
capacity: 0.493 and 0.459 from a TIFF, against 0.462 and 0.394 in a quality 75 JPEG (see
[Detectability](detectability.md#rich-models)).

## Audio

Carriers are 8, 16 and 24-bit PCM WAV files and FLAC files (FLAC needs the `audio`
extra). The output keeps the carrier's format. FLAC is lossless, so the bits survive.
Converting to MP3, AAC or Ogg destroys them.

Only the samples change. A WAV output is the cover with new sample bytes, so every other
chunk stays byte for byte: the format with its channel layout, tags, broadcast and cue
chunks. A FLAC's frames are encoded anew, and its tags, pictures, cue sheet, application
data and padding are kept. Its seek table is left out, since it points into the old frames.

### Audio LSB

The payload is stored in the two lowest bits of each sample of every channel, up to 0.8
bit per sample that is not digital silence. The changes go where the recording is busy
([trellis coding](#trellis-coding)), and no sample moves by more than one step
([LSB matching](#lsb-matching)). The bits are spread over the whole recording, in a keyed
order with a password and a public one without. This is the highest-capacity audio
method. It leaves digital silence (runs of 16 or more equal samples) alone unless the
payload is large or nearly all of the recording is silent, as image LSB does flat colour.
[Standard mode](user-guide.md#standard-and-research-mode) needs 16- or 24-bit samples and
a recording not so silent that a payload of the advised size would change some of its
silence.

### Phase coding

The bits set the phase of 64 frequency bins (bins 32 to 95) in each 4096-sample segment
of the first channel: +π/2 for a 0, −π/2 for a 1. The other channels get the same phase
shift, so they keep their phase differences to the first. A stereo image stays as it
was, and a mono recording stored as stereo stays mono. After the data the shift returns
to zero over 8 segments, and the rest of the recording is left exactly as it was. The
coded bins of the first channel get a magnitude floor well above the rounding noise, so
quantization cannot flip a bit. Another channel gets the floor where it holds the same
signal as the first, so a mono recording stays mono even in silence. A channel that is
silent wherever the data lies stays silent, unless the first channel is silent there too.
About 690 bit/s at 44.1 kHz. The stream is stored in whole Reed–Solomon codewords
([error correction](#error-correction)), and one codeword takes 32 segments, so the
smallest recording that holds anything is 131 072 samples long: about 3 seconds at 44.1
or 48 kHz, 16 seconds at 8 kHz.

### Echo hiding

Each 25 ms segment (at least 512 samples) gets a faint echo: delayed by 1 ms for a 0 or
1.5 ms for a 1. Echo changes are cross-faded over a quarter of a segment. The decoder
compares the cepstrum at the two delays. About 40 bit/s at 44.1 kHz. The stream is stored
in whole Reed–Solomon codewords of 223 bytes ([error correction](#error-correction)), and
each takes about 51 s, so a minute holds 223 bytes of carrier space. A recording shorter
than one codeword holds nothing: 51 seconds at any rate from 20.48 kHz up. Below that
every segment is 512 samples, and at 8 kHz one codeword takes 2 minutes 11 seconds.

### Error correction

Phase coding and echo hiding can flip bits, and a single flipped bit would fail the
authenticated decryption. Both protect the stream with Reed–Solomon codes: 32 parity
bytes per 255-byte codeword, which repairs up to 16 corrupted bytes per codeword (the
`ecc` extra, reedsolo). Both readers decide each bit from floating-point FFT results, the
sign of a phase or a comparison of two cepstrum values, so a bit that was marginal on the
machine that wrote the file may read differently on another. Those 16 bytes are there to
absorb that too.

After embedding, the result is decoded again. A recording that cannot carry the payload
reliably (echo hiding in near silence, heavy clipping) is refused with a capacity error
instead of producing an unreadable file.

### Detectability

Statistical steganalysis can detect all three audio methods. Phase coding and echo hiding
are easier still: anyone who knows the method can extract the bits and check whether
they decode as Reed–Solomon codewords.

## Documents

### Embedded object stream (PDF, DOCX)

The payload is stored whole in one place where documents keep an application's own data.
Pages and text are untouched.

- **PDF:** the private data of an application in the document's page-piece dictionary,
  where editors keep their own data (needs the `documents` extra, pikepdf). Private data
  of other applications is kept, and so is the document's own metadata, byte for byte.
  Password-protected PDFs are refused. A PDF with only owner restrictions keeps them. A
  signed PDF's signature no longer matches after hiding, because the file is saved anew.
- **DOCX:** the printer-settings part, linked from the main document as Word links it.
  The cover's own printer settings are replaced. The part gets the date and compression of
  the main document's entry in the package, as Word gives every entry the same ones. No
  extra needed.
- **In the metadata (Research mode, Experimental):** `obj.loc` metadata stores the payload
  as base64 in one property named `DocumentState`: in a PDF, a description in the XMP
  packet of the document's metadata, and in a DOCX, a custom property. Either is made if
  the cover has none. A payload stored in one place removes any left in the other.
- **Padding:** random bytes follow the payload, up to a random length of at most the
  capacity, so the object's length does not show the payload's size.
- **Capacity:** 64 KiB, or 48 KiB in the metadata, which holds base64. This is a policy
  (`SOFT_LIMIT` in `src/cipherstitch/methods/object_stream.py`, Research mode's
  `obj.limit`), not a format limit.

### Whitespace padding (DOCX)

The bits become invisible whitespace at the end of paragraphs: a space for 0 and a
no-break space for 1, at most 64 per paragraph (8 bytes). Random bits follow the payload,
up to a random length of at most the capacity, so the amount of whitespace does not show
the payload's size. The bits are spread evenly over the paragraphs. Spaces and no-break
spaces a paragraph already ended with are removed first, so the visible text does not
change.
Only the main document is written anew, by python-docx, with the cover's XML
declaration. Every other entry stays the cover's, byte for byte and in the same order,
and every entry keeps the cover's date and compression. Needs the `documents` extra
(python-docx).

In Research mode `ws.sym` space-tab (Experimental) writes a 1 as a tab instead of a
no-break space. A tab moves to the next tab stop and can wrap past the right margin, so a
long run may add a line. A cover whose paragraphs already end in tabs is refused, because
its tabs would read as bits and removing them could break a form or a tab leader. For the
same reason tab padding is never written over tab padding.

Storing a payload in a DOCX removes what any other method or location left behind: the
printer settings, the metadata property, and padding of spaces and no-break spaces. So a
document holds one payload at a time. Research mode's tab padding is the exception: a
later payload of another kind leaves its tabs in place, since a cover's own tabs look the
same.

### Detectability

Neither the object nor its place is named after CipherStitch, but anyone who inspects the
file closely can find that the object holds no real application data.
Unusual trailing whitespace shows *that* something is hidden to anyone who inspects the
file, whatever the cipher.

## Text

### Zero-width characters

`.txt` and `.md` files (UTF-8, up to 16 MiB) carry two bits per insertion point: one of
four invisible characters (U+200B, U+200C, U+2063 or U+2060). U+200D is not one of them,
because it joins the letters next to it and so changes the last letter of a word in Arabic
and the other cursive scripts. In Research mode `zw.sym` takes two symbols (U+200B and
U+200C, one bit per point) or eight (these four, then U+2061, U+2062, U+2064 and U+FEFF,
three bits per point).

A character may be followed by a symbol when all of these hold:

- It is not whitespace and not ASCII punctuation, and the next character is not ASCII
  punctuation.
- It ends a word (the next character is whitespace, or there is none), or both characters
  are letters, digits or punctuation marks. These are Unicode 3.2 categories, so the
  points do not change between Python versions.
- Inside a word, neither character is in a cursive script (Arabic, Syriac, N'Ko,
  Mongolian and others) or a conjoining Hangul jamo. U+200C would part the letters there,
  so these scripts get points at word ends only.
- It is not in text that a symbol would break: a URL, an e-mail address, a domain or a
  file name in any script (`example.com`, `приклад.укр`, `notes.txt`), Markdown code
  (inline spans and fenced blocks), or an HTML tag or entity. A dot next to Chinese,
  Japanese, Thai, Lao, Khmer or Burmese letters is not taken for a domain's. These
  scripts put no spaces between words, so that would cost the whole sentence its points.

So Markdown syntax (`#`, `-`, `**`, `](`…), links, code and HTML stay intact. A symbol is
never placed before a combining mark or inside an emoji sequence. Plain prose keeps its
capacity, while a README full of code and links loses about a quarter of it.

Research mode's relaxed rules (`zw.rules` relaxed) drop all of that: a symbol may follow
any character but a carriage return, unless the next one is a combining mark. Symbols
then land inside URLs, Markdown, code and emoji sequences, between an emoji and its skin
tone, or around U+200D.

- **Visible changes:** none in most text. With ligature fonts, U+200C inside a word can
  part a ligature such as "fi", and U+200B lets a line break inside a word.
- **Search:** a word with a symbol inside may not be found.
- **Reversible:** removing the symbols gives back the original text exactly.
- **Carrier restriction:** a text that already contains one of the symbols in use cannot
  be used as a carrier. A U+FEFF at the very start is the text's byte-order mark and is
  always kept. Emoji joined with U+200D are fine.
- **Destroyed by:** editors, web forms and messengers that strip zero-width characters.
  Treat a text carrier as fragile.

### Detectability

Zero-width characters show *that* something is hidden to anyone who looks for them.
