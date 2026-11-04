"""Qt desktop interface. Requires the ``gui`` extra (PySide6)."""


def main() -> int:
    try:
        from cipherstitch.gui.app import run
    except ImportError as exc:
        raise SystemExit("The app needs the gui extra, see Installation in README.md") from exc

    return run()
