# Protocol

Fixed before the detectors in each section ran on CipherStitch output. A later change only fixes a bug, and the change log at the end says what changed and why.

Common to every section:

- Seed `SEED = 20261020` (`common.py`). Every random choice derives from it.
- Covers and embedded files of the same image always fall in the same half of a split.
- 10 random 50/50 splits of the images. Split `s` is drawn with `numpy.random.default_rng([SEED, s])`.
- P_E = min over thresholds of (P_FA + P_MD) / 2. The threshold, and for a scalar score its orientation, is chosen on the training half; P_E is reported on the testing half. AUC is computed on the testing half. Both are reported as mean ± std over the splits.

## E-K: Kerckhoffs test of the public LSB positions

Written 2026-10-20, before E-K ran on CipherStitch output.

- **Public positions.** With a password, image LSB writes only the 16-byte salt (the prefix) at positions anyone can compute: `ImageChannel._coded_units(0, None, units_for(16))`, 1,386 values for every 512×512 RGB image. The head and the tail are in the keyed order.
- **Data.** The first 3,000 images of the `lsb` index set: covers, and CipherStitch image LSB at 1% and 5% fill (`embed.py`, encrypted, default cipher).
- **Positive controls**, simulated on the covers: ±1 changes (LSB matching, inwards at 0 and 255) on a random 1.6%, 10% and 50% of the public values. 1.6% is about CipherStitch's own change density there (22 of 1,386).
- **Per-image statistics.** Residuals r = x − x̂, with x̂ the KB prediction of the value from its 8 neighbours in the same colour plane (interior values only). Weights w = 1 / (5 + the variance of the 4 direct neighbours).
  - `ws_public`: Weighted Stego restricted to the public values, 2 Σ w (x − x̄) r / Σ w.
  - `spa_public`: Sample Pairs over the pairs (public value, its right neighbour) and (public value, the value below), the smaller real root (−b / 2a when there is none).
  - `targeted`: a Fisher linear discriminant (ridge 1e-6 × trace / d) over the differences between the public values and all other interior values of the same image. These are the weighted mean of r² and of |r| and the mean of w; the shares with |r| < 0.5, < 1 and < 2; the share whose parity agrees with the parity of round(x̂); and the histogram of round(r) over −4 … 4 (9 bins). That makes 16 features.
- **Metrics.** P_E and AUC per statistic, as above.
- **Verdict.** E-K detects when any statistic reaches P_E ≤ 0.40 at 1% or 5% fill.

## Scope: the reduced variant

Written 2026-10-20, before tiers 1–3 ran on CipherStitch output. With the measured costs (`docs/detectability.md#compute`), the full variant projected well over the 24-hour budget, so the study runs its reduced variant:

- N = 3,000 images per index set (`lsb`, `jpeg`).
- DCT on QF 75 covers only. QF 95 is left out.
- No Aletheia (tier 3a).
- Popular-tool references are OpenStego and steghide only (no OutGuess embedding). The simulators kept are the ones the validation gate and the implementation check need: LSB replacement, LSB matching, LSB matching with Hamming codes' efficiency and HILL at CipherStitch's 5% and 20% payloads, and nsF5 at CipherStitch's 5% and 20% payloads and at 0.2 bpnzAC.
- Every precover condition (P-0, P-SI, P-noSI, J-noSI) and E-C stay.

Payloads match CipherStitch's per image. A simulator embeds the bits CipherStitch's output stores at that fill (`rate` in the manifests: bits per colour value, or per non-zero AC coefficient over every component). A tool gets CipherStitch's payload bytes.

## Tier 1: popular tools

- The first 500 images of each set: their covers, and the embedded files of every condition in the tier.
- Each tool's verdict is binary per file (rules in `scan.py`). We report the detection rate on embedded files and the false-alarm rate on covers, each with a Wilson 95% interval.
- StegExpose's fused score is also scored as a statistic: P_E and AUC as above.
- StegSeek `--seed` searches all 2³² seeds of a file without steghide data, which takes 36 s per file. It runs on the first 30 images of each condition.
- binwalk's mean block entropy is scored as a statistic too. Its plot gives no verdict; its signature scan does.
- exiftool is a paired diff: an embedded file is flagged when any tag (outside the System and ExifTool groups) differs from its own cover's. Nobody but the sender has the cover, so this measures what the output gives away, and there is no false-alarm rate. The tags that differ are listed in `scan-metadata.csv`.

## Tier 2: classical statistical attacks

- All 3,000 images.
- LSB: sealwatch `ws` (KB predictor, weighted), `spa` and `chi2` (p-value), per colour. The image's score is the mean over the colours (for chi2, the largest p-value).
- JPEG: sealwatch `F5.attack` on luminance (the estimated change rate).
- Each estimate is a scalar score: P_E and AUC as above. We also report the mean estimate against the true value.

## Tier 3b: rich models and the FLD ensemble

- Features (`features.py`):
  - LSB: SRMQ1 on R, G and B plus CRM with T = 3, 43,663 values.
  - JPEG: DCTR and GFR on luminance plus DCTR on Cb and Cr at their own resolution, 41,000 values.
  - DCTR's step is sealwatch's for the file's quality factor. That factor is found by matching its luminance table against libjpeg's scaled Annex K tables.
  - GFR's per-scale steps are Song et al.'s at QF 75, (2, 4, 6, 8). Between 75 and 95 they are interpolated linearly in the quality factor between that set and the QF 95 set, (0.1, 1, 1.5, 2). At QF 90 that gives (0.575, 1.75, 2.625, 3.5).
  - The co-occurrences are computed by `fast_srm.py`. They are the same numbers as sealwatch's, checked on 11 images.
- Classifier: sealwatch `FldEnsembleTrainer` with its automatic L and d_sub (at most 500 base learners), trained per split on the training half's covers and embedded files, seed `SEED + split`.
- Score: `predict_confidence`, the vote share in [−1, 1]. P_E is that of the majority vote (embedded when the confidence is > 0) on the testing half; AUC uses the confidence. Mean ± std over the 10 splits.
- P_E is also reported per camera model. Each image's testing-half predictions are pooled over the splits, and only models with at least 100 such predictions are reported. The same pooling restricted to images whose Detection row raises no cover warning at that fill gives P_E on the covers the app does not warn about.
- Conditions: every embedded set against its own covers (PNG covers for LSB, the QF 75 covers for DCT on JPEG, P-0 for P-SI and P-noSI, Pillow's JPEG at the precover's settings for J-noSI).

## Validation gate

Every item must pass before any CipherStitch number counts.

1. WS and SPA (tier 2) detect conseal's LSB replacement and OpenStego at CipherStitch's 20% payload (about 0.2 bits per colour value) with P_E < 0.10. The 5% results are reported next to it. (The item was first set at 5%; see the change log.)
2. At each of CipherStitch's 5% and 20% payloads, tier 3b's P_E for LSB replacement is at most LSB matching's plus the larger of their two stds, and both are below HILL's. (The item first asked for LSB replacement < LSB matching < HILL; see the change log.)
3. LSB matching at CipherStitch's 20% payload (about 0.2 bits per colour value) gives tier 3b P_E ≤ 0.25.
4. nsF5 at 0.2 bpnzAC gives tier 3b P_E ≤ 0.25.

If an item fails, the pipeline is suspected broken: debug, at most twice, before reporting.

## Checks on CipherStitch

- **Implementation:** at 5% and 20%, CipherStitch image LSB's tier 3b P_E against LSB matching with Hamming efficiency. A difference within 0.03 means CipherStitch is as good as theory says. CipherStitch lower by more than 0.03 is a finding.
- **Side information:** at every fill, P-SI's P_E must be at least P-noSI's minus the larger of their two stds. Lower is a product finding. Within that band, the side information gains nothing here.
- **E-C, compressor fingerprint:** P-0 against Pillow's JPEG at the precover's settings (the J-noSI covers), from the same TIFFs, with the tier 3b JPEG protocol. P_E ≤ 0.40 is a fingerprint. We also compare the headers: segment order, Huffman tables, quantization tables, JFIF.

## DCT diagnostic

Added on 2026-10-21 at the owner's request, after tier 2's DCT numbers were seen. It is exploratory: it has no thresholds, and no gate item, check or headline number depends on it. `dct_diagnostic.py` runs it on the 3,000 images of the jpeg set and writes `dct-diagnostic.md` with two CSV files. P_E and AUC are the F5 estimator's, with tier 2's splits.

- **Where DCT's changes go**, at 5% and 20% on the QF 75 covers. For DCT, nsF5 and steghide: the changes per payload bit, the share in luminance, the change rate in the modes the F5 estimator reads (01, 10, 11), and the changes to zero. Three simulations at the bound use the same covers and CipherStitch's payloads:
  - binary changes towards zero at CipherStitch's UERD costs (mirrored from `cipherstitch.methods.uerd`), over every coefficient at once;
  - the same, split into DCT's lead, head and tail with their sizes and message bits;
  - conseal's ternary UERD per component, at the rate the nsF5 reference uses.
  - Added after the first run, to tell conseal's UERD and CipherStitch's apart: the first simulation with changes either way at random, and with the bits split over the components in proportion to their non-zero AC coefficients.
- **What tells P-0 from Pillow's JPEG** (E-C at tier 2). Each TIFF is compressed again step by step:
  - P-0 rebuilt in the script (checked: it must match P-0 exactly);
  - the same float DCT after libjpeg's 8-bit colour conversion and 4:2:0 downsampling;
  - libjpeg itself (jpeglib, libjpeg-turbo 2.1) with its float DCT, and with its integer DCT (checked: it must match Pillow's files exactly).

## Change log

- 2026-10-20, E-K: a wording fix. The feature list said 15 features and "the weighted mean of w". The code, which ran unchanged, computes 16, and the plain mean of w. The text now says what was measured.
- 2026-10-20, tier 1: two rule bugs fixed in `scan.py` after the first run.
  - The OpenStego extraction rule counted `ls`'s "cannot access" message as an extracted file. It flagged every file, covers included.
  - The strings rule matched the plain frame's magic without regard to case. That matched chance bytes (a mixed-case variant in one OpenStego file); CipherStitch's magic is upper case.
  - The raw tool outputs are unchanged; only the verdicts are recomputed.
- 2026-10-20, tier 1, before its numbers were reported:
  - The StegSeek sample, the binwalk entropy statistic and exiftool as a paired diff are now written down here. The first two were decided before the run.
  - Bug fix: `binwalk -E` computes entropy only, so the first run never scanned signatures. binwalk now runs a signature scan and an entropy scan, retrying a scan that analysed 0 files (it does under load).
  - Bug fix: 2,200 simulator records from the 200-image pilot had no status. Three nsF5 f05 images had been embedded where CipherStitch's payload did not fit. The records were regenerated from the same seeds, those three are now "no payload", and nsF5 f05's tier 2 scores were recomputed.
- 2026-10-20, validation gate item 1, **changed after its 5% numbers were seen**, by the owner's decision. The item said 5%; it now says 20%.
  - At 5%, WS gave P_E 0.098 (LSBR) and 0.0997 (OpenStego); SPA gave 0.162 and 0.163.
  - Two debugging attempts found no pipeline bug:
    - an independent SPA gives 0.134 on the same 1,000 images, correlation 0.90;
    - the estimate grows by 0.048 for a true rate of 0.0497;
    - 10% of the covers already score above 0.118;
    - per camera, SPA reaches 0.10 only on cameras holding 9–12% of the images, with the Sigma Foveon cameras worst at 0.32–0.35.
  - The 0.10 threshold at 5% was an assumption that ALASKA's mix of about 40 cameras does not meet. At 20%, WS gives 0.042 / 0.041 and SPA 0.052 / 0.052. The item checks that the pipeline works, which these numbers show. No CipherStitch number entered the decision, and no other item or threshold changed.
- 2026-10-20, tier 1, zsteg rule tightened after its first results: it flagged 99% of the covers.
  - The hits were runs of one character ("UUUU…", tabs) that flat bit planes produce, not text.
  - Text now counts when it is at least 20 characters long, has at least 10 distinct characters, at least 3 spaces, and is at least 80% letters and spaces.
  - Only hits in bit planes b1–b4 and in data after the image (extradata) count, for text and for file types. Planes b5–b8 are the picture itself, and smooth pixel values fall among the letters. A first fix without this still flagged 96% of covers.
  - With the final rule, a plaintext positive control (text written by LSB replacement in b1,rgb,lsb) is still flagged.
  - The file types are unchanged; gzip matches about 1.6% of covers by chance.
  - The rule is applied to covers and embedded files alike. The raw outputs are unchanged.
- 2026-10-21, validation gate item 2, **changed after its tier 3b numbers were seen**, by the owner's decision.
  - The item asked for LSB replacement < LSB matching < HILL. Replacement and matching tie in tier 3b: 0.274 / 0.273 at 5%, 0.138 / 0.137 at 20%. Per split, the difference is +0.0012 ± 0.0054 and +0.0008 ± 0.0034, and replacement is lower in 4 of 10 splits.
  - Two debugging attempts found no pipeline bug:
    - the simulators are right: replacement never moves an even value down, matching does for half of them, and both change the same share of values;
    - the tie is within the split noise.
  - The rich model sees the same rate of ±1 changes. Only the targeted tier 2 attacks use replacement's asymmetry (WS at 5%: 0.098 against 0.497).
  - The item now asks for replacement ≤ matching within the larger std, and both below HILL (0.481 / 0.373). No CipherStitch number entered the decision, and no other item or threshold changed.
- 2026-10-21, an exploratory DCT diagnostic added at the owner's request, after tier 2's DCT numbers were seen (see [DCT diagnostic](#dct-diagnostic)). It changes no condition, parameter, threshold or score of the protocol.
- 2026-10-21, DCT diagnostic: two simulations added after its first numbers were seen (changes either way, and bits split per component). They tell apart what the first run could not. Nothing else in the diagnostic changed.
- 2026-11-02, bug fix in the LSB features, found in round 4.
  - sealwatch appends two merged SPAM submodels of SRMQ1 (`s35_spam11_q1` and `s5x5_spam14hv_q1`) in set order, which follows each process's hash seed. So each 20-image chunk of LSB features was stored in one of two orders, and the ensembles partly learnt the order instead of the embedding. JPEG features and CRM are not affected.
  - `features.py` now takes SRMQ1's submodels in sorted order. The stored chunks were put into that order in place. A chunk whose images exist was matched against one image's recomputed features. A chunk of this round, whose embedded images are gone, was compared with its cover chunk row by row, and every row agreed. 24 rows recomputed afterwards match exactly.
  - Every LSB ensemble was trained again. Tier 1, tier 2 and every JPEG result are unchanged. In this round no LSB P_E moved by 0.01 or more.
  - The validation gate still passes: item 2 gives 0.273 and 0.272 at 5%, 0.133 and 0.135 at 20%, below HILL's 0.474 and 0.370, and item 3 gives 0.135.
