import os
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from cipherstitch import catalog, extension_loader, kdf, service
from cipherstitch.catalog import CipherKey, MethodKey
from cipherstitch.errors import NoHiddenDataError

# No windows on the screen. On macOS's own platform a window's cursor does not follow QTest's
# mouse moves, which the GUI tests of the cursors rely on.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ImageFactory = Callable[..., Path]
EXTENSION_FIXTURES = "tests.extension_fixtures"


@pytest.fixture(autouse=True)
def fast_argon2(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production Argon2id costs ~0.2 s and 256 MiB per call; tests only need correctness."""
    monkeypatch.setattr(kdf, "ARGON2_MEMORY_KIB", 8 * kdf.ARGON2_LANES)
    monkeypatch.setattr(kdf, "ARGON2_ITERATIONS", 1)


@pytest.fixture(autouse=True)
def no_extensions(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Every test starts with no extension loaded, from a folder that has none: what a
    developer keeps in src/cipherstitch/extensions/ never reaches the tests."""
    monkeypatch.setattr(extension_loader, "PACKAGE", f"{EXTENSION_FIXTURES}.empty")
    extension_loader.reset()
    yield
    extension_loader.reset()


def use_extensions(monkeypatch: pytest.MonkeyPatch, scenario: str) -> extension_loader.Loaded:
    """Point the loader at ``tests/extension_fixtures/<scenario>/`` and load it."""
    monkeypatch.setattr(extension_loader, "PACKAGE", f"{EXTENSION_FIXTURES}.{scenario}")
    extension_loader.reset()

    return extension_loader.load()


def skip_unless_installed(cipher_id: CipherKey) -> None:
    """Skip the test when ``cipher_id`` needs an optional extra that is not installed."""
    requirement = catalog.cipher(cipher_id).requires

    if requirement is not None:
        pytest.importorskip(requirement.module)


def skip_unless_method_installed(method_id: MethodKey) -> None:
    """Skip the test when ``method_id`` needs an optional extra that is not installed."""
    requirement = catalog.method(method_id).requires

    if requirement is not None:
        pytest.importorskip(requirement.module)


@pytest.fixture
def make_image(tmp_path: Path) -> ImageFactory:
    def factory(
        name: str = "cover.png", size: tuple[int, int] = (96, 72), mode: str = "RGB"
    ) -> Path:
        rng = np.random.default_rng(seed=0)
        channels = len(Image.new(mode, (1, 1)).getbands())
        pixels = rng.integers(0, 256, size=(size[1], size[0], channels), dtype=np.uint8)
        image = Image.fromarray(pixels.squeeze(axis=2) if channels == 1 else pixels)
        path = tmp_path / name
        image.save(path)

        return path

    return factory


@pytest.fixture
def secret_file(tmp_path: Path) -> Path:
    path = tmp_path / "secret.txt"
    path.write_bytes("Таємне повідомлення 🔒\n".encode() * 20)

    return path


def plain_method(carrier_path: Path) -> MethodKey | None:
    """The method of an unencrypted payload, as a reveal without a password finds it, or
    ``None``: encrypted payloads stay invisible."""
    with tempfile.TemporaryDirectory() as out_dir:
        try:
            return service.reveal(carrier_path, Path(out_dir)).method
        except NoHiddenDataError:
            return None
