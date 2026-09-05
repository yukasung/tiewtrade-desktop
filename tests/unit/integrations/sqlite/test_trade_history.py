import sqlite3
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import create_autospec
from uuid import UUID, uuid4

import pytest

from tests.support.trade_history_records import (
    BASKET_ID,
    SESSION_ID,
    basket_result,
    trade_fill,
)
from tiewtrade.integrations.sqlite.database import SQLiteDatabase
from tiewtrade.integrations.sqlite.trade_history import (
    SQLiteTradeHistory,
)
from tiewtrade.integrations.sqlite.trade_history_errors import (
    TradeHistoryConflictError,
    TradeHistoryUnavailableError,
)
from tiewtrade.trading.session_config import MarketType, TradeMode
from tiewtrade.trading.trade_history import (
    BasketResult,
    BasketStatus,
    FillSide,
    TradeFill,
)


@pytest.fixture
def history(tmp_path: Path) -> SQLiteTradeHistory:
    database = SQLiteDatabase(tmp_path / "history.sqlite3")
    database.migrate()
    return SQLiteTradeHistory(database)


def open_basket() -> BasketResult:
    return basket_result(
        closed_at_utc=None,
        invested_notional=Decimal("200"),
        realized_pnl=Decimal("-0.2"),
        status=BasketStatus.OPEN,
    )


def test_exact_duplicate_fill_is_a_no_op(history: SQLiteTradeHistory) -> None:
    basket = open_basket()
    fill = trade_fill()

    assert history.record_open_basket(basket, fill) is True
    assert history.record_open_basket(basket, fill) is False
    assert history.get_basket(basket.basket_id) == basket
    assert history.list_fills(basket.basket_id) == (fill,)


def test_find_cooldown_waiver_returns_none_when_none_recorded(
    history: SQLiteTradeHistory,
) -> None:
    basket = open_basket()
    history.record_open_basket(basket, trade_fill())

    assert history.find_cooldown_waiver(basket.basket_id) is None


def test_cooldown_waiver_round_trips(history: SQLiteTradeHistory) -> None:
    basket = open_basket()
    history.record_open_basket(basket, trade_fill())
    waived_at = datetime(2026, 1, 25, tzinfo=UTC)

    history.record_cooldown_waiver(basket.basket_id, 24313, waived_at)

    assert history.find_cooldown_waiver(basket.basket_id) == 24313


def test_cooldown_waiver_upsert_overwrites_the_prior_month(
    history: SQLiteTradeHistory,
) -> None:
    basket = open_basket()
    history.record_open_basket(basket, trade_fill())
    history.record_cooldown_waiver(
        basket.basket_id, 24313, datetime(2026, 1, 25, tzinfo=UTC)
    )

    history.record_cooldown_waiver(
        basket.basket_id, 24316, datetime(2026, 4, 25, tzinfo=UTC)
    )

    assert history.find_cooldown_waiver(basket.basket_id) == 24316


def test_find_open_basket_returns_only_open_basket_for_session(
    history: SQLiteTradeHistory,
) -> None:
    basket = open_basket()
    history.record_open_basket(basket, trade_fill())

    assert history.find_open_basket(basket.session_id) == basket
    assert history.find_open_basket(uuid4()) is None

    history.record_closed_basket(closed_basket(basket), sell_fill())

    assert history.find_open_basket(basket.session_id) is None


def test_list_pair_fills_orders_by_utc_then_fill_id_across_sessions(
    history: SQLiteTradeHistory,
) -> None:
    """The chart's marks belong to the Pair, so they outlive a Session.

    Read by Session, every trade vanished from the chart the moment a Start
    replaced the one that made it — while the trades themselves sat in this
    table, still true of BTCUSDT.
    """
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 2, tzinfo=UTC)
    first = trade_fill(
        fill_id="fill-a",
        basket_id=uuid4(),
        session_id=UUID("00000000-0000-0000-0000-000000000138"),
        filled_at_utc=start + timedelta(hours=1),
    )
    second = trade_fill(
        fill_id="fill-b",
        basket_id=uuid4(),
        session_id=uuid4(),
        filled_at_utc=start + timedelta(hours=1),
    )
    for fill in (second, first):
        history.record_open_basket(
            replace(
                open_basket(),
                basket_id=fill.basket_id,
                session_id=fill.session_id,
                opened_at_utc=fill.filled_at_utc,
            ),
            fill,
        )

    assert history.list_pair_fills(
        "BTCUSDT", TradeMode.PAPER, MarketType.SPOT, start, end
    ) == (first, second)


def test_list_pair_fills_leaves_out_another_pair_mode_or_market(
    history: SQLiteTradeHistory,
) -> None:
    """Paper marks have no business on a live chart, nor spot on futures."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 2, tzinfo=UTC)

    def record(hour: int, basket: BasketResult) -> None:
        fill = trade_fill(
            fill_id=f"fill-{hour}",
            basket_id=uuid4(),
            session_id=uuid4(),
            filled_at_utc=start + timedelta(hours=hour),
        )
        history.record_open_basket(
            replace(
                basket,
                basket_id=fill.basket_id,
                session_id=fill.session_id,
                opened_at_utc=fill.filled_at_utc,
            ),
            fill,
        )

    record(1, replace(open_basket(), symbol="ETHUSDT"))
    record(2, replace(open_basket(), trade_mode=TradeMode.LIVE))
    record(3, replace(open_basket(), market_type=MarketType.FUTURES, leverage=3))

    assert (
        history.list_pair_fills("BTCUSDT", TradeMode.PAPER, MarketType.SPOT, start, end)
        == ()
    )
    # Recorded all the same, or the emptiness above would prove nothing.
    for symbol, mode, market in (
        ("ETHUSDT", TradeMode.PAPER, MarketType.SPOT),
        ("BTCUSDT", TradeMode.LIVE, MarketType.SPOT),
        ("BTCUSDT", TradeMode.PAPER, MarketType.FUTURES),
    ):
        assert len(history.list_pair_fills(symbol, mode, market, start, end)) == 1


def test_open_basket_requires_open_status(history: SQLiteTradeHistory) -> None:
    basket = basket_result(status=BasketStatus.CLOSED)

    with pytest.raises(TradeHistoryConflictError, match="OPEN"):
        history.record_open_basket(basket, trade_fill())

    assert history.get_basket(basket.basket_id) is None


def test_same_fill_id_with_different_payload_is_a_conflict(
    history: SQLiteTradeHistory,
) -> None:
    basket = open_basket()
    fill = trade_fill()
    history.record_open_basket(basket, fill)
    conflicting = replace(
        fill,
        price=Decimal("101"),
        notional=Decimal("202"),
    )

    with pytest.raises(TradeHistoryConflictError, match="fill_id"):
        history.record_open_basket(basket, conflicting)

    assert history.get_basket(basket.basket_id) == basket
    assert history.list_fills(basket.basket_id) == (fill,)


@pytest.mark.parametrize(
    ("market_type", "leverage"),
    [
        (MarketType.SPOT, None),
        (MarketType.FUTURES, 3),
    ],
)
def test_partial_fills_share_order_and_entry_without_incrementing_entry_count(
    history: SQLiteTradeHistory,
    market_type: MarketType,
    leverage: int | None,
) -> None:
    first = trade_fill()
    opened = replace(
        open_basket(),
        market_type=market_type,
        leverage=leverage,
    )
    history.record_open_basket(opened, first)
    second = trade_fill(
        fill_id="fill-partial-2",
        order_id=first.order_id,
        entry_number=first.entry_number,
        filled_at_utc=first.filled_at_utc + timedelta(seconds=1),
        price=Decimal("101"),
        quantity=Decimal("0.5"),
        notional=Decimal("50.5"),
    )
    updated = replace(
        opened,
        invested_notional=Decimal("250.5"),
        realized_pnl=Decimal("-0.2505"),
    )

    assert history.record_entry_fill(updated, second) is True
    assert history.get_basket(opened.basket_id) == updated
    assert history.list_fills(opened.basket_id) == (first, second)


def closed_basket(opened: BasketResult) -> BasketResult:
    return replace(
        opened,
        closed_at_utc=datetime(2026, 1, 2, tzinfo=UTC),
        realized_pnl=Decimal("20"),
        status=BasketStatus.CLOSED,
    )


def sell_fill() -> TradeFill:
    return trade_fill(
        fill_id="fill-sell",
        order_id="order-sell",
        side=FillSide.SELL,
        entry_number=None,
        filled_at_utc=datetime(2026, 1, 2, tzinfo=UTC),
        price=Decimal("110"),
        notional=Decimal("220"),
        realized_pnl=Decimal("20"),
    )


@pytest.mark.parametrize(
    "change_identity",
    [
        lambda result: replace(
            result,
            session_id=UUID("00000000-0000-0000-0000-000000000999"),
        ),
        lambda result: replace(result, trade_mode=TradeMode.LIVE),
        lambda result: replace(
            result,
            market_type=MarketType.FUTURES,
            leverage=3,
        ),
        lambda result: replace(result, symbol="ETHUSDT"),
        lambda result: replace(result, timeframe="15m"),
        lambda result: replace(result, strategy_preset_version="rsi-step-grid-v2"),
        lambda result: replace(
            result,
            opened_at_utc=datetime(2026, 1, 2, tzinfo=UTC),
        ),
    ],
)
def test_entry_rejects_changed_basket_identity(
    history: SQLiteTradeHistory,
    change_identity: Callable[[BasketResult], BasketResult],
) -> None:
    opened = open_basket()
    history.record_open_basket(opened, trade_fill())
    second = trade_fill(fill_id="fill-2", order_id="order-2", entry_number=2)
    changed_identity = change_identity(opened)
    proposed = replace(
        changed_identity,
        entry_count=2,
        invested_notional=opened.invested_notional + second.notional,
        realized_pnl=opened.realized_pnl,
    )

    with pytest.raises(TradeHistoryConflictError):
        history.record_entry_fill(proposed, second)


@pytest.mark.parametrize(
    "change_identity",
    [
        lambda fill: replace(
            fill,
            basket_id=UUID("00000000-0000-0000-0000-000000000999"),
        ),
        lambda fill: replace(
            fill,
            session_id=UUID("00000000-0000-0000-0000-000000000999"),
        ),
    ],
)
def test_fill_rejects_different_basket_or_session(
    history: SQLiteTradeHistory,
    change_identity: Callable[[TradeFill], TradeFill],
) -> None:
    basket = open_basket()
    fill = change_identity(trade_fill())

    with pytest.raises(TradeHistoryConflictError):
        history.record_open_basket(basket, fill)

    assert history.get_basket(basket.basket_id) is None


@pytest.mark.parametrize("operation", ["entry", "close"])
def test_unknown_basket_rejects_entry_and_close(
    history: SQLiteTradeHistory,
    operation: str,
) -> None:
    with pytest.raises(TradeHistoryConflictError, match="does not exist"):
        if operation == "entry":
            history.record_entry_fill(
                replace(open_basket(), entry_count=2),
                trade_fill(
                    fill_id="fill-2",
                    order_id="order-2",
                    entry_number=2,
                ),
            )
        else:
            history.record_closed_basket(
                closed_basket(open_basket()),
                sell_fill(),
            )

    assert history.list_fills(BASKET_ID) == ()


def test_closed_basket_rejects_new_entry(history: SQLiteTradeHistory) -> None:
    opened = open_basket()
    first = trade_fill()
    closed = closed_basket(opened)
    exit_trade = sell_fill()
    history.record_open_basket(opened, first)
    history.record_closed_basket(closed, exit_trade)
    next_fill = trade_fill(fill_id="fill-3", order_id="order-3", entry_number=2)
    proposed = replace(closed, status=BasketStatus.OPEN, closed_at_utc=None)

    with pytest.raises(TradeHistoryConflictError, match="closed"):
        history.record_entry_fill(proposed, next_fill)

    assert history.get_basket(BASKET_ID) == closed
    assert history.list_fills(BASKET_ID) == (first, exit_trade)


def test_exact_duplicate_close_after_closed_is_a_no_op(
    history: SQLiteTradeHistory,
) -> None:
    opened = open_basket()
    first = trade_fill()
    closed = closed_basket(opened)
    exit_trade = sell_fill()
    history.record_open_basket(opened, first)

    assert history.record_closed_basket(closed, exit_trade) is True
    assert history.record_closed_basket(closed, exit_trade) is False
    assert history.list_fills(BASKET_ID) == (first, exit_trade)


def test_open_basket_rolls_back_when_fill_insert_fails(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "history.sqlite3")
    database.migrate()
    history = SQLiteTradeHistory(database)
    with database.connect() as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_trade_fill
            BEFORE INSERT ON trade_fills
            BEGIN
                SELECT RAISE(ABORT, 'forced fill failure');
            END
            """
        )

    with pytest.raises(TradeHistoryUnavailableError):
        history.record_open_basket(open_basket(), trade_fill())

    assert history.get_basket(BASKET_ID) is None
    assert history.list_fills(BASKET_ID) == ()


def test_entry_fill_rolls_back_when_basket_update_fails(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "history.sqlite3")
    database.migrate()
    history = SQLiteTradeHistory(database)
    opened = open_basket()
    first = trade_fill()
    history.record_open_basket(opened, first)
    with database.connect() as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_basket_update
            BEFORE UPDATE ON basket_results
            BEGIN
                SELECT RAISE(ABORT, 'forced Basket failure');
            END
            """
        )
    second = trade_fill(fill_id="fill-2", order_id="order-2", entry_number=2)
    proposed = replace(
        opened,
        entry_count=2,
        invested_notional=opened.invested_notional + second.notional,
        realized_pnl=opened.realized_pnl,
    )

    with pytest.raises(TradeHistoryUnavailableError):
        history.record_entry_fill(proposed, second)

    assert history.get_basket(BASKET_ID) == opened
    assert history.list_fills(BASKET_ID) == (first,)


def test_read_wraps_connection_close_failure() -> None:
    database = create_autospec(SQLiteDatabase, instance=True)
    connection = create_autospec(sqlite3.Connection, instance=True)
    database.connect.return_value = connection
    connection.execute.return_value.fetchone.return_value = None
    connection.close.side_effect = sqlite3.OperationalError("forced close failure")
    history = SQLiteTradeHistory(database)

    with pytest.raises(TradeHistoryUnavailableError, match="close") as raised:
        history.get_basket(BASKET_ID)

    assert isinstance(raised.value.__cause__, sqlite3.OperationalError)


def test_rollback_failure_does_not_escape_as_raw_sqlite_error() -> None:
    database = create_autospec(SQLiteDatabase, instance=True)
    connection = create_autospec(sqlite3.Connection, instance=True)
    database.connect.return_value = connection
    connection.execute.side_effect = [
        None,
        sqlite3.OperationalError("forced write failure"),
    ]
    connection.rollback.side_effect = sqlite3.OperationalError(
        "forced rollback failure"
    )
    history = SQLiteTradeHistory(database)

    with pytest.raises(TradeHistoryUnavailableError, match="write") as raised:
        history.record_open_basket(open_basket(), trade_fill())

    assert isinstance(raised.value.__cause__, sqlite3.OperationalError)


def test_read_closes_connection_when_row_mapping_fails() -> None:
    database = create_autospec(SQLiteDatabase, instance=True)
    connection = create_autospec(sqlite3.Connection, instance=True)
    database.connect.return_value = connection
    connection.execute.return_value.fetchone.return_value = {"basket_id": "not-a-uuid"}
    history = SQLiteTradeHistory(database)

    with pytest.raises(ValueError):
        history.get_basket(BASKET_ID)

    connection.close.assert_called_once_with()


def test_migration_creates_versioned_history_schema_and_indexes(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "history.sqlite3")

    database.migrate()
    database.migrate()

    with database.connect() as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        table_names = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        index_names = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        basket_columns = {
            row["name"]: row["type"]
            for row in connection.execute("PRAGMA table_info(basket_results)")
        }
        fill_columns = {
            row["name"]: row["type"]
            for row in connection.execute("PRAGMA table_info(trade_fills)")
        }

    assert version == 17
    assert "paper_runtime_lifecycle" in table_names
    assert {"basket_results", "trade_fills"} <= table_names
    assert {
        "basket_results_history_idx",
        "trade_fills_basket_time_idx",
        "trade_fills_session_time_idx",
    } <= index_names
    assert basket_columns["basket_id"] == "TEXT"
    assert basket_columns["invested_notional"] == "TEXT"
    assert basket_columns["opened_at_utc"] == "TEXT"
    assert basket_columns["leverage"] == "INTEGER"
    assert fill_columns["fill_id"] == "TEXT"
    assert fill_columns["price"] == "TEXT"
    assert fill_columns["filled_at_utc"] == "TEXT"


def test_migration_from_v4_adds_session_fill_history_index(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "history.sqlite3")
    database.migrate()

    with database.connect() as connection:
        connection.execute("DROP INDEX trade_fills_session_time_idx")
        connection.execute("PRAGMA user_version = 4")

    with database.connect() as connection:
        index_names_before = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }

    assert "trade_fills_session_time_idx" not in index_names_before

    database.migrate()

    with database.connect() as connection:
        index_names = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }

    assert "trade_fills_session_time_idx" in index_names


@pytest.mark.parametrize("version", [1, 2, 3])
def test_migration_from_legacy_schema_adds_session_fill_history_index(
    tmp_path: Path,
    version: int,
) -> None:
    database = SQLiteDatabase(tmp_path / "history.sqlite3")
    with database.connect() as connection:
        if version == 1:
            connection.execute(
                "CREATE TABLE basket_results (basket_id TEXT PRIMARY KEY)"
            )
        connection.execute(
            """
            CREATE TABLE trade_fills (
                fill_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                filled_at_utc TEXT NOT NULL
            )
            """
        )
        connection.execute(f"PRAGMA user_version = {version}")

    database.migrate()

    with database.connect() as connection:
        index_names = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        query_plan = connection.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT fill_id FROM trade_fills
            WHERE session_id = ? AND filled_at_utc >= ? AND filled_at_utc < ?
            ORDER BY filled_at_utc, fill_id
            """,
            ("session-1", "2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00"),
        ).fetchall()

    assert "trade_fills_session_time_idx" in index_names
    assert any("trade_fills_session_time_idx" in row[3] for row in query_plan)


def test_migration_rejects_future_schema_version(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "history.sqlite3")
    with database.connect() as connection:
        connection.execute("PRAGMA user_version = 18")

    with pytest.raises(ValueError, match="newer than supported"):
        database.migrate()


def test_migration_from_v1_preserves_spot_basket_with_null_leverage(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(tmp_path / "history.sqlite3")
    legacy = basket_result()
    assert legacy.closed_at_utc is not None
    with database.connect() as connection:
        connection.execute(
            """
            CREATE TABLE basket_results (
                basket_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                trade_mode TEXT NOT NULL,
                market_type TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                strategy_preset_version TEXT NOT NULL,
                opened_at_utc TEXT NOT NULL,
                closed_at_utc TEXT,
                entry_count INTEGER NOT NULL,
                invested_notional TEXT NOT NULL,
                gross_realized_pnl TEXT NOT NULL,
                trading_fees TEXT NOT NULL,
                funding_fee TEXT NOT NULL,
                net_realized_pnl TEXT NOT NULL,
                status TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO basket_results VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                str(legacy.basket_id),
                str(legacy.session_id),
                legacy.trade_mode.value,
                legacy.market_type.value,
                legacy.symbol,
                legacy.timeframe,
                legacy.strategy_preset_version,
                legacy.opened_at_utc.isoformat(),
                legacy.closed_at_utc.isoformat(),
                legacy.entry_count,
                str(legacy.invested_notional),
                # The gross and fees this database was written with; the
                # migration keeps the net, which is what actually moved.
                "20.42",
                "0.42",
                "0",
                str(legacy.realized_pnl),
                legacy.status.value,
            ),
        )
        connection.execute("PRAGMA user_version = 1")

    database.migrate()

    with database.connect() as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        leverage = connection.execute(
            "SELECT leverage FROM basket_results WHERE basket_id = ?",
            (str(legacy.basket_id),),
        ).fetchone()[0]
    assert version == 17
    assert leverage is None
    assert SQLiteTradeHistory(database).get_basket(legacy.basket_id) == legacy


def test_entry_atr_round_trips_and_an_exit_keeps_none(
    history: SQLiteTradeHistory,
) -> None:
    basket = open_basket()
    entry = trade_fill(entry_atr=Decimal("2.5"))
    exit_fill = trade_fill(
        fill_id="fill-2",
        order_id="order-2",
        side=FillSide.SELL,
        entry_number=None,
        filled_at_utc=entry.filled_at_utc + timedelta(minutes=1),
        realized_pnl=Decimal("20"),
    )

    history.record_open_basket(basket, entry)
    history.record_closed_basket(
        replace(
            basket, status=BasketStatus.CLOSED, closed_at_utc=exit_fill.filled_at_utc
        ),
        exit_fill,
    )

    stored = history.list_fills(basket.basket_id)
    assert [fill.entry_atr for fill in stored] == [Decimal("2.5"), None]


def test_migration_from_v6_keeps_fills_and_leaves_their_atr_unknown(
    tmp_path: Path,
) -> None:
    """A Fill recorded before the ATR was kept cannot invent one."""
    database = SQLiteDatabase(tmp_path / "history.sqlite3")
    database.migrate()
    history = SQLiteTradeHistory(database)
    basket = open_basket()
    legacy_fill = trade_fill()
    history.record_open_basket(basket, legacy_fill)
    with database.connect() as connection:
        connection.execute("ALTER TABLE trade_fills DROP COLUMN entry_atr")
        connection.execute("ALTER TABLE trade_fills DROP COLUMN closes_entry_number")
        connection.execute("PRAGMA user_version = 6")

    database.migrate()

    with database.connect() as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    assert version == 17
    assert history.list_fills(basket.basket_id) == (legacy_fill,)
    assert history.list_fills(basket.basket_id)[0].entry_atr is None


def test_closed_baskets_are_counted_for_their_own_session(
    history: SQLiteTradeHistory,
) -> None:
    closed = basket_result()
    other_session = replace(
        basket_result(),
        basket_id=UUID("00000000-0000-0000-0000-0000000000c1"),
        session_id=UUID("00000000-0000-0000-0000-0000000000c2"),
    )
    history.record_open_basket(
        replace(closed, status=BasketStatus.OPEN, closed_at_utc=None),
        trade_fill(),
    )
    history.record_closed_basket(
        closed,
        trade_fill(
            fill_id="fill-2",
            order_id="order-2",
            side=FillSide.SELL,
            entry_number=None,
            filled_at_utc=datetime(2026, 1, 2, tzinfo=UTC),
        ),
    )
    history.record_open_basket(
        replace(other_session, status=BasketStatus.OPEN, closed_at_utc=None),
        trade_fill(
            fill_id="fill-3",
            order_id="order-3",
            basket_id=other_session.basket_id,
            session_id=other_session.session_id,
        ),
    )

    assert history.count_closed_baskets(closed.session_id) == 1
    assert history.count_closed_baskets(other_session.session_id) == 0


def test_futures_leverage_round_trips_and_is_immutable(tmp_path: Path) -> None:
    database_path = tmp_path / "history.sqlite3"
    database = SQLiteDatabase(database_path)
    database.migrate()
    history = SQLiteTradeHistory(database)
    futures_open = replace(
        open_basket(),
        market_type=MarketType.FUTURES,
        leverage=3,
    )
    first = trade_fill()
    history.record_open_basket(futures_open, first)

    reopened = SQLiteTradeHistory(SQLiteDatabase(database_path))

    assert reopened.get_basket(futures_open.basket_id) == futures_open
    second = trade_fill(
        fill_id="fill-2",
        order_id="order-2",
        entry_number=2,
        filled_at_utc=first.filled_at_utc + timedelta(minutes=1),
    )
    changed = replace(
        futures_open,
        leverage=4,
        entry_count=2,
        invested_notional=Decimal("400"),
        realized_pnl=Decimal("-0.4"),
    )
    with pytest.raises(TradeHistoryConflictError, match="leverage"):
        reopened.record_entry_fill(changed, second)


def test_history_round_trips_exact_records_after_reopen(tmp_path: Path) -> None:
    database_path = tmp_path / "history.sqlite3"
    database = SQLiteDatabase(database_path)
    database.migrate()
    history = SQLiteTradeHistory(database)
    opened_at = datetime(2026, 1, 1, 12, 30, 45, 123456, tzinfo=UTC)
    closed_at = datetime(2026, 1, 2, 13, 31, 46, 654321, tzinfo=UTC)
    buy_fill = trade_fill(
        filled_at_utc=opened_at,
        price=Decimal("100.123456789"),
        quantity=Decimal("2.000000001"),
        notional=Decimal("200.246913678123456789"),
    )
    sell_fill = trade_fill(
        fill_id="fill-2",
        order_id="order-2",
        side=FillSide.SELL,
        entry_number=None,
        filled_at_utc=closed_at,
        price=Decimal("110.123456789"),
        quantity=Decimal("2.000000001"),
        notional=Decimal("220.246913688123456789"),
        realized_pnl=Decimal("20.000000002"),
    )
    basket = basket_result(
        opened_at_utc=opened_at,
        closed_at_utc=closed_at,
        invested_notional=buy_fill.notional,
        realized_pnl=Decimal("19.5800000018"),
    )
    open_basket = replace(
        basket,
        closed_at_utc=None,
        realized_pnl=Decimal("-0.2000000001"),
        status=BasketStatus.OPEN,
    )

    history.record_open_basket(open_basket, buy_fill)
    history.record_closed_basket(basket, sell_fill)

    reopened = SQLiteTradeHistory(SQLiteDatabase(database_path))

    assert reopened.get_basket(basket.basket_id) == basket
    assert reopened.list_fills(basket.basket_id) == (buy_fill, sell_fill)


def test_migration_to_v9_keeps_what_each_basket_realised(tmp_path: Path) -> None:
    """Fees leave the schema; the money that moved stays exactly as recorded."""
    path = tmp_path / "history.sqlite3"
    database = SQLiteDatabase(path)
    with database.connect() as connection:
        _create_v8_schema(connection)
        connection.execute(
            "INSERT INTO basket_results VALUES"
            " (?, ?, 'paper', 'spot', NULL, 'BTCUSDT', '5m', 'rsi-step-grid-v1',"
            "  '2026-01-01T00:00:00+00:00', '2026-01-02T00:00:00+00:00', 1,"
            "  '200', '20.42', '0.42', '0', '20', 'closed')",
            (str(BASKET_ID), str(SESSION_ID)),
        )
        connection.execute("PRAGMA user_version = 8")

    database.migrate()

    with database.connect() as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        realized = connection.execute(
            "SELECT realized_pnl FROM basket_results WHERE basket_id = ?",
            (str(BASKET_ID),),
        ).fetchone()[0]
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(basket_results)")
        }
        indexes = {
            row["name"] for row in connection.execute("PRAGMA index_list(trade_fills)")
        }
        broken = connection.execute("PRAGMA foreign_key_check").fetchall()

    assert version == 17
    # The stored text is carried across untouched, not recomputed.
    assert realized == "20"
    assert not columns & {"gross_realized_pnl", "trading_fees", "funding_fee"}
    assert "trade_fills_basket_time_idx" in indexes
    assert "trade_fills_session_time_idx" in indexes
    assert broken == []
    # The file it replaced is kept, because this is the first migration that
    # cannot be undone from what it leaves behind.
    assert path.with_suffix(".sqlite3.v8.bak").exists()

    database.migrate()

    with database.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 17


def _create_v8_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE basket_results (
            basket_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            trade_mode TEXT NOT NULL,
            market_type TEXT NOT NULL,
            leverage INTEGER,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            strategy_preset_version TEXT NOT NULL,
            opened_at_utc TEXT NOT NULL,
            closed_at_utc TEXT,
            entry_count INTEGER NOT NULL,
            invested_notional TEXT NOT NULL,
            gross_realized_pnl TEXT NOT NULL,
            trading_fees TEXT NOT NULL,
            funding_fee TEXT NOT NULL,
            net_realized_pnl TEXT NOT NULL,
            status TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE trade_fills (
            fill_id TEXT PRIMARY KEY,
            basket_id TEXT NOT NULL REFERENCES basket_results(basket_id),
            session_id TEXT NOT NULL,
            order_id TEXT NOT NULL,
            exchange_trade_id TEXT,
            side TEXT NOT NULL,
            entry_number INTEGER,
            filled_at_utc TEXT NOT NULL,
            price TEXT NOT NULL,
            quantity TEXT NOT NULL,
            notional TEXT NOT NULL,
            commission TEXT NOT NULL,
            commission_asset TEXT NOT NULL,
            realized_pnl TEXT NOT NULL,
            source TEXT NOT NULL,
            entry_atr TEXT,
            closes_entry_number INTEGER,
            UNIQUE (source, order_id, exchange_trade_id)
        )
        """
    )
    connection.execute(
        "CREATE INDEX trade_fills_basket_time_idx"
        " ON trade_fills (basket_id, filled_at_utc, fill_id)"
    )
    connection.execute(
        "CREATE INDEX trade_fills_session_time_idx"
        " ON trade_fills (session_id, filled_at_utc, fill_id)"
    )


def test_migration_to_v10_takes_slippage_out_of_a_session(tmp_path: Path) -> None:
    """A v9 database still carries the last execution cost; v10 does not."""
    path = tmp_path / "history.sqlite3"
    database = SQLiteDatabase(path)
    with database.connect() as connection:
        _create_v8_schema(connection)
        connection.execute(
            """
            CREATE TABLE bot_sessions (
                session_id TEXT PRIMARY KEY,
                slippage_bps TEXT NOT NULL,
                created_at_utc TEXT NOT NULL,
                ended_at_utc TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO bot_sessions VALUES"
            " (?, '5', '2026-01-01T00:00:00+00:00', NULL)",
            (str(SESSION_ID),),
        )
        connection.execute("PRAGMA user_version = 9")

    database.migrate()

    with database.connect() as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(bot_sessions)")
        }
        sessions = connection.execute("SELECT COUNT(*) FROM bot_sessions").fetchone()[0]
        broken = connection.execute("PRAGMA foreign_key_check").fetchall()

    assert version == 17
    assert "slippage_bps" not in columns
    # The Session itself survives; only what it charged is gone.
    assert sessions == 1
    assert broken == []
    assert path.with_suffix(".sqlite3.v9.bak").exists()

    database.migrate()

    with database.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 17
