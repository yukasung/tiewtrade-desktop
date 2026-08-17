import shutil
import sqlite3
from decimal import Decimal
from pathlib import Path


class UnsupportedSQLiteError(RuntimeError):
    """SQLite too old to drop a column, which the fee migration must do."""


def _require_drop_column_support() -> None:
    if sqlite3.sqlite_version_info < (3, 35):
        raise UnsupportedSQLiteError(
            f"SQLite 3.35 or newer is required to migrate this database, "
            f"and this build has {sqlite3.sqlite_version}"
        )


class UnsupportedDatabaseSchemaError(ValueError):
    def __init__(self, database_version: int, supported_version: int) -> None:
        super().__init__("database schema is newer than supported")
        self.database_version = database_version
        self.supported_version = supported_version


class SQLiteDatabase:
    _SCHEMA_VERSION = 12
    _BUSY_TIMEOUT_MS = 5_000

    def __init__(self, path: Path) -> None:
        self._path = path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {self._BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _keep_a_copy(self, connection: sqlite3.Connection, version: int) -> None:
        """A copy of the file before the first migration that discards data.

        Every migration before this one only ever added. This one drops the
        fee columns, so the shape it leaves cannot be undone from the file it
        leaves, and a trader's own record is what is in it.
        """
        backup = self._path.with_suffix(f"{self._path.suffix}.v{version}.bak")
        if backup.exists() or not self._path.exists():
            return
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        shutil.copy2(self._path, backup)

    def migrate(self) -> None:
        connection = self.connect()
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > self._SCHEMA_VERSION:
                raise UnsupportedDatabaseSchemaError(version, self._SCHEMA_VERSION)
            if version == self._SCHEMA_VERSION:
                return
            if 1 <= version <= 9:
                _require_drop_column_support()
            if 1 <= version <= 11:
                self._keep_a_copy(connection, version)
                # v11 drops the table paper_runtime_lifecycle points at and
                # puts an identical one back under the same name. Enforcement
                # would read the moment in between as an orphaned lifecycle,
                # so it is off for the migration and the connection is closed
                # before anything else can use it. Only a transaction-free
                # moment can switch it, which is why it is switched here.
                connection.execute("PRAGMA foreign_keys = OFF")

            with connection:
                if version == 0:
                    _create_schema(connection)
                elif version == 1:
                    connection.execute(
                        """
                        ALTER TABLE basket_results
                        ADD COLUMN leverage INTEGER
                        CHECK (leverage IS NULL OR leverage BETWEEN 1 AND 5)
                        """
                    )
                if version in {0, 1, 2}:
                    _create_bot_sessions_schema(connection)
                if version in {0, 1, 2, 3}:
                    _create_paper_runtime_lifecycle_schema(connection)
                if version == 5:
                    _migrate_paper_runtime_lifecycle_v5(connection)
                if version in {1, 2, 3, 4} and _table_exists(connection, "trade_fills"):
                    _create_chart_history_indexes(connection)
                if version in {1, 2, 3, 4, 5, 6} and _column_is_missing(
                    connection, "trade_fills", "entry_atr"
                ):
                    connection.execute(
                        "ALTER TABLE trade_fills ADD COLUMN entry_atr TEXT"
                    )
                if version in {1, 2, 3, 4, 5, 6, 7} and _column_is_missing(
                    connection, "trade_fills", "closes_entry_number"
                ):
                    connection.execute(
                        "ALTER TABLE trade_fills ADD COLUMN closes_entry_number INTEGER"
                    )
                if 1 <= version <= 8:
                    _migrate_drop_fees_v9(connection)
                if 1 <= version <= 9:
                    _migrate_drop_slippage_v10(connection)
                if 1 <= version <= 10:
                    _migrate_drop_spot_ratio_v11(connection)
                if 1 <= version <= 11:
                    _migrate_capital_per_entry_v12(connection)
                connection.execute(f"PRAGMA user_version = {self._SCHEMA_VERSION}")
        finally:
            connection.close()


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS basket_results (
            basket_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            trade_mode TEXT NOT NULL,
            market_type TEXT NOT NULL,
            leverage INTEGER CHECK (
                leverage IS NULL OR leverage BETWEEN 1 AND 5
            ),
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            strategy_preset_version TEXT NOT NULL,
            opened_at_utc TEXT NOT NULL,
            closed_at_utc TEXT,
            entry_count INTEGER NOT NULL CHECK (entry_count > 0),
            invested_notional TEXT NOT NULL,
            realized_pnl TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('open', 'closed'))
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS trade_fills (
            fill_id TEXT PRIMARY KEY,
            basket_id TEXT NOT NULL REFERENCES basket_results(basket_id),
            session_id TEXT NOT NULL,
            order_id TEXT NOT NULL,
            exchange_trade_id TEXT,
            side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
            entry_number INTEGER,
            filled_at_utc TEXT NOT NULL,
            price TEXT NOT NULL,
            quantity TEXT NOT NULL,
            notional TEXT NOT NULL,
            realized_pnl TEXT NOT NULL,
            source TEXT NOT NULL CHECK (
                source IN ('paper_executor', 'binance')
            ),
            -- The ATR an Entry was read against, which its Basket's take
            -- profit is derived from. NULL for an Exit, and for Entries
            -- recorded before it was kept.
            entry_atr TEXT,
            -- The Entry this Exit sold, when it sold one rather than the
            -- whole Basket. NULL for an Entry, a stop loss and a manual close.
            closes_entry_number INTEGER,
            UNIQUE (source, order_id, exchange_trade_id)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS basket_results_history_idx
        ON basket_results (opened_at_utc DESC, basket_id DESC)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS trade_fills_basket_time_idx
        ON trade_fills (basket_id, filled_at_utc, fill_id)
        """
    )
    _create_chart_history_indexes(connection)


def _create_chart_history_indexes(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS trade_fills_session_time_idx
        ON trade_fills (session_id, filled_at_utc, fill_id)
        """
    )


def _migrate_drop_fees_v9(connection: sqlite3.Connection) -> None:
    """Take the fee columns out, keeping what each Basket actually realised.

    net_realized_pnl is renamed rather than recomputed: it is the money that
    moved, and it stays true of the trades it records however the simulation
    charges from here on. What is lost is the breakdown of a past result into
    gross and fees, which is the concept being removed.

    Each table is guarded on its own because a database created before this
    version may have reached here through _create_schema or
    _create_bot_sessions_schema, which already emit the shape without fees.
    """
    if _has_column(connection, "basket_results", "trading_fees"):
        connection.execute(
            "ALTER TABLE basket_results RENAME COLUMN net_realized_pnl TO realized_pnl"
        )
        connection.execute("ALTER TABLE basket_results DROP COLUMN gross_realized_pnl")
        connection.execute("ALTER TABLE basket_results DROP COLUMN trading_fees")
        connection.execute("ALTER TABLE basket_results DROP COLUMN funding_fee")
    if _has_column(connection, "trade_fills", "commission"):
        connection.execute("ALTER TABLE trade_fills DROP COLUMN commission")
        connection.execute("ALTER TABLE trade_fills DROP COLUMN commission_asset")
    if _has_column(connection, "bot_sessions", "fee_rate"):
        connection.execute("ALTER TABLE bot_sessions DROP COLUMN fee_rate")


def _migrate_drop_slippage_v10(connection: sqlite3.Connection) -> None:
    """Take the last execution cost out of a Session's configuration.

    Nothing is renamed or recomputed: slippage only ever moved the price a
    fill was given, which is already written into every Fill that was taken.
    """
    if _has_column(connection, "bot_sessions", "slippage_bps"):
        connection.execute("ALTER TABLE bot_sessions DROP COLUMN slippage_bps")


def _migrate_drop_spot_ratio_v11(connection: sqlite3.Connection) -> None:
    """Take the Spot trading ratio out of a Session's configuration.

    A Spot Session now trades the capital it was given, so the ratio it was
    started with is not a smaller number to keep — it is a rule that no longer
    exists. Sessions written before this migrate to trading all of it.

    The table is rebuilt rather than altered: the column is named in the
    table's own CHECK, which SQLite will not let a DROP COLUMN leave dangling.
    Guarded on the column because a database created by
    _create_bot_sessions_schema already has the shape this leaves.
    """
    if not _has_column(connection, "bot_sessions", "spot_trading_capital_ratio"):
        return
    # The new table is raised beside the old one and takes its name only once
    # the old one is gone. Renaming the old one out of the way instead would
    # move paper_runtime_lifecycle's reference along with it, leaving the
    # lifecycle pointing at a table this migration then drops.
    connection.execute(_V11_BOT_SESSIONS_TABLE_SQL.format(table="new_bot_sessions"))
    connection.execute(
        f"""
        INSERT INTO new_bot_sessions ({_V11_BOT_SESSIONS_COLUMNS})
        SELECT {_V11_BOT_SESSIONS_COLUMNS} FROM bot_sessions
        """
    )
    connection.execute("DROP TABLE bot_sessions")
    connection.execute("ALTER TABLE new_bot_sessions RENAME TO bot_sessions")
    # The single-active index went with the table it indexed.
    _create_bot_sessions_indexes(connection)


def _migrate_capital_per_entry_v12(connection: sqlite3.Connection) -> None:
    """Ask what one Entry spends instead of what the wallet holds.

    A Session used to state a total that was divided down to reach the figure
    every Entry was actually sized with. It states that figure now, so the
    column is not renamed but converted: read as a total, an old 10,000 would
    come back as ten thousand per Entry and trade a hundred times the size it
    was started with.

    The arithmetic is the arithmetic the old plans did — Spot divided the
    total by its Entries, Futures took half of it first — so every Session
    that migrates goes on trading exactly what it traded before.
    """
    if not _has_column(connection, "bot_sessions", "available_capital"):
        return
    rows = connection.execute("SELECT * FROM bot_sessions").fetchall()
    converted = [
        (
            *tuple(row[name] for name in _BOT_SESSIONS_COLUMN_NAMES[:6]),
            str(_capital_per_entry_of(row)),
            *tuple(row[name] for name in _BOT_SESSIONS_COLUMN_NAMES[7:]),
        )
        for row in rows
    ]
    connection.execute(_BOT_SESSIONS_TABLE_SQL.format(table="new_bot_sessions"))
    placeholders = ", ".join("?" for _ in _BOT_SESSIONS_COLUMN_NAMES)
    connection.executemany(
        f"""
        INSERT INTO new_bot_sessions ({_BOT_SESSIONS_COLUMNS})
        VALUES ({placeholders})
        """,
        converted,
    )
    connection.execute("DROP TABLE bot_sessions")
    connection.execute("ALTER TABLE new_bot_sessions RENAME TO bot_sessions")
    # The single-active index went with the table it indexed.
    _create_bot_sessions_indexes(connection)


def _capital_per_entry_of(row: sqlite3.Row) -> Decimal:
    """What this Session was already spending on one Entry."""
    total = Decimal(row["available_capital"])
    entries = Decimal(row["max_entries"])
    ratio = row["futures_trading_capital_ratio"]
    if ratio is not None:
        total = total * Decimal(ratio)
    return total / entries


def _has_column(connection: sqlite3.Connection, table: str, column: str) -> bool:
    return _table_exists(connection, table) and not _column_is_missing(
        connection, table, column
    )


def _column_is_missing(connection: sqlite3.Connection, table: str, column: str) -> bool:
    """Whether a table is there to alter and still without the column."""
    if not _table_exists(connection, table):
        return False
    return not any(
        row["name"] == column
        for row in connection.execute(f"PRAGMA table_info({table})")
    )


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (name,),
        ).fetchone()
        is not None
    )


"""The shape of bot_sessions, written once because v11 rebuilds the table.

The name is a parameter so the rebuild can raise the same table beside the old
one; nothing else may fill it in, and no value but a literal name ever reaches
it.
"""
# The shape bot_sessions had at v11, frozen: a migration rebuilds the table
# of its own moment, not the one the code has since moved on to.
_V11_BOT_SESSIONS_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS {table} (
            session_id TEXT PRIMARY KEY,
            trade_mode TEXT NOT NULL CHECK (trade_mode = 'paper'),
            market_type TEXT NOT NULL CHECK (
                market_type IN ('spot', 'futures')
            ),
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            preset_version TEXT NOT NULL,
            available_capital TEXT NOT NULL,
            max_entries INTEGER NOT NULL CHECK (
                max_entries BETWEEN 2 AND 20 AND max_entries % 2 = 0
            ),
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
                (market_type = 'spot' AND futures_policy_version IS NULL)
                OR
                (market_type = 'futures' AND futures_policy_version IS NOT NULL)
            )
        )
"""

_V11_BOT_SESSIONS_COLUMNS = """
            session_id, trade_mode, market_type, symbol, timeframe,
            preset_version, available_capital, max_entries,
            futures_policy_version, futures_leverage,
            futures_trading_capital_ratio, futures_collateral_buffer_ratio,
            futures_maintenance_margin_rate, futures_margin_mode,
            futures_position_mode, created_at_utc, ended_at_utc
"""


_BOT_SESSIONS_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS {table} (
            session_id TEXT PRIMARY KEY,
            trade_mode TEXT NOT NULL CHECK (trade_mode = 'paper'),
            market_type TEXT NOT NULL CHECK (
                market_type IN ('spot', 'futures')
            ),
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
            futures_maintenance_margin_rate TEXT,
            futures_margin_mode TEXT,
            futures_position_mode TEXT,
            created_at_utc TEXT NOT NULL,
            ended_at_utc TEXT,
            CHECK (
                (market_type = 'spot' AND futures_policy_version IS NULL)
                OR
                (market_type = 'futures' AND futures_policy_version IS NOT NULL)
            )
        )
"""

# Every column of bot_sessions in one order, for a rebuild that copies rather
# than reshapes: naming them keeps the copy honest if the shape moves again.
_BOT_SESSIONS_COLUMN_NAMES = (
    "session_id",
    "trade_mode",
    "market_type",
    "symbol",
    "timeframe",
    "preset_version",
    "capital_per_entry",
    "max_entries",
    "futures_policy_version",
    "futures_leverage",
    "futures_trading_capital_ratio",
    "futures_collateral_buffer_ratio",
    "futures_maintenance_margin_rate",
    "futures_margin_mode",
    "futures_position_mode",
    "created_at_utc",
    "ended_at_utc",
)

_BOT_SESSIONS_COLUMNS = """
            session_id, trade_mode, market_type, symbol, timeframe,
            preset_version, capital_per_entry, max_entries,
            futures_policy_version, futures_leverage,
            futures_trading_capital_ratio, futures_collateral_buffer_ratio,
            futures_maintenance_margin_rate, futures_margin_mode,
            futures_position_mode, created_at_utc, ended_at_utc
"""


def _create_bot_sessions_schema(connection: sqlite3.Connection) -> None:
    connection.execute(_BOT_SESSIONS_TABLE_SQL.format(table="bot_sessions"))
    _create_bot_sessions_indexes(connection)


def _create_bot_sessions_indexes(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS bot_sessions_single_active_idx
        ON bot_sessions ((1))
        WHERE ended_at_utc IS NULL
        """
    )


def _create_paper_runtime_lifecycle_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_runtime_lifecycle (
            session_id TEXT PRIMARY KEY REFERENCES bot_sessions(session_id),
            state TEXT NOT NULL CHECK (state IN ('running', 'stopped', 'blocked')),
            observed_at_utc TEXT NOT NULL
        )
        """
    )


def _migrate_paper_runtime_lifecycle_v5(connection: sqlite3.Connection) -> None:
    connection.execute(
        "ALTER TABLE paper_runtime_lifecycle RENAME TO legacy_runtime_lifecycle"
    )
    _create_paper_runtime_lifecycle_schema(connection)
    connection.execute(
        """
        INSERT INTO paper_runtime_lifecycle (session_id, state, observed_at_utc)
        SELECT session_id, state, observed_at_utc
        FROM legacy_runtime_lifecycle
        """
    )
    connection.execute("DROP TABLE legacy_runtime_lifecycle")
