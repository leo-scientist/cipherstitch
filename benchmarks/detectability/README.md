# Detectability study

How well current analyzers find CipherStitch's image output: image LSB, and DCT on a JPEG or from a TIFF precover. The method, every table and the limitations are in [`docs/detectability.md`](../../docs/detectability.md). This folder holds the code and the results.

## Layout

| Path | What it is |
|---|---|
| `prepare.py` | Downloads the ALASKA v2 TIFFs (resumable, sizes and SHA-256 checked) and makes the covers |
| `embed.py` | CipherStitch at 1, 5, 10, 20 and 50% of the capacity, for rounds 2 to 4 at round 1's payloads, and for rounds 2 and 3 at root rates |
| `references.py` | conseal simulators, OpenStego, steghide and HStego at CipherStitch's payloads |
| `kerckhoffs.py` | E-K: the lead's public positions, for image LSB and DCT (`--method dct`) |
| `identity.py` | Whether the precover's JPEG is Pillow's, coefficient for coefficient and byte for byte |
| `scan.py` | Tier 1: popular tools |
| `attacks.py` | Tier 2: WS, SPA, chi-square, the F5 estimator |
| `features.py`, `fast_srm.py`, `ensemble.py` | Tier 3b: rich models and an FLD ensemble |
| `report.py`, `environment.py` | Tables and versions |
| `round2.py`, `round3.py`, `round4.py` | Each round's tables, its targets and the root rates of the payload advice |
| `chart.py` | The README's chart, from the numbers in its table (`assets/charts/`) |
| `dct_diagnostic.py` | Where DCT's changes go, and what tells P-0 from libjpeg (exploratory) |
| `common.py`, `conditions.py`, `metrics.py` | Shared paths, the conditions, P_E and AUC |
| `indices.txt`, `sha256.txt` | The images used, and every download's digest |
| `results/` | `protocol.md` (fixed before the runs), CSVs, `tables.md`, `environment.txt`, and rounds 2 to 4 in `round-2/`, `round-3/` and `round-4/` |
| `tools/`, `stegdetect/`, `hstego/` | Docker images of the popular tools, and HStego's with its embedding script |
| `work/` | Git-ignored: images, features, raw scores |

## Reproduce

You need uv, Docker (OrbStack on macOS) and about 75 GB of disk. With 10 worker processes on an Apple M3 Pro, the run takes about a day of compute, most of it rich-model features.

```bash
# Environments
uv sync                                          # main venv: CipherStitch
uv sync --project benchmarks/detectability       # bench venv: sealwatch, conseal
docker build -t cipherstitch-detectability-tools benchmarks/detectability/tools
docker build --platform linux/amd64 -t cipherstitch-stegdetect benchmarks/detectability/stegdetect

# Data and embedding (main venv)
uv run python benchmarks/detectability/prepare.py --set lsb --count 3000
uv run python benchmarks/detectability/prepare.py --set jpeg --count 3000
uv run python benchmarks/detectability/identity.py --count 3000
uv run python benchmarks/detectability/embed.py --count 3000 \
    --family lsb --family dct-q75 --family psi --family pnosi --family jnosi
uv run python benchmarks/detectability/kerckhoffs.py --count 3000

# References, detectors and tables (bench venv)
B="uv run --project benchmarks/detectability python -W ignore benchmarks/detectability"
$B/references.py --count 3000
$B/scan.py
$B/attacks.py --count 3000
$B/features.py --count 3000 --kind lsb --condition cover --condition lsb-f05  # every condition
$B/ensemble.py --count 3000
$B/report.py --count 3000
$B/dct_diagnostic.py --count 3000
uv run python benchmarks/detectability/environment.py
```

Round 2 ([`results/round-2/protocol.md`](results/round-2/protocol.md)) runs on the same images, after round 1:

```bash
M="uv run python benchmarks/detectability"
$M/prepare.py --set lsb --crops 750 && $M/prepare.py --set jpeg --crops 750
$M/embed.py --count 3000 --family lsb2 --family dct2-q75 --fills 1,5,20
$M/embed.py --count 3000 --family lsb2 --family lsb2-crop --rates 140,210,300
$M/embed.py --count 3000 --family dct2-q75 --family dct2-q75-crop --rates 12,19,28
$M/embed.py --count 3000 --family psi2 --family jnosi2
$M/kerckhoffs.py --count 3000 --round 2 && $M/kerckhoffs.py --count 3000 --round 2 --method dct
docker build --platform linux/amd64 -t cipherstitch-hstego benchmarks/detectability/hstego
$B/references.py --count 3000 --part tools  # HStego. OpenStego and steghide are round 1's
$B/features.py --count 3000 --kind lsb --condition lsb2-f05  # every condition but f01
$B/features.py --count 3000 --kind lsb --images lsb-crops --condition crop-cover  # and the crops'
$B/attacks.py --count 3000 --round 2
$B/scan.py --round 2
$B/ensemble.py --count 3000 --round 2
$B/round2.py --count 3000
```

Round 3 ([`results/round-3/protocol.md`](results/round-3/protocol.md)) runs on the same images with the present writers:

```bash
$M/embed.py --count 3000 --family lsb3 --family dct3-q75 --fills 1,5,20
$M/embed.py --count 3000 --family lsb3 --rates 25,40,60,90
$M/embed.py --count 3000 --family dct3-q75 --rates 10,18,30
$M/embed.py --count 3000 --family psi3 --fills 5,20
$M/embed.py --count 3000 --family psi3 --rates 45,65,90
$M/kerckhoffs.py --count 3000 --round 3 && $M/kerckhoffs.py --count 3000 --round 3 --method dct
$B/attacks.py --count 3000 --round 3
$B/features.py --count 3000 --kind lsb --condition lsb3-f05  # every condition but f01
$B/ensemble.py --count 3000 --round 3
$B/scan.py --round 3
$B/round3.py --count 3000
```

Round 4 ([`results/round-4/protocol.md`](results/round-4/protocol.md)) runs on the same images with the present writers:

```bash
$M/embed.py --count 3000 --family lsb4 --family dct4-q75 --family psi4 --fills 5,20
$B/attacks.py --count 3000 --round 4
$B/scan.py --round 4
$B/features.py --count 3000 --kind lsb --condition lsb4-f05 --condition lsb4-f20
$B/features.py --count 3000 --kind jpeg --condition dct4-q75-f05  # and the other three
$B/ensemble.py --count 3000 --round 4
$B/round4.py
```

Before round 4, `features.py` took SRMQ1's submodels in the order sealwatch returns them, which follows each process's hash seed for two of them. It now sorts them, and every LSB detector of rounds 1 to 3 was trained again on the stored features put into that order (the protocols' change logs have the details).

Each stage skips what it has already written, so an interrupted run picks up where it stopped. The salt and the writer's random choices come from CipherStitch unchanged. A rerun therefore gives statistically the same files, not the same bytes.

## Licences and terms

- **ALASKA v2** (Cogranne, Giboulot and Bas, IH&MMSec 2019) is licensed CC BY-NC-ND 4.0, for research and education only. The study publishes results and its own code, never the images, embedded or not. Its terms forbid hiding real data in its images, so every payload is synthetic random bytes.
- **sealwatch** and **conseal** (MPL-2.0), **OpenStego** (GPL-2.0), **HStego** (MIT), **IJG libjpeg** (for HStego), **steghide**, **OutGuess**, **zsteg**, **binwalk**, **StegSeek**, **stegdetect** and **exiftool** are used as external tools, and none is vendored. **StegExpose** has no licence, so the Docker build downloads it and the repo never contains it.

## Citations

- R. Cogranne, Q. Giboulot, P. Bas. *The ALASKA Steganalysis Challenge: A First Step Towards Steganalysis "Into The Wild"*. IH&MMSec 2019.
- A. D. Ker, R. Böhme. *Revisiting Weighted Stego-Image Steganalysis*. Information Hiding 2008.
- S. Dumitrescu, X. Wu, Z. Wang. *Detection of LSB Steganography via Sample Pair Analysis*. IEEE TSP 51(7), 2003.
- A. Westfeld, A. Pfitzmann. *Attacks on Steganographic Systems*. Information Hiding 1999 (chi-square).
- J. Fridrich, M. Goljan, D. Hogea. *Steganalysis of JPEG Images: Breaking the F5 Algorithm*. Information Hiding 2002 (the F5 estimator).
- J. Fridrich, J. Kodovský. *Rich Models for Steganalysis of Digital Images*. IEEE TIFS 7(3), 2012 (SRM).
- M. Goljan, J. Fridrich, R. Cogranne. *Rich Model for Steganalysis of Color Images*. IEEE WIFS 2014 (CRM).
- V. Holub, J. Fridrich. *Low-Complexity Features for JPEG Steganalysis Using Undecimated DCT*. IEEE TIFS 10(2), 2015 (DCTR).
- X. Song, F. Liu, C. Yang, X. Luo, Y. Zhang. *Steganalysis of Adaptive JPEG Steganography Using 2D Gabor Filters*. IH&MMSec 2015 (GFR).
- J. Kodovský, J. Fridrich, V. Holub. *Ensemble Classifiers for Steganalysis of Digital Media*. IEEE TIFS 7(2), 2012.
- J. Fridrich, T. Pevný, J. Kodovský. *Statistically Undetectable JPEG Steganography: Dead Ends, Challenges, and Opportunities*. MM&Sec 2007 (nsF5).
- B. Li, M. Wang, J. Huang, X. Li. *A New Cost Function for Spatial Image Steganography*. ICIP 2014 (HILL).
- V. Holub, J. Fridrich, T. Denemark. *Universal Distortion Function for Steganography in an Arbitrary Domain*. EURASIP JIS 2014 (S-UNIWARD and J-UNIWARD, which HStego uses).
- D. Lerch-Hostalot. *HStego* (`github.com/daniellerch/hstego`), v0.6.1.
- M. Beneš, B. Lorch. *sealwatch* and *conseal*, University of Innsbruck (`github.com/uibk-uncover`). The versions are in `results/environment.txt`.
