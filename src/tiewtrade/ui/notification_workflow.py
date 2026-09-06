"""Reads one Session's notices off the Qt thread, and keeps the tab honest.

The tab is not open by default — a trader who never looks does not pay for
a background read — so nothing is asked for until it is activated. From
there it follows the rule every background read in this application follows:
a reply is shown only while it is still the answer to the question on
screen. Switching Sessions bumps a generation, and a reply tagged with the
one before it is dropped rather than painted over what the trader has
already moved on to.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING
from uuid import UUID

from PySide6.QtCore import QObject, QThreadPool, Signal, Slot

from tiewtrade.application.session_notices import (
    ListSessionNotices,
    SessionNoticeHistoryItem,
)
from tiewtrade.ui.background_task import BackgroundTask

NO_NOTIFICATIONS = "No notifications yet"
NOTIFICATIONS_UNAVAILABLE = "Notifications unavailable"

if TYPE_CHECKING:
    from tiewtrade.ui.notification_page import NotificationPage


class NotificationWorkflow(QObject):
    """Reads what one Session said, and only while its tab is open."""

    loading = Signal(bool)
    ready = Signal(object)
    empty = Signal()
    unavailable = Signal(str)

    def __init__(
        self,
        *,
        list_session_notices: ListSessionNotices | None = None,
        thread_pool: QThreadPool | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._list_session_notices = list_session_notices
        self._thread_pool = (
            thread_pool if thread_pool is not None else QThreadPool.globalInstance()
        )
        self._session_id: UUID | None = None
        self._active = False
        self._closed = False
        self._generation = 0
        self._tasks: dict[int, BackgroundTask] = {}

    def attach(self, page: "NotificationPage") -> None:
        page.retry_requested.connect(self.retry)
        self.loading.connect(self._show_loading_state(page))
        self.ready.connect(page.show_items)
        self.empty.connect(lambda: page.show_empty(NO_NOTIFICATIONS))
        self.unavailable.connect(page.show_unavailable)

    @staticmethod
    def _show_loading_state(page: "NotificationPage") -> "Callable[[bool], None]":
        """Say a read is underway, and say nothing when one merely finished.

        The signal fires both ways — starting and settled — and only the
        first has anything of its own to say: settling is already announced
        by whichever of ready, empty or unavailable follows it, and a loading
        message shown again on top of that would wipe out what just arrived.
        """

        def show_it(is_loading: bool) -> None:
            if is_loading:
                page.show_loading("Loading notifications…")

        return show_it

    @Slot()
    def activate(self) -> None:
        """Read what the tab is opened onto, including reopening the same one.

        Reopening what is already showing is how a trader asks for what has
        landed since they last looked, so it reads again rather than trusting
        what is already on screen.
        """
        self._active = True
        self._request()

    @Slot()
    def deactivate(self) -> None:
        self._active = False

    def show_session(self, session_id: UUID | None) -> None:
        """Point future reads at this Session, without reading yet.

        A read only happens while the tab is open, so this alone does nothing
        for a trader looking elsewhere — it only decides what the next read
        will ask for.
        """
        if session_id == self._session_id:
            return
        self._session_id = session_id
        self._generation += 1
        if self._active:
            self._request()

    @Slot()
    def retry(self) -> None:
        self._request()

    @Slot()
    def close(self) -> None:
        self._closed = True
        self._generation += 1
        self._tasks.clear()

    def _request(self) -> None:
        if self._closed:
            return
        list_session_notices = self._list_session_notices
        session_id = self._session_id
        if list_session_notices is None or session_id is None:
            self.empty.emit()
            return
        self._generation += 1
        generation = self._generation
        task = BackgroundTask(lambda: list_session_notices(session_id))
        task.signals.succeeded.connect(
            lambda result: self._succeeded(generation, result)
        )
        task.signals.failed.connect(lambda error: self._failed(generation, error))
        task.signals.finished.connect(lambda: self._finished(generation))
        self._tasks[generation] = task
        self.loading.emit(True)
        self._thread_pool.start(task)

    def _is_current(self, generation: int) -> bool:
        return not self._closed and generation == self._generation

    def _succeeded(self, generation: int, result: object) -> None:
        if not self._is_current(generation):
            return
        if isinstance(result, tuple) and all(
            isinstance(item, SessionNoticeHistoryItem) for item in result
        ):
            if not result:
                self.empty.emit()
                return
            self.ready.emit(result)
            return
        self.unavailable.emit(NOTIFICATIONS_UNAVAILABLE)

    def _failed(self, generation: int, error: object) -> None:
        del error
        if not self._is_current(generation):
            return
        self.unavailable.emit(NOTIFICATIONS_UNAVAILABLE)

    def _finished(self, generation: int) -> None:
        self._tasks.pop(generation, None)
        if self._is_current(generation):
            self.loading.emit(False)
