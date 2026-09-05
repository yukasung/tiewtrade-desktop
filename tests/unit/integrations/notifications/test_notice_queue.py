import asyncio
import logging
import threading
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from tiewtrade.application.session_notice import EntryOpenedNotice, SessionMarket
from tiewtrade.integrations.notifications.notice_queue import (
    NoticeNotifier,
    PushOutcome,
    PushResult,
)

NOTICE = EntryOpenedNotice(
    market=SessionMarket(session_id=uuid4(), symbol="BTCUSDT", timeframe="5m"),
    basket_id=uuid4(),
    entry_number=1,
    price=Decimal("64120.50"),
    quantity=Decimal("0.0039"),
    take_profit_price=Decimal("65000.00"),
    filled_at=datetime(2026, 8, 26, 10, 5, tzinfo=UTC),
)


class FakePush:
    """A push that answers what the test says, and remembers the asking."""

    def __init__(self, outcomes: list[PushOutcome | PushResult | Exception]) -> None:
        self._outcomes = outcomes
        self.messages: list[str] = []

    async def push(self, message: str) -> PushResult:
        self.messages.append(message)
        outcome = self._outcomes.pop(0) if self._outcomes else PushOutcome.DELIVERED
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, PushResult):
            return outcome
        return PushResult(outcome)


def _notify_and_settle(push: FakePush, *, notices: int = 1) -> None:
    async def run() -> None:
        async with NoticeNotifier(
            push, channel="line", retry_delay_seconds=0, queue_size=8
        ) as notifier:
            for _ in range(notices):
                notifier.notify(NOTICE)

    asyncio.run(run())


def test_a_delivered_notice_is_pushed_once() -> None:
    push = FakePush([PushOutcome.DELIVERED])

    _notify_and_settle(push)

    assert len(push.messages) == 1
    assert push.messages[0].startswith("Entry #1 opened\nBTCUSDT 5m\n\n")


def test_a_retryable_push_is_tried_once_more() -> None:
    push = FakePush([PushOutcome.RETRYABLE, PushOutcome.DELIVERED])

    _notify_and_settle(push)

    assert len(push.messages) == 2


def test_a_push_that_keeps_failing_is_dropped_rather_than_retried_forever(
    caplog: pytest.LogCaptureFixture,
) -> None:
    push = FakePush([PushOutcome.RETRYABLE, PushOutcome.RETRYABLE])

    with caplog.at_level(logging.WARNING):
        _notify_and_settle(push)

    assert len(push.messages) == 2
    assert "line.notification.dropped" in caplog.text


def test_a_rejected_push_is_not_retried() -> None:
    push = FakePush([PushOutcome.REJECTED])

    _notify_and_settle(push)

    assert len(push.messages) == 1


def test_a_transport_that_raises_does_not_stop_the_next_notice() -> None:
    push = FakePush([RuntimeError("connection reset"), PushOutcome.DELIVERED])

    _notify_and_settle(push, notices=2)

    # The first notice is pushed, raises, and is retried; the second still goes.
    assert len(push.messages) == 3


def test_notifying_never_waits_on_a_full_queue(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def run() -> list[str]:
        held = asyncio.Event()

        class HeldPush(FakePush):
            async def push(self, message: str) -> PushResult:
                self.messages.append(message)
                await held.wait()
                return PushResult(PushOutcome.DELIVERED)

        push = HeldPush([])
        async with NoticeNotifier(
            push, channel="line", retry_delay_seconds=0, queue_size=1
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
    push = FakePush([PushOutcome.REJECTED])

    with caplog.at_level(logging.DEBUG):
        _notify_and_settle(push)

    assert "line.notification" in caplog.text
    assert "Bearer" not in caplog.text


def test_a_notice_raised_off_the_runtime_thread_still_goes_out() -> None:
    """A trader's ask arrives on their thread, not on the candle loop's.

    Selling a Position and cancelling a Cooldown are both answered on the
    thread the window asked from, and an asyncio queue takes work only from
    its own loop — a notice handed to it from anywhere else would be lost, or
    worse, would corrupt the queue the candle loop is using.
    """
    push = FakePush([PushOutcome.DELIVERED])
    handed_over = threading.Event()

    async def run() -> None:
        async with NoticeNotifier(
            push, channel="line", retry_delay_seconds=0, queue_size=8
        ) as notifier:

            def ask_from_another_thread() -> None:
                notifier.notify(NOTICE)
                handed_over.set()

            asker = threading.Thread(target=ask_from_another_thread)
            asker.start()
            asker.join()
            assert handed_over.wait(1)

    asyncio.run(run())

    assert len(push.messages) == 1


def test_a_notice_raised_after_the_notifier_closed_is_dropped_quietly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    push = FakePush([])
    notifier = NoticeNotifier(push, channel="line", retry_delay_seconds=0, queue_size=8)

    with caplog.at_level(logging.WARNING):
        notifier.notify(NOTICE)

    assert push.messages == []
    assert "line.notification.dropped" in caplog.text


def test_the_log_says_which_chat_service_a_notice_was_going_to(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Two channels write into one log, and a dropped notice belongs to one.

    A trader whose Telegram bot was removed from its chat reads the log to
    find out which of their two channels stopped — an event name that said
    only "notification.dropped" would leave them checking both.
    """
    push = FakePush([PushOutcome.REJECTED])

    async def run() -> None:
        async with NoticeNotifier(
            push, channel="telegram", retry_delay_seconds=0, queue_size=8
        ) as notifier:
            notifier.notify(NOTICE)

    with caplog.at_level(logging.WARNING):
        asyncio.run(run())

    assert "telegram.notification.dropped" in caplog.text
    assert "line.notification" not in caplog.text


def test_a_refusal_says_in_the_log_what_the_service_answered_with(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A refusal is a setting to correct, and the status says which one.

    400 is a chat id the service does not know, 401 a token that was never
    right, 403 a chat the bot was removed from — three different things for
    a trader to go and fix, and "rejected" on its own tells them none of it.
    """
    push = FakePush([PushResult(PushOutcome.REJECTED, 400)])

    with caplog.at_level(logging.WARNING):
        _notify_and_settle(push)

    assert "drop_reason=rejected status=400" in caplog.text


def test_a_service_that_never_answered_has_no_status_to_report(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A timeout is not a status, and an invented one would read as an answer."""
    push = FakePush([RuntimeError("connection reset"), RuntimeError("again")])

    with caplog.at_level(logging.WARNING):
        _notify_and_settle(push)

    assert "drop_reason=retryable" in caplog.text
    assert "status=" not in caplog.text
