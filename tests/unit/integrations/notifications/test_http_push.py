from __future__ import annotations

import asyncio

import aiohttp
import pytest
from _pytest.monkeypatch import MonkeyPatch

from tests.support.chat_push import FakeSession
from tiewtrade.integrations.notifications.http_push import (
    REQUEST_TIMEOUT_SECONDS,
    HttpChatPush,
    outcome_for_status,
)
from tiewtrade.integrations.notifications.notice_queue import PushOutcome, PushResult

URL = "https://chat.example/send"


def _post(session: FakeSession) -> PushResult:
    push = HttpChatPush(session)  # type: ignore[arg-type]
    return asyncio.run(push.post(URL, json={"text": "Entry #1 opened"}))


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (200, PushOutcome.DELIVERED),
        (204, PushOutcome.DELIVERED),
        (429, PushOutcome.RETRYABLE),
        (500, PushOutcome.RETRYABLE),
        (503, PushOutcome.RETRYABLE),
        (400, PushOutcome.REJECTED),
        (401, PushOutcome.REJECTED),
        (403, PushOutcome.REJECTED),
        (404, PushOutcome.REJECTED),
    ],
)
def test_what_a_service_answers_decides_whether_it_is_worth_asking_again(
    status: int,
    outcome: PushOutcome,
) -> None:
    """One reading of a status, for every chat service that shares this socket.

    The queue's whole retry policy hangs off this mapping, so it is pinned
    here rather than once per channel, where the two could drift apart.
    """
    assert outcome_for_status(status) is outcome


def test_a_delivered_push_carries_the_status_that_said_so() -> None:
    result = _post(FakeSession(status=200))

    assert result.outcome is PushOutcome.DELIVERED
    assert result.status == 200


def test_a_refusal_carries_the_status_so_the_log_can_say_which_it_was() -> None:
    result = _post(FakeSession(status=403))

    assert result.outcome is PushOutcome.REJECTED
    assert result.status == 403


@pytest.mark.parametrize(
    "error",
    [aiohttp.ClientConnectionError("no route"), aiohttp.ClientError(), TimeoutError()],
)
def test_a_service_that_never_answered_is_worth_asking_again(
    error: Exception,
) -> None:
    result = _post(FakeSession(error=error))

    assert result.outcome is PushOutcome.RETRYABLE
    # Nothing answered, so there is no status to carry — an invented one
    # would read in the log as an answer the service never gave.
    assert result.status is None


def test_a_cancelled_push_is_not_read_as_a_service_that_failed() -> None:
    """A Runtime shutting down is not weather to wait out and try again.

    Cancellation travels as an exception the way a network fault does, and
    swallowing it here would put the notice back on the queue of a Runtime
    that is on its way out.
    """
    push = HttpChatPush(FakeSession(error=asyncio.CancelledError()))  # type: ignore[arg-type]

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(push.post(URL, json={}))


def test_the_request_never_waits_longer_than_a_candle() -> None:
    session = FakeSession()

    _post(session)

    assert session.calls[0]["timeout"].total == REQUEST_TIMEOUT_SECONDS


def test_a_session_handed_in_belongs_to_whoever_handed_it_over() -> None:
    """Closing the transport must not close a socket it was only lent."""
    session = FakeSession()
    push = HttpChatPush(session)  # type: ignore[arg-type]

    asyncio.run(push.close())

    assert session.closed is False


def test_a_socket_the_transport_opened_is_one_it_closes(
    monkeypatch: MonkeyPatch,
) -> None:
    """A transport left holding an open socket outlives the Runtime that made it."""
    opened = FakeSession()
    monkeypatch.setattr(aiohttp, "ClientSession", lambda: opened)

    async def push_then_leave() -> None:
        async with HttpChatPush() as push:
            await push.post(URL, json={})

    asyncio.run(push_then_leave())

    assert opened.closed is True
