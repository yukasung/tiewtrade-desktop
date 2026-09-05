from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from tiewtrade.application.trade_history import TradeHistoryFilter
from tiewtrade.integrations.sqlite.database import SQLiteDatabase
from tiewtrade.integrations.sqlite.trade_history import SQLiteTradeHistory
from tiewtrade.trading.session_config import MarketType, TradeMode
from tiewtrade.trading.trade_history import (
    BasketResult,
    BasketStatus,
    FillSide,
    FillSource,
    TradeFill,
)

SESSION_ID = UUID("00000000-0000-0000-0000-000000000201")
LOW_ID = UUID("00000000-0000-0000-0000-000000000211")
HIGH_ID = UUID("00000000-0000-0000-0000-000000000212")
OLD_ID = UUID("00000000-0000-0000-0000-000000000213")


@pytest.fixture
def history(tmp_path: Path) -> SQLiteTradeHistory:
    database = SQLiteDatabase(tmp_path / "history.sqlite3")
    database.migrate()
    return SQLiteTradeHistory(database)


def basket(
    basket_id: UUID,
    *,
    opened_at_utc: datetime,
    symbol: str = "BTCUSDT",
    timeframe: str = "5m",
    market_type: MarketType = MarketType.SPOT,
    trade_mode: TradeMode = TradeMode.PAPER,
    status: BasketStatus = BasketStatus.CLOSED,
    realized_pnl: Decimal = Decimal("0"),
) -> BasketResult:
    closed = status is BasketStatus.CLOSED
    return BasketResult(
        basket_id=basket_id,
        session_id=SESSION_ID,
        trade_mode=trade_mode,
        market_type=market_type,
        symbol=symbol,
        timeframe=timeframe,
        strategy_preset_version="rsi-step-grid-v1",
        opened_at_utc=opened_at_utc,
        closed_at_utc=opened_at_utc + timedelta(hours=1) if closed else None,
        entry_count=1,
        invested_notional=Decimal("100"),
        realized_pnl=realized_pnl if closed else Decimal("0"),
        status=status,
        leverage=3 if market_type is MarketType.FUTURES else None,
    )


def fill(
    basket_result: BasketResult,
    *,
    fill_id: str,
    side: FillSide,
    filled_at_utc: datetime,
) -> TradeFill:
    return TradeFill(
        fill_id=fill_id,
        basket_id=basket_result.basket_id,
        session_id=basket_result.session_id,
        order_id=f"order-{fill_id}",
        exchange_trade_id=None,
        side=side,
        entry_number=1 if side is FillSide.BUY else None,
        filled_at_utc=filled_at_utc,
        price=Decimal("100"),
        quantity=Decimal("1"),
        notional=Decimal("100"),
        realized_pnl=(
            basket_result.realized_pnl if side is FillSide.SELL else Decimal("0")
        ),
        source=FillSource.PAPER_EXECUTOR,
    )


def record(history: SQLiteTradeHistory, result: BasketResult) -> None:
    opened = replace(
        result,
        closed_at_utc=None,
        realized_pnl=Decimal("0"),
        status=BasketStatus.OPEN,
    )
    history.record_open_basket(
        opened,
        fill(
            opened,
            fill_id=f"{result.basket_id}-buy",
            side=FillSide.BUY,
            filled_at_utc=result.opened_at_utc,
        ),
    )
    if result.status is BasketStatus.CLOSED:
        assert result.closed_at_utc is not None
        history.record_closed_basket(
            result,
            fill(
                result,
                fill_id=f"{result.basket_id}-sell",
                side=FillSide.SELL,
                filled_at_utc=result.closed_at_utc,
            ),
        )


def test_list_basket_history_returns_latest_first_with_an_exact_summary(
    history: SQLiteTradeHistory,
) -> None:
    latest = datetime(2026, 2, 1, tzinfo=UTC)
    records = (
        basket(
            LOW_ID,
            opened_at_utc=latest,
            realized_pnl=Decimal("9999999999999999999999999999"),
        ),
        basket(
            HIGH_ID,
            opened_at_utc=latest,
            realized_pnl=Decimal("0.1"),
        ),
        basket(
            OLD_ID,
            opened_at_utc=latest - timedelta(days=1),
            realized_pnl=Decimal("0.000000000000000001"),
        ),
    )
    for result in records:
        record(history, result)

    history_result = history.list_basket_history(TradeHistoryFilter())

    # Newest first, and Baskets opened at the same instant break the tie on id,
    # so the same store always answers in the same order.
    assert [item.basket_id for item in history_result.items] == [
        HIGH_ID,
        LOW_ID,
        OLD_ID,
    ]
    assert history_result.realized_pnl == Decimal(
        "9999999999999999999999999999.100000000000000001"
    )


def test_list_basket_history_filters_every_supported_dimension(
    history: SQLiteTradeHistory,
) -> None:
    boundary = datetime(2026, 2, 1, tzinfo=UTC)
    target = basket(
        HIGH_ID,
        opened_at_utc=boundary,
        realized_pnl=Decimal("5.123"),
    )
    alternatives = (
        basket(
            # Closed before the boundary too, not merely opened before it: a
            # round is read by the day it closed, so a round that opened a
            # moment earlier and closed after would land on the later day.
            LOW_ID,
            opened_at_utc=boundary - timedelta(hours=2),
            realized_pnl=Decimal("2.5"),
        ),
        basket(
            OLD_ID,
            opened_at_utc=boundary,
            symbol="ETHUSDT",
            timeframe="15m",
            market_type=MarketType.FUTURES,
            trade_mode=TradeMode.LIVE,
            status=BasketStatus.OPEN,
        ),
    )
    for result in (target, *alternatives):
        record(history, result)

    cases = (
        (
            TradeHistoryFilter(symbol="BTCUSDT"),
            (target, alternatives[0]),
            Decimal("7.623"),
        ),
        (
            TradeHistoryFilter(timeframe="5m"),
            (target, alternatives[0]),
            Decimal("7.623"),
        ),
        (
            TradeHistoryFilter(market_type=MarketType.SPOT),
            (target, alternatives[0]),
            Decimal("7.623"),
        ),
        (
            TradeHistoryFilter(trade_mode=TradeMode.PAPER),
            (target, alternatives[0]),
            Decimal("7.623"),
        ),
        (
            TradeHistoryFilter(status=BasketStatus.CLOSED),
            (target, alternatives[0]),
            Decimal("7.623"),
        ),
        (
            TradeHistoryFilter(from_utc=boundary),
            # Newest first by the moment each is read at: the round that
            # closed an hour in comes before the one still open at it.
            (target, alternatives[1]),
            Decimal("5.123"),
        ),
        (
            TradeHistoryFilter(before_utc=boundary),
            (alternatives[0],),
            Decimal("2.5"),
        ),
    )

    for filters, expected, expected_realized_pnl in cases:
        page = history.list_basket_history(filters)
        assert page.items == expected
        assert page.realized_pnl == expected_realized_pnl

    target_closed_at = target.closed_at_utc
    assert target_closed_at is not None
    exact = history.list_basket_history(
        TradeHistoryFilter(
            symbol="BTCUSDT",
            timeframe="5m",
            market_type=MarketType.SPOT,
            trade_mode=TradeMode.PAPER,
            status=BasketStatus.CLOSED,
            # Narrowed onto the instant this round closed, because that is
            # the moment a round is read at.
            from_utc=target_closed_at,
            before_utc=target_closed_at + timedelta(microseconds=1),
        ),
    )

    assert exact.items == (target,)
    assert exact.realized_pnl == Decimal("5.123")


def test_open_filter_has_zero_closed_basket_summary(
    history: SQLiteTradeHistory,
) -> None:
    opened = basket(
        HIGH_ID,
        opened_at_utc=datetime(2026, 2, 1, tzinfo=UTC),
        status=BasketStatus.OPEN,
    )
    record(history, opened)

    result = history.list_basket_history(TradeHistoryFilter(status=BasketStatus.OPEN))

    assert result.items == (opened,)
    assert result.realized_pnl == Decimal("0")


def test_list_fills_uses_execution_time_then_fill_id(
    history: SQLiteTradeHistory,
) -> None:
    timestamp = datetime(2026, 2, 1, tzinfo=UTC)
    opened = basket(HIGH_ID, opened_at_utc=timestamp, status=BasketStatus.OPEN)
    fill_z = fill(
        opened,
        fill_id="fill-z",
        side=FillSide.BUY,
        filled_at_utc=timestamp,
    )
    history.record_open_basket(opened, fill_z)
    fill_a = replace(
        fill_z,
        fill_id="fill-a",
    )
    history.record_entry_fill(opened, fill_a)

    assert history.list_fills(HIGH_ID) == (fill_a, fill_z)


def test_list_basket_history_returns_all_matching_rows_without_pagination(
    history: SQLiteTradeHistory,
) -> None:
    newest = basket(HIGH_ID, opened_at_utc=datetime(2026, 2, 2, tzinfo=UTC))
    oldest = basket(LOW_ID, opened_at_utc=datetime(2026, 2, 1, tzinfo=UTC))
    for basket_item in (oldest, newest):
        record(history, basket_item)

    result = history.list_basket_history(TradeHistoryFilter(symbol="BTCUSDT"))

    assert result.items == (newest, oldest)
    assert result.realized_pnl == Decimal("0")


def test_list_trade_fills_filters_joined_baskets_and_orders_newest_first(
    history: SQLiteTradeHistory,
) -> None:
    matching = basket(HIGH_ID, opened_at_utc=datetime(2026, 2, 2, tzinfo=UTC))
    excluded = basket(
        LOW_ID,
        opened_at_utc=datetime(2026, 2, 1, tzinfo=UTC),
        symbol="ETHUSDT",
    )
    for basket_item in (excluded, matching):
        record(history, basket_item)

    result = history.list_trade_fills(TradeHistoryFilter(symbol="BTCUSDT"))

    assert tuple(item.fill.fill_id for item in result) == (
        f"{HIGH_ID}-sell",
        f"{HIGH_ID}-buy",
    )
    assert {item.symbol for item in result} == {"BTCUSDT"}


def test_list_trade_fills_reads_a_fill_by_the_day_it_landed_on(
    history: SQLiteTradeHistory,
) -> None:
    """A Basket opened yesterday goes on filling today.

    Those Fills are today's trading, so a range covering today has to
    answer with them — reading them by the day their Basket opened would
    hide a whole day of trading behind an older Basket.
    """
    midnight = datetime(2026, 2, 2, tzinfo=UTC)
    opened = basket(HIGH_ID, opened_at_utc=midnight - timedelta(hours=2))
    opened = replace(opened, closed_at_utc=None, status=BasketStatus.OPEN)
    first = fill(
        opened,
        fill_id=f"{HIGH_ID}-buy",
        side=FillSide.BUY,
        filled_at_utc=opened.opened_at_utc,
    )
    history.record_open_basket(opened, first)
    grown = replace(opened, entry_count=2, invested_notional=Decimal("200"))
    second = replace(
        fill(
            grown,
            fill_id=f"{HIGH_ID}-buy-2",
            side=FillSide.BUY,
            filled_at_utc=midnight + timedelta(hours=3),
        ),
        entry_number=2,
    )
    history.record_entry_fill(grown, second)

    result = history.list_trade_fills(TradeHistoryFilter(from_utc=midnight))

    assert tuple(item.fill.fill_id for item in result) == (f"{HIGH_ID}-buy-2",)


def test_total_realized_pnl_counts_an_open_baskets_sold_entries(
    history: SQLiteTradeHistory,
) -> None:
    """A running total on an OPEN row is realised money and must be counted.

    An Entry sold out of a Basket that stays open adds to that Basket's row
    while its status is still `open`, so a closed-only sum drops it.
    """
    opened = datetime(2026, 3, 1, tzinfo=UTC)
    record(history, basket(LOW_ID, opened_at_utc=opened, realized_pnl=Decimal("2.5")))
    still_open = replace(
        basket(HIGH_ID, opened_at_utc=opened, status=BasketStatus.OPEN),
        realized_pnl=Decimal("5.123"),
    )
    history.record_open_basket(
        still_open,
        fill(
            still_open,
            fill_id=f"{HIGH_ID}-buy",
            side=FillSide.BUY,
            filled_at_utc=opened,
        ),
    )

    assert history.total_realized_pnl() == Decimal("7.623")


def test_total_realized_pnl_crosses_sessions_markets_and_days(
    history: SQLiteTradeHistory,
) -> None:
    latest = datetime(2026, 3, 2, tzinfo=UTC)
    record(
        history,
        basket(LOW_ID, opened_at_utc=latest, realized_pnl=Decimal("3")),
    )
    record(
        history,
        replace(
            basket(
                HIGH_ID,
                opened_at_utc=latest - timedelta(days=30),
                market_type=MarketType.FUTURES,
                realized_pnl=Decimal("-1.25"),
            ),
            session_id=UUID("00000000-0000-0000-0000-0000000002ff"),
        ),
    )

    assert history.total_realized_pnl() == Decimal("1.75")


def test_total_realized_pnl_adds_every_place_exactly(
    history: SQLiteTradeHistory,
) -> None:
    opened = datetime(2026, 3, 3, tzinfo=UTC)
    amounts = (
        Decimal("9999999999999999999999999999"),
        Decimal("0.1"),
        Decimal("0.000000000000000001"),
    )
    for index, amount in enumerate(amounts):
        record(
            history,
            basket(
                UUID(f"00000000-0000-0000-0000-00000000030{index}"),
                opened_at_utc=opened,
                realized_pnl=amount,
            ),
        )

    # Written out rather than summed here: Decimal's default context rounds
    # at 28 significant digits, which is exactly what the query must not do.
    assert history.total_realized_pnl() == Decimal(
        "9999999999999999999999999999.100000000000000001"
    )


def test_total_realized_pnl_of_an_empty_database_is_zero(
    history: SQLiteTradeHistory,
) -> None:
    assert history.total_realized_pnl() == Decimal("0")


def test_list_basket_history_reads_a_round_by_the_day_it_closed(
    history: SQLiteTradeHistory,
) -> None:
    """A Position opened yesterday and closed today is today's result.

    The profit it realised landed today, so a range covering today has to
    answer with it — reading it by the day it opened would hide today's
    result behind yesterday, which is the same reason a Fill is read by the
    day it landed on.
    """
    midnight = datetime(2026, 2, 2, tzinfo=UTC)
    opened = basket(HIGH_ID, opened_at_utc=midnight - timedelta(hours=2))
    closed = replace(opened, closed_at_utc=midnight + timedelta(hours=3))

    entry = fill(
        opened,
        fill_id=f"{HIGH_ID}-buy",
        side=FillSide.BUY,
        filled_at_utc=opened.opened_at_utc,
    )
    history.record_open_basket(
        replace(opened, closed_at_utc=None, status=BasketStatus.OPEN), entry
    )
    history.record_closed_basket(
        closed,
        replace(
            fill(
                closed,
                fill_id=f"{HIGH_ID}-sell",
                side=FillSide.SELL,
                filled_at_utc=midnight + timedelta(hours=3),
            ),
            entry_number=1,
        ),
    )

    result = history.list_basket_history(TradeHistoryFilter(from_utc=midnight))

    assert tuple(item.basket_id for item in result.items) == (HIGH_ID,)


def test_list_basket_history_reads_a_round_still_open_by_the_day_it_opened(
    history: SQLiteTradeHistory,
) -> None:
    """A Position that has not closed has only the day it opened to be read by.

    It still belongs in the list — dating it by a close that has not happened
    would drop the round a trader is holding right now.
    """
    midnight = datetime(2026, 2, 2, tzinfo=UTC)
    running = replace(
        basket(HIGH_ID, opened_at_utc=midnight + timedelta(hours=1)),
        closed_at_utc=None,
        status=BasketStatus.OPEN,
    )
    history.record_open_basket(
        running,
        fill(
            running,
            fill_id=f"{HIGH_ID}-buy",
            side=FillSide.BUY,
            filled_at_utc=running.opened_at_utc,
        ),
    )

    result = history.list_basket_history(TradeHistoryFilter(from_utc=midnight))

    assert tuple(item.basket_id for item in result.items) == (HIGH_ID,)
