"""The one place that holds a LINE channel access token and talks to LINE.

Kept apart from the queue so that queueing, retrying and logging can be read
and tested without a token anywhere near them: nothing in this module is
written to a log, and the token leaves it only as an Authorization header on
the way out.

LINE Notify — the old one-token webhook — was withdrawn in March 2025, so
the push endpoint of the Messaging API is the way a message reaches a chat.
"""

import aiohttp

from tiewtrade.integrations.notifications.http_push import HttpChatPush
from tiewtrade.integrations.notifications.notice_queue import PushResult

PUSH_URL = "https://api.line.me/v2/bot/message/push"


class LineMessagingApiPush(HttpChatPush):
    """Push one text message to one LINE recipient."""

    def __init__(
        self,
        *,
        channel_access_token: str,
        target_id: str,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        super().__init__(session)
        self._channel_access_token = channel_access_token
        self._target_id = target_id

    async def push(self, message: str) -> PushResult:
        """Send one message, and say what LINE made of it."""
        return await self.post(
            PUSH_URL,
            json={
                "to": self._target_id,
                "messages": [{"type": "text", "text": message}],
            },
            headers={"Authorization": f"Bearer {self._channel_access_token}"},
        )
