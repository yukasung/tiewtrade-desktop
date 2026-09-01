import sqlite3
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from tiewtrade.application.paper_session_setup import (
    ActivePaperSessionGoneError,
    ActivePaperSessionStillRunningError,
    ConfiguredPaperSession,
    PaperSessionCreateOutcome,
    PaperSessionUnavailableError,
)
from tiewtrade.application.paper_symbols import validate_paper_symbol
from tiewtrade.integrations.sqlite.connection_cleanup import (
    close_if_open,
    rollback_if_open,
)
from tiewtrade.integrations.sqlite.database import SQLiteDatabase
from tiewtrade.market_data.config import MarketDataConfig
from tiewtrade.strategies.registry import strategy_definition_for
from tiewtrade.trading.entry_policy import EntryPolicy
from tiewtrade.trading.futures_policy import (
    FuturesTradingPolicy,
    MarginMode,
    PositionMode,
)
from tiewtrade.trading.session_config import MarketType, SessionConfig, TradeMode

_INSERT_SQL = """
INSERT INTO bot_sessions (
    session_id, trade_mode, market_type, symbol, timeframe, preset_version,
    capital_per_entry, max_entries,
    futures_policy_version, futures_leverage,
    futures_trading_capital_ratio, futures_collateral_buffer_ratio,
    futures_margin_mode,
    futures_position_mode, created_at_utc, ended_at_utc
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


class SQLiteActivePaperSessions:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def create(self, session: ConfiguredPaperSession) -> PaperSessionCreateOutcome:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._database.connect()
            connection.execute("BEGIN IMMEDIATE")
            existing = _find_active(connection)
            if existing is not None:
                connection.commit()
                return PaperSessionCreateOutcome(session=existing, created=False)
            connection.execute(_INSERT_SQL, _session_values(session))
            connection.commit()
            return PaperSessionCreateOutcome(session=session, created=True)
        except sqlite3.Error as error:
            rollback_if_open(connection)
            raise PaperSessionUnavailableError(
                "Active Paper Session write failed"
            ) from error
        except BaseException:
            rollback_if_open(connection)
            raise
        finally:
            close_if_open(connection)

    def get_active(self) -> ConfiguredPaperSession | None:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._database.connect()
            result = _find_active(connection)
        except sqlite3.Error as error:
            raise PaperSessionUnavailableError(
                "Active Paper Session read failed"
            ) from error
        finally:
            close_if_open(connection)
        return result

    def retire(self, session_id: UUID, ended_at_utc: datetime) -> None:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._database.connect()
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                "SELECT 1 FROM bot_sessions "
                "WHERE session_id = ? AND ended_at_utc IS NULL",
                (str(session_id),),
            ).fetchone()
            lifecycle = connection.execute(
                "SELECT state FROM paper_runtime_lifecycle WHERE session_id = ?",
                (str(session_id),),
            ).fetchone()
            # No row left to retire is not a Session running somewhere: a
            # database cleared out from under the window leaves nothing to
            # end, and saying so is what lets the caller tell a trader the
            # truth about it.
            if active is None:
                raise ActivePaperSessionGoneError(
                    "Active Paper Session is no longer there"
                )
            # A missing lifecycle row means the runtime never started, so
            # there is nothing to stop and no work to lose. Refusing that
            # case used to strand a Session that could neither run nor be
            # replaced, leaving Start Paper Bot failing for good.
            if lifecycle is not None and lifecycle["state"] not in {
                "stopped",
                "blocked",
            }:
                raise ActivePaperSessionStillRunningError(
                    "Active Paper Session cannot be retired"
                )
            connection.execute(
                "UPDATE bot_sessions SET ended_at_utc = ? WHERE session_id = ?",
                (ended_at_utc.isoformat(), str(session_id)),
            )
            connection.commit()
        except sqlite3.Error as error:
            rollback_if_open(connection)
            raise PaperSessionUnavailableError(
                "Active Paper Session retirement failed"
            ) from error
        except BaseException:
            rollback_if_open(connection)
            raise
        finally:
            close_if_open(connection)


def _session_from_row(row: sqlite3.Row) -> ConfiguredPaperSession:
    try:
        validate_paper_symbol(row["symbol"])
    except ValueError as error:
        raise PaperSessionUnavailableError(
            "Active Paper Session read failed"
        ) from error
    try:
        strategy_definition_for(row["preset_version"])
    except ValueError as error:
        raise PaperSessionUnavailableError(
            "Active Paper Session read failed"
        ) from error
    market_type = MarketType(row["market_type"])
    futures_policy = (
        FuturesTradingPolicy(
            version=row["futures_policy_version"],
            leverage=row["futures_leverage"],
            trading_capital_ratio=Decimal(row["futures_trading_capital_ratio"]),
            collateral_buffer_ratio=Decimal(row["futures_collateral_buffer_ratio"]),
            margin_mode=MarginMode(row["futures_margin_mode"]),
            position_mode=PositionMode(row["futures_position_mode"]),
        )
        if market_type is MarketType.FUTURES
        else None
    )
    config = SessionConfig(
        session_id=UUID(row["session_id"]),
        preset_version=row["preset_version"],
        market_type=market_type,
        trade_mode=TradeMode(row["trade_mode"]),
        capital_per_entry=Decimal(row["capital_per_entry"]),
        entry_policy=EntryPolicy(row["max_entries"]),
        futures_policy=futures_policy,
    )
    return ConfiguredPaperSession(
        config=config,
        market_data=MarketDataConfig(
            symbol=row["symbol"],
            timeframe=row["timeframe"],
        ),
        created_at_utc=datetime.fromisoformat(row["created_at_utc"]),
    )


def _find_active(connection: sqlite3.Connection) -> ConfiguredPaperSession | None:
    row = connection.execute(
        "SELECT * FROM bot_sessions WHERE ended_at_utc IS NULL"
    ).fetchone()
    return None if row is None else _session_from_row(row)


def _session_values(session: ConfiguredPaperSession) -> tuple[object, ...]:
    config = session.config
    futures = config.futures_policy
    return (
        str(config.session_id),
        config.trade_mode.value,
        config.market_type.value,
        session.market_data.symbol,
        session.market_data.timeframe,
        config.preset_version,
        str(config.capital_per_entry),
        config.entry_policy.max_entries,
        None if futures is None else futures.version,
        None if futures is None else futures.leverage,
        None if futures is None else str(futures.trading_capital_ratio),
        None if futures is None else str(futures.collateral_buffer_ratio),
        None if futures is None else futures.margin_mode.value,
        None if futures is None else futures.position_mode.value,
        session.created_at_utc.isoformat(),
        None,
    )
