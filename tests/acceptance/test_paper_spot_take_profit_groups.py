"""An Entry Group leaves at what the Group paid, and the others hold.

The rules are stated in PRODUCT.md section 5: an Entry that averages its Group
down joins it and brings the Group's target with it, an Entry that averages up
opens a Group of its own, and reaching a Group's target sells every Entry in
it and nothing else. This walks the whole path — Session, executor, SQLite —
for a Basket holding two Groups a long way apart.
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

# Entry 1 paid 100 and opened Group 1, which leaves at 100 + 3 x 2 = 106.
# Entry 2 paid 110, which averages nothing down, so it opened Group 2 at 116.
# Entry 3 paid 60, which averages Group 2 down to 85 and brings that Group's
# target — Entry 2 included — to 91.
ENTRY_PRICES = (Decimal("100"), Decimal("110"), Decimal("60"))
ENTRY_GROUPS = (1, 2, 2)
# What each Fill recorded as its Group's target at the moment it filled.
RECORDED_GROUP_TARGETS = (Decimal("106"), Decimal("116"), Decimal("91"))
GROUP_ONE_TARGET = Decimal("106")
GROUP_TWO_TARGET = Decimal("91")


def test_a_group_leaves_at_its_target_and_leaves_the_others_holding(
    tmp_path: Path,
) -> None:
    """The Group that averaged down reaches its target first and goes.

    Both its Entries leave together at the one price, including the Entry that
    paid 110 and is losing at 91 — it is in that Group, so it goes when the
    Group goes. Group 1 is a long way above and is not touched.
    """
    database_path = tmp_path / "entry-exit.sqlite3"
    store, history = _seeded_history(database_path)
    session = _session_holding_the_open_basket()
    persistent = create_persistent_paper_spot_session(session, history)

    # The Basket's own figure is the nearer of its two Groups.
    assert session.snapshot.take_profit_price == GROUP_TWO_TARGET

    reached = _candle(minute=30, high=GROUP_TWO_TARGET)
    recorded = persistent.process_completed_candle(
        reached, received_at=reached.close_time, backfilled=False
    )

    assert recorded.persistence_state is PersistenceState.READY
    snapshot = recorded.session
    assert sorted(snapshot.sold_entry_numbers) == [2, 3]
    assert snapshot.closed_basket is None
    assert snapshot.basket_entry_count == 1
    # Group 1 is what is left, waiting where it always was.
    assert snapshot.take_profit_price == GROUP_ONE_TARGET

    basket = store.get_basket(BASKET_ID)
    assert basket is not None
    assert basket.status is BasketStatus.OPEN
    # (91-110) + (91-60) = -19 + 31, on a quantity of 1 each.
    assert basket.realized_pnl == Decimal("12")

    sells = [fill for fill in store.list_fills(BASKET_ID) if fill.side is FillSide.SELL]
    assert {fill.closes_entry_number: fill.price for fill in sells} == {
        2: GROUP_TWO_TARGET,
        3: GROUP_TWO_TARGET,
    }


def test_a_candle_reaching_both_groups_empties_the_basket_at_two_prices(
    tmp_path: Path,
) -> None:
    """Each Group sells at its own price, and the last of them closes the Basket.

    Group 2 sells at 91 rather than at 106: a limit order left at 91 would have
    filled long before the price reached 106, and booking the higher of two
    prices the Basket could not have had both of would flatter the result.
    """
    database_path = tmp_path / "sweep.sqlite3"
    store, history = _seeded_history(database_path)
    session = _session_holding_the_open_basket()
    persistent = create_persistent_paper_spot_session(session, history)

    reached = _candle(minute=30, high=GROUP_ONE_TARGET)
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
    assert {fill.closes_entry_number: fill.price for fill in sells} == {
        2: GROUP_TWO_TARGET,
        3: GROUP_TWO_TARGET,
        1: GROUP_ONE_TARGET,
    }

    basket = store.get_basket(BASKET_ID)
    assert basket is not None
    assert basket.status is BasketStatus.CLOSED
    assert basket.entry_count == 3
    # (91-110) + (91-60) + (106-100) = -19 + 31 + 6, on a quantity of 1 each.
    assert basket.realized_pnl == Decimal("18")


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
    for number, (price, group_id, group_target) in enumerate(
        zip(ENTRY_PRICES, ENTRY_GROUPS, RECORDED_GROUP_TARGETS, strict=True), start=1
    ):
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
            entry_group_id=group_id,
            group_take_profit_price=group_target,
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
                    group_id=group_id,
                )
                for number, (price, group_id) in enumerate(
                    zip(ENTRY_PRICES, ENTRY_GROUPS, strict=True), start=1
                )
            ),
            closed_basket_count=0,
            group_take_profit_prices={
                1: GROUP_ONE_TARGET,
                2: GROUP_TWO_TARGET,
            },
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
