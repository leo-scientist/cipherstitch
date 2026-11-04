# Changelog

Notable changes to CipherStitch, in the format of
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Releases follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Changes that are not released
yet go under *Unreleased*.

## [Unreleased]

## [0.1.0] - 2026-11-09

The first release.

### Added

- Standard mode, the default, with three recommended methods: image LSB (PNG, BMP, TIFF),
  DCT coefficients (JPEG, or a camera RAW, PNG or TIFF compressed to JPEG with side
  information) and audio LSB (WAV, FLAC). All three use syndrome-trellis codes with
  content-adaptive costs, always encrypt (Argon2id, then AES-256-GCM, ChaCha20-Poly1305 or
  XChaCha20-Poly1305) and keep payloads at the advised size.
- Research mode: the other methods (phase coding, echo hiding, embedded object streams,
  DOCX whitespace padding, zero-width characters, sequential LSB replacement), the other
  ciphers and no encryption, the parameter editor, `.csprofile` profiles and other cost
  functions (WOW, S-UNIWARD, MiPOD, UERD).
- Extensions: drop-in methods, ciphers, parameter groups and cost functions for Research
  mode, with a contract, load-time checks and a testing helper.
- The `ciphst` command line, the `ciphst-gui` desktop app and the Python API (`hide_file`,
  `reveal_file`, `capacity`). The app keeps no record of its use: no settings, and its file
  dialogs leave Qt's folder history as it was.
- The detectability study: 3,000 ALASKA v2 photos per method against detectors trained on
  CipherStitch's own output, with the protocol and results in `benchmarks/detectability/`
  and a summary in `docs/detectability.md`.

[Unreleased]: https://github.com/leo-scientist/cipherstitch/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/leo-scientist/cipherstitch/releases/tag/v0.1.0
