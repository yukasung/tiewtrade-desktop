"""An Entry bought high leaves with the Basket, and the books say so.

The rule is stated in PRODUCT.md section 5: one target per Basket, measured
from the weighted average, and every Entry sold at it together. This walks the
whole path — Session, executor, SQLite — for the case the rule exists for: a
Basket holding an Entry that is still losing when the target is reached.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid5

from tiewtrade.application.open_basket import OpenBasketEntry, OpenBasketState
from tiewtrade.application.paper_spot_session import PaperSpotSession
from tiewtrade.application.session_persistence import PersistenceState
from tiewtrade.execution.paper_spot import PaperSpotEntryFill
from tiewtrade.integrations.sqlite.database import SQLiteDatabase
from tiewtrade.integrations.sqlite.paper_spot_history import (
    PaperSpotHistoryContext,
    PaperSpotSQLiteHistory,
)
from tiewtrade.integrations.sqlite.persistent_paper_spot_session import (
    create_persistent_paper_spot_session,
)
from tiewtrade.integrations.sqlite.trade_history import SQLiteTradeHistory
from tiewtrade.market_data.candle import Candle
from tiewtrade.market_data.config import MarketDataConfig
from tiewtrade.strategies.rsi_step_grid.preset import RsiStepGridPreset
from tiewtrade.trading.entry_policy import EntryPolicy
from tiewtrade.trading.session_config import MarketType, SessionConfig, TradeMode
from tiewtrade.trading.symbol_rules import SymbolRules
from tiewtrade.trading.trade_history import BasketStatus, FillSide

SESSION_ID = UUID("00000000-0000-0000-0000-000000000081")
BASKET_ID = uuid5(SESSION_ID, "basket:1")
FILLED_AT = datetime(2026, 8, 24, 9, tzinfo=UTC)
MARKET_DATA = MarketDataConfig(symbol="BTCUSDT", timeframe="5m")
ENTRY_ATR = Decimal("2")

# Bought at 100 and then at 80, so the Basket paid 90 on average and leaves at
# 90 + 3 x 2. The Entry bought at 100 is still 4 down at that price, and goes
# anyway — which is the whole point of measuring the target from the average.
ENTRY_PRICES = (Decimal("100"), Decimal("80"))
SWEEP_PRICE = Decimal("96")


def test_a_basket_sweeps_its_losing_entry_out_at_the_average_target(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "sweep.sqlite3"
    store, history = _seeded_history(database_path)
    session = _session_holding_the_open_basket()
    persistent = create_persistent_paper_spot_session(session, history)

    assert session.snapshot.take_profit_price == SWEEP_PRICE

    reached = _candle(minute=30, high=SWEEP_PRICE)
    recorded = persistent.process_completed_candle(
        reached, received_at=reached.close_time, backfilled=False
    )

    assert recorded.persistence_state is PersistenceState.READY
    snapshot = recorded.session
    closed = snapshot.closed_basket
    assert closed is not None
    assert closed.entry_count == 2
    # Sold at 96: the Entry bought at 80 made 16, the one bought at 100 lost 4,
    # and the Basket comes out 12 ahead having carried both to the exit.
    assert closed.realized_pnl == Decimal("12")
    assert snapshot.basket_entry_count == 0
    assert snapshot.take_profit_price is None
    # One sale, naming no Entry, because the Position went as a whole.
    assert snapshot.entry_exits == ()
    assert snapshot.exit_fill is not None
    assert snapshot.exit_fill.entry_number is None
    assert snapshot.exit_fill.quantity == Decimal("2")

    basket = store.get_basket(BASKET_ID)
    assert basket is not None
    assert basket.status is BasketStatus.CLOSED
    assert basket.entry_count == 2
    assert basket.realized_pnl == Decimal("12")

    fills = store.list_fills(BASKET_ID)
    assert [fill.side for fill in fills] == [FillSide.BUY, FillSide.BUY, FillSide.SELL]
    sell = fills[-1]
    assert sell.price == SWEEP_PRICE
    assert sell.quantity == Decimal("2")
    assert sell.entry_number is None
    assert sell.closes_entry_number is None
    assert sell.realized_pnl == Decimal("12")


def test_a_sweep_after_a_manual_sale_books_only_what_it_sold(
    tmp_path: Path,
) -> None:
    """The Entry sold by hand already has a Fill carrying its result.

    Repeating the Basket's whole result on the closing Fill showed the same
    money twice down the Fills table. The Basket row keeps the round; the Fill
    keeps the sale.
    """
    database_path = tmp_path / "sweep-after-sale.sqlite3"
    store, history = _seeded_history(database_path)
    persistent = create_persistent_paper_spot_session(
        _session_holding_the_open_basket(), history
    )

    sold = persistent.close_entry(
        2, price=Decimal("95"), observed_at=FILLED_AT + timedelta(minutes=20)
    )
    assert sold.persistence_state is PersistenceState.READY
    # The Entry bought at 80 sold at 95, and the Basket is left holding only
    # the Entry bought at 100 — which moves the target it now leaves at.
    assert sold.session.basket_entry_count == 1
    assert sold.session.take_profit_price == Decimal("106")

    reached = _candle(minute=30, high=Decimal("106"))
    recorded = persistent.process_completed_candle(
        reached, received_at=reached.close_time, backfilled=False
    )

    assert recorded.persistence_state is PersistenceState.READY
    closed = recorded.session.closed_basket
    assert closed is not None
    # 15 realised by hand, 6 realised by the sweep.
    assert closed.realized_pnl == Decimal("21")
    assert closed.exit_realized_pnl == Decimal("6")

    basket = store.get_basket(BASKET_ID)
    assert basket is not None
    assert basket.realized_pnl == Decimal("21")

    sells = [fill for fill in store.list_fills(BASKET_ID) if fill.side is FillSide.SELL]
    assert [fill.realized_pnl for fill in sells] == [Decimal("15"), Decimal("6")]
    # The Fills add up to the round rather than double-counting the first sale.
    assert sum((fill.realized_pnl for fill in sells), Decimal("0")) == Decimal("21")


def _seeded_history(
    database_path: Path,
) -> tuple[SQLiteTradeHistory, PaperSpotSQLiteHistory]:
    """A store already holding the open Basket a previous Runtime left."""
    database = SQLiteDatabase(database_path)
    database.migrate()
    store = SQLiteTradeHistory(database)
    history = PaperSpotSQLiteHistory(
        PaperSpotHistoryContext(
            session_id=SESSION_ID,
            symbol=MARKET_DATA.symbol,
            timeframe=MARKET_DATA.timeframe,
            preset_version=RsiStepGridPreset.v1().version,
        ),
        store,
    )
    for number, price in enumerate(ENTRY_PRICES, start=1):
        order_id = f"entry:intent-{number}"
        history.record_entry(
            basket_id=BASKET_ID,
            entry_number=number,
            fill=PaperSpotEntryFill(
                order_id=order_id,
                fill_id=f"paper:{SESSION_ID}:{order_id}:fill",
                intent_id=f"intent-{number}",
                price=price,
                quantity=Decimal("1"),
                filled_at=FILLED_AT + timedelta(minutes=5 * (number - 1)),
            ),
            entry_atr=ENTRY_ATR,
        )
    return store, history


def _session_holding_the_open_basket() -> PaperSpotSession:
    preset = RsiStepGridPreset.v1()
    return PaperSpotSession(
        SessionConfig(
            session_id=SESSION_ID,
            preset_version=preset.version,
            market_type=MarketType.SPOT,
            trade_mode=TradeMode.PAPER,
            capital_per_entry=Decimal("100"),
            entry_policy=EntryPolicy(max_entries=4),
        ),
        MARKET_DATA,
        SymbolRules(
            symbol="BTCUSDT",
            tick_size=Decimal("0.01"),
            step_size=Decimal("0.001"),
            min_notional=Decimal("5"),
        ),
        preset,
        open_basket=OpenBasketState(
            basket_id=BASKET_ID,
            entries=tuple(
                OpenBasketEntry(
                    entry_number=number,
                    price=price,
                    quantity=Decimal("1"),
                    filled_at=FILLED_AT + timedelta(minutes=5 * (number - 1)),
                    atr=ENTRY_ATR,
                )
                for number, price in enumerate(ENTRY_PRICES, start=1)
            ),
            closed_basket_count=0,
        ),
    )


def _candle(*, minute: int, high: Decimal) -> Candle:
    open_time = FILLED_AT + timedelta(minutes=minute)
    return Candle(
        symbol=MARKET_DATA.symbol,
        timeframe=MARKET_DATA.timeframe,
        open_time=open_time,
        open=Decimal("95"),
        high=high,
        low=Decimal("94"),
        close=Decimal("95"),
    )
