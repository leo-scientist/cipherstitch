# Protocol, round 4

Written 2026-11-02, before any detector ran on round 4's output. A later change only fixes a
bug, and the change log at the end says what changed and why.

Round 4 measures the current writer against round 3's (in
[the round 3 protocol](../round-3/protocol.md)). Two changes between them reach the outputs
at these payloads:

- **The ideal split.** A stream's head now records how its tail's bits are shared between the
  values' second bits and their parities, and the writer shares them as an ideal ternary
  code would: the second bits' share of its entropy, a little over half. With halves, the
  parities took more changes than the second bits, so steps that keep a value in its pair
  (2k and 2k + 1) outnumbered the others. A 3,000-image check after round 3 found Weighted
  Stego at 0.483 on round 3's 20% LSB outputs and 0.503 with the split (not part of this
  round). The rich models have not seen it.
- The split takes 2 bytes of every stream, so the capacity is 2 bytes lower. Round 1's
  payload bytes stay the same.
- **The check matrix.** The syndrome-trellis codes take their columns from another label,
  the one of 4,096 tried that coded best on real covers
  ([format](../../../../docs/format.md#syndrome-trellis-codes)). On 150 of the study's
  covers, paired with round 3's columns at the same payloads, it lowers image LSB's
  distortion by 0.8% from the advised size to 50% of the capacity (2.2% at 20%) and leaves
  DCT's within 0.1% on average.

Other changes do not reach these outputs. The lead and the head keep halves. The lead's code
has the new columns too, but at its width (256 units per bit in each plane, ten of them
changeable) the columns moved the distortion by 0.2% or less in the search, so E-K carries
over. The memory saving gives the same output, and the retries near the capacity only meet
fills of 80% and more.

## Images and what carries over

- **The same images as rounds 1 to 3:** the first 3,000 of the `lsb` and `jpeg` index sets.
- **Reused unchanged:** the covers and their features, round 2's HStego outputs and scores,
  and every round 3 result, which round 4 compares with directly.
- **Validation gate.** `fast_srm.py`, `features.py` and `metrics.py` are unchanged since
  round 2. `ensemble.py`, `attacks.py` and `scan.py` only learn round 4's conditions and
  where to write. So the gate carries over.
- Common rules as before: seed `SEED`, 10 random 50/50 splits with each cover and its
  outputs in one half, P_E as the minimum of (P_FA + P_MD) / 2, mean ± std over the splits.

## Conditions

Every payload is encrypted with the default cipher.

| Condition | Carrier → output | Cover | Payload per image |
|---|---|---|---|
| `lsb4-f05`, `lsb4-f20` | PNG → PNG | `cover` | round 1's `lsb` bytes at that fill |
| `dct4-q75-f05`, `dct4-q75-f20` | QF 75 JPEG → JPEG | `q75` | round 1's `dct-q75` bytes |
| `psi4-f05`, `psi4-f20` | TIFF → JPEG, from the precover | `jnosi-cover` | round 1's `psi` bytes |

- These are round 3's `f05` and `f20` conditions, the ones the README and
  `docs/detectability.md` lead with. Where round 1's payload did not fit, there is no
  output.
- No root rates (see [Root rates](#root-rates)), no `f01` (E-K carries over), no crops.

## Tier 3b

As round 3, for every condition above, against its cover.

## Targets

A target of X means a mean P_E of at least X. A comparison holds when the difference is
larger than the larger std. "Not below" fails only when the other is higher by more than
that.

| Check | Condition | Target |
|---|---|---|
| Not below round 3 | each of the six against `lsb3`, `dct3-q75`, `psi3` at its fill | not below |
| Round 2's targets | `lsb4-f05`, `lsb4-f20` | ≥ 0.46, ≥ 0.33 |
| | `dct4-q75-f05`, `dct4-q75-f20` | ≥ 0.45, ≥ 0.25 |
| | `psi4-f05`, `psi4-f20` | ≥ 0.47, ≥ 0.30 |
| Above HStego | `lsb4-f05`, `lsb4-f20` against `hstego-png`; `dct4-q75-f05`, `dct4-q75-f20` against `hstego-q75` | above |
| Tier 1 | outputs at 5% and 20% | no tool flags them above the covers' rate beyond the Wilson interval |
| Tier 2 | LSB at 5% and 20%; DCT at 20% (on a JPEG and from a TIFF) | WS, SPA, chi-square ≥ 0.48; the F5 estimator ≥ 0.45 |

"Not below round 3" is the check this round is for. A target that is missed is reported as a
finding with its numbers. None is changed after its numbers are seen.

## Root rates

Round 3's root rates (67 for image LSB, 14 for DCT, 39 for DCT from a precover) hold for a
method if neither of its two conditions is below round 3's at the same fill. Otherwise that
method's rates are measured again, as round 3 measured them, after a change-log entry here
and before those detectors run.

## Tiers 1 and 2

- **Tier 1** (`scan.py --round 4`): the first 500 images of `lsb4-f05`, `lsb4-f20`,
  `dct4-q75-f05` and `dct4-q75-f20`.
- **Tier 2** (`attacks.py --round 4`): all 3,000 images of every condition.

## Results

Everything goes to `results/round-4/`: each stage's CSV, then `round4.py` writes `report.md`
(the tables and every target's result). The docs then take its six numbers as the headline,
with round 3 as the "before". Round 3's rates stay unless [Root rates](#root-rates) measures
them again.

## Change log

- 2026-11-02, bug fix in the LSB features (see the change log of
  [round 1's protocol](../protocol.md)), found by this round's first LSB ensembles, which gave
  0.296 and 0.274: far more of `lsb4`'s chunks were stored in another SRMQ1 order than their
  covers' than in earlier rounds. After the fix every LSB ensemble of rounds 1 to 3 was
  trained again, so this round compares with the corrected round 3, and image LSB's root
  rate is 76. No condition or target changed.
- 2026-11-02, the writer was refactored for a research mode after the outputs were made: its
  settings are passed through it, and Standard's output stays byte-identical (golden
  digests on every method and cipher). The outputs stand.
