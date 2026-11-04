"""Run a service call on a worker thread with step progress and cooperative cancellation."""

import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, SignalInstance

from cipherstitch.errors import OperationCancelledError
from cipherstitch.service import ProgressCallback

Job = Callable[[ProgressCallback], object]


class _TaskSignals(QObject):
    progressed = Signal(int, int, str)
    succeeded = Signal(object)
    failed = Signal(object)
    finished = Signal()


class BackgroundTask(QRunnable):
    """Runs ``job(report)``; ``report`` emits progress and aborts the job once cancelled.

    A cancelled job stops at its next report, and its result or error is dropped; every
    task emits ``finished`` last. Once the app has quit, PySide deletes the signals' object
    while the job may still run: the job then stops at its next report, and nothing is
    emitted.
    """

    def __init__(self, job: Job) -> None:
        super().__init__()
        self.setAutoDelete(False)  # the page keeps the task (and its signals) alive
        self._job = job
        self._cancel = threading.Event()
        self.signals = _TaskSignals()

    def cancel(self) -> None:
        self._cancel.set()

    def start(self) -> None:
        QThreadPool.globalInstance().start(self)

    def run(self) -> None:
        try:
            result = self._job(self._report)
        except OperationCancelledError:
            pass
        except Exception as exc:  # report every failure to the UI instead of losing it
            self._fail(exc)
        except BaseException as exc:
            # Nothing may leave a QRunnable: a SystemExit from an extension's code would end
            # the process from this thread, and anything else would never reach the UI.
            self._fail(_unexpected(exc))
        else:
            if not self._cancel.is_set():
                self._emit(self.signals.succeeded, result)
        finally:
            self._emit(self.signals.finished)

    def _fail(self, error: Exception) -> None:
        if not self._cancel.is_set():
            self._emit(self.signals.failed, error)

    def _report(self, index: int, count: int, text: str) -> None:
        if self._cancel.is_set() or not self._emit(self.signals.progressed, index, count, text):
            raise OperationCancelledError("Cancelled")

    def _emit(self, signal: SignalInstance, *args: object) -> bool:
        """Whether anyone could still hear it: ``False`` once the signals' object is gone."""
        try:
            signal.emit(*args)
        except RuntimeError:  # "Signal source has been deleted"
            return False

        return True


def _unexpected(error: BaseException) -> RuntimeError:
    name = type(error).__name__
    text = str(error)

    return RuntimeError(f"{name}: {text}" if text else name)
