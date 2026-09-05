"""Queue what the Bot did, and push it to a chat service one message at a time.

What differs between LINE and Telegram is the request that carries a message,
and that lives in each channel's own transport. Everything else — holding a
notice, retrying it once, giving up on it, and saying so in the log — reads
the same whichever chat service is on the other end, so it is written once
here and told which channel it is working for.

A message is worth sending and never worth waiting for. The Bot decides on a
completed candle, and the candle after it is due whether or not the chat
service answered the last one — so notices are queued and pushed by a worker
of this module's own, and the Session that raised one is never made to wait on
a socket, never blocked by a slow reply, and never stopped by a failed one.

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
from tiewtrade.integrations.notifications.notice_message import notice_message

_logger = logging.getLogger(__name__)

# How many unsent notices to hold before dropping the newest.
#
# A Session raises at most a handful a candle, so a queue this deep is only
# ever reached by the chat service being unreachable for far longer than the
# messages would still be worth reading. Holding more would only queue up a burst of
# stale notices to arrive at once when it comes back.
DEFAULT_QUEUE_SIZE = 16
DEFAULT_RETRY_DELAY_SECONDS = 2.0
# How long to go on pushing what is queued once the Runtime is stopping.
SHUTDOWN_DRAIN_SECONDS = 10.0


class NoticeEventName(StrEnum):
    """What happened to one notice, said under the channel it was going to."""

    SENT = "notification.sent"
    FAILED = "notification.failed"
    DROPPED = "notification.dropped"


class PushOutcome(StrEnum):
    """What became of one push, as far as trying again is concerned."""

    DELIVERED = "delivered"
    # The chat service could not be reached, or could not answer — worth one
    # more try.
    RETRYABLE = "retryable"
    # The chat service answered and refused: a bad token, an unknown recipient,
    # a message it will not take. Trying again would be refused the same way.
    REJECTED = "rejected"


class NoticePush(Protocol):
    """Put one text message in front of whichever chat service is set up."""

    def push(self, message: str) -> Awaitable[PushOutcome]: ...


class NoticeNotifier:
    """Queues notices and pushes them, one at a time, off the candle loop.

    Opened for the life of one Runtime: entering starts the worker, and leaving
    gives what is queued a bounded moment to go out before the loop closes.
    """

    def __init__(
        self,
        push: NoticePush,
        *,
        channel: str,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        retry_delay_seconds: float = DEFAULT_RETRY_DELAY_SECONDS,
    ) -> None:
        self._push = push
        # Named rather than inferred from the transport: the log is read to
        # find out which chat service stopped answering, and a class name is
        # not that answer once a second channel exists.
        self._channel = channel
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
            self._log(
                logging.WARNING,
                NoticeEventName.DROPPED,
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
            self._log(
                logging.WARNING,
                NoticeEventName.DROPPED,
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
            self._log(
                logging.WARNING,
                NoticeEventName.DROPPED,
                _fields(notice, drop_reason="the Runtime loop has closed"),
            )

    def _enqueue(self, notice: SessionNotice) -> None:
        try:
            self._queue.put_nowait(notice)
        except asyncio.QueueFull:
            self._log(
                logging.WARNING,
                NoticeEventName.DROPPED,
                _fields(
                    notice,
                    drop_reason="more notices than the chat service is taking",
                ),
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
        if outcome is PushOutcome.RETRYABLE:
            await asyncio.sleep(self._retry_delay_seconds)
            outcome = await self._attempt(message, notice)
        if outcome is PushOutcome.DELIVERED:
            self._log(logging.INFO, NoticeEventName.SENT, _fields(notice))
            return
        self._log(
            logging.WARNING,
            NoticeEventName.DROPPED,
            _fields(notice, drop_reason=outcome.value),
        )

    async def _attempt(
        self,
        message: str,
        notice: SessionNotice,
    ) -> PushOutcome:
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
            self._log(
                logging.WARNING,
                NoticeEventName.FAILED,
                _fields(notice, failure_reason=type(error).__name__),
            )
            return PushOutcome.RETRYABLE

    def _log(
        self,
        level: int,
        event: NoticeEventName,
        fields: dict[str, object],
    ) -> None:
        """One line per event, with nothing in it that identifies the account.

        The channel goes in front of the event, so a log holding both LINE and
        Telegram lines can be read for one of them. The token is held by the
        transport and never reaches here, so no field written by this class
        can carry it into a log file.
        """
        detail = " ".join(f"{name}={value}" for name, value in fields.items())
        _logger.log(level, "%s.%s %s", self._channel, event.value, detail)


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
