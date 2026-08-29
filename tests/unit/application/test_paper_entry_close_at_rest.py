"""Selling one Entry of a Basket no Runtime is holding."""

import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from tests.support.paper_session_setup import configured_spot_session
from tiewtrade.application.open_basket import reopen_basket
from tiewtrade.application.paper_manual_close import ClosePaperPosition
from tiewtrade.application.paper_session_sink import (
    close_one_entry_at_rest,
    read_open_basket_state,
    waive_cooldown_at_rest,
)
from tiewtrade.execution.paper_spot import PaperSpotEntryFill
from tiewtrade.integrations.sqlite.database import SQLiteDatabase
from tiewtrade.integrations.sqlite.paper_spot_history import (
    PaperSpotHistoryContext,
    PaperSpotSQLiteHistory,
)
from tiewtrade.integrations.sqlite.trade_history import SQLiteTradeHistory
from tiewtrade.strategies.registry import strategy_definition_for
from tiewtrade.trading.symbol_rules import SymbolRules

BASKET_ID = UUID("00000000-0000-0000-0000-0000000000e9")
OPENED_AT = datetime(2026, 8, 1, 12, tzinfo=UTC)


def _rules() -> SymbolRules:
    return SymbolRules(
        symbol="BTCUSDT",
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.001"),
        min_notional=Decimal("5"),
    )


@pytest.fixture
def history(tmp_path: Path) -> SQLiteTradeHistory:
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    database.migrate()
    return SQLiteTradeHistory(database)


def _record_two_entries(history: SQLiteTradeHistory, *, waived: bool = False) -> None:
    """A Basket left open with Entries at 100 and 110, in a Group each.

    110 does not average 100 down, so the second Entry opened a Group of its
    own, and the two Groups leave at 106 and 116.

    The Cooldown these two Entries leave behind does not stand in the way of
    selling one of them, so the Basket is left exactly as it stands — pass
    `waived=True` for the Basket whose Cooldown the trader had already lifted.
    """
    session = configured_spot_session()
    spot = PaperSpotSQLiteHistory(
        PaperSpotHistoryContext(
            session_id=session.config.session_id,
            symbol=session.market_data.symbol,
            timeframe=session.market_data.timeframe,
            preset_version=session.config.preset_version,
        ),
        history,
    )
    for number, price, group_target in ((1, "100", "106"), (2, "110", "116")):
        spot.record_entry(
            basket_id=BASKET_ID,
            entry_number=number,
            fill=PaperSpotEntryFill(
                order_id=f"entry:{number}",
                fill_id=f"paper:entry:{number}:fill",
                intent_id=f"intent-{number}",
                price=Decimal(price),
                quantity=Decimal("2"),
                filled_at=OPENED_AT + timedelta(days=number - 1),
            ),
            entry_atr=Decimal("2"),
            entry_group_id=number,
            group_take_profit_price=Decimal(group_target),
        )
    if waived:
        history.record_cooldown_waiver(BASKET_ID, 1, OPENED_AT)


def _sell(history: SQLiteTradeHistory, entry_number: int, price: str) -> bool:
    session = configured_spot_session()
    return close_one_entry_at_rest(
        session,
        entry_number,
        trade_history=history,
        symbol_rules=_rules(),
        preset=strategy_definition_for(session.config.preset_version).build_preset(),
        price=Decimal(price),
        observed_at=datetime(2026, 8, 3, tzinfo=UTC),
    )


def test_one_entry_goes_and_the_basket_stays_open_for_the_rest(
    history: SQLiteTradeHistory,
) -> None:
    """A stopped Bot leaves its Basket here, and the trader can still act on it."""
    _record_two_entries(history)

    assert _sell(history, 1, "105.678") is True

    basket = history.find_open_basket(configured_spot_session().config.session_id)
    fills = history.list_fills(BASKET_ID)
    assert basket is not None
    # Floored to the tick, and the Basket keeps the Entry that did not sell.
    assert basket.realized_pnl == Decimal("11.34")
    assert [(fill.side.value, fill.closes_entry_number) for fill in fills] == [
        ("buy", None),
        ("buy", None),
        ("sell", 1),
    ]


def test_selling_the_last_entry_closes_the_basket_on_the_running_total(
    history: SQLiteTradeHistory,
    tmp_path: Path,
) -> None:
    """Every Entry the Basket ever had is in the total it closes on.

    Each sale composes the Session again from what is in the database, so the
    Basket handed over knows only the Entry it was asked about — the store is
    what has been counting all along.
    """
    _record_two_entries(history)
    _sell(history, 1, "105.678")

    assert _sell(history, 2, "120") is True

    assert history.find_open_basket(configured_spot_session().config.session_id) is None
    connection = sqlite3.connect(tmp_path / "tiewtrade.sqlite3")
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        "SELECT entry_count, realized_pnl, status FROM basket_results "
        "WHERE basket_id = ?",
        (str(BASKET_ID),),
    ).fetchone()
    connection.close()
    assert row["status"] == "closed"
    assert row["entry_count"] == 2
    assert Decimal(row["realized_pnl"]) == Decimal("31.34")


def test_selling_an_entry_at_rest_ends_the_cooldown_it_sold_from(
    history: SQLiteTradeHistory,
) -> None:
    """A stopped Bot's Basket ends its Cooldown the same way a running one does.

    Nothing is written to say the Cooldown was lifted: the Entries the Basket
    is still holding say the Pair is no longer whole, and the next Session
    composed from the database reads it back off them.
    """
    _record_two_entries(history, waived=False)

    assert _sell(history, 1, "105.678") is True

    session = configured_spot_session()
    open_basket = read_open_basket_state(history, session.config.session_id)
    assert open_basket is not None
    assert open_basket.waived_pair is None
    assert [entry.entry_number for entry in open_basket.entries] == [2]


def test_an_entry_that_has_gone_is_not_sold_twice(
    history: SQLiteTradeHistory,
) -> None:
    _record_two_entries(history)
    _sell(history, 1, "105")

    assert _sell(history, 1, "105") is False
    assert len(history.list_fills(BASKET_ID)) == 3


def test_a_session_holding_nothing_has_no_entry_to_sell(
    history: SQLiteTradeHistory,
) -> None:
    assert _sell(history, 1, "105") is False


def test_closing_at_market_after_a_row_sale_sells_only_what_is_left(
    history: SQLiteTradeHistory,
    tmp_path: Path,
) -> None:
    """The two ways out of a Position, one after the other.

    Selling one Entry by its row and then closing the rest at the market is
    what the row button makes ordinary. Counting the sold Entry again put its
    quantity into the exit Fill a second time, and wrote a Basket result that
    replaced what it had realised instead of adding to it.
    """
    _record_two_entries(history)
    _sell(history, 1, "105.678")

    ClosePaperPosition(
        find_open_basket=history.find_open_basket,
        list_fills=history.list_fills,
        record_closed=history.record_closed_basket,
        record_entry_exit=history.record_entry_exit_fill,
        clock=lambda: datetime(2026, 8, 4, tzinfo=UTC),
    ).execute(
        configured_spot_session(),
        market_price=lambda: Decimal("120"),
        tick_size=Decimal("0.01"),
    )

    fills = history.list_fills(BASKET_ID)
    exit_fill = fills[-1]
    # Entry 2 alone, at 2 units — not the 4 both Entries were bought at.
    assert exit_fill.quantity == Decimal("2")
    assert exit_fill.realized_pnl == Decimal("20")
    connection = sqlite3.connect(tmp_path / "tiewtrade.sqlite3")
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        "SELECT entry_count, realized_pnl, status FROM basket_results "
        "WHERE basket_id = ?",
        (str(BASKET_ID),),
    ).fetchone()
    connection.close()
    assert row["status"] == "closed"
    assert row["entry_count"] == 2
    # 11.34 from the row sale, and 20 from what was left.
    assert Decimal(row["realized_pnl"]) == Decimal("31.34")


def test_a_basket_read_back_knows_what_it_already_settled(
    history: SQLiteTradeHistory,
) -> None:
    """What a restart hands the Runtime, which is what its result is owed."""
    _record_two_entries(history)
    _sell(history, 1, "105.678")

    state = read_open_basket_state(history, configured_spot_session().config.session_id)

    assert state is not None
    # The Entry still open is the only one to replay, in the Group it bought
    # into. The Group the sold Entry emptied is not handed back.
    assert [entry.entry_number for entry in state.entries] == [2]
    assert [entry.group_id for entry in state.entries] == [2]
    assert state.group_take_profit_prices == {2: Decimal("116")}
    assert state.settled.count == 1
    assert state.settled.quantity == Decimal("2")
    assert state.settled.notional == Decimal("200")
    assert state.settled.realized_pnl == Decimal("11.34")


def _waive(history: SQLiteTradeHistory, observed_at: datetime) -> bool:
    session = configured_spot_session()
    return waive_cooldown_at_rest(
        session,
        trade_history=history,
        symbol_rules=_rules(),
        preset=strategy_definition_for(session.config.preset_version).build_preset(),
        observed_at=observed_at,
    )


def test_a_cooldown_at_rest_can_be_waived(history: SQLiteTradeHistory) -> None:
    _record_two_entries(history, waived=False)

    assert _waive(history, datetime(2026, 8, 5, tzinfo=UTC)) is True
    assert history.find_cooldown_waiver(BASKET_ID) is not None

    state = read_open_basket_state(history, configured_spot_session().config.session_id)
    assert state is not None
    assert state.waived_pair is not None


def test_waiving_a_basket_with_no_cooldown_does_nothing(
    history: SQLiteTradeHistory,
) -> None:
    session = configured_spot_session()
    spot = PaperSpotSQLiteHistory(
        PaperSpotHistoryContext(
            session_id=session.config.session_id,
            symbol=session.market_data.symbol,
            timeframe=session.market_data.timeframe,
            preset_version=session.config.preset_version,
        ),
        history,
    )
    spot.record_entry(
        basket_id=BASKET_ID,
        entry_number=1,
        fill=PaperSpotEntryFill(
            order_id="entry:1",
            fill_id="paper:entry:1:fill",
            intent_id="intent-1",
            price=Decimal("100"),
            quantity=Decimal("2"),
            filled_at=OPENED_AT,
        ),
        entry_atr=Decimal("2"),
        entry_group_id=1,
        group_take_profit_price=Decimal("106"),
    )

    assert _waive(history, datetime(2026, 8, 5, tzinfo=UTC)) is False
    assert history.find_cooldown_waiver(BASKET_ID) is None


def test_waiving_with_no_open_basket_does_nothing(
    history: SQLiteTradeHistory,
) -> None:
    assert _waive(history, datetime(2026, 8, 5, tzinfo=UTC)) is False


def test_a_basket_read_back_carries_its_cooldown_waiver(
    history: SQLiteTradeHistory,
) -> None:
    _record_two_entries(history)
    history.record_cooldown_waiver(BASKET_ID, 24313, datetime(2026, 1, 25, tzinfo=UTC))

    state = read_open_basket_state(history, configured_spot_session().config.session_id)

    assert state is not None
    assert state.waived_pair == 24313


def test_a_group_comes_back_at_the_price_it_was_ratcheted_to(
    history: SQLiteTradeHistory,
) -> None:
    """The target a Group had moved to is read back, not measured again.

    Both Entries are one Group here, and the second Fill recorded a target of
    103 — closer than the 106 the first Fill left, and closer than measuring
    the pair afresh would give. Measuring again on the way back would push it
    out to a price the Group had already come in from.
    """
    session = configured_spot_session()
    spot = PaperSpotSQLiteHistory(
        PaperSpotHistoryContext(
            session_id=session.config.session_id,
            symbol=session.market_data.symbol,
            timeframe=session.market_data.timeframe,
            preset_version=session.config.preset_version,
        ),
        history,
    )
    for number, price, group_target in ((1, "100", "106"), (2, "98", "103")):
        spot.record_entry(
            basket_id=BASKET_ID,
            entry_number=number,
            fill=PaperSpotEntryFill(
                order_id=f"entry:{number}",
                fill_id=f"paper:entry:{number}:fill",
                intent_id=f"intent-{number}",
                price=Decimal(price),
                quantity=Decimal("2"),
                filled_at=OPENED_AT + timedelta(days=number - 1),
            ),
            entry_atr=Decimal("2"),
            entry_group_id=1,
            group_take_profit_price=Decimal(group_target),
        )

    state = read_open_basket_state(history, session.config.session_id)

    assert state is not None
    assert [entry.group_id for entry in state.entries] == [1, 1]
    assert state.group_take_profit_prices == {1: Decimal("103")}
    reopened = reopen_basket(
        state,
        entry_policy=session.config.entry_policy,
        take_profit_atr_multiplier=Decimal("3"),
        tick_size=_rules().tick_size,
    )
    assert reopened.basket.take_profit_price == Decimal("103")
    assert reopened.basket.entry_take_profit_price(1) == Decimal("103")
