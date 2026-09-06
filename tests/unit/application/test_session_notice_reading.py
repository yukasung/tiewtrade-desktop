from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from tiewtrade.application.session_notice import (
    BasketClosedNotice,
    BotErrorNotice,
    CooldownCancelledNotice,
    CooldownEndedNotice,
    CooldownStartedNotice,
    EntryOpenedNotice,
    MarketFeedLostNotice,
    MarketFeedRestoredNotice,
    NoticeKind,
    SessionMarket,
    SessionNotice,
    notice_kind,
    notice_moment,
)
from tiewtrade.trading.basket import BasketCloseReason

MARKET = SessionMarket(session_id=uuid4(), symbol="BTCUSDT", timeframe="5m")
MOMENT = datetime(2026, 8, 26, 10, 5, tzinfo=UTC)
LIFTS_AT = datetime(2026, 8, 29, 10, 5, tzinfo=UTC)


def _entry() -> EntryOpenedNotice:
    return EntryOpenedNotice(
        market=MARKET,
        basket_id=uuid4(),
        entry_number=2,
        price=Decimal("64120.50"),
        quantity=Decimal("0.0039"),
        take_profit_price=Decimal("65000.00"),
        filled_at=MOMENT,
    )


def _closed() -> BasketClosedNotice:
    return BasketClosedNotice(
        market=MARKET,
        basket_id=uuid4(),
        entry_count=4,
        average_entry_price=Decimal("64000.00"),
        exit_price=Decimal("65000.00"),
        realized_pnl=Decimal("23.679"),
        close_reason=BasketCloseReason.TAKE_PROFIT,
        closed_at=MOMENT,
    )


def _cooldown_started() -> CooldownStartedNotice:
    return CooldownStartedNotice(
        market=MARKET,
        basket_id=uuid4(),
        completed_pairs=1,
        open_entry_count=2,
        started_at=MOMENT,
        ends_at=LIFTS_AT,
    )


ALL_NOTICES: tuple[tuple[SessionNotice, NoticeKind], ...] = (
    (_entry(), NoticeKind.ENTRY_OPENED),
    (_closed(), NoticeKind.BASKET_CLOSED),
    (_cooldown_started(), NoticeKind.COOLDOWN_STARTED),
    (
        CooldownEndedNotice(
            market=MARKET, basket_id=uuid4(), completed_pairs=1, ended_at=MOMENT
        ),
        NoticeKind.COOLDOWN_ENDED,
    ),
    (
        CooldownCancelledNotice(
            market=MARKET, basket_id=uuid4(), completed_pairs=1, cancelled_at=MOMENT
        ),
        NoticeKind.COOLDOWN_CANCELLED,
    ),
    (
        MarketFeedLostNotice(
            market=MARKET, reason="the stream closed", observed_at=MOMENT
        ),
        NoticeKind.MARKET_FEED_LOST,
    ),
    (
        MarketFeedRestoredNotice(market=MARKET, observed_at=MOMENT),
        NoticeKind.MARKET_FEED_RESTORED,
    ),
    (
        BotErrorNotice(market=MARKET, reason="stopped", observed_at=MOMENT),
        NoticeKind.BOT_ERROR,
    ),
)


@pytest.mark.parametrize(
    ("notice", "kind"),
    [pytest.param(notice, kind, id=kind.value) for notice, kind in ALL_NOTICES],
)
def test_every_notice_says_what_kind_it_is(
    notice: SessionNotice, kind: NoticeKind
) -> None:
    """A durable row is read back long after the object that wrote it is gone.

    The name is the row's own, not the class's, so renaming a class does not
    rewrite what a record from last month says happened.
    """
    assert notice_kind(notice) is kind


@pytest.mark.parametrize(
    "notice",
    [pytest.param(notice, id=kind.value) for notice, kind in ALL_NOTICES],
)
def test_every_notice_says_when_the_thing_it_is_about_happened(
    notice: SessionNotice,
) -> None:
    """Each notice names its moment differently, and every one has one.

    It is the moment of the thing that happened, never the moment a message
    about it was written: a notice queued behind a chat service that is down
    still reports the Fill's own time.
    """
    assert notice_moment(notice) == MOMENT


def test_a_cooldown_is_dated_by_its_start_not_by_the_day_it_lifts() -> None:
    """A Cooldown that starts today and lifts in thirty days happened today."""
    assert notice_moment(_cooldown_started()) == MOMENT
