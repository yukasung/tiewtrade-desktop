"""The one place that holds a LINE channel access token and talks to LINE.

Kept apart from the queue so that queueing, retrying and logging can be
read and tested without a token anywhere near them: nothing in this module
is written to a log, and the token leaves it only as an Authorization header
on the way out.

LINE Notify — the old one-token webhook — was withdrawn in March 2025, so
the push endpoint of the Messaging API is the way a message reaches a chat.
"""

import asyncio
import logging
from types import TracebackType
from typing import Self

import aiohttp

from tiewtrade.integrations.notifications.notice_queue import (
    PushOutcome,
    PushResult,
)

_logger = logging.getLogger(__name__)

PUSH_URL = "https://api.line.me/v2/bot/message/push"
# Long enough for a phone network having a bad minute, short enough that a
# LINE that never answers cannot hold a notice past the candle after it.
REQUEST_TIMEOUT_SECONDS = 10.0


class LineMessagingApiPush:
    """Push one text message to one LINE recipient."""

    def __init__(
        self,
        *,
        channel_access_token: str,
        target_id: str,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._channel_access_token = channel_access_token
        self._target_id = target_id
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

    async def push(self, message: str) -> PushResult:
        """Send one message, and say only whether it is worth sending again.

        A 429 is LINE saying the account has sent too much this month, which
        one more try in a moment will not change — but the monthly quota and
        a momentary burst read the same from here, and one extra attempt is
        cheap enough to be worth making.
        """
        session = self._network_session()
        try:
            async with session.post(
                PUSH_URL,
                json={
                    "to": self._target_id,
                    "messages": [{"type": "text", "text": message}],
                },
                headers={
                    "Authorization": f"Bearer {self._channel_access_token}",
                },
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
            ) as response:
                return PushResult(_outcome_for_status(response.status), response.status)
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


def _outcome_for_status(status: int) -> PushOutcome:
    if 200 <= status < 300:
        return PushOutcome.DELIVERED
    if status == 429 or 500 <= status < 600:
        return PushOutcome.RETRYABLE
    # A 401 is a token that is wrong or withdrawn, a 400 a recipient LINE
    # does not know. Both are settings to be corrected, not weather to wait
    # out, and the notifier logs the refusal without the token in it.
    return PushOutcome.REJECTED
