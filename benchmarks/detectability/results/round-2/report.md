# Round 2

Mean P_E ± std over 10 splits. See `protocol.md`.

## Tier 3b: round 1 against round 2 at the same payload

| Payload (round 1's bytes) | Round 1 | Round 2 | Other tools |
|---|---|---|---|
| Image LSB, PNG, 5% | 0.406 ± 0.004 | 0.467 ± 0.004 | openstego 0.273 ± 0.006, hstego-png 0.465 ± 0.003 |
| Image LSB, PNG, 20% | 0.214 ± 0.006 | 0.327 ± 0.008 | openstego 0.135 ± 0.004, hstego-png 0.338 ± 0.005 |
| DCT, JPEG QF 75, 5% | 0.391 ± 0.007 | 0.442 ± 0.003 | steghide 0.109 ± 0.004, hstego-q75 0.367 ± 0.005 |
| DCT, JPEG QF 75, 20% | 0.146 ± 0.008 | 0.242 ± 0.007 | steghide 0.009 ± 0.002, hstego-q75 0.341 ± 0.009 |

## DCT from a precover

| Payload (round 1's P-SI bytes) | P-SI, round 1 | P-noSI, round 1 | P-SI, round 2 | P-noSI (`jnosi2`), round 2 |
|---|---|---|---|---|
| 5% | 0.425 ± 0.007 | 0.442 ± 0.005 | 0.494 ± 0.004 | 0.485 ± 0.004 |
| 20% | 0.168 ± 0.013 | 0.185 ± 0.010 | 0.456 ± 0.005 | 0.373 ± 0.005 |

## Targets (tier 3b)

| Condition | P_E | Target | Result |
|---|---|---|---|
| lsb2-f05 | 0.467 ± 0.004 | ≥ 0.46 | met |
| lsb2-f20 | 0.327 ± 0.008 | ≥ 0.33 | **missed** |
| dct2-q75-f05 | 0.442 ± 0.003 | ≥ 0.45 | **missed** |
| dct2-q75-f20 | 0.242 ± 0.007 | ≥ 0.25 | **missed** |
| psi2-f05 | 0.494 ± 0.004 | ≥ 0.47 | met |
| psi2-f20 | 0.456 ± 0.005 | ≥ 0.3 | met |
| dct2-q75-f05 | 0.442 ± 0.003 | above nsf5-f05 (0.433 ± 0.006) | met |
| dct2-q75-f20 | 0.242 ± 0.007 | above nsf5-f20 (0.179 ± 0.005) | met |
| psi2-f20 | 0.456 ± 0.005 | 0.03 above jnosi2-f20 (0.373 ± 0.005) | met |
| psi2-f05 | 0.494 ± 0.004 | not below jnosi2-f05 (0.485 ± 0.004) | met |
| psi2-f20 | 0.456 ± 0.005 | not below jnosi2-f20 (0.373 ± 0.005) | met |

## Calibration of the root rates

| Method | Root rate (bits per √N) | Mean share of the capacity, 512 × 512 | P_E |
|---|---|---|---|
| lsb | 140 | 20.3% | 0.361 ± 0.005 |
| lsb | 210 | 30.4% | 0.297 ± 0.005 |
| lsb | 300 | 43.5% | 0.238 ± 0.004 |
| lsb | 42.6 (lsb2-f05) | 6.2% | 0.467 ± 0.004 |
| lsb | **58** |  | P_E crosses 0.45 here |
| dct | 12 | 6.6% | 0.419 ± 0.005 |
| dct | 19 | 10.5% | 0.366 ± 0.008 |
| dct | 28 | 15.5% | 0.303 ± 0.005 |
| dct | **7** |  | P_E crosses 0.45 here |

## Scaling check (256 × 256 crops)

| Method | Root rate | P_E, 512 × 512 | P_E, 256 × 256 | Difference | Law holds |
|---|---|---|---|---|---|
| lsb | 140 | 0.361 ± 0.005 | 0.275 ± 0.009 | -0.086 | **missed** |
| lsb | 210 | 0.297 ± 0.005 | 0.193 ± 0.008 | -0.104 | **missed** |
| lsb | 300 | 0.238 ± 0.004 | 0.107 ± 0.008 | -0.131 | **missed** |
| dct | 12 | 0.419 ± 0.005 | 0.387 ± 0.006 | -0.032 | **missed** |
| dct | 19 | 0.366 ± 0.008 | 0.315 ± 0.005 | -0.051 | **missed** |
| dct | 28 | 0.303 ± 0.005 | 0.234 ± 0.005 | -0.068 | **missed** |

## E-K: the lead's public positions

| Condition | Statistic | P_E | ≥ 0.45 |
|---|---|---|---|
| lsb2-f01 | ws_public | 0.501 ± 0.002 | met |
| lsb2-f01 | spa_public | 0.499 ± 0.002 | met |
| lsb2-f01 | targeted | 0.501 ± 0.002 | met |
| lsb2-f01 | sca | 0.502 ± 0.004 | met |
| lsb2-f05 | ws_public | 0.500 ± 0.002 | met |
| lsb2-f05 | spa_public | 0.501 ± 0.003 | met |
| lsb2-f05 | targeted | 0.483 ± 0.002 | met |
| lsb2-f05 | sca | 0.491 ± 0.006 | met |
| dct2-q75-f01 | histogram | 0.495 ± 0.005 | met |
| dct2-q75-f01 | sca | 0.497 ± 0.006 | met |
| dct2-q75-f05 | histogram | 0.487 ± 0.002 | met |
| dct2-q75-f05 | sca | 0.486 ± 0.005 | met |

## Tier 2

| Condition | Statistic | P_E | Target |
|---|---|---|---|
| lsb2-f05 | ws | 0.499 ± 0.003 | met |
| lsb2-f05 | spa | 0.500 ± 0.002 | met |
| lsb2-f05 | chi2 | 0.498 ± 0.001 | met |
| lsb2-f20 | ws | 0.501 ± 0.002 | met |
| lsb2-f20 | spa | 0.499 ± 0.005 | met |
| lsb2-f20 | chi2 | 0.495 ± 0.001 | met |
| dct2-q75-f20 | f5 | 0.495 ± 0.004 | met |
| psi2-f20 | f5 | 0.495 ± 0.006 | met |
| jnosi2-f20 | f5 | 0.499 ± 0.002 | met |
| dct2-q75-f05 | f5 | 0.500 ± 0.002 | – |
| psi2-f05 | f5 | 0.501 ± 0.003 | – |
| jnosi2-f05 | f5 | 0.500 ± 0.003 | – |
| hstego-png-f05 | ws | 0.500 ± 0.003 | – |
| hstego-png-f05 | spa | 0.500 ± 0.001 | – |
| hstego-png-f05 | chi2 | 0.498 ± 0.001 | – |
| hstego-png-f20 | ws | 0.495 ± 0.006 | – |
| hstego-png-f20 | spa | 0.502 ± 0.003 | – |
| hstego-png-f20 | chi2 | 0.498 ± 0.002 | – |
| hstego-q75-f05 | f5 | 0.498 ± 0.002 | – |
| hstego-q75-f20 | f5 | 0.490 ± 0.003 | – |

## Tier 1

| Condition | Tool | Rate on outputs | Rate on covers | Target |
|---|---|---|---|---|
| lsb2-f05 | zsteg | 0.008 | 0.008 | met |
| lsb2-f05 | binwalk | 0.000 | 0.000 | met |
| lsb2-f05 | pngcheck | 0.000 | 0.000 | met |
| lsb2-f05 | foremost | 0.000 | 0.000 | met |
| lsb2-f05 | strings | 0.000 | 0.000 | met |
| lsb2-f05 | exiftool | 0.000 | – (paired diff) | – |
| lsb2-f05 | openstego | 0.000 | 0.000 | met |
| lsb2-f05 | stegexpose | 0.070 | 0.070 | met |
| lsb2-f20 | zsteg | 0.006 | 0.008 | met |
| lsb2-f20 | binwalk | 0.000 | 0.000 | met |
| lsb2-f20 | pngcheck | 0.000 | 0.000 | met |
| lsb2-f20 | foremost | 0.000 | 0.000 | met |
| lsb2-f20 | strings | 0.000 | 0.000 | met |
| lsb2-f20 | exiftool | 0.000 | – (paired diff) | – |
| lsb2-f20 | openstego | 0.000 | 0.000 | met |
| lsb2-f20 | stegexpose | 0.080 | 0.070 | met |
| dct2-q75-f05 | binwalk | 0.000 | 0.000 | met |
| dct2-q75-f05 | foremost | 0.000 | 0.000 | met |
| dct2-q75-f05 | strings | 0.000 | 0.000 | met |
| dct2-q75-f05 | exiftool | 0.000 | – (paired diff) | – |
| dct2-q75-f05 | steghide | 0.000 | 0.000 | met |
| dct2-q75-f05 | outguess | 0.035 | 0.036 | met |
| dct2-q75-f05 | stegseek | 0.000 | 0.000 | met |
| dct2-q75-f05 | stegdetect | 0.063 | 0.064 | met |
| dct2-q75-f20 | binwalk | 0.000 | 0.000 | met |
| dct2-q75-f20 | foremost | 0.000 | 0.000 | met |
| dct2-q75-f20 | strings | 0.000 | 0.000 | met |
| dct2-q75-f20 | exiftool | 0.000 | – (paired diff) | – |
| dct2-q75-f20 | steghide | 0.000 | 0.000 | met |
| dct2-q75-f20 | outguess | 0.056 | 0.036 | met |
| dct2-q75-f20 | stegseek | 0.000 | 0.000 | met |
| dct2-q75-f20 | stegdetect | 0.066 | 0.064 | met |
