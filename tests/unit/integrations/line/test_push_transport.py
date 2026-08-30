from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
import pytest

from tiewtrade.integrations.line.messaging_api import LinePushOutcome
from tiewtrade.integrations.line.push_transport import PUSH_URL, LineMessagingApiPush


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


def _push(session: FakeSession) -> LinePushOutcome:
    transport = LineMessagingApiPush(
        channel_access_token="token-value",
        target_id="U123",
        session=session,  # type: ignore[arg-type]
    )
    return asyncio.run(transport.push("Entry #1 opened"))


def test_a_push_carries_the_message_the_token_and_the_recipient() -> None:
    session = FakeSession()

    assert _push(session) is LinePushOutcome.DELIVERED

    call = session.calls[0]
    assert call["url"] == PUSH_URL
    assert call["headers"]["Authorization"] == "Bearer token-value"
    assert call["json"] == {
        "to": "U123",
        "messages": [{"type": "text", "text": "Entry #1 opened"}],
    }
    assert call["timeout"].total == 10.0


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (200, LinePushOutcome.DELIVERED),
        (429, LinePushOutcome.RETRYABLE),
        (500, LinePushOutcome.RETRYABLE),
        (503, LinePushOutcome.RETRYABLE),
        (400, LinePushOutcome.REJECTED),
        (401, LinePushOutcome.REJECTED),
        (403, LinePushOutcome.REJECTED),
    ],
)
def test_what_line_answers_decides_whether_it_is_worth_asking_again(
    status: int,
    outcome: LinePushOutcome,
) -> None:
    assert _push(FakeSession(status=status)) is outcome


@pytest.mark.parametrize(
    "error",
    [aiohttp.ClientConnectionError("no route"), TimeoutError()],
)
def test_a_transport_that_never_answered_is_worth_asking_again(
    error: Exception,
) -> None:
    assert _push(FakeSession(error=error)) is LinePushOutcome.RETRYABLE
