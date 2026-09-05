"""The one place that holds a Telegram bot token and talks to Telegram.

Kept apart from the queue for the same reason the LINE transport is: nothing
in this module is written to a log, and the token leaves it only inside the
URL it is sent to.

Telegram puts the token in the path rather than in a header, which is the one
thing that makes it unlike every other credential in this application — a URL
is the part of a request that gets logged by default. So the address is built
where it is sent and nothing else here writes it down: what leaves this module
is an outcome.

A chat id is not a secret and a bot token is, but they are kept together for
the same reason the LINE settings are: splitting them would only make two
places to go wrong.
"""

import asyncio
from types import TracebackType
from typing import Self

import aiohttp

from tiewtrade.integrations.notifications.notice_queue import PushOutcome

API_BASE_URL = "https://api.telegram.org"
"""Where a bot reaches Telegram, with the token spliced into the path."""

# Long enough for a phone network having a bad minute, short enough that a
# Telegram that never answers cannot hold a notice past the candle after it.
REQUEST_TIMEOUT_SECONDS = 10.0


def push_url(bot_token: str) -> str:
    """The sendMessage address for one bot. Holds the token — never logged."""
    return f"{API_BASE_URL}/bot{bot_token}/sendMessage"


class TelegramBotApiPush:
    """Push one text message to one Telegram chat."""

    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
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

    async def push(self, message: str) -> PushOutcome:
        """Send one message, and say only whether it is worth sending again.

        The text goes as it is written, with no parse mode asked for: a notice
        carries symbols a Markdown reader would take for formatting, and a
        message Telegram refuses to parse is a message nobody reads.
        """
        session = self._network_session()
        try:
            async with session.post(
                push_url(self._bot_token),
                json={"chat_id": self._chat_id, "text": message},
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
            ) as response:
                return _outcome_for_status(response.status)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return PushOutcome.RETRYABLE
        except aiohttp.ClientError:
            return PushOutcome.RETRYABLE

    def _network_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session


def _outcome_for_status(status: int) -> PushOutcome:
    """What a status code means for trying the same message once more.

    A 429 is Telegram asking the bot to slow down, which one more try a couple
    of seconds later often satisfies. A 401 is a token that was revoked or
    never right, a 403 a chat the bot was removed from, and a 400 a chat id
    Telegram does not know — all settings to be corrected rather than weather
    to wait out.
    """
    if 200 <= status < 300:
        return PushOutcome.DELIVERED
    if status == 429 or 500 <= status < 600:
        return PushOutcome.RETRYABLE
    return PushOutcome.REJECTED
