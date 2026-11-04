# Protocol, round 3

Written 2026-10-29, before any detector ran on round 3's output. A later change only fixes a
bug, and the change log at the end says what changed and why.

Round 3 measures new writers against round 2's (in
[the round 2 protocol](../round-2/protocol.md)):

- Image LSB, audio LSB and DCT: two-layer ternary syndrome-trellis codes, the payload in each
  value's two lowest bits. LSB's capacity also leaves out the values at the ends
  of the range.
- DCT on a JPEG cover: cost polarization. A step towards a 3 × 3 Wiener-filtered estimate of
  the picture costs 0.65 times the J-UNIWARD cost. A 1,000-image check with this study's
  detector led to it (not part of this round).
- DCT from a precover: a step away from the precover costs (1 + 2|e|) times the J-UNIWARD
  cost, a step towards it (1 − 2|e|) times, as before.
- Payload advice: round 3 calibrates the root rates again, and a separate one for DCT from a
  precover (follow-up 3 of the improvements plan, the owner's decision DT4).

## Images and what carries over

- **The same images as rounds 1 and 2:** the first 3,000 of the `lsb` and `jpeg` index sets.
- **Reused unchanged:** the covers and their features, every round 1 and round 2 condition's
  outputs, features and scores, among them HStego, OpenStego, steghide and the simulated
  references. They are told apart from the same covers, so round 3's rows compare with
  them directly.
- **Validation gate.** `fast_srm.py`, `features.py` and `metrics.py` are unchanged since
  round 2. `ensemble.py`, `attacks.py`, `kerckhoffs.py` and `scan.py` only learn round 3's
  conditions and where to write. `kerckhoffs.py`'s DCT statistics read only the AC
  coefficients, as the writer still changes only those. So the gate carries over.
- Common rules as before: seed `SEED`, 10 random 50/50 splits with each cover and its
  outputs in one half, P_E as the minimum of (P_FA + P_MD) / 2, mean ± std over the splits.

## Conditions

Every payload is encrypted with the default cipher.

| Condition | Carrier → output | Cover | Payload per image |
|---|---|---|---|
| `lsb3-f01`, `lsb3-f05`, `lsb3-f20` | PNG → PNG | `cover` | round 1's `lsb` bytes at that fill |
| `lsb3-r25`, `lsb3-r40`, `lsb3-r60` | PNG → PNG | `cover` | root rate r |
| `dct3-q75-f01`, `dct3-q75-f05`, `dct3-q75-f20` | QF 75 JPEG → JPEG | `q75` | round 1's `dct-q75` bytes |
| `dct3-q75-r10`, `dct3-q75-r18`, `dct3-q75-r30` | QF 75 JPEG → JPEG | `q75` | root rate r |
| `psi3-f05`, `psi3-f20` | TIFF → JPEG, from the precover | `jnosi-cover` | round 1's `psi` bytes |
| `psi3-r45`, `psi3-r65`, `psi3-r90` | TIFF → JPEG, from the precover | `jnosi-cover` | root rate r |

- **Payloads from round 1** as in round 2: the same bytes in each image, so round 1, 2 and 3
  and the tools compare at one payload. Where round 1's payload did not fit, there is no
  output.
- **Root rates** as in round 2: the largest payload whose carrier space is at most
  ⌊r √N⌋ / 8 bytes and the capacity, N the channel's `element_count`. This time they
  bracket the crossing of 0.45. Round 2 crossed at 33 for LSB and below 12 for DCT. Ternary
  codes and polarization should move both up, so the rates are 25, 40 and 60 for LSB and 10,
  18 and 30 for DCT. P-SI's 20% payloads sat at a median root rate of 45 with P_E 0.456 in
  round 2, so its rates are 45, 65 and 90.
- The `f01` conditions serve E-K only.
- No crops: round 2's scaling check stands, and the advice keeps its fixed share below
  `risks.CALIBRATED_ELEMENTS`.
- No `jnosi3`: P-noSI′ would now be polarized, a third kind of cover rather than round 2's
  comparison. Round 2 measured the gain of side information.

## Tier 3b

As round 2, for every condition above except `f01`, against its cover.

## Targets

A target of X means a mean P_E of at least X. A comparison holds when the difference is
larger than the larger std. "Not below" fails only when the other is higher by more than
that.

| Check | Condition | Target |
|---|---|---|
| Round 2's targets | `lsb3-f05`, `lsb3-f20` | ≥ 0.46, ≥ 0.33 |
| | `dct3-q75-f05`, `dct3-q75-f20` | ≥ 0.45, ≥ 0.25 |
| | `psi3-f05`, `psi3-f20` | ≥ 0.47, ≥ 0.30 |
| Not below round 2 | each of the six against `lsb2`, `dct2-q75`, `psi2` at its fill | not below |
| Above HStego | `lsb3-f05`, `lsb3-f20` against `hstego-png`; `dct3-q75-f05`, `dct3-q75-f20` against `hstego-q75` | above |
| E-K, public positions | LSB's and DCT's lead at 1% and 5% | every statistic ≥ 0.45 |
| Tier 1 | outputs at 5% and 20% | no tool flags them above the covers' rate beyond the Wilson interval |
| Tier 2 | LSB at 5% and 20%; DCT at 20% (on a JPEG and from a TIFF) | WS, SPA, chi-square ≥ 0.48; the F5 estimator ≥ 0.45 |

A target that is missed is reported as a finding with its numbers. None is changed after its
numbers are seen.

## Calibration of the advice

- For image LSB, DCT on a QF 75 JPEG and DCT from a precover, the root rate is where tier
  3b's mean P_E crosses 0.45: linear interpolation in r between the two measured rates around
  the crossing, from (0, 0.5) on, rounded down to a whole number. If P_E is at least 0.45 at
  every measured rate, the rate is the largest measured one.
- As in round 2's change log (2026-10-23), `lsb3-f05` counts as a measured LSB rate at the
  median root rate of its payloads: every 512 × 512 PNG holds the same bytes there.
- Audio LSB keeps image LSB's rate.
- The precover rate needs the app to tell a precover apart (`service.compresses_carrier`).
  Its `CALIBRATED_ELEMENTS` is the median non-zero AC count of the `psi3` covers, and below
  it the advice keeps its share, as for the other methods.
- These rates replace `risks.ROOT_RATES`.

## Tiers 1 and 2, and E-K

- **Tier 1** (`scan.py --round 3`): the first 500 images of `lsb3-f05`, `lsb3-f20`,
  `dct3-q75-f05` and `dct3-q75-f20`.
- **Tier 2** (`attacks.py --round 3`): all 3,000 images of the `f05` and `f20` conditions.
- **E-K** (`kerckhoffs.py --round 3`): as in round 2, at 1% and 5%, with its positive
  controls. Polarization makes the lead's changes lean one way. The cheapest-changes control
  shows how little 128 bits move in up to 16,384 coefficients.

## Results

Everything goes to `results/round-3/`: each stage's CSV, then `round3.py` writes `report.md`
(the tables and every target's result) and `calibration.csv` (the rates). The docs then make
round 3 the headline and round 2 the "before".

## Change log

- The LSB and DCT ensembles ran before the precover features, and the run paused overnight
  once. Neither changes what was computed.
- 2026-10-30, after the numbers were seen: one more LSB rate, `lsb3-r90`. Image LSB's P_E
  stayed above 0.45 at every rate measured (0.465 at 60), so by the rule above its rate was
  the largest measured one, below the true crossing. The owner chose to measure one more
  rate, as the other rates were measured, so the crossing falls between two of them. The
  rule, the targets and every other condition stay as they were.
- 2026-11-02, bug fix in the LSB features (see the change log of
  [round 1's protocol](../protocol.md)): every LSB ensemble was trained again on features in
  one SRMQ1 order. `lsb3-f05` is 0.475 (it was 0.484) and `lsb3-f20` 0.368 (0.367), against
  HStego's 0.465 and 0.338, and every LSB target is still met. The rates give 0.485, 0.476,
  0.464 and 0.438 at 25, 40, 60 and 90, so image LSB's rate is 76, not 67. The DCT and
  precover rates and every JPEG result are unchanged.
