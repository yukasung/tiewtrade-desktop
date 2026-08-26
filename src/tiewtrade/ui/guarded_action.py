"""One thing the trader asks for that runs off the UI thread, guarded.

Every such action is asked the same way and answered the same way: the control
that asks is switched off while the work is in flight, the failure is logged
and told to the trader in the words that control speaks in, and the control
comes back whichever way the work went. What differs between them is only the
work itself and where the answer is shown, so those are what an action is
given.

The task handle is held here rather than by the window, because holding it is
what keeps a second press from starting a second run — and a window that kept
one field per action for that was keeping four names for one idea.
"""

import logging
from collections.abc import Callable

from tiewtrade.ui.background_task import BackgroundTask

_LOGGER = logging.getLogger("tiewtrade.ui.guarded_action")


class GuardedAction:
    """A background action the trader can ask for once at a time.

    `available` switches the asking control on and off — True while it may be
    pressed, which is how the controls themselves put it. `report_failure`
    says what went wrong where the trader is looking: a message on the control
    itself, or a dialog where the control has nowhere to put one.
    `failure_log` is the line the log carries, which names the action rather
    than the trader's situation.
    """

    def __init__(
        self,
        *,
        available: Callable[[bool], None],
        # Whatever it hands back is not read — a dialog answers with the
        # button that closed it, and there is nothing here to do with that.
        report_failure: Callable[[], object],
        failure_log: str,
        start: Callable[[BackgroundTask], None],
    ) -> None:
        self._available = available
        self._report_failure = report_failure
        self._failure_log = failure_log
        self._start = start
        self._task: BackgroundTask | None = None

    @property
    def is_running(self) -> bool:
        """Whether this action's work is still in flight.

        Asked before the trader is put to the question, so a press arriving
        while the last one is still running never opens a second dialog.
        """
        return self._task is not None

    def run(
        self,
        work: Callable[[], object],
        *,
        succeeded: Callable[[object], None],
    ) -> None:
        """Switch the control off, run the work, and switch it back on after."""
        if self._task is not None:
            return
        self._available(False)
        task = BackgroundTask(work)
        task.signals.succeeded.connect(succeeded)
        task.signals.failed.connect(self._failed)
        task.signals.finished.connect(self._finished)
        self._task = task
        self._start(task)

    def _failed(self, error: object) -> None:
        if isinstance(error, BaseException):
            _LOGGER.exception(self._failure_log, exc_info=error)
        self._report_failure()

    def _finished(self) -> None:
        self._task = None
        self._available(True)
