# Third-party notices

CipherStitch is released under the
[MIT License](https://github.com/leo-scientist/cipherstitch/blob/main/LICENSE). This file
lists the third-party work it contains or is based on, and the licences of the packages it
installs.

## Bundled with the app

| Asset          | Where                                    | Copyright                      | Licence                                       |
| -------------- | ---------------------------------------- | ------------------------------ | --------------------------------------------- |
| Inter font     | `src/cipherstitch/gui/resources/fonts/`  | 2016 The Inter Project Authors | SIL Open Font License 1.1, in `OFL-inter.txt` |
| Phosphor icons | `src/cipherstitch/gui/resources/icons/`  | 2023 Phosphor Icons            | MIT, in `LICENSE-phosphor.txt`                |

## The Independent JPEG Group's software

This software is based in part on the work of the Independent JPEG Group.

`src/cipherstitch/media/precover.py` compresses a picture to JPEG with the integer
arithmetic of the Independent JPEG Group's software, so that its coefficients are the ones
libjpeg would write. It is a numpy translation of parts of these files, as libjpeg-turbo
keeps them: `jccolor.c` (RGB to YCbCr), `jcprepare.c` and `jcsample.c` (edge padding,
`h2v2_downsample`, `h2v1_downsample` and `fullsize_downsample`), `jcparam.c` (the tables
scaled for the quality), `jfdctint.c` (`jpeg_fdct_islow`) and `jcdctmgr.c` (quantization).
The translation works on whole arrays of blocks and keeps only what CipherStitch uses: 8-bit
samples, 4:2:0, 4:2:2 or 4:4:4 colour or greyscale, and qualities of 50 or more. No C source
is included. The quantization tables come from ITU-T T.81 Annex K.

The Independent JPEG Group's README, as libjpeg-turbo distributes it, is included unaltered
as [`licenses/README.ijg`](https://github.com/leo-scientist/cipherstitch/blob/main/licenses/README.ijg),
in the wheel too (under `dist-info/licenses/`). Its "LEGAL ISSUES" section reads:

```text
In plain English:

1. We don't promise that this software works.  (But if you find any bugs,
   please let us know!)
2. You can use this software for whatever you want.  You don't have to pay us.
3. You may not pretend that you wrote this software.  If you use it in a
   program, you must acknowledge somewhere in your documentation that
   you've used the IJG code.

In legalese:

The authors make NO WARRANTY or representation, either express or implied,
with respect to this software, its quality, accuracy, merchantability, or
fitness for a particular purpose.  This software is provided "AS IS", and you,
its user, assume the entire risk as to its quality and accuracy.

This software is copyright (C) 1991-2020, Thomas G. Lane, Guido Vollbeding.
All Rights Reserved except as specified below.

Permission is hereby granted to use, copy, modify, and distribute this
software (or portions thereof) for any purpose, without fee, subject to these
conditions:
(1) If any part of the source code for this software is distributed, then this
README file must be included, with this copyright and no-warranty notice
unaltered; and any additions, deletions, or changes to the original files
must be clearly indicated in accompanying documentation.
(2) If only executable code is distributed, then the accompanying
documentation must state that "this software is based in part on the work of
the Independent JPEG Group".
(3) Permission for use of this software is granted only if the user accepts
full responsibility for any undesirable consequences; the authors accept
NO LIABILITY for damages of any kind.

These conditions apply to any software derived from or based on the IJG code,
not just to the unmodified library.  If you use our work, you ought to
acknowledge us.

Permission is NOT granted for the use of any IJG author's name or company name
in advertising or publicity relating to this software or products derived from
it.  This software may be referred to only as "the Independent JPEG Group's
software".

We specifically permit and encourage the use of this software as the basis of
commercial products, provided that all warranty or liability claims are
assumed by the product vendor.
```

## The detectability study

`benchmarks/detectability/` measures CipherStitch on the images of
[ALASKA v2](https://alaska.utt.fr/) (R. Cogranne, Q. Giboulot, P. Bas, *The ALASKA
Steganalysis Challenge: A First Step Towards Steganalysis "Into The Wild"*, IH&MMSec 2019),
which are licensed CC BY-NC-ND 4.0 for research and education. The repository contains none
of these images, only their file names, SHA-256 checksums and the results measured on
them. The study runs other tools (sealwatch, conseal, HStego, OpenStego, steghide and
more) from its own environment and Docker images and copies none of them into the
repository ([Licences and
terms](https://github.com/leo-scientist/cipherstitch/blob/main/benchmarks/detectability/README.md#licences-and-terms)).
OpenStego, steghide and HStego are named only to compare measurements, and their names
belong to their authors.

## Dependencies

The packages below are installed separately, each under its own licence, and none is
copied into this repository. Anyone who ships CipherStitch in a single app bundle with
them also has to meet their terms, the LGPL's in particular. The last five rows are the
packages the others pull in.

| Package                | Installed with       | Licence                                                    |
| ---------------------- | -------------------- | ---------------------------------------------------------- |
| cryptography           | always               | Apache-2.0 or BSD-3-Clause                                 |
| NumPy                  | always               | BSD-3-Clause                                               |
| Pillow                 | always               | MIT-CMU                                                    |
| zxcvbn                 | always               | MIT                                                        |
| PySide6 (Essentials)   | `gui`                | LGPL-3.0 (or GPL-2.0 or GPL-3.0)                           |
| PyNaCl                 | `crypto-extra`       | Apache-2.0                                                 |
| soundfile              | `audio`              | BSD-3-Clause, with libsndfile (LGPL-2.1) in its wheels     |
| pikepdf                | `documents`          | MPL-2.0                                                    |
| python-docx            | `documents`          | MIT                                                        |
| jpeglib                | `jpeg`               | MPL-2.0                                                    |
| reedsolo               | `ecc`                | Unlicense or MIT-0                                         |
| rawpy                  | `raw`                | MIT, with LibRaw (LGPL-2.1) in its wheels                  |
| cffi                   | always               | MIT-0, with cryptography, PyNaCl and soundfile             |
| pycparser              | always               | BSD-3-Clause, with cffi                                    |
| shiboken6              | `gui`                | LGPL-3.0 (or GPL-2.0 or GPL-3.0), with PySide6             |
| lxml                   | `documents`          | BSD-3-Clause, with pikepdf and python-docx                 |
| typing-extensions      | `audio`, `documents` | PSF-2.0, with soundfile and python-docx                    |
