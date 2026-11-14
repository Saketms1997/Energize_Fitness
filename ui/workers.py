from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from database.connection import ConnectionStatus, check_connection


class _Signals(QObject):
    finished = Signal(object)


class _ConnectionCheck(QRunnable):
    def __init__(self) -> None:
        super().__init__()
        self.signals = _Signals()

    def run(self) -> None:
        self.signals.finished.emit(check_connection())


class DbStatusMonitor(QObject):
    """Polls database connectivity on a worker thread so the UI never blocks."""

    status_changed = Signal(object)  # ConnectionStatus

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._busy = False
        self.last: ConnectionStatus | None = None

    def refresh(self) -> None:
        if self._busy:
            return
        self._busy = True
        task = _ConnectionCheck()
        task.setAutoDelete(False)  # keep the signal emitter alive until delivery
        self._task = task
        task.signals.finished.connect(self._done)
        QThreadPool.globalInstance().start(task)

    def _done(self, status: ConnectionStatus) -> None:
        self._busy = False
        self._task = None
        self.last = status
        self.status_changed.emit(status)
