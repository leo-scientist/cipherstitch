# Detectability

How hard is it to tell an image with a CipherStitch payload from one without? This page
gives the measured answer for the two image methods, LSB and JPEG DCT coefficients, in
four rounds on the same images: round 4 for the present writers, rounds 1 to 3 for earlier
development versions of them. The code, the protocols and every table are in
[`benchmarks/detectability/`](../benchmarks/detectability/). Every rich-model number for
image LSB was corrected after round 4 found a flaw in the study's features (see
[A correction to the LSB numbers](#a-correction-to-the-lsb-numbers)).

## In short

P_E is a detector's error rate when images with and without a payload are equally
likely: 0.5 is a coin toss, 0 a detector that is never wrong. The strongest detector here
was trained on 1,500 pairs of CipherStitch's own output (each cover with and without a
payload of the same size), so it knows everything but the password. It is the strongest
detector this study ran, not the strongest there is: a CNN trained the same way would
likely do better, so these numbers say at most how hard detection is.

| Payload and cover | CipherStitch now | Round 3 | Round 2 | Round 1 | Other tools, same payload |
|---|---|---|---|---|---|
| Image LSB, PNG, 5% | **0.474** | 0.475 | 0.467 | 0.406 | HStego 0.465, OpenStego 0.273 |
| Image LSB, PNG, 20% | **0.369** | 0.368 | 0.327 | 0.214 | HStego 0.338, OpenStego 0.135 |
| DCT, JPEG at quality 75, 5% | **0.462** | 0.463 | 0.442 | 0.391 | nsF5 (simulated) 0.433, HStego 0.367, steghide 0.109 |
| DCT, JPEG at quality 75, 20% | **0.394** | 0.389 | 0.242 | 0.146 | HStego 0.341, nsF5 (simulated) 0.179, steghide 0.009 |
| DCT from a TIFF, 5% | **0.493** | 0.493 | 0.494 | 0.425 | |
| DCT from a TIFF, 20% | **0.459** | 0.452 | 0.456 | 0.168 | |

A payload of 5% is, in each image, the bytes round 1's writer hid at 5% of its capacity,
and every number in a row is for those bytes. Image LSB's present capacity is smaller (80%
of the values that are neither flat nor at an end of the range), so for it they are about
6% and 25% of the capacity.

- **The present writers are as hard to detect as round 3's,** within the noise in every
  condition, and every target of round 4 is met. Against round 2's at the same payload,
  image LSB gains 0.01 at 5% and 0.04 at 20%, DCT on a JPEG 0.02 and 0.15. DCT from a TIFF
  stays as hard to detect as before.
- **HStego** (S-UNIWARD and J-UNIWARD) comes closest, and CipherStitch is harder to detect
  at both payloads: by 0.01 and 0.03 for image LSB, by 0.10 and 0.05 for DCT. Image LSB's
  lead at 5% is small, though larger than the noise. In round 2 HStego was ahead at 20%
  (see [Round 3](#round-3)).
- **Popular tools** (zsteg, StegExpose, binwalk, stegdetect, StegSeek and others) flag
  CipherStitch's files no more often than the covers, in every round. The classical
  attacks (Weighted Stego, Sample Pairs and chi-square on LSB, the F5 estimator on DCT)
  give 0.48 or more. Weighted Stego saw a faint trace of round 3's image LSB at 20%,
  0.483, which the present writer removes: 0.497 (see
  [why](#popular-tools-and-classical-attacks)).
- **The positions anyone can compute** give nothing away in either method (see
  [E-K](#e-k-the-leads-public-positions)).
- **The app advises a payload size for each carrier,** where this detector's error rate is
  0.45 or more: about 11% of a 512 × 512 PNG's capacity for image LSB, 7% of a typical
  quality 75 JPEG's for DCT, 17% of a precover's, and 1.6% of a 12-megapixel PNG's (see
  [The payload advice](#the-payload-advice)).
- **DCT from a camera RAW, PNG or TIFF is the hardest to detect** of everything measured,
  at 5% and 20%. Its side information gained 0.08 at 20% in round 2, and the quality 90
  JPEG it compresses to is a better cover than one at quality 75 (see
  [DCT from a precover](#dct-from-a-precover)).

## Round 4

### What changed in round 4

- **The ideal split.** The writer shares each stream's bits between the values' second bits
  and their parities as an ideal ternary code would, a little over half for the second
  bits, and records the share in the file. Round 3's writer gave each half, so the parities
  took a few more changes than they should (see
  [why that mattered](#popular-tools-and-classical-attacks)).
- **The check matrix.** The syndrome-trellis codes take their columns from the label, of
  4,096 tried, that coded best on real covers
  ([format](format.md#syndrome-trellis-codes)). On 150 of the study's covers it lowers
  image LSB's distortion by 0.8% (2.2% at 20%) and leaves DCT's about the same.

### How round 4 was measured

- **The same 3,000 images per set,** covers, detectors and splits as before, round 1's
  payload bytes, and round 3's 5% and 20% conditions for the three writers.
- **No root rates and no crops:** round 3's rate holds for a method unless one of its
  conditions falls below round 3's.
- Tiers 1 and 2 as in round 3. The protocol is in
  [`results/round-4/protocol.md`](../benchmarks/detectability/results/round-4/protocol.md).

### Rich models in round 4

| Condition | 5% | 20% |
|---|---|---|
| CipherStitch image LSB, round 4 | 0.474 ± 0.004 | 0.369 ± 0.003 |
| CipherStitch image LSB, round 3 | 0.475 ± 0.004 | 0.368 ± 0.005 |
| HStego, S-UNIWARD | 0.465 ± 0.003 | 0.338 ± 0.005 |
| CipherStitch DCT, round 4 | 0.462 ± 0.006 | 0.394 ± 0.006 |
| CipherStitch DCT, round 3 | 0.463 ± 0.004 | 0.389 ± 0.006 |
| HStego, J-UNIWARD | 0.367 ± 0.005 | 0.341 ± 0.009 |
| CipherStitch DCT from a TIFF, round 4 | 0.493 ± 0.004 | 0.459 ± 0.004 |
| CipherStitch DCT from a TIFF, round 3 | 0.493 ± 0.004 | 0.452 ± 0.004 |

Tier 3b, mean P_E ± std over 10 splits, about 1,500 testing pairs per split.

- **Every condition is within the noise of round 3's,** and every target holds: round 2's
  targets, not below round 3, and above HStego. So round 3's root rates hold for all three
  writers.
- The two changes lower the cost of the changes a little, too little for the rich models
  to see.

### Tiers 1 and 2 in round 4

- **Tier 1,** on 500 files per condition. StegExpose flags 7.0% of image LSB's files at 5%
  and 7.6% at 20%, against 7.0% of the covers, and zsteg 0.8% and 1.0% against 0.8%.
  stegdetect flags 6.9% and 6.4% of DCT's files against 6.4%, and OutGuess's extraction
  3.7% and 3.6% against 3.6%, within the intervals. No other tool flags any file.
- **Tier 2,** on all 3,000 images. On image LSB, Weighted Stego, Sample Pairs and
  chi-square give 0.497 to 0.503 at both payloads, so round 3's trace at 20% (0.483) is
  gone. The F5 estimator gives 0.491 and 0.488 on DCT, 0.501 and 0.496 on DCT from a TIFF.

### A correction to the LSB numbers

Round 4's first rich-model numbers for image LSB were far lower than round 3's, 0.296 and
0.274, though its writer changes a cover just as round 3's does. The cause was in the
study, not in CipherStitch. sealwatch, which computes the rich-model features, appends two
of SRMQ1's submodels (`s35_spam11_q1` and `s5x5_spam14hv_q1`) in an order that follows each
process's hash seed. The features are computed 20 images at a time in parallel, so every
batch had one of two orders, and a detector partly learnt the order instead of the payload.
Every earlier round had the same flaw, mostly with smaller effects.

`features.py` now puts the submodels in a fixed order. The stored features were put into
that order, and every image LSB detector of every round was trained again. The numbers on
this page, in the README and in `results/` are the corrected ones. JPEG features were never
affected, nor tiers 1 and 2. The largest changes:

| Condition | Before | Corrected |
|---|---|---|
| HStego, S-UNIWARD, 5% | 0.347 | 0.465 |
| Image LSB, round 2, 5% | 0.436 | 0.467 |
| Image LSB, round 3, root rate 90 | 0.403 | 0.438 |
| Image LSB, round 3, 5% | 0.484 | 0.475 |

Image LSB's root rate rises from 67 to 76 as a result, so the app advises about 11% of a
512 × 512 PNG's capacity rather than 10%. The rounds' other conclusions stand, except where
the sections below say otherwise. The protocols' change logs give the details.

## Round 3

### What changed

- **Two bits per value.** Image and audio LSB and DCT store half the payload in each
  value's second lowest bit and half in its lowest, with two-layer syndrome-trellis codes
  (the multi-layered construction of Filler, Judas and Fridrich). A step up and a step down
  both carry data, so the same payload takes 4% to 9% fewer changes, at 10% to 19% less
  total cost. Round 2's writers coded the lowest bits alone. Image LSB's capacity also
  leaves out the values at the ends of the range, which cannot flip their second bit.
- **DCT on a JPEG: cost polarization.** The writer estimates the picture before compression:
  each component decompressed, smoothed by a 3 × 3 Wiener filter and compressed again
  without rounding. A step towards that estimate costs 0.65 times the J-UNIWARD cost, as in
  HStego, so about three changes in four go that way.
- **DCT from a precover:** a step away from the precover costs 1 + 2 × |error| times the
  J-UNIWARD cost, rather than never being taken. Nearly every change still goes towards it.
- **The payload advice:** the rates are calibrated again, and DCT from a precover gets a
  rate of its own.

### How it was measured

- **The same 3,000 images per set,** covers, detectors and splits as rounds 1 and 2, and
  round 1's payload bytes. So every row compares with round 2's and with the tools', which
  were not run again.
- **Root rates** 25, 40 and 60 for image LSB, 10, 18 and 30 for DCT, and 45, 65 and 90 for
  DCT from a TIFF, chosen to bracket the crossing of 0.45. Image LSB stayed above 0.45 at
  all three, so a fourth rate, 90, was measured afterwards (the protocol's change log).
- **No crops and no P-noSI** this time: round 2's scaling check stands, and polarization
  would make DCT on Pillow's JPEG a third kind of writer rather than the one without side
  information.
- Tiers 1 and 2 and E-K as in round 2. The protocol is in
  [`results/round-3/protocol.md`](../benchmarks/detectability/results/round-3/protocol.md).

### Rich models

| Condition | 5% | 20% |
|---|---|---|
| CipherStitch image LSB, round 3 | 0.475 ± 0.004 | 0.368 ± 0.005 |
| CipherStitch image LSB, round 2 | 0.467 ± 0.004 | 0.327 ± 0.008 |
| HStego, S-UNIWARD | 0.465 ± 0.003 | 0.338 ± 0.005 |
| CipherStitch DCT, round 3 | 0.463 ± 0.004 | 0.389 ± 0.006 |
| CipherStitch DCT, round 2 | 0.442 ± 0.003 | 0.242 ± 0.007 |
| HStego, J-UNIWARD | 0.367 ± 0.005 | 0.341 ± 0.009 |
| CipherStitch DCT from a TIFF, round 3 | 0.493 ± 0.004 | 0.452 ± 0.004 |
| CipherStitch DCT from a TIFF, round 2 | 0.494 ± 0.004 | 0.456 ± 0.005 |

Tier 3b, mean P_E ± std over 10 splits, about 1,500 testing pairs per split.

- **Image LSB** gains 0.008 at 5% and 0.041 at 20% from the two-bit codes alone. A
  simulation predicted 0.02 to 0.04.
- **DCT on a JPEG** gains 0.02 at 5% and 0.15 at 20%, most of it from polarization. A check
  on 1,000 of the images before this round (a detector trained on fewer pairs, so every
  P_E is higher) gave at 20%: 0.289 for round 2's writer, 0.319 with the two-bit codes,
  0.418 with polarization too, and 0.378 for HStego. Changing DC coefficients as well, as
  HStego does, added nothing (0.324 without polarization, 0.415 with it), so the writer
  does not.
- **DCT from a TIFF** stays where it was. Its side information already sends nearly every
  change the cheap way, so two bits per value gain nothing there, as the simulation
  predicted.
- **Why HStego led at 20% in round 2.** Under J-UNIWARD its changes cost about as much as
  round 2's writer's, but three in four went towards its Wiener estimate of the picture,
  and a sixth were DC coefficients. Its weak spot at 5% is its header: 320 bits in 640
  random coefficients, most of them zeros, whatever the payload.

### The payload advice

The app advises r √N bits for a carrier, framing included, with r a root rate per method
(`risks.ROOT_RATES`, and `risks.PRECOVER_ROOT_RATE` for DCT from a RAW, PNG or TIFF). By
the square-root law a payload of that size is about as hard to detect in any cover from
one source.

| Method | Root rate | Share of a 512 × 512 cover's capacity | P_E |
|---|---|---|---|
| Image LSB | 25 | 3.6% | 0.485 ± 0.002 |
| Image LSB | 40 | 5.8% | 0.476 ± 0.004 |
| Image LSB | 42.6 (round 1's 5% bytes) | 6.3% | 0.475 ± 0.004 |
| Image LSB | 60 | 8.7% | 0.464 ± 0.004 |
| Image LSB | 90 | 13.1% | 0.438 ± 0.006 |
| DCT | 10 | 5.5% | 0.463 ± 0.004 |
| DCT | 18 | 9.9% | 0.437 ± 0.005 |
| DCT | 30 | 16.6% | 0.403 ± 0.005 |
| DCT from a TIFF | 45 | 20.8% | 0.443 ± 0.006 |
| DCT from a TIFF | 65 | 30.1% | 0.414 ± 0.010 |
| DCT from a TIFF | 90 | 41.6% | 0.360 ± 0.006 |

- **The rates.** DCT crosses 0.45 between 10 and 18, at **14**, twice round 2's 7. DCT from
  a TIFF crosses it below 45, on the line from (0, 0.5), at **39**. Image LSB crosses it
  between 60 and 90, at **76**, above round 2's 58.
- **What that means.** About 11% of a 512 × 512 PNG's capacity and 1.6% of a 12-megapixel
  PNG's for image LSB, 7% of a median quality 75 JPEG's of that size for DCT, and 17% of a
  median 512 × 512 precover's. Audio LSB borrows image LSB's rate, which nothing here measures.
- **Below the size the rates were measured on** (a 512 × 512 PNG, the median quality 75
  JPEG of that size, and the median precover's 87,863 non-zero AC coefficients) the advice
  keeps the share of the capacity it has at that size, since round 2's scaling check found
  smaller covers easier to detect at the same rate.

### E-K: the lead's public positions

With a password, only the 16-byte salt sits at positions anyone can compute: the lead,
16,384 values or coefficients.

| Statistic | 1% | 5% |
|---|---|---|
| Image LSB, Weighted Stego on the lead | 0.497 | 0.501 |
| Image LSB, Sample Pairs on the lead | 0.500 | 0.501 |
| Image LSB, targeted discriminant | 0.500 | 0.486 |
| Image LSB, the same weighted by HILL's change probabilities | 0.505 | 0.482 |
| DCT, histogram of the lead | 0.493 | 0.496 |
| DCT, the same weighted by J-UNIWARD's change probabilities | 0.493 | 0.502 |

Every statistic stays at 0.48 or more, and the positive controls (±1 on the lead's cheapest
1.6%) give 0.448 and 0.359 as in round 2. Polarization leaves no trace in the lead.

### Popular tools and classical attacks

- **Tier 1,** on 500 files per condition. StegExpose flags 7.4% of image LSB's files at 5%
  and 7.2% at 20%, against 7.0% of the covers, and zsteg 0.6% against 0.8%. stegdetect flags
  6.5% and 6.6% of DCT's files against 6.4% of the covers, and OutGuess's extraction 3.0%
  and 4.4% against 3.6%, within the intervals. No other tool flags any file.
- **Tier 2,** on all 3,000 images. On image LSB, Sample Pairs and chi-square give 0.497 to
  0.501 at both payloads, and Weighted Stego 0.501 at 5% and 0.483 at 20%: a faint trace at
  20%, where round 2 gave 0.501, still above the target of 0.48. The F5 estimator gives
  0.495 and 0.488 on DCT, though polarization lowers magnitudes more often than it raises
  them, and 0.502 and 0.500 on DCT from a TIFF.
- **Why Weighted Stego saw image LSB at 20%, and the fix.** It estimates LSB replacement,
  which moves a value within its pair, 2k and 2k + 1. A step that changes only the parity
  stays within the pair, and one that also flips the second bit leaves it. Round 3's
  writer gave each bit plane half of the payload, while an ideal ternary code at 20% gives
  the second bits about 50.4%. So the parities took a few more changes than they should:
  50.45% of the changes stayed within a pair, against 50.04% for the ideal code, and
  Weighted Stego's term for that rose in 94% of 1,000 images checked. At the advised size
  it saw nothing even then: 0.501 at root rate 60 and 0.493 at 90. The writer now gives
  the second bits the ideal code's share of each payload and records it in the file.
  Hidden again with it, round 1's 20% payloads in the same 3,000 covers keep 50.22% of the
  changes within a pair, and Weighted Stego gives 0.503 ± 0.003, Sample Pairs 0.501 and
  chi-square 0.498, all scored as tier 2 is. Round 4 measured the writer itself: 0.497.

## Round 2

Round 3 replaced these writers and the payload advice, and kept these numbers as the
"before".

### What changed

- **Image LSB:** HILL costs over syndrome-trellis codes, with ±1 changes. Flat areas are
  left out of the capacity, which is 80% of the values that are not flat. Round 1's writer
  used Hamming codes.
- **DCT:** J-UNIWARD costs, ±1 changes either way, and zero coefficients may change too.
  Round 1's writer lowered magnitudes at UERD costs.
- **The precover** is compressed exactly as libjpeg-turbo compresses it: its JPEG has
  Pillow's coefficients for all 3,000 TIFFs. Side information scales J-UNIWARD's costs as
  SI-UNIWARD does.
- **The payload advice** follows the square-root law, with rates calibrated here.

### How it was measured

- **The same 3,000 images per set,** covers, detectors and splits as round 1. Round 1's
  validation gate carries over, since the feature, metric and ensemble code computes what
  it computed then.
- **Payloads:** each output carries, in each image, the bytes round 1's writer hid there.
- **Root rates,** in bits per square root of N, the values the capacity counts (image LSB:
  those that are not flat, DCT: the non-zero AC coefficients): 140, 210 and 300 for image
  LSB, 12, 19 and 28 for DCT. They ran on the 512 × 512 images and on 3,000 crops of
  256 × 256 per set, the four quadrants of the first 750 images, with a crop always in the
  same half of a split as its picture.
- **DCT from a TIFF:** from the TIFF with side information (P-SI), and on Pillow's JPEG at
  the precover's settings (quality 90, 4:2:0) without it (P-noSI). Both carry round 1's
  P-SI bytes and are told apart from that JPEG, which has the precover's coefficients.
- Tiers 1 and 2 and E-K as in round 1. The precover conditions had no tier 1.
- **HStego** v0.6.1 is another tool, at the same payloads: S-UNIWARD on PNG, J-UNIWARD on
  JPEG. Its released version refuses a payload above 5% of its own capacity, which is every
  20% payload here and 24% of the JPEG payloads at 5%, so the study lifts that limit and
  HStego carries the same bytes as the other tools. It failed on 1 and 2 of the PNGs (its
  trellis code gave up) and on 13 and 86 of the JPEGs (it reported no capacity), which are
  left out.
- The protocol was written to
  [`results/round-2/protocol.md`](../benchmarks/detectability/results/round-2/protocol.md)
  before any detector ran. Its change log records the owner's decisions: the precover
  conditions were dropped to save compute and brought back after the rest was measured,
  the calibration took one more measured point, and the advice changed for small covers
  (below).

### Rich models

| Condition | 5% | 20% |
|---|---|---|
| CipherStitch image LSB, round 2 | 0.467 ± 0.004 | 0.327 ± 0.008 |
| CipherStitch image LSB, round 1 | 0.406 ± 0.004 | 0.214 ± 0.006 |
| HILL, ternary, at the bound (simulated) | 0.474 ± 0.004 | 0.369 ± 0.005 |
| HStego, S-UNIWARD | 0.465 ± 0.003 | 0.338 ± 0.005 |
| OpenStego RandomLSB | 0.273 ± 0.006 | 0.135 ± 0.004 |
| CipherStitch DCT, round 2 | 0.442 ± 0.003 | 0.242 ± 0.007 |
| CipherStitch DCT, round 1 | 0.391 ± 0.007 | 0.146 ± 0.008 |
| nsF5 (simulated) | 0.433 ± 0.006 | 0.179 ± 0.005 |
| HStego, J-UNIWARD | 0.367 ± 0.005 | 0.341 ± 0.009 |
| steghide | 0.109 ± 0.004 | 0.009 ± 0.002 |

Tier 3b, mean P_E ± std over 10 splits, 1,500 testing pairs per split (DCT at 5%: 1,456,
where the payload fits).

- **Image LSB** makes 0.20 changes per hidden bit at 5% and 0.23 at 20%. A binary trellis
  over HILL's costs stays short of ternary HILL at the bound, by 0.007 and 0.043. The plan
  set its targets at 0.46 and 0.33: the first is met, the second missed by 0.003.
- **DCT** is above nsF5 at both payloads by more than the larger std, by 0.009 and 0.063.
  The plan's targets, 0.45 and 0.25, are missed by 0.008 each.
- **HStego** is harder to detect than CipherStitch at 20%, by 0.011 on PNG and 0.10 on
  JPEG. At 5% the two are level on PNG, and HStego is easier to detect on JPEG. Its ternary
  codes make fewer changes per payload bit: 0.14 and 0.17 on PNG against CipherStitch's
  0.20 and 0.23, and 0.23 against 0.28 on JPEG at 20%. On JPEG its error rate barely falls
  from 5% to 20% (0.367 to 0.341), so something other than the payload's size gives part
  of it away there. It puts 13% of its changes in the colour components, against
  CipherStitch's 5%, and its code points to its header: 320 bits in the first 640 cover
  values of its random order, whatever the payload (see [round 3](#rich-models)).

### DCT from a precover

Round 2 measured the gain of side information. Round 3's P-SI is in its
[rich models](#rich-models) table.

| Condition | 5% | 20% |
|---|---|---|
| P-SI: from the TIFF, with side information | 0.494 ± 0.004 | 0.456 ± 0.005 |
| P-noSI: Pillow's JPEG at the precover's settings, without | 0.485 ± 0.004 | 0.373 ± 0.005 |
| P-SI, round 1 | 0.425 ± 0.007 | 0.168 ± 0.013 |
| P-noSI, round 1 | 0.442 ± 0.005 | 0.185 ± 0.010 |

Tier 3b, 1,488 and 1,499 testing pairs per split. Round 2 tells both from Pillow's JPEG,
round 1 from its own precover's JPEG (P-0).

- **Side information now helps.** P-SI is above P-noSI by 0.009 at 5% and 0.083 at 20%,
  more than the larger std at both. The plan's targets (0.47 and 0.30, and at least 0.03
  above P-noSI at 20%) are met. Round 1's writer did worse with side information than
  without.
- **More changes, in better places.** P-SI changes 0.26 coefficients per payload bit at 5%
  and 0.31 at 20%, against 0.23 and 0.28 without side information. Nearly all of them (99
  to 100%) move a coefficient towards the picture's own value, where a change adds the
  least distortion.
- **These are not the quality 75 rows' payloads.** A quality 90 JPEG has more non-zero
  coefficients (a median of 88,000 here, against 64,000 at quality 75), so 5% and 20% of
  its capacity are about 350 and 1,600 bytes, against 250 and 1,200. The cover itself
  accounts for part of the gap: without side information the same writer gives 0.373 at
  20% here, against 0.242 on quality 75.
- **The advice was far on the safe side here.** It used DCT's rate, 7, for a precover too,
  while P-SI's 20% payloads, a median root rate of 45, still gave 0.456. Round 3 gave
  precovers a rate of their own.
- **The F5 estimator** gives 0.501 and 0.495 for P-SI, 0.500 and 0.499 for P-noSI.

### The payload advice in round 2

The app advised r √N bits for a carrier, framing included, with r a root rate per method
(`risks.ROOT_RATES`). By the square-root law a payload of that size is about as hard to
detect in any cover from one source.

| Method | Root rate | Share of a 512 × 512 cover's capacity | P_E |
|---|---|---|---|
| Image LSB | 42.6 (round 1's 5% bytes) | 6.2% | 0.467 ± 0.004 |
| Image LSB | 140 | 20.3% | 0.361 ± 0.005 |
| Image LSB | 210 | 30.4% | 0.297 ± 0.005 |
| Image LSB | 300 | 43.5% | 0.238 ± 0.004 |
| DCT | 12 | 6.6% | 0.419 ± 0.005 |
| DCT | 19 | 10.5% | 0.366 ± 0.008 |
| DCT | 28 | 15.5% | 0.303 ± 0.005 |

- **The rates.** DCT gave less than 0.45 at every measured rate, so its rate is where a
  line from (0, 0.5) through the smallest measured one crosses 0.45: **7**. A power law
  through its points gives 7.7. Round 1's 5% bytes are one root rate on every PNG, since
  every 512 × 512 PNG has the same N, so they count as a measured rate for image LSB (the
  owner's decision), and image LSB crosses 0.45 between them and 140, at **58**. On JPEGs
  they spread over rates 8 to 12.
- **What that means.** About 8.4% of a 512 × 512 PNG's capacity and 3.6% of a median quality
  75 JPEG's of that size. The app used 33 for image LSB at the time, about 4.7%, since
  before the [correction](#a-correction-to-the-lsb-numbers) round 1's 5% bytes measured
  below 0.45. Audio LSB borrows image LSB's rate, which nothing here measures.

| Method | Root rate | P_E, 512 × 512 | P_E, 256 × 256 crops | Share of a crop's capacity |
|---|---|---|---|---|
| Image LSB | 140 | 0.361 ± 0.005 | 0.275 ± 0.009 | 45.8% |
| Image LSB | 210 | 0.297 ± 0.005 | 0.193 ± 0.008 | 68.6% |
| Image LSB | 300 | 0.238 ± 0.004 | 0.107 ± 0.008 | 97.9% |
| DCT | 12 | 0.419 ± 0.005 | 0.387 ± 0.006 | 13.8% |
| DCT | 19 | 0.366 ± 0.008 | 0.315 ± 0.005 | 22.5% |
| DCT | 28 | 0.303 ± 0.005 | 0.234 ± 0.005 | 33.3% |

- **The law does not hold from 256 × 256 to 512 × 512.** At the same root rate the crops are
  easier to detect, by 0.03 to 0.13, and at the same share of the capacity harder (image
  LSB at about 45%: 0.275 on the crops, 0.238 on the full images). What a cover hides
  equally well grows faster than the square root of its size here.
- **So the app keeps, below the size the rates were measured on, the share of the capacity
  it has at that size** (`risks.CALIBRATED_ELEMENTS`: a 512 × 512 PNG, and the median
  quality 75 JPEG of that size), by the owner's decision. Above it the square-root law
  applies, which on this evidence errs on the safe side. Covers larger than 512 × 512 were
  not measured.

### E-K in round 2

| Statistic | 1% | 5% | ±1 on the cheapest 1.6% |
|---|---|---|---|
| Image LSB, Weighted Stego on the lead | 0.501 | 0.500 | 0.500 |
| Image LSB, Sample Pairs on the lead | 0.499 | 0.501 | 0.500 |
| Image LSB, targeted discriminant | 0.501 | 0.483 | 0.478 |
| Image LSB, the same weighted by HILL's change probabilities | 0.502 | 0.491 | 0.448 |
| DCT, histogram of the lead | 0.495 | 0.487 | 0.384 |
| DCT, the same weighted by J-UNIWARD's change probabilities | 0.497 | 0.486 | 0.359 |

The last column is a positive control on the covers, changing the lead's cheapest values
as an adaptive writer would, more often than CipherStitch does. Every statistic stays at
0.48 or more on CipherStitch's output. E-K would count as a finding at 0.40 or less, and
the plan's target is 0.45: it is clean. At 1%, DCT holds a payload in 1,257 of 3,000
images.

### Popular tools and classical attacks in round 2

- **Tier 1,** on 500 files per condition. StegExpose flags 7.0% of image LSB's files at 5%
  and 8.0% at 20%, against 7.0% of the covers, and zsteg 0.8% and 0.6% against 0.8%.
  stegdetect flags 6.3% and 6.6% of DCT's files against 6.4% of the covers, and OutGuess's
  extraction 3.5% and 5.6% against 3.6%, within the intervals. StegSeek finds nothing, and
  exiftool finds no tag that differs from the cover's. No other tool flags any file.
- **Tier 2,** on all 3,000 images. Weighted Stego, Sample Pairs and chi-square give 0.495 to
  0.501 on image LSB at both payloads, and the F5 estimator 0.500 and 0.495 on DCT, where
  round 1's writer gave 0.439 and 0.252, and 0.495 to 0.501 on DCT from a TIFF. They see
  nothing in HStego's files either.

### How many images

On round 1's features, an ensemble trained on fewer images is weaker and gives a higher
P_E: image LSB at 20% gives 0.401 on 200 images, 0.326 on 500 and 0.274 on 1,000, against
0.214 on 3,000. Numbers from a few hundred images, such as the README's provisional ones
before this round, overstate how hard detection is. Round 2 uses all 3,000.

## Round 1

The "before": the measurement that led to round 2's changes. Its findings, and what came of
them, are under [Findings](#findings).

### What was measured

#### Covers

The 512 × 512 TIFFs of ALASKA v2 (Cogranne, Giboulot and Bas, 2019), from about 40
cameras and phones, CC BY-NC-ND 4.0. The study uses two disjoint sets of 3,000 images:

- the `lsb` set, saved as PNG for image LSB
- the `jpeg` set, compressed by Pillow at quality 75 (4:4:4, the covers of DCT on a JPEG),
  by CipherStitch's own precover compressor (P-0: quality 90, 4:2:0), and by Pillow at
  P-0's settings (quality 90, 4:2:0, optimized Huffman tables).

The monochrome Leica and ALASKA's FLATS and TEXTURES series are left out, and so are images
whose pixels are more than half grey (R = G = B). The image list and the SHA-256 of every
download are in `indices.txt` and `sha256.txt`.

#### Payloads

Random bytes, encrypted with the default cipher (AES-256-GCM) as the app does it. Only the
key derivation is made cheaper in the harness, since the sealed bytes look random either
way. CipherStitch hides at 1, 5, 10, 20 and 50% of the capacity. A JPEG whose framing
alone takes more than the fill is left out of that condition: 1,706 of 3,000 at 1% on the
quality 75 covers, and 996 for the precover conditions.

#### Conditions

| Name | What | Told apart from |
|---|---|---|
| `lsb` | CipherStitch image LSB | the PNG cover |
| `dct-q75` | CipherStitch DCT on a JPEG | the quality 75 cover |
| P-SI | CipherStitch DCT from the TIFF itself, with side information | P-0 |
| P-noSI | CipherStitch DCT on P-0's coefficients, without side information | P-0 |
| J-noSI | CipherStitch DCT on Pillow's JPEG at P-0's settings | that JPEG |
| E-C | P-0 itself, nothing hidden | Pillow's JPEG of the same TIFF |
| OpenStego | RandomLSB with AES128 | the PNG cover |
| steghide | default settings | the quality 75 cover |
| Simulated | LSB replacement, LSB matching, LSB matching with Hamming codes' efficiency, HILL, nsF5 (conseal, at the bound) | their covers |

The simulated references embed the bits that CipherStitch's output stores at 5% and 20%.
nsF5 also runs at 0.2 bits per non-zero AC coefficient for the validation gate.

#### Detectors

- **Tier 1, popular tools,** on 500 files per condition: zsteg, StegExpose, binwalk,
  pngcheck, foremost, strings, exiftool, the extraction commands of OpenStego, steghide and
  OutGuess, StegSeek (on 30 files: it takes 36 s per file) and stegdetect. Each gives a
  verdict per file by a fixed rule. We report the share of files flagged, with a Wilson 95%
  interval. StegExpose's fused score and binwalk's entropy are also scored like tier 2.
- **Tier 2, classical attacks,** on all 3,000 images: Weighted Stego, Sample Pairs and
  chi-square per colour for LSB, and the F5 calibration estimator on luminance for JPEG.
  Each estimate is a score, with its threshold chosen on a training half.
- **Tier 3b, rich models,** trained on our own output:
  - LSB: the spatial rich model SRMQ1 on each colour plus the colour rich model CRM, 43,663
    features.
  - JPEG: DCTR on Y, Cb and Cr plus GFR on Y, 41,000 features.
  - Classifier: sealwatch's FLD ensemble.
- **Splits:** 10 random 50/50 splits of the images that keep each cover and its own
  embedded file in the same half. P_E is measured on the testing half, as mean ± std over
  the splits.

This is the study's reduced variant. A ready-made CNN detector (Aletheia, tier 3a) was not
run, and DCT on quality 95 JPEGs was left out (see [Compute](#compute)).

#### The protocol

The protocol was written to
[`results/protocol.md`](../benchmarks/detectability/results/protocol.md) before any rich
model ran on CipherStitch's output. Each later change is in its change log with the
reason. Three tier 1 rules were fixed after their first run because they raised false
alarms, two of them on covers. Two
validation gate items were changed after their numbers were seen, by the owner's decision,
as described below.

### Validation gate

The gate checks the pipeline on references whose detectability is known. Every item must
pass before any CipherStitch number counts. All four pass.

| Item | Needs | Result |
|---|---|---|
| 1. WS and SPA find LSB replacement and OpenStego at 20% | P_E < 0.10 | WS 0.042 and 0.041, SPA 0.052 and 0.051 |
| 2. Rich models rank LSB replacement, LSB matching and HILL | replacement ≤ matching + std, both below HILL | 0.273, 0.272 and 0.474 at 5%, 0.133, 0.135 and 0.369 at 20% |
| 3. Rich models find LSB matching at 20% | P_E ≤ 0.25 | 0.135 |
| 4. Rich models find nsF5 at 0.2 bpnzAC | P_E ≤ 0.25 | 0.120 |

Two items were changed after their numbers were seen. No CipherStitch number entered
either decision.

- **Item 1** first asked for 5%. There WS gives 0.098 and 0.100, and SPA 0.162 and 0.163.
  Two rounds of debugging found no bug in the pipeline. ALASKA's mix of cameras limits
  SPA: 10% of the covers already score above 0.118, and the Sigma cameras with Foveon
  sensors are worst at 0.32 to 0.35.
- **Item 2** first asked for LSB replacement < LSB matching < HILL. The rich models give
  replacement and matching the same P_E within the noise of the splits, because they see
  the same rate of ±1 changes. Only the targeted tier 2 attacks use replacement's
  asymmetry (WS at 5%: 0.098 against 0.497).

### Results

#### Image LSB

| Condition | 1% | 5% | 10% | 20% | 50% |
|---|---|---|---|---|---|
| CipherStitch image LSB | 0.484 ± 0.003 | 0.406 ± 0.004 | 0.314 ± 0.005 | 0.214 ± 0.006 | 0.098 ± 0.004 |
| LSB matching with Hamming efficiency (simulated) | | 0.391 ± 0.003 | | 0.214 ± 0.006 | |
| OpenStego RandomLSB | | 0.273 ± 0.006 | | 0.135 ± 0.004 | |
| LSB replacement (simulated) | | 0.273 ± 0.005 | | 0.133 ± 0.003 | |
| LSB matching (simulated) | | 0.272 ± 0.006 | | 0.135 ± 0.004 | |
| HILL (simulated) | | 0.474 ± 0.004 | | 0.369 ± 0.005 | |

Tier 3b, rich models, P_E with 1,500 testing pairs per split.

- **As theory says.** CipherStitch's LSB is within 0.015 of the simulated LSB matching
  with Hamming codes' efficiency at both payloads. The keyed order, the framing and the
  avoidance of flat areas cost nothing measurable. Matrix embedding is what makes it
  harder to detect than OpenStego: it changes 0.7% of the values at 5% where OpenStego
  changes 2.4%.
- **Tier 2:** Weighted Stego, Sample Pairs and chi-square give 0.48 to 0.50 at every fill.
  They are built for LSB replacement and find OpenStego at 5% with P_E 0.10 (WS).
- **Tier 1:** StegExpose flags 7.2% of CipherStitch's files at 5% and 9.0% at 50%, against
  7.0% of the covers. It flags OpenStego's files at 20% in 50.6% of cases. binwalk's entropy
  tells OpenStego's PNGs from their covers (P_E 0.27) at either payload: OpenStego's files
  are about 40% larger than their covers, CipherStitch's 0.2%. zsteg flags 0.8% of the
  covers, 0.8 to 1.0% of CipherStitch's files and 0.4% of OpenStego's. Nothing else flags
  any PNG.
- **Covers the app warns about.** At 50% the Detection row warns about 887 of 3,000 covers
  as too flat. Leaving them out moves P_E from 0.097 to 0.117. At 20% and below, leaving
  out the few warned covers moves P_E by 0.002 at most.

#### DCT on a JPEG

| Condition | 1% | 5% | 10% | 20% | 50% |
|---|---|---|---|---|---|
| CipherStitch DCT | 0.486 ± 0.005 | 0.391 ± 0.007 | 0.293 ± 0.010 | 0.146 ± 0.008 | 0.023 ± 0.004 |
| nsF5 (simulated) | | 0.433 ± 0.006 | | 0.179 ± 0.005 | |
| steghide | | 0.109 ± 0.004 | | 0.009 ± 0.002 | |

Tier 3b on the quality 75 covers. At 1% only 1,294 images hold a payload.

- **Easier to detect than nsF5** at the same payload, by 0.04 at 5% and 0.03 at 20%.
  Cost-based embedding with UERD costs usually beats nsF5 against rich models by a wide
  margin. Here the writer's other choices lose that advantage (see the
  [DCT diagnostic](#dct-diagnostic)).
- **Far harder to detect than steghide,** which rich models find almost every time at
  20%.
- **Tier 2:** the F5 estimator gives 0.439 at 5% and 0.252 at 20%, against 0.477 and 0.376
  for nsF5.
- **Tier 1:** stegdetect flags 6.7% of CipherStitch's files at 5% and 6.4% at 50%, against
  6.4% of the covers. OutGuess's extraction flags 4.0 to 4.5%, against 3.6% of the covers,
  within the intervals. No other tool flags any of them. StegSeek finds steghide in 30 of 30
  files and CipherStitch in none.

#### DCT from a precover

| Condition | 1% | 5% | 10% | 20% | 50% |
|---|---|---|---|---|---|
| P-SI: from the TIFF, with side information | 0.489 ± 0.004 | 0.425 ± 0.007 | 0.339 ± 0.013 | 0.168 ± 0.013 | 0.010 ± 0.002 |
| P-noSI: P-0's coefficients, without | 0.494 ± 0.006 | 0.442 ± 0.005 | 0.364 ± 0.006 | 0.185 ± 0.010 | 0.007 ± 0.001 |
| J-noSI: Pillow's JPEG at P-0's settings | | 0.440 ± 0.006 | | 0.176 ± 0.009 | |

Tier 3b. P-SI and P-noSI are told apart from P-0, J-noSI from Pillow's JPEG.

- **Side information does not help against rich models.** P-SI is below P-noSI by more
  than the larger std at 5, 10 and 20%, so the protocol counts it as worse. At 1% the two
  tie. At 50% P-SI is slightly higher (0.010 against 0.007), where both are found almost
  every time. Research finds side-informed embedding clearly harder to detect, so this is
  likely a matter of how the writer uses the side information. The cause is not yet known.
- **Against the F5 estimator it helps a lot:** P-SI gives 0.496 at 5%, 0.468 at 20% and
  0.404 at 50%, against 0.444, 0.272 and 0.100 for P-noSI. Moving towards the precover
  removes the shift towards zero that this attack looks for.
- **Tier 1:** no tool flags P-SI more often than P-0.

#### E-C: CipherStitch's JPEG against Pillow's

P-0 against Pillow's JPEG of the same TIFF at the same settings, with nothing hidden:

| Detector | P_E |
|---|---|
| Rich models (tier 3b) | 0.225 ± 0.015 |
| F5 estimator (tier 2) | 0.360 ± 0.005 |

The protocol calls a P_E of 0.40 or less a compressor fingerprint. A JPEG made from a
precover can be told from Pillow's JPEG of the same picture, whether or not it holds a
payload. In 50 files compared, the segment order, the JFIF header, the quantization tables
and the frame are the same. The Huffman tables differ in all 50, because both encoders
optimize them for coefficients that differ.

#### E-K: the public LSB positions

With a password, image LSB writes only the 16-byte salt at positions anyone can compute:
1,386 colour values in a 512 × 512 image, about 22 of which change. E-K asks whether a
detector that looks only at those positions finds anything.

| Statistic | CipherStitch at 1% | CipherStitch at 5% | ±1 on 1.6% | ±1 on 10% | ±1 on 50% |
|---|---|---|---|---|---|
| Weighted Stego on the public values | 0.499 | 0.502 | 0.503 | 0.496 | 0.482 |
| Sample Pairs on the public values | 0.500 | 0.499 | 0.500 | 0.495 | 0.477 |
| Targeted discriminant (16 features) | 0.475 | 0.487 | 0.470 | 0.358 | 0.218 |

The ±1 columns are positive controls simulated on the covers. 1.6% is about CipherStitch's
own change density at these positions, and it scores the same as CipherStitch. E-K would
count as a finding at 0.40 or less. It is clean.

#### By camera

The per-camera tables are in
[`results/tables.md`](../benchmarks/detectability/results/tables.md), for 46 camera models
with at least 100 pooled testing predictions each.

| Condition | Lowest P_E | Highest P_E |
|---|---|---|
| Image LSB at 5% | 0.323 (Nikon D90) | 0.482 (Sigma SD10) |
| Image LSB at 20% | 0.082 (Pentax K50) | 0.407 (Sigma SD10) |
| DCT on a JPEG at 5% | 0.350 (Sony Alpha 6000) | 0.429 (iPad Pro, 2nd gen.) |
| DCT on a JPEG at 20% | 0.062 (Canon PowerShot G11) | 0.214 (Nikon D7100) |
| P-SI at 20% | 0.099 (Nikon D90) | 0.226 (iPad Pro, 2nd gen.) |

The noisy pictures of the Sigma cameras hide LSB changes best.

### DCT diagnostic

An exploratory follow-up on the tier 2 results, added at the owner's request after they were
seen. It has no thresholds, and nothing above depends on it. The script is
`dct_diagnostic.py`, its tables are in
[`results/dct-diagnostic.md`](../benchmarks/detectability/results/dct-diagnostic.md).

#### Why DCT on a JPEG is easier to detect than nsF5

The same 3,000 quality 75 covers and the same payloads. Simulations at the bound use the
writer's costs with an ideal coder. P_E is the F5 estimator's.

| At 20% | Changes per bit | Change rate in modes 01, 10, 11 | F5 estimator P_E |
|---|---|---|---|
| nsF5 (simulated) | 0.146 | 2.3% | 0.376 |
| CipherStitch's costs at the bound, magnitudes lowered | 0.209 | 7.1% | 0.292 |
| The same, split into DCT's lead, head and tail | 0.211 | 7.1% | 0.289 |
| The same, changes either way at random | 0.209 | 7.1% | 0.412 |
| The same, the bits split over the colour components | 0.191 | 5.0% | 0.349 |
| conseal's UERD (simulated, ±1 either way) | 0.152 | 3.7% | 0.497 |
| CipherStitch DCT | 0.264 | 8.8% | 0.252 |

- **The costs.** UERD's cost is the quantization step over the block's energy, so the
  cheapest changes are the low luminance frequencies. Those are the very modes that the F5
  estimator reads, where CipherStitch changes 8.8% of the non-zero coefficients against
  nsF5's 2.3%. 96% of its changes are in luminance, against 70% for nsF5.
- **The direction.** Every change lowers a magnitude, so the histogram shifts towards
  zero, which is what the estimator measures. Changing either way at random, at the same
  costs and distortion, raises P_E from 0.292 to 0.412. The writer could have done that
  without a new format, because round 1's reader only read parities.
- **The code.** The syndrome-trellis code (constraint height 6) makes 26% more changes than
  the bound. That takes P_E from 0.292 to 0.252.
- **The layout.** Coding the lead, head and tail separately costs nothing measurable.
- Splitting the bits over the colour components also helps against this estimator, but
  more than doubles the UERD distortion per bit.

These simulations ran against the F5 estimator only. Which change also helps against rich
models is not measured here.

#### What tells P-0 from Pillow's JPEG

Each TIFF is compressed again, step by step from P-0 towards libjpeg:

| Variant | Luminance coefficients equal to Pillow's | F5 estimator P_E against Pillow |
|---|---|---|
| P-0 | 97.1% | 0.360 |
| P-0 rebuilt in the script | 97.1% | 0.360 |
| With libjpeg's 8-bit colour conversion and chroma downsampling, float DCT | 99.2% | 0.360 |
| libjpeg's float DCT | 99.3% | 0.361 |
| libjpeg's integer DCT | 100% | 0.500 |

The rebuilt P-0 matches P-0 in every coefficient, and libjpeg's integer DCT (libjpeg-turbo
through jpeglib) matches Pillow's files in every coefficient, over all 3,000 images. P-0
keeps its colour samples in floating point, which explains most of the coefficients that
differ. The F5 estimator, though, reacts to the float DCT against libjpeg's integer DCT. A
precover compressed exactly as libjpeg compresses it would match Pillow's JPEG, and its
rounding errors would still be known, since the integer DCT keeps 1/8 of a coefficient unit.

### Findings

Suspected weaknesses of the round 1 writers, and what came of them.

- **DCT on a JPEG was easier to detect than nsF5** at the same payload, against the rich
  models and the F5 estimator. Round 2's writer changes coefficients either way at
  J-UNIWARD costs, and the rich models find it harder to detect than nsF5.
- **The precover's JPEG carried a compressor fingerprint** (E-C, P_E 0.225). The precover
  now compresses exactly as libjpeg-turbo does, so its JPEG has Pillow's coefficients.
- **Side information did not help against rich models** (P-SI below P-noSI at 5, 10 and
  20%). Round 2's writer, with SI-UNIWARD's costs, gains 0.08 from it at 20%.
- **A carrier smaller than the framing** showed as 100% full in the meter, then hiding
  failed with internal sizes. The meter now shows it as over full, and hiding says the
  carrier is too small.

## Limitations

- **One cover source.** ALASKA's pictures come from about 40 cameras, all developed the
  same way. Real collections vary more.
- **Cover size.** Every round used 512 × 512 pictures. Round 2's crops were easier to
  detect than the square-root law predicts, at the same rate. Full-size photos were not
  measured, so above 512 × 512 the advice rests on the law, and a payload that is the same
  share of a larger image is easier to detect.
- **The detectors.** The rich models were trained on CipherStitch's own output on the same
  kind of covers. A detector trained on other embedders or other cameras usually does
  worse. A CNN detector trained the same way would likely do better, and none was run.
- **Precovers** were measured on 8-bit TIFFs, not on camera RAW or 16-bit images.
- **JPEG quality:** DCT was measured on quality 75 covers, and from precovers at the quality
  90 they compress to.
- **Only the image methods.** Audio, document and text methods were not measured. Audio
  LSB's advice borrows image LSB's rate.
- **Image LSB's fourth rate** was added after the first three were seen, since none of them
  reached 0.45. It was measured as they were, and the rule that turns rates into advice
  stayed the same.
- **Tuned on the same images.** The check that chose polarization over DC changes ran on
  1,000 of round 3's 3,000 JPEG covers. Its factor, 0.65, is HStego's and was not tuned.
- **The LSB numbers were corrected** after round 4 found a flaw in the study's features
  (see [the correction](#a-correction-to-the-lsb-numbers)). The detectors were trained again
  on the stored features put into one order, not on features computed again from the
  images, which for round 1's outputs are gone. 24 rows computed again from images that
  remain match the stored ones exactly.
- **This version only.** Any change to the writer needs new measurements, on images it was
  not tuned on.

## Compute

Apple M3 Pro (6 performance and 6 efficiency cores, 36 GB), with 10 worker processes as a
rule. For 47 minutes the LSB ensembles ran beside the JPEG features, 14 processes in all,
and the DCT diagnostic added an 11th while it ran.

| Stage | Time |
|---|---|
| Downloads and covers | 0.5 h |
| CipherStitch embedding (every family) | 1.5 h |
| Reference embedding | 2 h |
| Tier 1 tools | 3 h |
| Tier 2 attacks | 0.8 h |
| LSB rich-model features (16 conditions) | 6 h |
| JPEG rich-model features (25 conditions) | about 12 h at full speed |
| FLD ensembles (38 conditions × 10 splits) | 2 h |
| DCT diagnostic | 2.3 h |

sealwatch's LSB features took 2.49 s per image in the pilot. `fast_srm.py` computes the same
co-occurrences with `np.bincount` and brings that to 0.39 s, identical on 11 images checked.
The JPEG features take about 0.8 s per image. The work folder peaks at about 71 GB.

The pilot projected more than 60 hours for the full variant (5,000 images per set, quality
95 covers, Aletheia), against a budget of 24, so the study ran its reduced variant: 3,000
images per set, quality 75 only, no Aletheia, and only the simulated references the gate
and the checks need.

Round 2 ran on the same machine and reused round 1's covers, features and references:

| Stage | Time |
|---|---|
| Embedding (16 conditions, on the images and the crops) | 1.5 h |
| LSB rich-model features (5 conditions) | 1.5 h |
| JPEG rich-model features (5 conditions) | 2.7 h |
| The crops' covers and features (8 sets) | 0.9 h |
| E-K | 0.3 h |
| FLD ensembles (16 conditions × 10 splits) | 0.6 h |
| Tier 1 tools | 0.6 h |
| HStego embedding (12,000 runs, amd64 under Rosetta) | 7.4 h |
| HStego's rich-model features (4 conditions), beside its embedding | about 2.5 h |
| Tier 2 attacks | 0.1 h |
| DCT from a TIFF: embedding, features and ensembles (4 conditions) | 2 h |

Round 3, on the same machine, reused the covers, their features and every earlier condition:

| Stage | Time |
|---|---|
| A check of writer variants on 1,000 JPEG covers, before the round | 1.5 h |
| Embedding (17 conditions) | 1.2 h |
| E-K and tier 2 | 0.3 h |
| LSB rich-model features (5 conditions) | 1.7 h |
| JPEG rich-model features (10 conditions) | 5 h |
| FLD ensembles (15 conditions × 10 splits), mostly beside the features | 0.75 h |
| Tier 1 tools | 0.5 h |
| The bit-plane split, after the round: image LSB at 20%, embedding and tier 2 | 0.3 h |
| Image LSB's fourth rate: embedding, features and ensembles, afterwards | 0.5 h |

Round 4, on the same machine, reused the covers, their features and every earlier condition:

| Stage | Time |
|---|---|
| Embedding (6 conditions) | 0.5 h |
| Tier 1 tools and tier 2 | 0.5 h |
| LSB rich-model features (2 conditions) | 0.65 h |
| JPEG rich-model features (4 conditions) | 2.6 h |
| FLD ensembles (6 conditions × 10 splits) | 0.2 h |
| The correction: the stored LSB features put into one order | 0.15 h |
| The correction: every LSB ensemble of rounds 1 to 3 again (33 conditions × 10 splits), beside the JPEG features | 2 h |

## Reproduce

The commands are in [`benchmarks/detectability/README.md`](../benchmarks/detectability/README.md).
ALASKA's licence allows research and education only, and forbids hiding real data in its
images, so the study publishes its code and results but never the images.

## Citations

- R. Cogranne, Q. Giboulot, P. Bas. *The ALASKA Steganalysis Challenge: A First Step
  Towards Steganalysis "Into The Wild"*. IH&MMSec 2019.
- A. D. Ker, R. Böhme. *Revisiting Weighted Stego-Image Steganalysis*. Information Hiding
  2008.
- S. Dumitrescu, X. Wu, Z. Wang. *Detection of LSB Steganography via Sample Pair Analysis*.
  IEEE TSP 51(7), 2003.
- A. Westfeld, A. Pfitzmann. *Attacks on Steganographic Systems*. Information Hiding 1999.
- J. Fridrich, M. Goljan, D. Hogea. *Steganalysis of JPEG Images: Breaking the F5
  Algorithm*. Information Hiding 2002.
- J. Fridrich, J. Kodovský. *Rich Models for Steganalysis of Digital Images*. IEEE TIFS
  7(3), 2012.
- M. Goljan, J. Fridrich, R. Cogranne. *Rich Model for Steganalysis of Color Images*. IEEE
  WIFS 2014.
- V. Holub, J. Fridrich. *Low-Complexity Features for JPEG Steganalysis Using Undecimated
  DCT*. IEEE TIFS 10(2), 2015.
- X. Song, F. Liu, C. Yang, X. Luo, Y. Zhang. *Steganalysis of Adaptive JPEG Steganography
  Using 2D Gabor Filters*. IH&MMSec 2015.
- J. Kodovský, J. Fridrich, V. Holub. *Ensemble Classifiers for Steganalysis of Digital
  Media*. IEEE TIFS 7(2), 2012.
- J. Fridrich, T. Pevný, J. Kodovský. *Statistically Undetectable JPEG Steganography: Dead
  Ends, Challenges, and Opportunities*. MM&Sec 2007.
- B. Li, M. Wang, J. Huang, X. Li. *A New Cost Function for Spatial Image Steganography*.
  ICIP 2014.
- L. Guo, J. Ni, W. Su, C. Tang, Y.-Q. Shi. *Using Statistical Image Model for JPEG
  Steganography: Uniform Embedding Revisited*. IEEE TIFS 10(12), 2015.
- T. Denemark, J. Fridrich. *Side-Informed Steganography with Additive Distortion*. IEEE
  WIFS 2015.
- V. Holub, J. Fridrich, T. Denemark. *Universal Distortion Function for Steganography in
  an Arbitrary Domain*. EURASIP Journal on Information Security 2014 (S-UNIWARD,
  J-UNIWARD).
- D. Lerch-Hostalot. *HStego* (`github.com/daniellerch/hstego`), v0.6.1.
- T. Filler, J. Judas, J. Fridrich. *Minimizing Additive Distortion in Steganography Using
  Syndrome-Trellis Codes*. IEEE TIFS 6(3), 2011.
- A. D. Ker, T. Pevný, J. Kodovský, J. Fridrich. *The Square Root Law of Steganographic
  Capacity*. MM&Sec 2008.
- M. Beneš, B. Lorch. *sealwatch* and *conseal*, University of Innsbruck
  (`github.com/uibk-uncover`). The versions are in `results/environment.txt`.
