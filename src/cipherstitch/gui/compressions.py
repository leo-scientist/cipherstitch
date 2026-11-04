"""Precover compressions on a worker thread, one at a time: decoding and compressing a
large RAW takes seconds and hundreds of MB, so the latest request waits for the running
one, and a request a later one made obsolete never runs, or has its outcome dropped."""

import functools
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from cipherstitch.gui.worker import BackgroundTask, Job
from cipherstitch.params.settings import Settings

# A file's path, modification time, size, inode and change time: the service's cache key.
Version = tuple[Path, int, int, int, int]
Compression = tuple[Version, Settings]  # a carrier version, compressed at these settings
OnFailed = Callable[[Exception], None]
_Request = tuple[Compression, Job, OnFailed]


def version(path: Path) -> Version | None:
    try:
        stat = path.stat()
    except OSError:
        return None  # describing it reports why

    return path, stat.st_mtime_ns, stat.st_size, stat.st_ino, stat.st_ctime_ns


class CompressionQueue(QObject):
    """Emits ``done(compression)`` when a compression ends and is still the one wanted, and
    ``idle()`` once nothing runs any more."""

    done = Signal(object)
    idle = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._running: tuple[Compression, BackgroundTask] | None = None
        self._queued: _Request | None = None

    @property
    def is_running(self) -> bool:
        return self._running is not None

    def request(self, compression: Compression, job: Job, on_failed: OnFailed) -> None:
        """Run ``job`` for ``compression``, after the running one if there is one, unless it
        is requested already. It takes the place of a request still waiting."""
        if self._is_requested(compression):
            return

        request = (compression, job, on_failed)

        if self._running is None:
            self._start(request)
        else:
            self._queued = request

    def cancel(self) -> None:
        """Drop the waiting request and the running one's outcome. Its job runs on:
        compressing has no step to stop at."""
        self._queued = None

        if self._running is not None:
            self._running[1].cancel()

    def _is_requested(self, compression: Compression) -> bool:
        requests = (self._running, self._queued)

        return any(request is not None and request[0] == compression for request in requests)

    def _start(self, request: _Request) -> None:
        compression, job, on_failed = request
        task = BackgroundTask(job)
        task.signals.failed.connect(on_failed)
        task.signals.finished.connect(functools.partial(self._on_finished, compression))
        self._running = (compression, task)
        task.start()

    def _on_finished(self, compression: Compression) -> None:
        self._running = None
        queued, self._queued = self._queued, None

        if queued is not None and queued[0] != compression:
            self._start(queued)  # the outcome that just arrived is not wanted any more

            return

        self.done.emit(compression)  # may request the next one: a version written meanwhile

        if self._running is None:
            self.idle.emit()
