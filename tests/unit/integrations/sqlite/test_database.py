import sqlite3
from pathlib import Path

import pytest

from tiewtrade.integrations.sqlite.database import (
    _V17_BOT_SESSIONS_TABLE_SQL,
    SQLiteDatabase,
    UnsupportedDatabaseSchemaError,
)


def test_connect_enables_wal_and_explicit_busy_timeout(tmp_path: Path) -> None:
    connection = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3").connect()
    try:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        connection.close()

    assert journal_mode == "wal"
    assert busy_timeout == 5_000


def test_reader_can_read_committed_snapshot_while_writer_is_active(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    writer = database.connect()
    reader = database.connect()
    try:
        writer.execute("CREATE TABLE concurrency_probe (value TEXT NOT NULL)")
        writer.commit()
        reader.execute("PRAGMA busy_timeout = 50")

        writer.execute("BEGIN EXCLUSIVE")
        writer.execute("INSERT INTO concurrency_probe (value) VALUES ('uncommitted')")

        rows = reader.execute(
            "SELECT value FROM concurrency_probe ORDER BY value"
        ).fetchall()
    finally:
        writer.rollback()
        reader.close()
        writer.close()

    assert rows == []


def test_migrate_reports_newer_schema_with_version_facts(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    with database.connect() as connection:
        connection.execute("PRAGMA user_version = 19")

    with pytest.raises(UnsupportedDatabaseSchemaError) as caught:
        database.migrate()

    assert caught.value.database_version == 19
    assert caught.value.supported_version == 18


def test_migrate_creates_cooldown_waivers_table_on_a_fresh_database(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")

    database.migrate()

    with database.connect() as connection:
        connection.execute(
            "SELECT basket_id, waived_pair, waived_at_utc FROM paper_cooldown_waivers"
        )


def test_migrate_adds_cooldown_waivers_table_to_an_older_database(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    with database.connect() as connection:
        connection.execute("PRAGMA user_version = 12")

    database.migrate()

    with database.connect() as connection:
        connection.execute(
            "SELECT basket_id, waived_pair, waived_at_utc FROM paper_cooldown_waivers"
        )


def test_migrate_replaces_a_v13_waiver_table_keyed_by_month(
    tmp_path: Path,
) -> None:
    """A month named when a Cooldown lapsed; nothing lapses now, so it goes."""
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    with database.connect() as connection:
        connection.execute(
            """
            CREATE TABLE paper_cooldown_waivers (
                basket_id TEXT PRIMARY KEY,
                completed_pair_month INTEGER NOT NULL,
                waived_at_utc TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO paper_cooldown_waivers "
            "(basket_id, completed_pair_month, waived_at_utc) "
            "VALUES ('basket', 24313, '2026-01-25T00:00:00+00:00')"
        )
        connection.execute("PRAGMA user_version = 13")

    database.migrate()

    with database.connect() as connection:
        rows = connection.execute(
            "SELECT basket_id, waived_pair, waived_at_utc FROM paper_cooldown_waivers"
        ).fetchall()

    assert rows == []


def test_migrate_v5_rebuilds_lifecycle_constraint_for_blocked_state(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    with database.connect() as connection:
        connection.execute("CREATE TABLE bot_sessions (session_id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO bot_sessions (session_id) VALUES ('session')")
        connection.execute(
            """
            CREATE TABLE paper_runtime_lifecycle (
                session_id TEXT PRIMARY KEY,
                state TEXT NOT NULL CHECK (state IN ('running', 'stopped')),
                observed_at_utc TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO paper_runtime_lifecycle (session_id, state, observed_at_utc)
            VALUES ('session', 'stopped', '2026-08-08T00:00:00+00:00')
            """
        )
        connection.execute("PRAGMA user_version = 5")

    database.migrate()

    with database.connect() as connection:
        states = [
            row["state"]
            for row in connection.execute(
                "SELECT state FROM paper_runtime_lifecycle ORDER BY session_id"
            )
        ]
        connection.execute(
            """
            UPDATE paper_runtime_lifecycle
            SET state = 'blocked', observed_at_utc = '2026-08-08T00:01:00+00:00'
            WHERE session_id = 'session'
            """
        )

    assert states == ["stopped"]


def test_clear_all_data_empties_every_table_and_keeps_the_schema_version(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    database.migrate()

    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO bot_sessions (
                session_id, trade_mode, market_type, symbol, timeframe,
                preset_version, capital_per_entry, max_entries,
                created_at_utc
            ) VALUES (
                'session', 'paper', 'spot', 'BTCUSDT', '5m',
                'v1', '250', 4,
                '2026-01-01T00:00:00+00:00'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO basket_results (
                basket_id, session_id, trade_mode, market_type, symbol,
                timeframe, strategy_preset_version, opened_at_utc,
                entry_count, invested_notional, realized_pnl, status
            ) VALUES (
                'basket', 'session', 'paper', 'spot', 'BTCUSDT', '5m',
                'v1', '2026-01-01T00:00:00+00:00', 1, '250', '0', 'open'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO trade_fills (
                fill_id, basket_id, session_id, order_id, side,
                entry_number, filled_at_utc, price, quantity, notional,
                realized_pnl, source
            ) VALUES (
                'fill', 'basket', 'session', 'order', 'buy',
                1, '2026-01-01T00:00:00+00:00', '100', '1', '100',
                '0', 'paper_executor'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO paper_runtime_lifecycle (session_id, state, observed_at_utc)
            VALUES ('session', 'running', '2026-01-01T00:00:00+00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO paper_cooldown_waivers (basket_id, waived_pair, waived_at_utc)
            VALUES ('basket', 1, '2026-01-01T00:00:00+00:00')
            """
        )

    database.clear_all_data()

    with database.connect() as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "bot_sessions",
                "basket_results",
                "trade_fills",
                "paper_runtime_lifecycle",
                "paper_cooldown_waivers",
            )
        }

    assert version == SQLiteDatabase._SCHEMA_VERSION
    assert counts == {
        "bot_sessions": 0,
        "basket_results": 0,
        "trade_fills": 0,
        "paper_runtime_lifecycle": 0,
        "paper_cooldown_waivers": 0,
    }


_V16_BOT_SESSIONS_TABLE_SQL = """
    CREATE TABLE bot_sessions (
        session_id TEXT PRIMARY KEY,
        trade_mode TEXT NOT NULL CHECK (trade_mode = 'paper'),
        market_type TEXT NOT NULL CHECK (market_type IN ('spot', 'futures')),
        symbol TEXT NOT NULL,
        timeframe TEXT NOT NULL,
        preset_version TEXT NOT NULL,
        capital_per_entry TEXT NOT NULL,
        max_entries INTEGER NOT NULL CHECK (
            max_entries BETWEEN 2 AND 20 AND max_entries % 2 = 0
        ),
        futures_policy_version TEXT,
        futures_leverage INTEGER,
        futures_trading_capital_ratio TEXT,
        futures_collateral_buffer_ratio TEXT,
        futures_margin_mode TEXT,
        futures_position_mode TEXT,
        created_at_utc TEXT NOT NULL,
        ended_at_utc TEXT
    )
"""
"""bot_sessions as v16 left it: no Cooldown of the Session's own."""


def _v16_bot_session(connection: sqlite3.Connection) -> None:
    """One Spot Session as v16 wrote it, before a Cooldown could be chosen."""
    connection.execute(
        """
        INSERT INTO bot_sessions (
            session_id, trade_mode, market_type, symbol, timeframe,
            preset_version, capital_per_entry, max_entries, created_at_utc
        ) VALUES (
            'session', 'paper', 'spot', 'BTCUSDT', '5m',
            'rsi-step-grid-v1', '250', 4, '2026-01-01T00:00:00+00:00'
        )
        """
    )


def test_migrate_gives_an_older_session_the_longest_cooldown(
    tmp_path: Path,
) -> None:
    """Every Session written before the choice existed waits to be cancelled."""
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    database.migrate()
    with database.connect() as connection:
        connection.execute("DROP TABLE bot_sessions")
        connection.execute(_V16_BOT_SESSIONS_TABLE_SQL)
        _v16_bot_session(connection)
        connection.execute("PRAGMA user_version = 16")

    database.migrate()

    with database.connect() as connection:
        row = connection.execute("SELECT cooldown_days FROM bot_sessions").fetchone()

    # v17 called this "until cancelled"; v18 says the same thing as the
    # longest wait there is, which is the closest choice that remains.
    assert row["cooldown_days"] == 30


@pytest.mark.parametrize("days", [0, 31, -1])
def test_a_session_row_cannot_state_days_the_policy_would_reject(
    tmp_path: Path, days: int
) -> None:
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    database.migrate()

    with database.connect() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO bot_sessions (
                    session_id, trade_mode, market_type, symbol, timeframe,
                    preset_version, capital_per_entry, max_entries,
                    cooldown_days, created_at_utc
                ) VALUES (
                    'session', 'paper', 'spot', 'BTCUSDT', '5m',
                    'rsi-step-grid-v1', '250', 4, ?,
                    '2026-01-01T00:00:00+00:00'
                )
                """,
                (days,),
            )


@pytest.mark.parametrize(
    ("mode", "days", "expected"),
    [
        ("until_cancelled", None, 30),
        ("off", None, None),
        ("duration", 3, 3),
    ],
)
def test_migrate_says_a_v17_cooldown_as_the_days_it_waits(
    tmp_path: Path, mode: str, days: int | None, expected: int | None
) -> None:
    """A Cooldown is days or nothing now, so the three older names collapse."""
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    database.migrate()
    with database.connect() as connection:
        connection.execute("DROP TABLE bot_sessions")
        connection.execute(_V17_BOT_SESSIONS_TABLE_SQL.format(table="bot_sessions"))
        connection.execute(
            """
            INSERT INTO bot_sessions (
                session_id, trade_mode, market_type, symbol, timeframe,
                preset_version, capital_per_entry, max_entries,
                cooldown_mode, cooldown_days, created_at_utc
            ) VALUES (
                'session', 'paper', 'spot', 'BTCUSDT', '5m',
                'rsi-step-grid-v1', '250', 4, ?, ?,
                '2026-01-01T00:00:00+00:00'
            )
            """,
            (mode, days),
        )
        connection.execute("PRAGMA user_version = 17")

    database.migrate()

    with database.connect() as connection:
        row = connection.execute("SELECT * FROM bot_sessions").fetchone()

    assert row["cooldown_days"] == expected
    assert "cooldown_mode" not in row.keys()


def test_a_rebuild_that_names_no_cooldown_leaves_the_longest_one(
    tmp_path: Path,
) -> None:
    """An older Session must never come back with its Cooldown switched off.

    Migrations before v17 rebuild this table without naming a Cooldown at all,
    because none existed when they were written. The column's default is what
    answers for them, and it has to answer with the most cautious Cooldown
    there is rather than with none.
    """
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    database.migrate()

    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO bot_sessions (
                session_id, trade_mode, market_type, symbol, timeframe,
                preset_version, capital_per_entry, max_entries, created_at_utc
            ) VALUES (
                'session', 'paper', 'spot', 'BTCUSDT', '5m',
                'rsi-step-grid-v1', '250', 4, '2026-01-01T00:00:00+00:00'
            )
            """
        )
        days = connection.execute("SELECT cooldown_days FROM bot_sessions").fetchone()[
            "cooldown_days"
        ]

    assert days == 30
