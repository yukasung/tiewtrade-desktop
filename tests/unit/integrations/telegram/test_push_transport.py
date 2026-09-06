from __future__ import annotations

import asyncio

from tests.support.chat_push import FakeSession
from tiewtrade.integrations.notifications.notice_queue import PushOutcome, PushResult
from tiewtrade.integrations.telegram.push_transport import (
    TelegramBotApiPush,
    push_url,
)

# What a status comes back as, and what a service that never answered does,
# is one rule for every channel and is pinned where that rule lives — see
# tests/unit/integrations/notifications/test_http_push.py. What is Telegram's
# own is the address, the payload shape, and where the token rides.


def _push(session: FakeSession) -> PushResult:
    transport = TelegramBotApiPush(
        bot_token="123:token-value",
        chat_id="-1001234",
        session=session,  # type: ignore[arg-type]
    )
    return asyncio.run(transport.push("Entry #1 opened"))


def test_a_push_carries_the_message_the_token_and_the_chat() -> None:
    session = FakeSession()

    result = _push(session)

    assert result.outcome is PushOutcome.DELIVERED
    assert result.status == 200

    call = session.calls[0]
    assert call["url"] == push_url("123:token-value")
    assert call["json"] == {"chat_id": "-1001234", "text": "Entry #1 opened"}


def test_the_message_goes_as_written_with_no_formatting_asked_for() -> None:
    """A notice carries characters a Markdown reader would take for markup.

    Telegram refuses a message whose parse mode it cannot honour, and a
    refused message is one nobody reads — so nothing asks it to parse.
    """
    session = FakeSession()

    _push(session)

    assert "parse_mode" not in session.calls[0]["json"]


def test_the_token_travels_in_the_path_and_nowhere_else() -> None:
    """Telegram names the bot in the URL, so no header should carry it too.

    The address is the one place a token can leak into an access log, and it
    is exactly one place — this pins that nothing else copies it.
    """
    session = FakeSession()

    _push(session)

    call = session.calls[0]
    assert "123:token-value" in call["url"]
    assert "123:token-value" not in str(call.get("headers", {}))
    assert "123:token-value" not in str(call["json"])
