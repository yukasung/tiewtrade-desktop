import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from tiewtrade.application.entry_opened_notice import EntryOpenedNotice
from tiewtrade.integrations.line.messaging_api import (
    LineEntryOpenedNotifier,
    LinePushOutcome,
)

NOTICE = EntryOpenedNotice(
    session_id=uuid4(),
    symbol="BTCUSDT",
    timeframe="5m",
    basket_id=uuid4(),
    entry_number=1,
    price=Decimal("64120.50"),
    quantity=Decimal("0.0039"),
    take_profit_price=Decimal("65000.00"),
    filled_at=datetime(2026, 8, 26, 10, 5, tzinfo=UTC),
)


class FakePush:
    """A LINE push that answers what the test says, and remembers the asking."""

    def __init__(self, outcomes: list[LinePushOutcome | Exception]) -> None:
        self._outcomes = outcomes
        self.messages: list[str] = []

    async def push(self, message: str) -> LinePushOutcome:
        self.messages.append(message)
        outcome = self._outcomes.pop(0) if self._outcomes else LinePushOutcome.DELIVERED
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _notify_and_settle(push: FakePush, *, notices: int = 1) -> None:
    async def run() -> None:
        async with LineEntryOpenedNotifier(
            push, retry_delay_seconds=0, queue_size=8
        ) as notifier:
            for _ in range(notices):
                notifier.notify(NOTICE)

    asyncio.run(run())


def test_a_delivered_notice_is_pushed_once() -> None:
    push = FakePush([LinePushOutcome.DELIVERED])

    _notify_and_settle(push)

    assert len(push.messages) == 1
    assert "Entry #1 opened — BTCUSDT 5m" in push.messages[0]


def test_a_retryable_push_is_tried_once_more() -> None:
    push = FakePush([LinePushOutcome.RETRYABLE, LinePushOutcome.DELIVERED])

    _notify_and_settle(push)

    assert len(push.messages) == 2


def test_a_push_that_keeps_failing_is_dropped_rather_than_retried_forever(
    caplog: pytest.LogCaptureFixture,
) -> None:
    push = FakePush([LinePushOutcome.RETRYABLE, LinePushOutcome.RETRYABLE])

    with caplog.at_level(logging.WARNING):
        _notify_and_settle(push)

    assert len(push.messages) == 2
    assert "line.notification.dropped" in caplog.text


def test_a_rejected_push_is_not_retried() -> None:
    push = FakePush([LinePushOutcome.REJECTED])

    _notify_and_settle(push)

    assert len(push.messages) == 1


def test_a_transport_that_raises_does_not_stop_the_next_notice() -> None:
    push = FakePush([RuntimeError("connection reset"), LinePushOutcome.DELIVERED])

    _notify_and_settle(push, notices=2)

    # The first notice is pushed, raises, and is retried; the second still goes.
    assert len(push.messages) == 3


def test_notifying_never_waits_on_a_full_queue(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def run() -> list[str]:
        held = asyncio.Event()

        class HeldPush(FakePush):
            async def push(self, message: str) -> LinePushOutcome:
                self.messages.append(message)
                await held.wait()
                return LinePushOutcome.DELIVERED

        push = HeldPush([])
        async with LineEntryOpenedNotifier(
            push, retry_delay_seconds=0, queue_size=1
        ) as notifier:
            for _ in range(5):
                notifier.notify(NOTICE)
            held.set()
        return push.messages

    with caplog.at_level(logging.WARNING):
        messages = asyncio.run(run())

    assert "line.notification.dropped" in caplog.text
    assert len(messages) < 5


def test_every_notice_left_in_the_queue_is_pushed_before_the_runtime_goes() -> None:
    push = FakePush([])

    _notify_and_settle(push, notices=3)

    assert len(push.messages) == 3


def test_a_failing_push_never_writes_the_token_into_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    push = FakePush([LinePushOutcome.REJECTED])

    with caplog.at_level(logging.DEBUG):
        _notify_and_settle(push)

    assert "line.notification" in caplog.text
    assert "Bearer" not in caplog.text
