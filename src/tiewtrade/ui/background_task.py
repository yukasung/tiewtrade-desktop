from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class BackgroundTaskSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(object)
    finished = Signal()


class BackgroundTask(QRunnable):
    """One piece of work run off the UI thread, owned by Python alone.

    A thread pool deletes a QRunnable as soon as its `run` returns. The Python
    object does not go with it: every caller holds the task while its work is
    in flight, and the task, its signals and the handlers connected to them
    refer to each other in a circle, so what actually frees it is the cyclic
    collector — later, on whichever thread happened to allocate. It then freed
    a C++ object the pool had already freed, and the process died inside the
    collection rather than at anything the traceback pointed to.

    So the pool is told not to delete it. Python owns the task for as long as
    it holds it, and frees it once.
    """

    def __init__(self, operation: Callable[[], object]) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self._operation = operation
        self.signals = BackgroundTaskSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self._operation()
        except Exception as error:
            self.signals.failed.emit(error)
        else:
            self.signals.succeeded.emit(result)
        finally:
            self.signals.finished.emit()
