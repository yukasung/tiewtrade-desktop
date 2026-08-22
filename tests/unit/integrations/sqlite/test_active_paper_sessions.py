import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from uuid import UUID

import pytest

from tests.support.paper_session_setup import (
    configured_futures_session,
    configured_spot_session,
)
from tiewtrade.application.paper_session_setup import (
    ConfiguredPaperSession,
    PaperSessionCreateOutcome,
    PaperSessionUnavailableError,
)
from tiewtrade.integrations.sqlite.active_paper_sessions import (
    SQLiteActivePaperSessions,
)
from tiewtrade.integrations.sqlite.database import (
    _V11_BOT_SESSIONS_COLUMNS,
    _V11_BOT_SESSIONS_TABLE_SQL,
    SQLiteDatabase,
)
from tiewtrade.integrations.sqlite.paper_runtime_lifecycle import (
    SQLitePaperRuntimeLifecycle,
)


def test_retire_stopped_session_removes_only_active_registration(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    database.migrate()
    store = SQLiteActivePaperSessions(database)
    session = configured_spot_session()
    store.create(session)
    SQLitePaperRuntimeLifecycle(database).mark_stopped(
        session.config.session_id, datetime(2026, 8, 8, 5, 0, tzinfo=UTC)
    )

    store.retire(session.config.session_id, datetime(2026, 8, 8, 5, 1, tzinfo=UTC))

    assert store.get_active() is None
    with database.connect() as connection:
        row = connection.execute(
            "SELECT ended_at_utc FROM bot_sessions WHERE session_id = ?",
            (str(session.config.session_id),),
        ).fetchone()
    assert row["ended_at_utc"] == "2026-08-08T05:01:00+00:00"


def test_retire_releases_a_session_whose_runtime_never_started(tmp_path: Path) -> None:
    """A Session with no lifecycle row has nothing to stop, so it can go.

    Holding on to one stranded it: startup refuses to run a Session with no
    marker, so refusing to retire it too left Start Paper Bot failing every
    time with no way out of it.
    """
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    database.migrate()
    store = SQLiteActivePaperSessions(database)
    session = configured_spot_session()
    store.create(session)

    store.retire(session.config.session_id, datetime(2026, 8, 8, 5, 1, tzinfo=UTC))

    assert store.get_active() is None


def test_retire_rejects_running_session_without_mutation(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    database.migrate()
    store = SQLiteActivePaperSessions(database)
    session = configured_spot_session()
    store.create(session)
    SQLitePaperRuntimeLifecycle(database).mark_running(
        session.config.session_id, datetime(2026, 8, 8, 5, 0, tzinfo=UTC)
    )

    with pytest.raises(PaperSessionUnavailableError, match="cannot be retired"):
        store.retire(session.config.session_id, datetime(2026, 8, 8, 5, 1, tzinfo=UTC))

    assert store.get_active() == session


def test_create_and_restart_round_trip_exact_spot_session(tmp_path: Path) -> None:
    database_path = tmp_path / "tiewtrade.sqlite3"
    database = SQLiteDatabase(database_path)
    database.migrate()
    store = SQLiteActivePaperSessions(database)
    session = configured_spot_session()

    first = store.create(session)
    restarted = SQLiteActivePaperSessions(SQLiteDatabase(database_path)).get_active()

    assert first == PaperSessionCreateOutcome(session=session, created=True)
    assert restarted == session


def test_create_and_restart_round_trip_exact_ethusdt_spot_session(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "tiewtrade.sqlite3"
    database = SQLiteDatabase(database_path)
    database.migrate()
    store = SQLiteActivePaperSessions(database)
    btcusdt_session = configured_spot_session()
    session = replace(
        btcusdt_session,
        market_data=replace(btcusdt_session.market_data, symbol="ETHUSDT"),
    )

    store.create(session)

    restarted = SQLiteActivePaperSessions(SQLiteDatabase(database_path)).get_active()

    assert restarted == session


def test_create_and_restart_round_trip_exact_futures_session(tmp_path: Path) -> None:
    database_path = tmp_path / "tiewtrade.sqlite3"
    database = SQLiteDatabase(database_path)
    database.migrate()
    store = SQLiteActivePaperSessions(database)
    session = configured_futures_session(leverage=5)

    first = store.create(session)
    restarted = SQLiteActivePaperSessions(SQLiteDatabase(database_path)).get_active()

    assert first == PaperSessionCreateOutcome(session=session, created=True)
    assert restarted == session


def test_second_active_session_returns_existing_record(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    database.migrate()
    store = SQLiteActivePaperSessions(database)
    existing = configured_spot_session()
    store.create(existing)

    outcome = store.create(
        configured_futures_session(
            session_id=UUID("00000000-0000-0000-0000-000000000124")
        )
    )

    assert outcome == PaperSessionCreateOutcome(session=existing, created=False)


def test_get_active_returns_none_when_database_has_no_session(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    database.migrate()

    assert SQLiteActivePaperSessions(database).get_active() is None


@pytest.mark.parametrize(
    ("column", "unsupported_value"),
    [
        pytest.param("symbol", "DOGEUSDT", id="symbol"),
        pytest.param("preset_version", "rsi-step-grid-v2", id="preset-version"),
    ],
)
def test_get_active_fails_closed_for_unsupported_durable_session_identity(
    tmp_path: Path,
    column: str,
    unsupported_value: str,
) -> None:
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    database.migrate()
    store = SQLiteActivePaperSessions(database)
    store.create(configured_spot_session())
    with database.connect() as connection:
        connection.execute(
            f"UPDATE bot_sessions SET {column} = ?",
            (unsupported_value,),
        )

    with pytest.raises(
        PaperSessionUnavailableError,
        match="Active Paper Session read failed",
    ):
        store.get_active()


def test_migration_from_v2_preserves_trade_history_record(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    with database.connect() as connection:
        connection.execute("CREATE TABLE basket_results (basket_id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO basket_results VALUES ('basket-sentinel')")
        connection.execute("PRAGMA user_version = 2")

    database.migrate()

    with database.connect() as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        basket_id = connection.execute(
            "SELECT basket_id FROM basket_results"
        ).fetchone()[0]
        bot_sessions_exists = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'bot_sessions'
            """
        ).fetchone()

    assert version == 15
    assert basket_id == "basket-sentinel"
    assert bot_sessions_exists is not None


def test_migration_to_v11_takes_the_spot_ratio_out_of_a_session(
    tmp_path: Path,
) -> None:
    """A Session started under a ratio keeps running, trading all of it now."""
    path = tmp_path / "tiewtrade.sqlite3"
    database = SQLiteDatabase(path)
    database.migrate()
    store = SQLiteActivePaperSessions(database)
    session = configured_spot_session()
    store.create(session)
    # The lifecycle points at the Session, so the rebuild has a reference to
    # keep pointing somewhere; without one the migration passes by luck.
    SQLitePaperRuntimeLifecycle(database).mark_stopped(
        session.config.session_id, datetime(2026, 8, 8, 5, 0, tzinfo=UTC)
    )
    with database.connect() as connection:
        _restore_v10_bot_sessions(connection)

    database.migrate()

    with database.connect() as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(bot_sessions)")
        }
        indexes = {
            row["name"] for row in connection.execute("PRAGMA index_list(bot_sessions)")
        }
        broken_references = connection.execute("PRAGMA foreign_key_check").fetchall()
        lifecycle_target = next(
            row["table"]
            for row in connection.execute(
                "PRAGMA foreign_key_list(paper_runtime_lifecycle)"
            )
        )

    assert version == 15
    assert "spot_trading_capital_ratio" not in columns
    # The one guarantee the rebuilt table has to keep carrying.
    assert "bot_sessions_single_active_idx" in indexes
    # The lifecycle still points at the Session, not at a table left behind.
    assert broken_references == []
    assert lifecycle_target == "bot_sessions"
    assert store.get_active() == session
    assert path.with_suffix(".sqlite3.v10.bak").exists()


@pytest.mark.parametrize(
    ("session", "total", "expected_per_entry"),
    [
        pytest.param(configured_spot_session(), "200000", Decimal("20000"), id="spot"),
        pytest.param(
            configured_futures_session(), "200000", Decimal("10000"), id="futures"
        ),
    ],
)
def test_migration_to_v12_keeps_every_session_trading_the_size_it_was(
    tmp_path: Path,
    session: ConfiguredPaperSession,
    total: str,
    expected_per_entry: Decimal,
) -> None:
    """A wallet total becomes what it was already spending on one Entry.

    Renamed rather than converted, an old 200,000 would come back as two
    hundred thousand per Entry and trade a hundred times the size it was
    started with. Spot divided the total by its Entries; Futures took half of
    it first.
    """
    path = tmp_path / "tiewtrade.sqlite3"
    database = SQLiteDatabase(path)
    database.migrate()
    store = SQLiteActivePaperSessions(database)
    store.create(session)
    with database.connect() as connection:
        _restore_v11_bot_sessions(connection, total)

    database.migrate()

    active = store.get_active()
    assert active is not None
    assert active.config.capital_per_entry == expected_per_entry
    # Everything else about the Session is carried across untouched.
    assert active.config.session_id == session.config.session_id
    assert active.config.entry_policy == session.config.entry_policy


def test_migration_to_v15_drops_the_rate_and_keeps_the_session(
    tmp_path: Path,
) -> None:
    """The Maintenance Margin Rate went with the Liquidation it was for.

    Nothing computes a Liquidation Price any more, so the rate a Session was
    started with is not a value to keep — and the Session it was started for
    has to survive losing it.
    """
    path = tmp_path / "tiewtrade.sqlite3"
    database = SQLiteDatabase(path)
    database.migrate()
    store = SQLiteActivePaperSessions(database)
    session = configured_futures_session()
    store.create(session)
    with database.connect() as connection:
        _restore_v14_bot_sessions(connection)
        assert _has_column(connection, "futures_maintenance_margin_rate")

    database.migrate()

    with database.connect() as connection:
        assert not _has_column(connection, "futures_maintenance_margin_rate")
    active = store.get_active()
    assert active is not None
    assert active.config.session_id == session.config.session_id
    assert active.config.capital_per_entry == session.config.capital_per_entry
    assert active.config.futures_policy == session.config.futures_policy


def test_migration_to_v15_ignores_a_half_built_table_left_behind(
    tmp_path: Path,
) -> None:
    """A rebuild that was interrupted once must not be adopted the next time.

    CREATE IF NOT EXISTS would take the abandoned table for its own and
    rename a stale shape over the real Sessions, which is how a database
    carrying one from an earlier interrupted migration lost the column drop.
    """
    path = tmp_path / "tiewtrade.sqlite3"
    database = SQLiteDatabase(path)
    database.migrate()
    store = SQLiteActivePaperSessions(database)
    session = configured_futures_session()
    store.create(session)
    with database.connect() as connection:
        _restore_v14_bot_sessions(connection)
        # Exactly what an interrupted rebuild leaves: the old shape, empty.
        connection.execute(
            "CREATE TABLE new_bot_sessions AS SELECT * FROM bot_sessions"
        )
        connection.execute("DELETE FROM new_bot_sessions")

    database.migrate()

    with database.connect() as connection:
        assert not _has_column(connection, "futures_maintenance_margin_rate")
    active = store.get_active()
    assert active is not None
    assert active.config.session_id == session.config.session_id


def _has_column(connection: sqlite3.Connection, column: str) -> bool:
    return any(
        row["name"] == column
        for row in connection.execute("PRAGMA table_info(bot_sessions)")
    )


def _restore_v14_bot_sessions(connection: sqlite3.Connection) -> None:
    """Put the rate column back, in the shape v14 wrote it."""
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute(
        """
        ALTER TABLE bot_sessions
        ADD COLUMN futures_maintenance_margin_rate TEXT
        """
    )
    connection.execute(
        """
        UPDATE bot_sessions SET futures_maintenance_margin_rate = '0.005'
        WHERE futures_policy_version IS NOT NULL
        """
    )
    connection.execute("PRAGMA user_version = 14")


def _restore_v11_bot_sessions(connection: sqlite3.Connection, total: str) -> None:
    """Put the Session back in the shape v11 wrote it: a wallet total."""
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute(_V11_BOT_SESSIONS_TABLE_SQL.format(table="v11_bot_sessions"))
    connection.execute(
        f"""
        INSERT INTO v11_bot_sessions ({_V11_BOT_SESSIONS_COLUMNS})
        SELECT
            session_id, trade_mode, market_type, symbol, timeframe,
            preset_version, '{total}', max_entries,
            futures_policy_version, futures_leverage,
            futures_trading_capital_ratio, futures_collateral_buffer_ratio,
            CASE WHEN futures_policy_version IS NULL THEN NULL ELSE '0.005' END,
            futures_margin_mode,
            futures_position_mode, created_at_utc, ended_at_utc
        FROM bot_sessions
        """
    )
    connection.execute("DROP TABLE bot_sessions")
    connection.execute("ALTER TABLE v11_bot_sessions RENAME TO bot_sessions")
    connection.execute(
        """
        CREATE UNIQUE INDEX bot_sessions_single_active_idx
        ON bot_sessions ((1))
        WHERE ended_at_utc IS NULL
        """
    )
    connection.execute("PRAGMA user_version = 11")


def _restore_v10_bot_sessions(connection: sqlite3.Connection) -> None:
    """Put a Session back in the shape v10 wrote it, ratio column and all.

    Built beside the migrated table and renamed over it, the way the migration
    itself has to, so the lifecycle's reference is never pointed at a table
    that is about to be dropped. Enforcement is off for the swap.
    """
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute(
        """
        CREATE TABLE v10_bot_sessions (
            session_id TEXT PRIMARY KEY,
            trade_mode TEXT NOT NULL,
            market_type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            preset_version TEXT NOT NULL,
            available_capital TEXT NOT NULL,
            max_entries INTEGER NOT NULL,
            spot_trading_capital_ratio TEXT,
            futures_policy_version TEXT,
            futures_leverage INTEGER,
            futures_trading_capital_ratio TEXT,
            futures_collateral_buffer_ratio TEXT,
            futures_maintenance_margin_rate TEXT,
            futures_margin_mode TEXT,
            futures_position_mode TEXT,
            created_at_utc TEXT NOT NULL,
            ended_at_utc TEXT,
            CHECK (
                (
                    market_type = 'spot'
                    AND spot_trading_capital_ratio IS NOT NULL
                    AND futures_policy_version IS NULL
                )
                OR
                (
                    market_type = 'futures'
                    AND spot_trading_capital_ratio IS NULL
                    AND futures_policy_version IS NOT NULL
                )
            )
        )
        """
    )
    connection.execute(
        """
        INSERT INTO v10_bot_sessions (
            session_id, trade_mode, market_type, symbol, timeframe,
            preset_version, available_capital, max_entries,
            spot_trading_capital_ratio, futures_policy_version, futures_leverage,
            futures_trading_capital_ratio, futures_collateral_buffer_ratio,
            futures_maintenance_margin_rate, futures_margin_mode,
            futures_position_mode, created_at_utc, ended_at_utc
        )
        SELECT
            session_id, trade_mode, market_type, symbol, timeframe,
            preset_version, capital_per_entry * max_entries, max_entries,
            '0.8', futures_policy_version, futures_leverage,
            futures_trading_capital_ratio, futures_collateral_buffer_ratio,
            CASE WHEN futures_policy_version IS NULL THEN NULL ELSE '0.005' END,
            futures_margin_mode,
            futures_position_mode, created_at_utc, ended_at_utc
        FROM bot_sessions
        """
    )
    connection.execute("DROP TABLE bot_sessions")
    connection.execute("ALTER TABLE v10_bot_sessions RENAME TO bot_sessions")
    connection.execute(
        """
        CREATE UNIQUE INDEX bot_sessions_single_active_idx
        ON bot_sessions ((1))
        WHERE ended_at_utc IS NULL
        """
    )
    connection.execute("PRAGMA user_version = 10")


class _CommitFailingConnection(sqlite3.Connection):
    def commit(self) -> None:
        raise sqlite3.OperationalError("injected commit failure")


class _CommitFailingDatabase(SQLiteDatabase):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self._failure_path = path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._failure_path,
            factory=_CommitFailingConnection,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def test_commit_failure_rolls_back_active_session(tmp_path: Path) -> None:
    database_path = tmp_path / "tiewtrade.sqlite3"
    database = SQLiteDatabase(database_path)
    database.migrate()
    store = SQLiteActivePaperSessions(_CommitFailingDatabase(database_path))

    with pytest.raises(PaperSessionUnavailableError, match="write failed"):
        store.create(configured_spot_session())

    assert SQLiteActivePaperSessions(database).get_active() is None


def test_concurrent_create_keeps_exactly_one_active_session(tmp_path: Path) -> None:
    database_path = tmp_path / "tiewtrade.sqlite3"
    database = SQLiteDatabase(database_path)
    database.migrate()
    barrier = Barrier(2)
    sessions = (
        configured_spot_session(
            session_id=UUID("00000000-0000-0000-0000-000000000125")
        ),
        configured_futures_session(
            session_id=UUID("00000000-0000-0000-0000-000000000126")
        ),
    )

    def create(session: ConfiguredPaperSession) -> PaperSessionCreateOutcome:
        barrier.wait()
        store = SQLiteActivePaperSessions(SQLiteDatabase(database_path))
        return store.create(session)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(create, sessions))

    active = SQLiteActivePaperSessions(database).get_active()
    with database.connect() as connection:
        active_count = connection.execute(
            "SELECT COUNT(*) FROM bot_sessions WHERE ended_at_utc IS NULL"
        ).fetchone()[0]

    assert sum(outcome.created for outcome in outcomes) == 1
    assert active is not None
    assert {outcome.session for outcome in outcomes} == {active}
    assert active_count == 1


def test_database_constraint_rejects_a_second_active_session(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "tiewtrade.sqlite3")
    database.migrate()
    SQLiteActivePaperSessions(database).create(configured_spot_session())

    with database.connect() as connection:
        existing = connection.execute("SELECT * FROM bot_sessions").fetchone()
        assert existing is not None
        duplicate = (
            "00000000-0000-0000-0000-000000000127",
            *tuple(existing)[1:],
        )
        placeholders = ", ".join("?" for _ in duplicate)

        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            connection.execute(
                f"INSERT INTO bot_sessions VALUES ({placeholders})",
                duplicate,
            )
