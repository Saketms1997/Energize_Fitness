"""Run slow work (backups, sync, exports, HTTP) off the UI thread."""
from __future__ import annotations

import logging
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

log = logging.getLogger("gym.ui")
_alive: set["_Job"] = set()  # keeps jobs (and their signal emitters) from being garbage collected


class _Signals(QObject):
    result = Signal(object)
    error = Signal(object)
    progress = Signal(int, int, str)


class _Job(QRunnable):
    def __init__(self, fn: Callable[..., Any], with_progress: bool):
        super().__init__()
        self.fn, self.with_progress = fn, with_progress
        self.signals = _Signals()
        self.setAutoDelete(False)

    def run(self) -> None:
        try:
            if self.with_progress:
                out = self.fn(lambda done, total, msg: self.signals.progress.emit(done, total, msg))
            else:
                out = self.fn()
        except BaseException as exc:  # noqa: BLE001 - delivered to the UI thread
            self.signals.error.emit(exc)
        else:
            self.signals.result.emit(out)


def run_async(fn: Callable[..., Any], on_result: Callable[[Any], None] | None = None,
              on_error: Callable[[BaseException], None] | None = None,
              on_progress: Callable[[int, int, str], None] | None = None) -> _Job:
    """Call ``fn`` on a worker thread. Callbacks run on the UI thread. ``fn`` receives a progress
    emitter only if ``on_progress`` is given."""
    job = _Job(fn, on_progress is not None)
    _alive.add(job)

    def finish() -> None:
        _alive.discard(job)

    if on_result:
        job.signals.result.connect(on_result)
    if on_error:
        job.signals.error.connect(on_error)
    else:
        job.signals.error.connect(lambda exc: log.error("Background task failed", exc_info=exc))
    if on_progress:
        job.signals.progress.connect(on_progress)
    job.signals.result.connect(lambda _: finish())
    job.signals.error.connect(lambda _: finish())
    QThreadPool.globalInstance().start(job)
    return job
