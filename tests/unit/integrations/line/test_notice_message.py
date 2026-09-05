from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from tiewtrade.application.session_notice import (
    BasketClosedNotice,
    BotErrorNotice,
    CooldownCancelledNotice,
    CooldownEndedNotice,
    CooldownStartedNotice,
    EntryOpenedNotice,
    MarketFeedLostNotice,
    MarketFeedRestoredNotice,
    SessionMarket,
)
from tiewtrade.integrations.line.notice_message import notice_message
from tiewtrade.trading.basket import BasketCloseReason

MARKET = SessionMarket(session_id=uuid4(), symbol="BTCUSDT", timeframe="5m")
MOMENT = datetime(2026, 8, 26, 10, 5, tzinfo=UTC)


def _entry(
    *,
    entry_number: int = 2,
    price: Decimal = Decimal("64120.50"),
    quantity: Decimal = Decimal("0.0039"),
    take_profit_price: Decimal | None = Decimal("65000.00"),
) -> EntryOpenedNotice:
    return EntryOpenedNotice(
        market=MARKET,
        basket_id=uuid4(),
        entry_number=entry_number,
        price=price,
        quantity=quantity,
        take_profit_price=take_profit_price,
        filled_at=MOMENT,
    )


def _closed(
    *,
    realized_pnl: Decimal = Decimal("23.679"),
    close_reason: BasketCloseReason = BasketCloseReason.TAKE_PROFIT,
) -> BasketClosedNotice:
    return BasketClosedNotice(
        market=MARKET,
        basket_id=uuid4(),
        entry_count=4,
        average_entry_price=Decimal("64000.00"),
        exit_price=Decimal("65000.00"),
        realized_pnl=realized_pnl,
        close_reason=close_reason,
        closed_at=MOMENT,
    )


def _closed_with_average(average_entry_price: Decimal) -> BasketClosedNotice:
    return BasketClosedNotice(
        market=MARKET,
        basket_id=uuid4(),
        entry_count=4,
        average_entry_price=average_entry_price,
        exit_price=Decimal("65000.00"),
        realized_pnl=Decimal("23.679"),
        close_reason=BasketCloseReason.TAKE_PROFIT,
        closed_at=MOMENT,
    )


def test_an_entry_reads_as_what_it_paid_and_where_it_is_heading() -> None:
    assert notice_message(_entry()).splitlines() == [
        "Entry #2 opened",
        "BTCUSDT 5m",
        "",
        "Price 64,120.50 USDT",
        "Quantity 0.0039 (cost 250.07 USDT)",
        "Take profit at 65,000.00 USDT",
        "",
        "Filled 2026-08-26 10:05:00 UTC",
    ]


def test_an_entry_says_nothing_of_a_take_profit_it_has_not_got() -> None:
    message = notice_message(_entry(take_profit_price=None))

    assert "Take profit" not in message
    assert message.startswith("Entry #2 opened\nBTCUSDT 5m\n\n")


def test_an_entry_leaves_a_quantity_ungrouped() -> None:
    message = notice_message(
        _entry(price=Decimal("1.25"), quantity=Decimal("12345.6789"))
    )

    assert "Quantity 12345.6789 (cost 15,432.10 USDT)" in message


def test_a_closed_position_reads_as_why_it_went_and_what_it_came_to() -> None:
    assert notice_message(_closed()).splitlines() == [
        "Position closed",
        "BTCUSDT 5m",
        "",
        "Take profit · Profit 23.68 USDT",
        "Entries 4",
        "Average entry price 64,000.00 USDT",
        "Exit price 65,000.00 USDT",
        "",
        "Closed 2026-08-26 10:05:00 UTC",
    ]


def test_a_losing_position_is_named_a_loss_rather_than_a_negative_profit() -> None:
    message = notice_message(
        _closed(
            realized_pnl=Decimal("-41.20"),
            close_reason=BasketCloseReason.MANUAL,
        )
    )

    assert "Closed by you · Loss 41.20 USDT" in message
    assert "-" not in message.splitlines()[3]


def test_a_position_the_trader_closed_says_who_closed_it() -> None:
    message = notice_message(_closed(close_reason=BasketCloseReason.MANUAL))

    assert "Closed by you · Profit 23.68 USDT" in message


def test_a_cooldown_says_that_nothing_but_the_trader_will_end_it() -> None:
    message = notice_message(
        CooldownStartedNotice(
            market=MARKET,
            basket_id=uuid4(),
            completed_pairs=2,
            open_entry_count=4,
            started_at=MOMENT,
        )
    )

    assert message.splitlines() == [
        "Cooldown started",
        "BTCUSDT 5m",
        "",
        "2 Entry Pairs completed · 4 Entries open",
        "The Bot will not open a new Entry until you cancel the Cooldown.",
        "",
        "Started 2026-08-26 10:05:00 UTC",
    ]


def test_a_cancelled_cooldown_names_the_pair_it_was_holding() -> None:
    message = notice_message(
        CooldownCancelledNotice(
            market=MARKET,
            basket_id=uuid4(),
            completed_pairs=2,
            cancelled_at=MOMENT,
        )
    )

    assert message.splitlines() == [
        "Cooldown cancelled",
        "BTCUSDT 5m",
        "",
        "2 Entry Pairs completed",
        "The Bot can open a new Entry again.",
        "",
        "Cancelled 2026-08-26 10:05:00 UTC",
    ]


def test_a_lost_feed_says_the_bot_is_waiting_rather_than_stopped() -> None:
    message = notice_message(
        MarketFeedLostNotice(
            market=MARKET,
            reason="source_disconnected",
            observed_at=MOMENT,
        )
    )

    assert message.startswith("Market data lost\nBTCUSDT 5m\n\n")
    assert "Reason: source_disconnected" in message
    assert "holding its Position" in message


def test_a_feed_that_came_back_says_so_in_one_line() -> None:
    message = notice_message(
        MarketFeedRestoredNotice(market=MARKET, observed_at=MOMENT)
    )

    assert message.splitlines() == [
        "Market data restored",
        "BTCUSDT 5m",
        "",
        "The Bot is reading candles again.",
        "",
        "Observed 2026-08-26 10:05:00 UTC",
    ]


def test_a_stopped_bot_says_why_and_that_it_will_not_start_itself() -> None:
    message = notice_message(
        BotErrorNotice(
            market=MARKET,
            reason="Paper Bot stopped: the Session could not continue",
            observed_at=MOMENT,
        )
    )

    assert message.splitlines() == [
        "Bot stopped",
        "BTCUSDT 5m",
        "",
        "Reason: Paper Bot stopped: the Session could not continue",
        "The Bot will not trade again until you start it.",
        "",
        "Observed 2026-08-26 10:05:00 UTC",
    ]


def test_a_cooldown_of_one_pair_and_one_entry_reads_in_the_singular() -> None:
    message = notice_message(
        CooldownStartedNotice(
            market=MARKET,
            basket_id=uuid4(),
            completed_pairs=1,
            open_entry_count=1,
            started_at=MOMENT,
        )
    )

    assert "1 Entry Pair completed · 1 Entry open" in message


def test_a_price_reads_to_the_cent_however_it_was_quoted() -> None:
    message = notice_message(
        _entry(price=Decimal("64120.5"), take_profit_price=Decimal("65000"))
    )

    assert "Price 64,120.50 USDT" in message
    assert "Take profit at 65,000.00 USDT" in message


def test_an_average_entry_price_stops_at_the_cent_like_the_rest() -> None:
    message = notice_message(_closed_with_average(Decimal("64000.123456789")))

    assert "Average entry price 64,000.12 USDT" in message


def test_every_message_reads_as_three_blocks_split_by_a_blank_line() -> None:
    for message in (
        notice_message(_entry()),
        notice_message(_closed()),
        notice_message(MarketFeedRestoredNotice(market=MARKET, observed_at=MOMENT)),
    ):
        headline, body, when = message.split("\n\n")

        assert headline.splitlines()[1] == "BTCUSDT 5m"
        assert "" not in body.splitlines()
        assert when.endswith(" UTC")


def test_a_cooldown_that_waits_says_the_day_it_lifts_on_by_itself() -> None:
    message = notice_message(
        CooldownStartedNotice(
            market=MARKET,
            basket_id=uuid4(),
            completed_pairs=2,
            open_entry_count=4,
            started_at=MOMENT,
            ends_at=datetime(2026, 8, 29, 10, 5, tzinfo=UTC),
        )
    )

    assert message.splitlines() == [
        "Cooldown started",
        "BTCUSDT 5m",
        "",
        "2 Entry Pairs completed · 4 Entries open",
        "The Bot will not open a new Entry until 2026-08-29 10:05:00 UTC,"
        " or until you cancel the Cooldown.",
        "",
        "Started 2026-08-26 10:05:00 UTC",
    ]


def test_a_cooldown_that_ran_out_says_the_bot_may_buy_again() -> None:
    message = notice_message(
        CooldownEndedNotice(
            market=MARKET,
            basket_id=uuid4(),
            completed_pairs=2,
            ended_at=MOMENT,
        )
    )

    assert message.splitlines() == [
        "Cooldown ended",
        "BTCUSDT 5m",
        "",
        "2 Entry Pairs completed",
        "The Cooldown ran out of days on its own. The Bot can open a new Entry again.",
        "",
        "Ended 2026-08-26 10:05:00 UTC",
    ]
