"""An Entry leaves at what it paid, and the net under the rest.

The rules are stated in PRODUCT.md section 5: each Entry is measured from what
that Entry paid and leaves at its own target, and the Basket carries one more
target of its own that takes everything still held. This walks the whole path
— Session, executor, SQLite — for the case the second rule exists for: an
Entry the market left behind, carried out by the ones that did reach theirs.
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

# Each Entry leaves at what it paid plus 3 x 2: Entry 1 at 106, Entry 2 at
# 116, Entry 3 at 66. The Basket paid 90 across all three, so its own target
# — the net — sits at 96, among them.
ENTRY_PRICES = (Decimal("100"), Decimal("110"), Decimal("60"))
ENTRY_THREE_TARGET = Decimal("66")
SWEEP_PRICE = Decimal("96")
# With Entry 3 gone the Basket is Entries 1 and 2, which paid 105 on average.
REMAINING_SWEEP_PRICE = Decimal("111")


def test_an_entry_leaves_at_its_own_target_and_leaves_the_others_holding(
    tmp_path: Path,
) -> None:
    """The cheap Entry reaches its target first and goes on its own.

    This is the ordinary case, and the one that makes the net rare: a price
    that climbs gradually takes each Entry out at its own target, and never
    reaches the Basket's while more than one Entry is open.
    """
    database_path = tmp_path / "entry-exit.sqlite3"
    store, history = _seeded_history(database_path)
    session = _session_holding_the_open_basket()
    persistent = create_persistent_paper_spot_session(session, history)

    reached = _candle(minute=30, high=ENTRY_THREE_TARGET)
    recorded = persistent.process_completed_candle(
        reached, received_at=reached.close_time, backfilled=False
    )

    assert recorded.persistence_state is PersistenceState.READY
    snapshot = recorded.session
    # Only Entry 3 went, at 66 — the price never came near Entry 1's 106.
    assert snapshot.sold_entry_numbers == (3,)
    assert snapshot.closed_basket is None
    assert snapshot.basket_entry_count == 2
    # The net is restated on what is left: Entries 1 and 2 paid 105 on
    # average, so it moves up to 111 while their own targets stay put.
    assert snapshot.take_profit_price == REMAINING_SWEEP_PRICE

    basket = store.get_basket(BASKET_ID)
    assert basket is not None
    assert basket.status is BasketStatus.OPEN
    # Entry 3 bought at 60 and sold at 66, on a quantity of 1.
    assert basket.realized_pnl == Decimal("6")

    sells = [fill for fill in store.list_fills(BASKET_ID) if fill.side is FillSide.SELL]
    assert [fill.closes_entry_number for fill in sells] == [3]


def test_the_basket_target_sweeps_out_the_entries_the_market_left_behind(
    tmp_path: Path,
) -> None:
    """One candle clears the Basket's target, and everything goes with it.

    Entry 3 still sells at its own 66 rather than at 96: a limit order left at
    66 would have filled long before the price reached 96, and booking the
    higher of two prices the Basket could not have had both of would flatter
    the result. Entries 1 and 2 never reached 106 and 116, and are swept at 96
    — one below the target it was aiming at and above what it paid, the other
    below both, which is the trade the net makes.
    """
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
    assert sorted(snapshot.sold_entry_numbers) == [1, 2, 3]
    closed = snapshot.closed_basket
    assert closed is not None
    assert closed.entry_count == 3
    assert snapshot.basket_entry_count == 0
    assert snapshot.take_profit_price is None

    sells = [fill for fill in store.list_fills(BASKET_ID) if fill.side is FillSide.SELL]
    # Entry 3 at its own target, the rest swept at the Basket's.
    assert {fill.closes_entry_number: fill.price for fill in sells} == {
        3: ENTRY_THREE_TARGET,
        1: SWEEP_PRICE,
        2: SWEEP_PRICE,
    }

    basket = store.get_basket(BASKET_ID)
    assert basket is not None
    assert basket.status is BasketStatus.CLOSED
    assert basket.entry_count == 3
    # (66-60) + (96-100) + (96-110) = 6 - 4 - 14, on a quantity of 1 each.
    # The net does not promise a profit: it promises the round ends rather
    # than leaving an Entry holding a target the market has walked away from.
    assert basket.realized_pnl == Decimal("-12")


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
    """A candle that rises to `high` and closes just under it."""
    body = high - Decimal("1")
    return Candle(
        symbol=MARKET_DATA.symbol,
        timeframe=MARKET_DATA.timeframe,
        open_time=FILLED_AT + timedelta(minutes=minute),
        open=body,
        high=high,
        low=body - Decimal("1"),
        close=body,
    )
