"""Say that an Entry opened, over the LINE Messaging API.

A message is worth sending and never worth waiting for. The Bot decides on a
completed candle, and the candle after it is due whether or not LINE answered
the last one — so notices are queued and pushed by a worker of this module's
own, and the Session that filled the Entry is never made to wait on a socket,
never blocked by a slow reply, and never stopped by a failed one.

What a failure costs is the message, deliberately. A push that cannot be made
is logged and dropped; the Fill it describes is already in the database, and
the Position tab still shows it. Nothing here may reach back into a trading
decision.
"""

import asyncio
import logging
from collections.abc import Awaitable
from enum import StrEnum
from types import TracebackType
from typing import Protocol, Self

from tiewtrade.application.entry_opened_notice import EntryOpenedNotice
from tiewtrade.integrations.line.entry_message import entry_opened_message

_logger = logging.getLogger(__name__)

# How many unsent notices to hold before dropping the newest.
#
# An Entry fills at most once a candle, so a queue this deep is only ever
# reached by LINE being unreachable for far longer than the messages would
# still be worth reading. Holding more would only queue up a burst of stale
# notices to arrive at once when it comes back.
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


class LineEntryOpenedNotifier:
    """Queues Entry notices and pushes them, one at a time, off the candle loop.

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
        self._queue: asyncio.Queue[EntryOpenedNotice] = asyncio.Queue(
            maxsize=queue_size
        )
        self._worker: asyncio.Task[None] | None = None

    async def __aenter__(self) -> Self:
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
        if worker is None:
            return
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

    def notify(self, notice: EntryOpenedNotice) -> None:
        """Queue one notice. Returns at once, and never raises."""
        try:
            self._queue.put_nowait(notice)
        except asyncio.QueueFull:
            _log(
                logging.WARNING,
                LineEventName.DROPPED,
                {
                    "entry_number": notice.entry_number,
                    "drop_reason": "more notices than LINE is taking",
                },
            )

    async def _deliver_queued(self) -> None:
        while True:
            notice = await self._queue.get()
            try:
                await self._deliver(notice)
            finally:
                self._queue.task_done()

    async def _deliver(self, notice: EntryOpenedNotice) -> None:
        message = entry_opened_message(notice)
        outcome = await self._attempt(message, notice)
        if outcome is LinePushOutcome.RETRYABLE:
            await asyncio.sleep(self._retry_delay_seconds)
            outcome = await self._attempt(message, notice)
        if outcome is LinePushOutcome.DELIVERED:
            _log(
                logging.INFO,
                LineEventName.SENT,
                {"entry_number": notice.entry_number, "symbol": notice.symbol},
            )
            return
        _log(
            logging.WARNING,
            LineEventName.DROPPED,
            {"entry_number": notice.entry_number, "drop_reason": outcome.value},
        )

    async def _attempt(
        self,
        message: str,
        notice: EntryOpenedNotice,
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
                {
                    "entry_number": notice.entry_number,
                    "failure_reason": type(error).__name__,
                },
            )
            return LinePushOutcome.RETRYABLE


def _log(level: int, event: LineEventName, fields: dict[str, object]) -> None:
    """One line per event, with nothing in it that identifies the account.

    The token is held by the transport and never reaches here, so no field
    written by this module can carry it into a log file.
    """
    detail = " ".join(f"{name}={value}" for name, value in fields.items())
    _logger.log(level, "%s %s", event.value, detail)
