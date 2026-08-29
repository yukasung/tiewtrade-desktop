from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from tiewtrade.execution.paper_futures import (
    PaperFuturesEntryFill,
    PaperFuturesExitFill,
)
from tiewtrade.integrations.sqlite.database import SQLiteDatabase
from tiewtrade.integrations.sqlite.paper_futures_history import (
    PaperFuturesHistoryContext,
    PaperFuturesSQLiteHistory,
)
from tiewtrade.integrations.sqlite.trade_history import (
    SQLiteTradeHistory,
    TradeHistoryConflictError,
)
from tiewtrade.trading.basket import (
    BasketCloseReason,
    ClosedBasket,
    ClosedEntry,
)
from tiewtrade.trading.position import PositionSide
from tiewtrade.trading.session_config import MarketType, TradeMode
from tiewtrade.trading.trade_history import BasketStatus, FillSide

SESSION_ID = UUID("00000000-0000-0000-0000-000000000108")
BASKET_ID = UUID("00000000-0000-0000-0000-000000000109")
OPENED_AT = datetime(2026, 1, 1, tzinfo=UTC)
CLOSED_AT = datetime(2026, 1, 2, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> SQLiteTradeHistory:
    database = SQLiteDatabase(tmp_path / "history.sqlite3")
    database.migrate()
    return SQLiteTradeHistory(database)


@pytest.fixture
def history(store: SQLiteTradeHistory) -> PaperFuturesSQLiteHistory:
    return PaperFuturesSQLiteHistory(history_context(), store)


def history_context() -> PaperFuturesHistoryContext:
    return PaperFuturesHistoryContext(
        session_id=SESSION_ID,
        symbol="BTCUSDT",
        timeframe="5m",
        preset_version="rsi-step-grid-v1",
        leverage=3,
    )


@pytest.mark.parametrize("leverage", [0, 6, True, 1.5])
def test_history_context_rejects_leverage_outside_session_cap(
    leverage: object,
) -> None:
    with pytest.raises(ValueError, match="between 1 and 5"):
        PaperFuturesHistoryContext(
            session_id=SESSION_ID,
            symbol="BTCUSDT",
            timeframe="5m",
            preset_version="rsi-step-grid-v1",
            leverage=leverage,  # type: ignore[arg-type]
        )


def entry_fill(
    *,
    side: PositionSide = PositionSide.LONG,
    sequence: int = 1,
) -> PaperFuturesEntryFill:
    price = Decimal("100") if sequence == 1 else Decimal("90")
    quantity = Decimal("2") if sequence == 1 else Decimal("1")
    intent_id = f"intent-{sequence}"
    order_id = f"entry:{intent_id}"
    return PaperFuturesEntryFill(
        order_id=order_id,
        fill_id=f"paper:{SESSION_ID}:{order_id}:fill",
        intent_id=intent_id,
        side=side,
        price=price,
        quantity=quantity,
        filled_at=OPENED_AT + timedelta(hours=sequence - 1),
    )


def exit_fill(
    *,
    side: PositionSide = PositionSide.LONG,
    entry_number: int = 1,
) -> PaperFuturesExitFill:
    order_id = f"take_profit:{BASKET_ID}:{entry_number}"
    return PaperFuturesExitFill(
        order_id=order_id,
        fill_id=f"paper:{SESSION_ID}:{order_id}:fill",
        side=side,
        close_reason=BasketCloseReason.TAKE_PROFIT,
        price=Decimal("110") if side is PositionSide.LONG else Decimal("90"),
        quantity=Decimal("2"),
        filled_at=CLOSED_AT,
        entry_number=entry_number,
    )


def closed_entry(*, side: PositionSide = PositionSide.LONG) -> ClosedEntry:
    """What the Entry the exit Fill sold left behind, on a quantity of 2."""
    exit_price = Decimal("110") if side is PositionSide.LONG else Decimal("90")
    return ClosedEntry(
        entry_number=1,
        quantity=Decimal("2"),
        entry_price=Decimal("100"),
        exit_price=exit_price,
        realized_pnl=Decimal("20"),
        closed_at=CLOSED_AT,
        group_id=1,
    )


def closed_basket(
    *,
    side: PositionSide = PositionSide.LONG,
) -> ClosedBasket:
    gross_pnl = Decimal("20")
    return ClosedBasket(
        basket_id=BASKET_ID,
        entry_count=1,
        average_entry_price=Decimal("100"),
        exit_price=Decimal("110") if side is PositionSide.LONG else Decimal("90"),
        realized_pnl=gross_pnl,
        exit_realized_pnl=gross_pnl,
        closed_at=CLOSED_AT,
        position_side=side,
        close_reason=BasketCloseReason.TAKE_PROFIT,
    )


def test_long_entry_creates_futures_basket_and_buy_fill(
    history: PaperFuturesSQLiteHistory,
    store: SQLiteTradeHistory,
) -> None:
    fill = entry_fill()

    assert history.record_entry(
        basket_id=BASKET_ID,
        entry_number=1,
        fill=fill,
        entry_group_id=1,
        group_take_profit_price=Decimal("106"),
    )

    basket = store.get_basket(BASKET_ID)
    fills = store.list_fills(BASKET_ID)
    assert basket is not None
    assert basket.trade_mode is TradeMode.PAPER
    assert basket.market_type is MarketType.FUTURES
    assert basket.leverage == 3
    assert basket.entry_count == 1
    assert basket.invested_notional == Decimal("200")
    assert basket.realized_pnl == Decimal("0")
    assert basket.status is BasketStatus.OPEN
    assert len(fills) == 1
    assert fills[0].side is FillSide.BUY
    assert fills[0].entry_number == 1
    assert fills[0].realized_pnl == Decimal("0")


def test_short_entry_maps_to_sell_fill(
    history: PaperFuturesSQLiteHistory,
    store: SQLiteTradeHistory,
) -> None:
    history.record_entry(
        basket_id=BASKET_ID,
        entry_number=1,
        fill=entry_fill(side=PositionSide.SHORT),
        entry_group_id=1,
        group_take_profit_price=Decimal("106"),
    )

    assert store.list_fills(BASKET_ID)[0].side is FillSide.SELL


def test_second_entry_updates_notional_fees_and_entry_count(
    history: PaperFuturesSQLiteHistory,
    store: SQLiteTradeHistory,
) -> None:
    history.record_entry(
        basket_id=BASKET_ID,
        entry_number=1,
        fill=entry_fill(),
        entry_group_id=1,
        group_take_profit_price=Decimal("106"),
    )
    history.record_entry(
        basket_id=BASKET_ID,
        entry_number=2,
        fill=entry_fill(sequence=2),
        entry_group_id=1,
        group_take_profit_price=Decimal("106"),
    )

    basket = store.get_basket(BASKET_ID)
    assert basket is not None
    assert basket.entry_count == 2
    assert basket.invested_notional == Decimal("290")
    assert basket.realized_pnl == Decimal("0")


def test_partial_fill_for_same_order_does_not_increment_entry_count(
    history: PaperFuturesSQLiteHistory,
    store: SQLiteTradeHistory,
) -> None:
    first = entry_fill()
    history.record_entry(
        basket_id=BASKET_ID,
        entry_number=1,
        fill=first,
        entry_group_id=1,
        group_take_profit_price=Decimal("106"),
    )
    partial = PaperFuturesEntryFill(
        order_id=first.order_id,
        fill_id=f"{first.fill_id}:partial-2",
        intent_id=first.intent_id,
        side=first.side,
        price=Decimal("101"),
        quantity=Decimal("0.5"),
        filled_at=first.filled_at + timedelta(seconds=1),
    )

    history.record_entry(
        basket_id=BASKET_ID,
        entry_number=1,
        fill=partial,
        entry_group_id=1,
        group_take_profit_price=Decimal("106"),
    )

    basket = store.get_basket(BASKET_ID)
    assert basket is not None
    assert basket.entry_count == 1
    assert basket.invested_notional == Decimal("250.5")
    assert len(store.list_fills(BASKET_ID)) == 2


def test_existing_basket_rejects_changed_session_leverage(
    history: PaperFuturesSQLiteHistory,
    store: SQLiteTradeHistory,
) -> None:
    history.record_entry(
        basket_id=BASKET_ID,
        entry_number=1,
        fill=entry_fill(),
        entry_group_id=1,
        group_take_profit_price=Decimal("106"),
    )
    changed_context = PaperFuturesHistoryContext(
        session_id=SESSION_ID,
        symbol="BTCUSDT",
        timeframe="5m",
        preset_version="rsi-step-grid-v1",
        leverage=4,
    )

    with pytest.raises(TradeHistoryConflictError, match="leverage"):
        PaperFuturesSQLiteHistory(changed_context, store).record_entry(
            basket_id=BASKET_ID,
            entry_number=2,
            fill=entry_fill(sequence=2),
            entry_group_id=1,
            group_take_profit_price=Decimal("106"),
        )


@pytest.mark.parametrize(
    ("position_side", "entry_side", "exit_side"),
    [
        (PositionSide.LONG, FillSide.BUY, FillSide.SELL),
        (PositionSide.SHORT, FillSide.SELL, FillSide.BUY),
    ],
)
def test_close_uses_shared_pnl_and_opposite_fill_side(
    tmp_path: Path,
    position_side: PositionSide,
    entry_side: FillSide,
    exit_side: FillSide,
) -> None:
    database = SQLiteDatabase(tmp_path / f"{position_side.value}.sqlite3")
    database.migrate()
    store = SQLiteTradeHistory(database)
    history = PaperFuturesSQLiteHistory(history_context(), store)
    history.record_entry(
        basket_id=BASKET_ID,
        entry_number=1,
        fill=entry_fill(side=position_side),
        entry_group_id=1,
        group_take_profit_price=Decimal("106"),
    )
    closed = closed_basket(side=position_side)

    assert history.record_entry_exit(
        basket_id=BASKET_ID,
        fill=exit_fill(side=position_side),
        closed=closed_entry(side=position_side),
        closed_basket=closed,
    )

    basket = store.get_basket(BASKET_ID)
    fills = store.list_fills(BASKET_ID)
    assert basket is not None
    assert basket.status is BasketStatus.CLOSED
    assert basket.realized_pnl == closed.realized_pnl
    assert basket.realized_pnl == closed.realized_pnl
    assert [item.side for item in fills] == [entry_side, exit_side]
    assert fills[-1].realized_pnl == closed.realized_pnl


def test_one_entry_sold_leaves_the_basket_open_with_its_result_counted(
    history: PaperFuturesSQLiteHistory,
    store: SQLiteTradeHistory,
) -> None:
    """A Pair that reached its target goes, and the rest keeps trading."""
    history.record_entry(
        basket_id=BASKET_ID,
        entry_number=1,
        fill=entry_fill(),
        entry_group_id=1,
        group_take_profit_price=Decimal("106"),
    )
    history.record_entry(
        basket_id=BASKET_ID,
        entry_number=2,
        fill=entry_fill(sequence=2),
        entry_group_id=1,
        group_take_profit_price=Decimal("106"),
    )

    assert history.record_entry_exit(
        basket_id=BASKET_ID,
        fill=exit_fill(),
        closed=closed_entry(),
        closed_basket=None,
    )

    basket = store.get_basket(BASKET_ID)
    assert basket is not None
    assert basket.status is BasketStatus.OPEN
    assert basket.realized_pnl == Decimal("20")
    sold = store.list_fills(BASKET_ID)[-1]
    assert sold.side is FillSide.SELL
    assert sold.closes_entry_number == 1
    assert sold.realized_pnl == Decimal("20")


def test_an_exit_fill_and_the_entry_it_closed_must_name_the_same_entry(
    history: PaperFuturesSQLiteHistory,
) -> None:
    history.record_entry(
        basket_id=BASKET_ID,
        entry_number=1,
        fill=entry_fill(),
        entry_group_id=1,
        group_take_profit_price=Decimal("106"),
    )

    with pytest.raises(ValueError, match="different Entries"):
        history.record_entry_exit(
            basket_id=BASKET_ID,
            fill=exit_fill(entry_number=2),
            closed=closed_entry(),
            closed_basket=None,
        )


def test_duplicate_entry_and_close_are_idempotent(
    history: PaperFuturesSQLiteHistory,
    store: SQLiteTradeHistory,
) -> None:
    entry = entry_fill()
    exit = exit_fill()
    closed = closed_basket()
    sold = closed_entry()

    assert history.record_entry(
        basket_id=BASKET_ID,
        entry_number=1,
        fill=entry,
        entry_group_id=1,
        group_take_profit_price=Decimal("106"),
    )
    assert not history.record_entry(
        basket_id=BASKET_ID,
        entry_number=1,
        fill=entry,
        entry_group_id=1,
        group_take_profit_price=Decimal("106"),
    )
    assert history.record_entry_exit(
        basket_id=BASKET_ID,
        fill=exit,
        closed=sold,
        closed_basket=closed,
    )
    persisted = store.get_basket(BASKET_ID)
    assert not history.record_entry_exit(
        basket_id=BASKET_ID,
        fill=exit,
        closed=sold,
        closed_basket=closed,
    )

    assert store.get_basket(BASKET_ID) == persisted
    assert len(store.list_fills(BASKET_ID)) == 2


@pytest.mark.parametrize(
    ("basket_id", "fill_side", "message"),
    [
        (
            UUID("00000000-0000-0000-0000-000000000999"),
            PositionSide.LONG,
            "does not match",
        ),
        (BASKET_ID, PositionSide.SHORT, "different sides"),
    ],
)
def test_mapper_rejects_inconsistent_close_ownership(
    history: PaperFuturesSQLiteHistory,
    basket_id: UUID,
    fill_side: PositionSide,
    message: str,
) -> None:
    history.record_entry(
        basket_id=BASKET_ID,
        entry_number=1,
        fill=entry_fill(),
        entry_group_id=1,
        group_take_profit_price=Decimal("106"),
    )

    with pytest.raises(ValueError, match=message):
        history.record_entry_exit(
            basket_id=basket_id,
            fill=exit_fill(side=fill_side),
            closed=closed_entry(),
            closed_basket=closed_basket(),
        )
