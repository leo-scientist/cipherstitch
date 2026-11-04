# Round 4

Mean P_E ± std over 10 splits. See `protocol.md`.

## Tier 3b: round 3 against round 4 at the same payload

| Payload (round 1's bytes) | Round 3 | Round 4 | Difference | HStego |
|---|---|---|---|---|
| Image LSB, PNG, 5% | 0.475 ± 0.004 | 0.474 ± 0.004 | -0.0011 | 0.465 ± 0.003 |
| Image LSB, PNG, 20% | 0.368 ± 0.005 | 0.369 ± 0.003 | +0.0007 | 0.338 ± 0.005 |
| DCT, JPEG QF 75, 5% | 0.463 ± 0.004 | 0.462 ± 0.006 | -0.0007 | 0.367 ± 0.005 |
| DCT, JPEG QF 75, 20% | 0.389 ± 0.006 | 0.394 ± 0.006 | +0.0049 | 0.341 ± 0.009 |
| DCT from a TIFF, 5% | 0.493 ± 0.004 | 0.493 ± 0.004 | -0.0002 | – |
| DCT from a TIFF, 20% | 0.452 ± 0.004 | 0.459 ± 0.004 | +0.0070 | – |

## Targets (tier 3b)

| Condition | P_E | Target | Result |
|---|---|---|---|
| lsb4-f05 | 0.474 ± 0.004 | ≥ 0.46 | met |
| lsb4-f20 | 0.369 ± 0.003 | ≥ 0.33 | met |
| dct4-q75-f05 | 0.462 ± 0.006 | ≥ 0.45 | met |
| dct4-q75-f20 | 0.394 ± 0.006 | ≥ 0.25 | met |
| psi4-f05 | 0.493 ± 0.004 | ≥ 0.47 | met |
| psi4-f20 | 0.459 ± 0.004 | ≥ 0.3 | met |
| lsb4-f05 | 0.474 ± 0.004 | not below lsb3-f05 (0.475 ± 0.004) | met |
| lsb4-f20 | 0.369 ± 0.003 | not below lsb3-f20 (0.368 ± 0.005) | met |
| dct4-q75-f05 | 0.462 ± 0.006 | not below dct3-q75-f05 (0.463 ± 0.004) | met |
| dct4-q75-f20 | 0.394 ± 0.006 | not below dct3-q75-f20 (0.389 ± 0.006) | met |
| psi4-f05 | 0.493 ± 0.004 | not below psi3-f05 (0.493 ± 0.004) | met |
| psi4-f20 | 0.459 ± 0.004 | not below psi3-f20 (0.452 ± 0.004) | met |
| lsb4-f05 | 0.474 ± 0.004 | above hstego-png-f05 (0.465 ± 0.003) | met |
| lsb4-f20 | 0.369 ± 0.003 | above hstego-png-f20 (0.338 ± 0.005) | met |
| dct4-q75-f05 | 0.462 ± 0.006 | above hstego-q75-f05 (0.367 ± 0.005) | met |
| dct4-q75-f20 | 0.394 ± 0.006 | above hstego-q75-f20 (0.341 ± 0.009) | met |

## Root rates

| Method | Conditions | Round 3's rate |
|---|---|---|
| lsb | lsb4-f05, lsb4-f20 | holds |
| dct | dct4-q75-f05, dct4-q75-f20 | holds |
| precover | psi4-f05, psi4-f20 | holds |

## Tier 2

| Condition | Statistic | P_E | Target |
|---|---|---|---|
| lsb4-f05 | ws | 0.500 ± 0.002 | met |
| lsb4-f05 | spa | 0.501 ± 0.002 | met |
| lsb4-f05 | chi2 | 0.499 ± 0.001 | met |
| lsb4-f20 | ws | 0.497 ± 0.003 | met |
| lsb4-f20 | spa | 0.503 ± 0.002 | met |
| lsb4-f20 | chi2 | 0.497 ± 0.001 | met |
| dct4-q75-f05 | f5 | 0.491 ± 0.002 | – |
| dct4-q75-f20 | f5 | 0.488 ± 0.004 | met |
| psi4-f05 | f5 | 0.501 ± 0.002 | – |
| psi4-f20 | f5 | 0.496 ± 0.005 | met |

## Tier 1

| Condition | Tool | Rate on outputs | Rate on covers | Target |
|---|---|---|---|---|
| lsb4-f05 | zsteg | 0.008 | 0.008 | met |
| lsb4-f05 | binwalk | 0.000 | 0.000 | met |
| lsb4-f05 | pngcheck | 0.000 | 0.000 | met |
| lsb4-f05 | foremost | 0.000 | 0.000 | met |
| lsb4-f05 | strings | 0.000 | 0.000 | met |
| lsb4-f05 | exiftool | 0.000 | – (paired diff) | – |
| lsb4-f05 | openstego | 0.000 | 0.000 | met |
| lsb4-f05 | stegexpose | 0.070 | 0.070 | met |
| lsb4-f20 | zsteg | 0.010 | 0.008 | met |
| lsb4-f20 | binwalk | 0.000 | 0.000 | met |
| lsb4-f20 | pngcheck | 0.000 | 0.000 | met |
| lsb4-f20 | foremost | 0.000 | 0.000 | met |
| lsb4-f20 | strings | 0.000 | 0.000 | met |
| lsb4-f20 | exiftool | 0.000 | – (paired diff) | – |
| lsb4-f20 | openstego | 0.000 | 0.000 | met |
| lsb4-f20 | stegexpose | 0.076 | 0.070 | met |
| dct4-q75-f05 | binwalk | 0.000 | 0.000 | met |
| dct4-q75-f05 | foremost | 0.000 | 0.000 | met |
| dct4-q75-f05 | strings | 0.000 | 0.000 | met |
| dct4-q75-f05 | exiftool | 0.000 | – (paired diff) | – |
| dct4-q75-f05 | steghide | 0.000 | 0.000 | met |
| dct4-q75-f05 | outguess | 0.037 | 0.036 | met |
| dct4-q75-f05 | stegseek | 0.000 | 0.000 | met |
| dct4-q75-f05 | stegdetect | 0.069 | 0.064 | met |
| dct4-q75-f20 | binwalk | 0.000 | 0.000 | met |
| dct4-q75-f20 | foremost | 0.000 | 0.000 | met |
| dct4-q75-f20 | strings | 0.000 | 0.000 | met |
| dct4-q75-f20 | exiftool | 0.000 | – (paired diff) | – |
| dct4-q75-f20 | steghide | 0.000 | 0.000 | met |
| dct4-q75-f20 | outguess | 0.036 | 0.036 | met |
| dct4-q75-f20 | stegseek | 0.000 | 0.000 | met |
| dct4-q75-f20 | stegdetect | 0.064 | 0.064 | met |
