"""The socket a chat service is reached over, and what its answer means.

Two chat services, one shape of request: open a session lazily, POST once,
read the status, and never wait longer than a candle. What differs is the
address, the payload and whether the token rides in a header — and that is
all each channel's own transport is left holding.

The status mapping is here rather than in either channel because the queue's
whole retry policy hangs off it: a service that could not answer is worth one
more try, and a service that answered "no" never is. Written twice, the two
could drift, and the drift would show up as a message quietly not sent.

Nothing in this module is written to a log. The token never reaches it as a
value of its own — it arrives already built into a URL or a header the
channel handed over — and it leaves only on the way out.
"""

import asyncio
from types import TracebackType
from typing import Self

import aiohttp

from tiewtrade.integrations.notifications.notice_queue import PushOutcome, PushResult

# Long enough for a phone network having a bad minute, short enough that a
# chat service that never answers cannot hold a notice past the candle after
# it.
REQUEST_TIMEOUT_SECONDS = 10.0


class HttpChatPush:
    """One chat service's socket, opened when it is first needed.

    Held open for the life of a Runtime by the composition that built it, and
    shared by every notice that goes out over it. A session passed in belongs
    to the caller and is left alone; one made here is closed here.
    """

    def __init__(self, session: aiohttp.ClientSession | None = None) -> None:
        self._session = session
        self._owns_session = session is None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def post(
        self,
        url: str,
        *,
        json: dict[str, object],
        headers: dict[str, str] | None = None,
    ) -> PushResult:
        """One request, answered with what it came to and what was said back.

        A service that never answered has no status to report: a timeout and
        a closed socket are both worth one more try, and inventing a number
        for them would read in the log as an answer nobody gave.
        """
        session = self._network_session()
        try:
            async with session.post(
                url,
                json=json,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
            ) as response:
                return PushResult(outcome_for_status(response.status), response.status)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return PushResult(PushOutcome.RETRYABLE)
        except aiohttp.ClientError:
            return PushResult(PushOutcome.RETRYABLE)

    def _network_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session


def outcome_for_status(status: int) -> PushOutcome:
    """Whether the same message is worth sending again, by what came back.

    A 429 is the service asking the account to slow down, which one more try
    a couple of seconds later often satisfies — the monthly quota and a
    momentary burst read the same from here, and one extra attempt is cheap
    enough to be worth making either way. A 5xx is the service having a bad
    minute.

    Everything else in the 4xx range is a setting for the trader to correct
    rather than weather to wait out: a token that was never right or has been
    withdrawn, a recipient the service does not know, a chat the bot was
    removed from. The status goes back with the refusal so the log can say
    which of those it was.
    """
    if 200 <= status < 300:
        return PushOutcome.DELIVERED
    if status == 429 or 500 <= status < 600:
        return PushOutcome.RETRYABLE
    return PushOutcome.REJECTED
