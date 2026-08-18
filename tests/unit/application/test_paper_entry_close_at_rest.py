"""Selling one Entry of a Basket no Runtime is holding."""

import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from tests.support.paper_session_setup import configured_spot_session
from tiewtrade.application.paper_session_sink import close_one_entry_at_rest
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


def _record_two_entries(history: SQLiteTradeHistory) -> None:
    """A Basket left open with Entries at 100 and 110, targets 106 and 116."""
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
    for number, price in ((1, "100"), (2, "110")):
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
        )


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
