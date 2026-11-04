# Protocol, round 2

Written 2026-10-22, before any detector ran on round 2's output. A later change only fixes a
bug, and the change log at the end says what changed and why.

Round 2 measures new writers against round 1's (in
[the round 1 protocol](../protocol.md)):

- DCT: J-UNIWARD costs, ±1 changes either way, zeros changeable with pruning.
- Image LSB: HILL costs over syndrome-trellis codes, capacity 80% of the non-flat values.
- Payload advice: a root rate per method, in bits per square root of the cover's counted
  values (`risks.ROOT_RATES`). Round 2 calibrates it.

## Images and what carries over

- **The same images as round 1:** the first 3,000 of the `lsb` and `jpeg` index sets
  (`work/selection-*.txt`). No development loop ran on them, so they need no replacement
  (the owner's decision D9). The provisional root rates came from the first 200 images of
  each set, with 3 splits. Round 2 replaces them.
- **Reused unchanged:** round 1's covers, their features, and every round 1 condition's
  outputs, features and scores. That includes the old writers (`lsb`, `dct-q75`, `psi`,
  `pnosi`, `jnosi`), the tools (OpenStego, steghide) and the simulators (LSB replacement,
  LSB matching, LSB matching with Hamming efficiency, HILL, nsF5).
- **Validation gate.** Round 1's gate passed on these images, covers and references. The
  feature, metric and ensemble code computes what it computed then: `fast_srm.py` and
  `metrics.py` are unchanged since round 1, `features.py` only gains the crops' cover
  folders and `--images`, and `ensemble.py` only learns the round's conditions, where to
  write and to split the crops by picture. So the gate carries over and is not rerun.
- Common rules as in round 1: seed `SEED`, 10 random 50/50 splits with each cover and its
  outputs in one half, P_E as the minimum of (P_FA + P_MD) / 2, mean ± std over the splits.

## Conditions

Every payload is encrypted with the default cipher, as in round 1.

| Condition | Carrier → output | Cover | Payload per image |
|---|---|---|---|
| `lsb2-f01`, `lsb2-f05`, `lsb2-f20` | PNG → PNG | `cover` | round 1's `lsb` bytes at that fill |
| `lsb2-r140`, `lsb2-r210`, `lsb2-r300` | PNG → PNG | `cover` | root rate r |
| `dct2-q75-f01`, `dct2-q75-f05`, `dct2-q75-f20` | QF 75 JPEG → JPEG | `q75` | round 1's `dct-q75` bytes |
| `dct2-q75-r12`, `dct2-q75-r19`, `dct2-q75-r28` | QF 75 JPEG → JPEG | `q75` | root rate r |
| `psi2-f05`, `psi2-f20` | TIFF → JPEG, from the precover, with side information | `jnosi-cover` | round 1's `psi` bytes |
| `jnosi2-f05`, `jnosi2-f20` | Pillow's JPEG at the precover's settings → JPEG | `jnosi-cover` | round 1's `psi` bytes |
| `hstego-png-f05`, `hstego-png-f20` | PNG → PNG, HStego S-UNIWARD | `cover` | round 1's `lsb` bytes |
| `hstego-q75-f05`, `hstego-q75-f20` | QF 75 JPEG → JPEG, HStego J-UNIWARD | `q75` | round 1's `dct-q75` bytes |

- **Payloads from round 1.** A condition named after a fill hides, in each image, the bytes
  round 1's writer hid there at that fill (`payload_bytes` in its manifest, the same random
  bytes). A capacity change cannot move the comparison. The new LSB capacity is about 80% of
  the old, so those payloads fill about 6% and 25% of it. Where round 1's payload did not
  fit, the image has no output in round 2 either.
- **Root rates.** A condition `-r<r>` hides the largest payload whose carrier space
  (framing included, as the app counts it) is at most ⌊r √N⌋ / 8 bytes and at most the
  capacity. N is the channel's `element_count`: non-flat values for LSB, non-zero AC
  coefficients for DCT. The rates bracket the provisional ones (139 and 19), where the
  first 200 images gave P_E about 0.45 to 0.47.
- **The precover** (P-0′) now has the coefficients of Pillow's JPEG at its settings
  (quality 90, 4:2:0): `identity.py` found them the same on all 3,000 TIFFs. So that JPEG
  (`jnosi-cover`) is the cover of both precover conditions, and `jnosi2` is DCT on the
  precover's own coefficients without side information (P-noSI′). Both hide round 1's `psi`
  bytes, which round 1's `pnosi` hid too.
- **HStego** v0.6.1 (commit `bf71f6e`, `hstego/`) is a tool reference beside round 1's
  OpenStego and steghide, run by `references.py` like them. It embeds with its own costs
  (S-UNIWARD for PNG, J-UNIWARD with cost polarization for JPEG). The released tool caps a
  payload at 5% of its own capacity and refuses round 1's DCT payloads on many images, so
  the cap is lifted: HStego carries the same bytes as the other tools. Each manifest
  records the capacity the released tool allows.
- The `f01` conditions serve E-K only and get no rich-model features.

## Tier 3b

As round 1, for every condition above except `f01`, against its cover.

## Targets

From the improvements plan (section 3). A target of X means a mean P_E of at least X. A
comparison holds when the difference is larger than the larger std.

| Check | Condition | Target |
|---|---|---|
| Image LSB | `lsb2-f05`, `lsb2-f20` | ≥ 0.46, ≥ 0.33 |
| DCT on a QF 75 JPEG | `dct2-q75-f05`, `dct2-q75-f20` | ≥ 0.45, ≥ 0.25, and above `nsf5-f05`, `nsf5-f20` |
| DCT from a TIFF | `psi2-f05`, `psi2-f20` | ≥ 0.47, ≥ 0.30; at 20% at least 0.03 above `jnosi2-f20`; never below `jnosi2` |
| E-K, public positions | LSB's and DCT's lead at 1% and 5% | every statistic ≥ 0.45 |
| Tier 1 | outputs at 5% and 20% | no tool flags them above the covers' rate beyond the Wilson interval |
| Tier 2 | LSB at 5% and 20%; DCT at 20% (on a JPEG and from a TIFF) | WS, SPA, chi-square ≥ 0.48; the F5 estimator ≥ 0.45 |

A target that is missed is reported as a finding with its numbers. None is changed after its
numbers are seen.

## Calibration of the advice (C8)

- For image LSB and for DCT on a QF 75 JPEG, the root rate is where tier 3b's mean P_E
  crosses 0.45 (the owner's decision D1), by linear interpolation in r between the two
  measured rates around the crossing, rounded down to a whole number.
- If P_E is at least 0.45 at every measured rate, the rate is the largest measured one. If
  it is below 0.45 at every rate, the line runs from (0, 0.5) to the smallest rate.
- Audio LSB keeps image LSB's rate, which nothing here measures. DCT from a precover uses
  DCT's rate.
- These rates replace `risks.ROOT_RATES`.

## Scaling check (256 × 256)

- The first 750 images of each set, each TIFF cut into its four 256 × 256 quadrants: 3,000
  crops per set. Covers as in round 1: the crop's pixels as a PNG for LSB, Pillow's JPEG at
  QF 75 with 4:4:4 for DCT.
- At the same three root rates per method, with tier 3b. Splits: round 1's 10 splits drawn
  over the 750 images, and each crop goes where its image goes.
- The square-root law holds over this range when, at every rate, P_E on the crops is
  within 0.03 of P_E on the 512 × 512 images. Otherwise it is a finding. The rates still
  come from the 512 × 512 images.

## Tiers 1 and 2, and E-K

- **Tier 1** (`scan.py`, as round 1): the first 500 images of `lsb2-f05`, `lsb2-f20`,
  `dct2-q75-f05` and `dct2-q75-f20`. The precover conditions have none.
- **Tier 2** (`attacks.py`, as round 1): all 3,000 images of the `f05` and `f20` conditions.
- **E-K** (`kerckhoffs.py`): with a password only the salt sits at public positions, in the
  lead. For LSB the statistics are `ws_public`, `spa_public`, `targeted` and `sca` (the
  targeted discriminant weighted by HILL's change probabilities, which anyone can compute
  from the image). For DCT they are `histogram` and `sca` (J-UNIWARD's). All 3,000 images
  at 1% and 5%, with the positive controls of `kerckhoffs.py`.

## Results

Everything goes to `results/round-2/`: each stage's CSV, then `round2.py` writes
`report.md` (the tables and every target's result) and `calibration.csv` (the rates). The
docs then make round 2 the headline and round 1 the "before" (the owner's decision D10).

## Change log

- 2026-10-22, before any detector ran on round 2's output: the owner dropped the precover
  conditions `psi2` and `jnosi2`, with their targets and their tier 1 files, to save
  compute. DCT from a precover keeps round 1's numbers, measured with the earlier
  compressor and writer. Everything else stays at 3,000 images: on round 1's features, the
  ensemble trained on fewer images gives a higher P_E (image LSB at 20%: 0.374 on 200
  images, 0.326 on 500, 0.268 on 1,000, against 0.215 on 3,000), so a smaller run would
  overstate how hard detection is. The provisional root rates and the README's provisional
  rows came from 200 images, so they are likely optimistic too.
- 2026-10-23, a wording fix: the validation gate's paragraph said `features.py` and
  `ensemble.py` were unchanged apart from choosing the round, while they also gained the
  crops' covers and their grouped split. The text now says so. No computation changed.
- 2026-10-23, the calibration, **changed after its numbers were seen**, by the owner's
  decision. Every measured rate gave P_E below 0.45 (LSB 0.349 at 140, DCT 0.419 at 12), so
  the rule's line from (0, 0.5) to the smallest rate applied: 46 for LSB, 7 for DCT.
  `lsb2-f05` holds round 1's 5% bytes, which on every 512 × 512 PNG is the same root rate,
  42.6, and its P_E of 0.436 is already below 0.45. The LSB rate therefore takes that point
  as its smallest measured rate, which gives 33 (a power law through the five LSB points
  gives 31). DCT's 5% payloads spread over root rates 8 to 12, so they are not one rate, and
  DCT's stays 7 (a power law through its three rates gives 7.7).
- 2026-10-23, the scaling check missed at every rate: at the same root rate the 256 × 256
  crops were easier to detect, by 0.03 to 0.12, and at the same share of the capacity harder.
  By the owner's decision the app's advice keeps, below the size the rates were measured
  on (`risks.CALIBRATED_ELEMENTS`: a 512 × 512 PNG, the median QF 75 JPEG of that size),
  the share of the capacity it has at that size. The rates still come from the 512 × 512
  images.
- 2026-10-29, by the owner's decision, after round 2's other numbers were seen and before
  any detector ran on their output: the precover conditions `psi2` and `jnosi2` are back at
  5% and 20%. The docs still gave round 1's precover numbers, which came from the earlier
  compressor and writer and found side information no help. Their payloads and targets are
  the improvement plan's (section 3), written before round 2 began: P-SI′ at least 0.47
  and 0.30, at least 0.03 above P-noSI′ at 20%, and never below it. "Below" follows the
  comparison rule: by more than the larger std. They get tier 3b and tier 2, not tier 1.
- 2026-11-02, bug fix in the LSB features (see the change log of
  [round 1's protocol](../protocol.md)): every LSB ensemble was trained again on features in
  one SRMQ1 order. `lsb2-f05` rises from 0.436 to 0.467, `lsb2-f20` from 0.317 to 0.327,
  and HStego's PNG outputs from 0.347 to 0.465 at 5% and from 0.330 to 0.338 at 20%. The
  LSB calibration gives a rate of 58, not 33. Every JPEG result is unchanged.
