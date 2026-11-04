# Security policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| 0.1.x   | yes       |

Fixes go into the latest release. Older releases are not patched.

## Reporting a vulnerability

Report it privately through *Report a vulnerability* on the repository's
[Security tab](https://github.com/leo-scientist/cipherstitch/security), which opens a
draft advisory that only the maintainer sees. Don't open a public issue for it, and don't
attach a real payload or a real password. A report is easiest to act on with the version
(`ciphst --version`), the platform, the mode, method and cipher, and the steps or a file
that shows the problem.

### In scope

- Reading a payload, or learning anything about it, without the password.
- Breaking file-name safety: a revealed file that lands outside the chosen folder, replaces
  an existing file, or gets a name the rules refuse.
- Breaking the atomic write: a failed or cancelled hide that leaves the destination changed.
- A hostile carrier, payload, profile or extension input that crashes or hangs the program,
  or makes it allocate memory without bound.
- Anything else that contradicts the [security model](docs/security.md).

### Out of scope

- How detectable Research mode's methods, parameters and cost functions are. Research mode
  is exploratory and guarantees nothing (see [Limitations](README.md#limitations)).
- Steganalysis results against Standard mode. They are measurements, not vulnerabilities,
  and welcome as an issue. [Detectability](docs/detectability.md) lists what was measured.
- Weak passwords below the strength the modes ask for, and anything that needs the password.
- Vulnerabilities in a dependency. Report them to its project, and open an issue here if
  CipherStitch has to move to a fixed version.

### What to expect

- An acknowledgement within a week.
- A fix in a release, with the advisory published and a line in the
  [changelog](CHANGELOG.md) once the release is out.
- Credit in the advisory and the changelog, if you want it.
