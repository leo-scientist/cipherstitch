# Round 3

Mean P_E ± std over 10 splits. See `protocol.md`.

## Tier 3b: round 2 against round 3 at the same payload

| Payload (round 1's bytes) | Round 2 | Round 3 | Others, round 2 |
|---|---|---|---|
| Image LSB, PNG, 5% | 0.467 ± 0.004 | 0.475 ± 0.004 | openstego 0.273 ± 0.006, hstego-png 0.465 ± 0.003 |
| Image LSB, PNG, 20% | 0.327 ± 0.008 | 0.368 ± 0.005 | openstego 0.135 ± 0.004, hstego-png 0.338 ± 0.005 |
| DCT, JPEG QF 75, 5% | 0.442 ± 0.003 | 0.463 ± 0.004 | steghide 0.109 ± 0.004, hstego-q75 0.367 ± 0.005 |
| DCT, JPEG QF 75, 20% | 0.242 ± 0.007 | 0.389 ± 0.006 | steghide 0.009 ± 0.002, hstego-q75 0.341 ± 0.009 |
| DCT from a TIFF, 5% | 0.494 ± 0.004 | 0.493 ± 0.004 | jnosi2 0.485 ± 0.004 |
| DCT from a TIFF, 20% | 0.456 ± 0.005 | 0.452 ± 0.004 | jnosi2 0.373 ± 0.005 |

## Targets (tier 3b)

| Condition | P_E | Target | Result |
|---|---|---|---|
| lsb3-f05 | 0.475 ± 0.004 | ≥ 0.46 | met |
| lsb3-f20 | 0.368 ± 0.005 | ≥ 0.33 | met |
| dct3-q75-f05 | 0.463 ± 0.004 | ≥ 0.45 | met |
| dct3-q75-f20 | 0.389 ± 0.006 | ≥ 0.25 | met |
| psi3-f05 | 0.493 ± 0.004 | ≥ 0.47 | met |
| psi3-f20 | 0.452 ± 0.004 | ≥ 0.3 | met |
| lsb3-f05 | 0.475 ± 0.004 | not below lsb2-f05 (0.467 ± 0.004) | met |
| lsb3-f20 | 0.368 ± 0.005 | not below lsb2-f20 (0.327 ± 0.008) | met |
| dct3-q75-f05 | 0.463 ± 0.004 | not below dct2-q75-f05 (0.442 ± 0.003) | met |
| dct3-q75-f20 | 0.389 ± 0.006 | not below dct2-q75-f20 (0.242 ± 0.007) | met |
| psi3-f05 | 0.493 ± 0.004 | not below psi2-f05 (0.494 ± 0.004) | met |
| psi3-f20 | 0.452 ± 0.004 | not below psi2-f20 (0.456 ± 0.005) | met |
| lsb3-f05 | 0.475 ± 0.004 | above hstego-png-f05 (0.465 ± 0.003) | met |
| lsb3-f20 | 0.368 ± 0.005 | above hstego-png-f20 (0.338 ± 0.005) | met |
| dct3-q75-f05 | 0.463 ± 0.004 | above hstego-q75-f05 (0.367 ± 0.005) | met |
| dct3-q75-f20 | 0.389 ± 0.006 | above hstego-q75-f20 (0.341 ± 0.009) | met |

## Calibration of the root rates

| Method | Root rate (bits per √N) | Mean share of the capacity, 512 × 512 | P_E |
|---|---|---|---|
| lsb | 25 | 3.6% | 0.485 ± 0.002 |
| lsb | 40 | 5.8% | 0.476 ± 0.004 |
| lsb | 60 | 8.7% | 0.464 ± 0.004 |
| lsb | 90 | 13.1% | 0.438 ± 0.006 |
| lsb | 42.6 (lsb3-f05) | 6.3% | 0.475 ± 0.004 |
| lsb | **76** |  | P_E crosses 0.45 here |
| dct | 10 | 5.5% | 0.463 ± 0.004 |
| dct | 18 | 9.9% | 0.437 ± 0.005 |
| dct | 30 | 16.6% | 0.403 ± 0.005 |
| dct | **14** |  | P_E crosses 0.45 here |
| precover | 45 | 20.8% | 0.443 ± 0.006 |
| precover | 65 | 30.1% | 0.414 ± 0.010 |
| precover | 90 | 41.6% | 0.360 ± 0.006 |
| precover | **39** |  | P_E crosses 0.45 here |

## E-K: the lead's public positions

| Condition | Statistic | P_E | ≥ 0.45 |
|---|---|---|---|
| lsb3-f01 | ws_public | 0.497 ± 0.002 | met |
| lsb3-f01 | spa_public | 0.500 ± 0.001 | met |
| lsb3-f01 | targeted | 0.500 ± 0.003 | met |
| lsb3-f01 | sca | 0.505 ± 0.005 | met |
| lsb3-f05 | ws_public | 0.501 ± 0.001 | met |
| lsb3-f05 | spa_public | 0.501 ± 0.003 | met |
| lsb3-f05 | targeted | 0.486 ± 0.003 | met |
| lsb3-f05 | sca | 0.482 ± 0.005 | met |
| dct3-q75-f01 | histogram | 0.493 ± 0.003 | met |
| dct3-q75-f01 | sca | 0.493 ± 0.008 | met |
| dct3-q75-f05 | histogram | 0.496 ± 0.002 | met |
| dct3-q75-f05 | sca | 0.502 ± 0.005 | met |

## Tier 2

| Condition | Statistic | P_E | Target |
|---|---|---|---|
| lsb3-f05 | ws | 0.501 ± 0.002 | met |
| lsb3-f05 | spa | 0.501 ± 0.002 | met |
| lsb3-f05 | chi2 | 0.499 ± 0.001 | met |
| lsb3-f20 | ws | 0.483 ± 0.003 | met |
| lsb3-f20 | spa | 0.500 ± 0.002 | met |
| lsb3-f20 | chi2 | 0.497 ± 0.001 | met |
| dct3-q75-f20 | f5 | 0.488 ± 0.002 | met |
| psi3-f20 | f5 | 0.500 ± 0.003 | met |
| dct3-q75-f05 | f5 | 0.495 ± 0.001 | – |
| psi3-f05 | f5 | 0.502 ± 0.001 | – |

## Tier 1

| Condition | Tool | Rate on outputs | Rate on covers | Target |
|---|---|---|---|---|
| lsb3-f05 | zsteg | 0.006 | 0.008 | met |
| lsb3-f05 | binwalk | 0.000 | 0.000 | met |
| lsb3-f05 | pngcheck | 0.000 | 0.000 | met |
| lsb3-f05 | foremost | 0.000 | 0.000 | met |
| lsb3-f05 | strings | 0.000 | 0.000 | met |
| lsb3-f05 | exiftool | 0.000 | – (paired diff) | – |
| lsb3-f05 | openstego | 0.000 | 0.000 | met |
| lsb3-f05 | stegexpose | 0.074 | 0.070 | met |
| lsb3-f20 | zsteg | 0.006 | 0.008 | met |
| lsb3-f20 | binwalk | 0.000 | 0.000 | met |
| lsb3-f20 | pngcheck | 0.000 | 0.000 | met |
| lsb3-f20 | foremost | 0.000 | 0.000 | met |
| lsb3-f20 | strings | 0.000 | 0.000 | met |
| lsb3-f20 | exiftool | 0.000 | – (paired diff) | – |
| lsb3-f20 | openstego | 0.000 | 0.000 | met |
| lsb3-f20 | stegexpose | 0.072 | 0.070 | met |
| dct3-q75-f05 | binwalk | 0.000 | 0.000 | met |
| dct3-q75-f05 | foremost | 0.000 | 0.000 | met |
| dct3-q75-f05 | strings | 0.000 | 0.000 | met |
| dct3-q75-f05 | exiftool | 0.000 | – (paired diff) | – |
| dct3-q75-f05 | steghide | 0.000 | 0.000 | met |
| dct3-q75-f05 | outguess | 0.030 | 0.036 | met |
| dct3-q75-f05 | stegseek | 0.000 | 0.000 | met |
| dct3-q75-f05 | stegdetect | 0.065 | 0.064 | met |
| dct3-q75-f20 | binwalk | 0.000 | 0.000 | met |
| dct3-q75-f20 | foremost | 0.000 | 0.000 | met |
| dct3-q75-f20 | strings | 0.000 | 0.000 | met |
| dct3-q75-f20 | exiftool | 0.000 | – (paired diff) | – |
| dct3-q75-f20 | steghide | 0.000 | 0.000 | met |
| dct3-q75-f20 | outguess | 0.044 | 0.036 | met |
| dct3-q75-f20 | stegseek | 0.000 | 0.000 | met |
| dct3-q75-f20 | stegdetect | 0.066 | 0.064 | met |
