# Contributing

## Setup

CipherStitch uses [uv](https://docs.astral.sh/uv/) and needs Python 3.12 or later.

```bash
uv sync                    # .venv with every extra and the dev tools
uv run ciphst --help
uv run ciphst-gui
```

The `Makefile` wraps the everyday commands, and `make` alone lists them:

| Command                   | Does                                                       |
| ------------------------- | ---------------------------------------------------------- |
| `make install`            | `uv sync`: `.venv` with every extra and the dev tools       |
| `make run`                | starts the desktop app                                     |
| `make cli ARGS="…"`       | runs the command line, e.g. `ARGS="capacity cover.png"`    |
| `make test`               | runs the suite headless                                    |
| `make test-core`          | runs the suite on a core-only install                      |
| `make check`              | runs every CI check                                        |
| `make format`             | formats the code                                           |
| `make install-app`        | installs `ciphst` and `ciphst-gui` for your user           |
| `make clean`              | removes caches and build output (keeps `.venv`)            |

Every target that needs uv says so if it is missing.

Read [Architecture](docs/architecture.md) first. [Stored format](docs/format.md) lists
what must never change.

## Checks

CI runs these on Python 3.12, 3.13 and 3.14, and every one must pass:

```bash
uv run ruff check
uv run ruff format --check   # the whole repository, Python blocks in Markdown included
uv run mypy                  # strict, over src, tests and examples
uv run pytest                # fails below 80 % coverage
```

## Tests

```bash
uv run pytest --no-cov tests/test_dct.py             # one file: skip the coverage gate
uv run pytest --no-cov tests/test_cli.py -k cipher   # matching tests only
QT_QPA_PLATFORM=cocoa uv run pytest                  # on macOS's own platform, not offscreen
```

The GUI tests run on Qt's offscreen platform unless `QT_QPA_PLATFORM` names another.

The suite must also pass with no extras installed. Tests of optional features skip
themselves through `pytest.importorskip`, or `skip_unless_installed` /
`skip_unless_method_installed` from `tests/conftest.py`. `make test-core` runs it on a
core-only install in a throwaway environment, as CI does on Python 3.12:

```bash
uv run --isolated --locked --no-dev --group test pytest
```

`UV_PROJECT_ENVIRONMENT` runs the suite on another Python version without touching `.venv`:

```bash
UV_PROJECT_ENVIRONMENT=/tmp/cipherstitch-3.12 uv run --python 3.12 pytest
```

Fixtures in `tests/conftest.py`:

- `fast_argon2` (automatic) lowers the Argon2id cost. Scripts outside the suite can do the
  same with `kdf.ARGON2_MEMORY_KIB = 32` and `kdf.ARGON2_ITERATIONS = 1`.
- `make_image` creates a noise image, and `secret_file` a small payload.

GUI tests patch `show_in_folder` in the page modules, because the pages import the name
directly.

## Conventions

- **Style:** ruff with a line length of 100, mypy in strict mode and Python 3.12 syntax
  (type parameter lists such as `class Channel[Output]`).
- **Blank lines:** inside a function, put a blank line before and after every `if`, `for`,
  `while`, `try`, `with` or nested `def`, and before every `return`. Skip it at the start
  of a block, right after a docstring and inside `elif` / `else` / `except` chains. A
  comment stays attached to the statement below it.
- **Small functions:** split a long function into named steps when that does not cost
  performance, but keep hot loops over pixels, samples or characters inline.
- **Comments and docstrings:** only what the code cannot say (a reason, a contract, a
  trap). Format and method details live in `docs/`, so point to them, e.g.
  `(docs/format.md#keyed-order)`, instead of repeating them.
- **Data:** frozen dataclasses with slots, and functions that return new objects instead
  of changing their arguments.
- **Errors:** every exception derives from `CipherStitchError`, and messages are written
  for the user.
- **Optional dependencies:** import them inside functions. When one is missing, raise
  `NotAvailableError(catalog.X.install_hint(...))`. Add a `[[tool.mypy.overrides]]`
  entry for untyped libraries, and a coverage omit rule for modules that run only with
  the extra.
- **Naming:** a carrier with a payload is `Embedded*` / `embedded`.
- **Unicode in strings:** ruff (RUF001) flags lone Cyrillic look-alikes in string literals,
  so build them with `chr(...)`.
- **Styles of the app** live in `src/cipherstitch/gui/resources/cipherstitch.qss`.

## Compatibility

Data hidden by one release must stay readable by every later one. The values listed in
[Stored format § Compatibility](docs/format.md#compatibility) are frozen, and test
vectors pin them: never regenerate a vector to make a test pass.

## Dependencies

`pyproject.toml` pins every dependency to an exact version, and `uv.lock` pins the packages
they pull in. CI installs with `uv sync --locked`. To update, change the versions in
`pyproject.toml`, run `uv lock --upgrade`, and commit `uv.lock` with it. Qt (PySide6) stays
on a long-term support release.

Platform limits: jpeglib has no Linux aarch64 wheel, and pikepdf's macOS arm64 wheels need
macOS 15 or later.

## Commits

One logical change per commit, with the message `<type>: <description>`, where the type is
one of `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf` or `ci`.
