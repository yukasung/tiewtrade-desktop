from tiewtrade.ui.background_task import BackgroundTask
from tiewtrade.ui.guarded_action import GuardedAction


def test_the_control_goes_off_while_the_work_runs_and_comes_back_after() -> None:
    action, record = make_action()

    action.run(lambda: "done", succeeded=record.succeeded.append)

    assert record.available == [False, True]
    assert record.succeeded == ["done"]
    assert record.failures == 0
    assert action.is_running is False


def test_the_control_comes_back_when_the_work_fails() -> None:
    """A control left off by a failure is one the trader cannot try again with."""
    action, record = make_action()

    def raise_error() -> None:
        raise RuntimeError("could not reach the database")

    action.run(raise_error, succeeded=record.succeeded.append)

    assert record.available == [False, True]
    assert record.succeeded == []
    assert record.failures == 1
    assert action.is_running is False


def test_a_second_ask_is_ignored_while_the_first_is_still_running() -> None:
    """One press starts one run, however many arrive before it answers."""
    record = _Record()
    held: list[BackgroundTask] = []
    action = GuardedAction(
        available=record.available.append,
        report_failure=record.report_failure,
        failure_log="Could not do the thing",
        # Held rather than run, which is a task still in flight.
        start=held.append,
    )

    action.run(lambda: "first", succeeded=record.succeeded.append)
    assert action.is_running is True
    action.run(lambda: "second", succeeded=record.succeeded.append)

    assert len(held) == 1
    assert record.available == [False]

    held[0].run()

    assert record.succeeded == ["first"]
    assert action.is_running is False


class _Record:
    def __init__(self) -> None:
        self.available: list[bool] = []
        self.succeeded: list[object] = []
        self.failures = 0

    def report_failure(self) -> None:
        self.failures += 1


def make_action() -> tuple[GuardedAction, _Record]:
    """An action whose work runs where it is asked, so a test can read it back."""
    record = _Record()
    action = GuardedAction(
        available=record.available.append,
        report_failure=record.report_failure,
        failure_log="Could not do the thing",
        start=lambda task: task.run(),
    )
    return action, record
