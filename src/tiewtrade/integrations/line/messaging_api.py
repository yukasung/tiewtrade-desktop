"""Say what the Bot did, over the LINE Messaging API.

A message is worth sending and never worth waiting for. The Bot decides on a
completed candle, and the candle after it is due whether or not LINE answered
the last one — so notices are queued and pushed by a worker of this module's
own, and the Session that raised one is never made to wait on a socket, never
blocked by a slow reply, and never stopped by a failed one.

Notices arrive from two places: the candle loop, on the Runtime's own thread,
and the thread a trader's ask comes in on. Both are taken here, and only this
module knows the difference — see `notify`.

What a failure costs is the message, deliberately. A push that cannot be made
is logged and dropped; whatever it describes is already in the database, and
the Position tab still shows it. Nothing here may reach back into a trading
decision.
"""

import asyncio
import logging
from collections.abc import Awaitable
from enum import StrEnum
from types import TracebackType
from typing import Protocol, Self

from tiewtrade.application.session_notice import SessionNotice
from tiewtrade.integrations.line.notice_message import notice_message

_logger = logging.getLogger(__name__)

# How many unsent notices to hold before dropping the newest.
#
# A Session raises at most a handful a candle, so a queue this deep is only
# ever reached by LINE being unreachable for far longer than the messages
# would still be worth reading. Holding more would only queue up a burst of
# stale notices to arrive at once when it comes back.
DEFAULT_QUEUE_SIZE = 16
DEFAULT_RETRY_DELAY_SECONDS = 2.0
# How long to go on pushing what is queued once the Runtime is stopping.
SHUTDOWN_DRAIN_SECONDS = 10.0


class LineEventName(StrEnum):
    SENT = "line.notification.sent"
    FAILED = "line.notification.failed"
    DROPPED = "line.notification.dropped"


class LinePushOutcome(StrEnum):
    """What became of one push, as far as trying again is concerned."""

    DELIVERED = "delivered"
    # LINE could not be reached, or could not answer — worth one more try.
    RETRYABLE = "retryable"
    # LINE answered and refused: a bad token, an unknown recipient, a message
    # it will not take. Trying again would only be refused the same way.
    REJECTED = "rejected"


class LinePush(Protocol):
    """Put one text message in front of LINE."""

    def push(self, message: str) -> Awaitable[LinePushOutcome]: ...


class LineNoticeNotifier:
    """Queues notices and pushes them, one at a time, off the candle loop.

    Opened for the life of one Runtime: entering starts the worker, and leaving
    gives what is queued a bounded moment to go out before the loop closes.
    """

    def __init__(
        self,
        push: LinePush,
        *,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        retry_delay_seconds: float = DEFAULT_RETRY_DELAY_SECONDS,
    ) -> None:
        self._push = push
        self._retry_delay_seconds = retry_delay_seconds
        self._queue: asyncio.Queue[SessionNotice] = asyncio.Queue(maxsize=queue_size)
        self._worker: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def __aenter__(self) -> Self:
        self._loop = asyncio.get_running_loop()
        self._worker = asyncio.create_task(self._deliver_queued())
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        worker = self._worker
        self._worker = None
        self._loop = None
        if worker is None:
            return
        # A notice handed over from another thread is queued by a callback
        # the loop has yet to run. Letting the loop turn once first is what
        # keeps a sale made a moment before the Bot stopped from being a
        # message nobody ever gets.
        await asyncio.sleep(0)
        try:
            await asyncio.wait_for(self._queue.join(), SHUTDOWN_DRAIN_SECONDS)
        except TimeoutError:
            _log(
                logging.WARNING,
                LineEventName.DROPPED,
                {"drop_reason": "runtime stopped before the queue was empty"},
            )
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass

    def notify(self, notice: SessionNotice) -> None:
        """Queue one notice. Returns at once, and never raises.

        A candle raises a notice on the Runtime's loop, and a trader's ask
        raises one on the thread they asked from. An asyncio queue answers
        only its own loop, so a notice from anywhere else is handed to that
        loop to enqueue rather than enqueued where it was raised.
        """
        loop = self._loop
        if loop is None:
            _log(
                logging.WARNING,
                LineEventName.DROPPED,
                _fields(notice, drop_reason="nothing is listening for notices"),
            )
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._enqueue(notice)
            return
        try:
            loop.call_soon_threadsafe(self._enqueue, notice)
        except RuntimeError:
            _log(
                logging.WARNING,
                LineEventName.DROPPED,
                _fields(notice, drop_reason="the Runtime loop has closed"),
            )

    def _enqueue(self, notice: SessionNotice) -> None:
        try:
            self._queue.put_nowait(notice)
        except asyncio.QueueFull:
            _log(
                logging.WARNING,
                LineEventName.DROPPED,
                _fields(notice, drop_reason="more notices than LINE is taking"),
            )

    async def _deliver_queued(self) -> None:
        while True:
            notice = await self._queue.get()
            try:
                await self._deliver(notice)
            finally:
                self._queue.task_done()

    async def _deliver(self, notice: SessionNotice) -> None:
        message = notice_message(notice)
        outcome = await self._attempt(message, notice)
        if outcome is LinePushOutcome.RETRYABLE:
            await asyncio.sleep(self._retry_delay_seconds)
            outcome = await self._attempt(message, notice)
        if outcome is LinePushOutcome.DELIVERED:
            _log(logging.INFO, LineEventName.SENT, _fields(notice))
            return
        _log(
            logging.WARNING,
            LineEventName.DROPPED,
            _fields(notice, drop_reason=outcome.value),
        )

    async def _attempt(
        self,
        message: str,
        notice: SessionNotice,
    ) -> LinePushOutcome:
        """One push, with anything the transport throws read as worth retrying.

        A transport that raises is a transport that did not answer, which is
        the same thing a timeout is. Cancellation is not: that is the Runtime
        stopping, and it is left to propagate.
        """
        try:
            return await self._push.push(message)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            _log(
                logging.WARNING,
                LineEventName.FAILED,
                _fields(notice, failure_reason=type(error).__name__),
            )
            return LinePushOutcome.RETRYABLE


def _fields(notice: SessionNotice, **extra: object) -> dict[str, object]:
    """What a log line says about a notice: which kind, in which market.

    Deliberately not what it was worth. A log file is read to find out
    whether messages are going out, and the figures are already in the
    database — putting a position's result in a log only spreads it.
    """
    fields: dict[str, object] = {
        "notice": type(notice).__name__,
        "symbol": notice.market.symbol,
    }
    fields.update(extra)
    return fields


def _log(level: int, event: LineEventName, fields: dict[str, object]) -> None:
    """One line per event, with nothing in it that identifies the account.

    The token is held by the transport and never reaches here, so no field
    written by this module can carry it into a log file.
    """
    detail = " ".join(f"{name}={value}" for name, value in fields.items())
    _logger.log(level, "%s %s", event.value, detail)
