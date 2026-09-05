from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
import pytest

from tiewtrade.integrations.notifications.notice_queue import PushOutcome
from tiewtrade.integrations.telegram.push_transport import (
    TelegramBotApiPush,
    push_url,
)


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class FakeSession:
    def __init__(self, status: int = 200, error: Exception | None = None) -> None:
        self._status = status
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if self._error is not None:
            raise self._error
        return FakeResponse(self._status)

    async def close(self) -> None:
        return None


def _push(session: FakeSession) -> PushOutcome:
    transport = TelegramBotApiPush(
        bot_token="123:token-value",
        chat_id="-1001234",
        session=session,  # type: ignore[arg-type]
    )
    return asyncio.run(transport.push("Entry #1 opened"))


def test_a_push_carries_the_message_the_token_and_the_chat() -> None:
    session = FakeSession()

    assert _push(session) is PushOutcome.DELIVERED

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


@pytest.mark.parametrize("status", [429, 500, 503])
def test_a_service_that_cannot_answer_now_is_worth_asking_again(
    status: int,
) -> None:
    assert _push(FakeSession(status=status)) is PushOutcome.RETRYABLE


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_a_refusal_telegram_will_repeat_is_not_retried(status: int) -> None:
    """A revoked token, a chat the bot left, a chat id nobody knows.

    All three are settings for the trader to correct, and asking again in two
    seconds would only be refused the same way.
    """
    assert _push(FakeSession(status=status)) is PushOutcome.REJECTED


def test_a_network_that_never_answers_is_worth_asking_again() -> None:
    session = FakeSession(error=aiohttp.ClientError())

    assert _push(session) is PushOutcome.RETRYABLE


def test_a_timeout_is_worth_asking_again() -> None:
    session = FakeSession(error=TimeoutError())

    assert _push(session) is PushOutcome.RETRYABLE


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
