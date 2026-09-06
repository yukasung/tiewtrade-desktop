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

import aiohttp

from tiewtrade.integrations.notifications.http_push import HttpChatPush
from tiewtrade.integrations.notifications.notice_queue import PushResult

API_BASE_URL = "https://api.telegram.org"
"""Where a bot reaches Telegram, with the token spliced into the path."""


def push_url(bot_token: str) -> str:
    """The sendMessage address for one bot. Holds the token — never logged."""
    return f"{API_BASE_URL}/bot{bot_token}/sendMessage"


class TelegramBotApiPush(HttpChatPush):
    """Push one text message to one Telegram chat."""

    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        super().__init__(session)
        self._bot_token = bot_token
        self._chat_id = chat_id

    async def push(self, message: str) -> PushResult:
        """Send one message, and say what Telegram made of it.

        The text goes as it is written, with no parse mode asked for: a notice
        carries symbols a Markdown reader would take for formatting, and a
        message Telegram refuses to parse is a message nobody reads.
        """
        return await self.post(
            push_url(self._bot_token),
            json={"chat_id": self._chat_id, "text": message},
        )
