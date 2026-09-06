from __future__ import annotations

import asyncio

from tests.support.chat_push import FakeSession
from tiewtrade.integrations.line.push_transport import PUSH_URL, LineMessagingApiPush
from tiewtrade.integrations.notifications.notice_queue import PushOutcome, PushResult

# What a status comes back as, and what a service that never answered does,
# is one rule for every channel and is pinned where that rule lives — see
# tests/unit/integrations/notifications/test_http_push.py. What is LINE's own
# is the address, the payload shape, and where the token rides.


def _push(session: FakeSession) -> PushResult:
    transport = LineMessagingApiPush(
        channel_access_token="token-value",
        target_id="U123",
        session=session,  # type: ignore[arg-type]
    )
    return asyncio.run(transport.push("Entry #1 opened"))


def test_a_push_carries_the_message_the_token_and_the_recipient() -> None:
    session = FakeSession()

    assert _push(session).outcome is PushOutcome.DELIVERED

    call = session.calls[0]
    assert call["url"] == PUSH_URL
    assert call["headers"]["Authorization"] == "Bearer token-value"
    assert call["json"] == {
        "to": "U123",
        "messages": [{"type": "text", "text": "Entry #1 opened"}],
    }


def test_the_token_travels_in_the_header_and_nowhere_else() -> None:
    """LINE names the bot in a header, so nothing should copy it elsewhere.

    A URL is the part of a request that gets logged by default; this pins
    that the credential is not in the one part that leaks by accident.
    """
    session = FakeSession()

    _push(session)

    call = session.calls[0]
    assert "token-value" not in call["url"]
    assert "token-value" not in str(call["json"])
