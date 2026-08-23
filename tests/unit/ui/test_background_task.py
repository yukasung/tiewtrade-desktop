from pytestqt.qtbot import QtBot

from tiewtrade.ui.background_task import BackgroundTask


def test_a_task_is_not_deleted_out_from_under_the_python_object(
    qtbot: QtBot,
) -> None:
    """Qt must not free a task Python is still holding, or it is freed twice.

    A thread pool deletes a QRunnable the moment its `run` returns, while the
    Python object outlives that: every caller keeps the task to know its own
    work is in flight, and the task, its signals and the handlers connected to
    them refer to each other in a circle that only the cyclic collector
    breaks. Whenever the collector got to it — on any thread, at any
    allocation — it freed a C++ object Qt had already freed, and the process
    died mid-collection.
    """
    task = BackgroundTask(lambda: None)

    assert task.autoDelete() is False


def test_background_task_emits_success_before_finished(qtbot: QtBot) -> None:
    task = BackgroundTask(lambda: "result")
    events: list[object] = []
    task.signals.succeeded.connect(lambda value: events.append(value))
    task.signals.finished.connect(lambda: events.append("finished"))

    task.run()

    assert events == ["result", "finished"]


def test_background_task_emits_failure_before_finished(qtbot: QtBot) -> None:
    error = RuntimeError("failed")

    def fail() -> object:
        raise error

    task = BackgroundTask(fail)
    events: list[object] = []
    task.signals.failed.connect(lambda value: events.append(value))
    task.signals.finished.connect(lambda: events.append("finished"))

    task.run()

    assert events == [error, "finished"]
