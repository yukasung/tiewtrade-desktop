import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import TypeVar
from uuid import UUID

from tiewtrade.application.trade_history import (
    BasketHistoryResult,
    TradeFillHistoryItem,
    TradeHistoryFilter,
)
from tiewtrade.integrations.sqlite.connection_cleanup import (
    close_if_open,
    rollback_if_open,
)
from tiewtrade.integrations.sqlite.database import SQLiteDatabase
from tiewtrade.integrations.sqlite.trade_history_conflicts import (
    check_duplicate_fill,
    entry_count_after_fill,
    validate_entry_count,
    validate_fill_ownership,
    validate_immutable_identity,
    validate_paper_spot_identity,
)
from tiewtrade.integrations.sqlite.trade_history_errors import (
    TradeHistoryConflictError,
    TradeHistoryError,
    TradeHistoryUnavailableError,
)
from tiewtrade.integrations.sqlite.trade_history_rows import (
    basket_from_row,
    fill_from_row,
    find_basket,
    insert_basket,
    insert_fill,
    require_basket,
    trade_fill_history_item_from_row,
    update_basket,
    utc_text,
)
from tiewtrade.trading.session_config import MarketType, TradeMode
from tiewtrade.trading.trade_history import (
    BasketResult,
    BasketStatus,
    TradeFill,
)

ReadResult = TypeVar("ReadResult")


class SQLiteTradeHistory:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def record_open_basket(self, basket: BasketResult, fill: TradeFill) -> bool:
        validate_fill_ownership(basket, fill)
        if basket.status is not BasketStatus.OPEN:
            raise TradeHistoryConflictError("new Basket must have OPEN status")

        def operation(connection: sqlite3.Connection) -> bool:
            if check_duplicate_fill(connection, fill):
                return False
            if find_basket(connection, basket.basket_id) is not None:
                raise TradeHistoryConflictError("Basket already exists")
            insert_basket(connection, basket)
            insert_fill(connection, fill)
            return True

        return self._run_write(operation)

    def record_entry_fill(self, basket: BasketResult, fill: TradeFill) -> bool:
        validate_fill_ownership(basket, fill)

        def operation(connection: sqlite3.Connection) -> bool:
            if check_duplicate_fill(connection, fill):
                return False
            current = require_basket(connection, basket.basket_id)
            validate_immutable_identity(current, basket)
            if current.status is not BasketStatus.OPEN:
                raise TradeHistoryConflictError("closed Basket cannot receive a Fill")
            if basket.status is not BasketStatus.OPEN:
                raise TradeHistoryConflictError("Entry Fill requires an OPEN Basket")
            validate_entry_count(connection, current, basket, fill)
            insert_fill(connection, fill)
            update_basket(connection, basket)
            return True

        return self._run_write(operation)

    def record_paper_spot_entry_fill(
        self,
        fill: TradeFill,
        *,
        symbol: str,
        timeframe: str,
        strategy_preset_version: str,
    ) -> bool:
        def operation(connection: sqlite3.Connection) -> bool:
            if check_duplicate_fill(connection, fill):
                return False
            current = find_basket(connection, fill.basket_id)
            if current is None:
                if fill.entry_number != 1:
                    raise TradeHistoryConflictError(
                        "new Entry Fill has an unexpected entry_number"
                    )
                insert_basket(
                    connection,
                    BasketResult(
                        basket_id=fill.basket_id,
                        session_id=fill.session_id,
                        trade_mode=TradeMode.PAPER,
                        market_type=MarketType.SPOT,
                        symbol=symbol,
                        timeframe=timeframe,
                        strategy_preset_version=strategy_preset_version,
                        opened_at_utc=fill.filled_at_utc,
                        closed_at_utc=None,
                        entry_count=1,
                        invested_notional=fill.notional,
                        realized_pnl=Decimal("0"),
                        status=BasketStatus.OPEN,
                    ),
                )
                insert_fill(connection, fill)
                return True
            validate_paper_spot_identity(
                current,
                fill,
                symbol=symbol,
                timeframe=timeframe,
                strategy_preset_version=strategy_preset_version,
            )
            if current.status is not BasketStatus.OPEN:
                raise TradeHistoryConflictError("closed Basket cannot receive a Fill")
            entry_count = entry_count_after_fill(connection, current, fill)
            insert_fill(connection, fill)
            update_basket(
                connection,
                replace(
                    current,
                    entry_count=entry_count,
                    invested_notional=current.invested_notional + fill.notional,
                ),
            )
            return True

        return self._run_write(operation)

    def record_entry_exit_fill(self, basket: BasketResult, fill: TradeFill) -> bool:
        """Record one Entry sold out of a Basket that others may still hold.

        The Basket row carries the running total either way; whether this sale
        was its last is the caller's reading, and arrives as the status.
        """
        validate_fill_ownership(basket, fill)
        if fill.closes_entry_number is None:
            raise TradeHistoryConflictError("Entry Exit Fill must name its Entry")

        def operation(connection: sqlite3.Connection) -> bool:
            if check_duplicate_fill(connection, fill):
                return False
            current = require_basket(connection, basket.basket_id)
            validate_immutable_identity(current, basket)
            if current.status is not BasketStatus.OPEN:
                raise TradeHistoryConflictError("closed Basket cannot receive a Fill")
            insert_fill(connection, fill)
            update_basket(connection, basket)
            return True

        return self._run_write(operation)

    def record_closed_basket(self, basket: BasketResult, fill: TradeFill) -> bool:
        validate_fill_ownership(basket, fill)

        def operation(connection: sqlite3.Connection) -> bool:
            if check_duplicate_fill(connection, fill):
                return False
            current = require_basket(connection, basket.basket_id)
            validate_immutable_identity(current, basket)
            if current.status is not BasketStatus.OPEN:
                raise TradeHistoryConflictError("Basket is already closed")
            if basket.status is not BasketStatus.CLOSED:
                raise TradeHistoryConflictError("Close Fill requires a CLOSED Basket")
            insert_fill(connection, fill)
            update_basket(connection, basket)
            return True

        return self._run_write(operation)

    def get_basket(self, basket_id: UUID) -> BasketResult | None:
        return self._run_read(lambda connection: find_basket(connection, basket_id))

    def record_cooldown_waiver(
        self, basket_id: UUID, waived_pair: int, waived_at: datetime
    ) -> None:
        def operation(connection: sqlite3.Connection) -> bool:
            connection.execute(
                """
                INSERT INTO paper_cooldown_waivers
                    (basket_id, waived_pair, waived_at_utc)
                VALUES (?, ?, ?)
                ON CONFLICT(basket_id) DO UPDATE SET
                    waived_pair = excluded.waived_pair,
                    waived_at_utc = excluded.waived_at_utc
                """,
                (str(basket_id), waived_pair, utc_text(waived_at)),
            )
            return True

        self._run_write(operation)

    def find_cooldown_waiver(self, basket_id: UUID) -> int | None:
        def operation(connection: sqlite3.Connection) -> int | None:
            row = connection.execute(
                """
                SELECT waived_pair FROM paper_cooldown_waivers
                WHERE basket_id = ?
                """,
                (str(basket_id),),
            ).fetchone()
            return None if row is None else int(row["waived_pair"])

        return self._run_read(operation)

    def find_open_basket(self, session_id: UUID) -> BasketResult | None:
        def operation(connection: sqlite3.Connection) -> BasketResult | None:
            rows = connection.execute(
                """
                SELECT * FROM basket_results
                WHERE session_id = ? AND status = ?
                ORDER BY opened_at_utc, basket_id
                LIMIT 2
                """,
                (str(session_id), BasketStatus.OPEN.value),
            ).fetchall()
            if len(rows) > 1:
                raise TradeHistoryConflictError("Session has more than one open Basket")
            return basket_from_row(rows[0]) if rows else None

        return self._run_read(operation)

    def list_basket_history(
        self,
        filters: TradeHistoryFilter,
    ) -> BasketHistoryResult:
        def operation(connection: sqlite3.Connection) -> BasketHistoryResult:
            where_sql, parameters = _basket_where(filters)
            rows = connection.execute(
                f"""
                SELECT * FROM basket_results
                {where_sql}
                ORDER BY opened_at_utc DESC, basket_id DESC
                """,
                parameters,
            ).fetchall()
            return BasketHistoryResult(
                items=tuple(basket_from_row(row) for row in rows),
                realized_pnl=_closed_realized_pnl(connection, filters),
            )

        return self._run_read(operation)

    def list_trade_fills(
        self,
        filters: TradeHistoryFilter,
    ) -> tuple[TradeFillHistoryItem, ...]:
        def operation(
            connection: sqlite3.Connection,
        ) -> tuple[TradeFillHistoryItem, ...]:
            where_sql, parameters = _fill_where(filters)
            rows = connection.execute(
                f"""
                SELECT trade_fills.*, basket_results.symbol, basket_results.timeframe,
                       basket_results.market_type, basket_results.trade_mode
                FROM trade_fills
                JOIN basket_results ON basket_results.basket_id = trade_fills.basket_id
                {where_sql}
                ORDER BY trade_fills.filled_at_utc DESC, trade_fills.fill_id DESC
                """,
                parameters,
            ).fetchall()
            return tuple(trade_fill_history_item_from_row(row) for row in rows)

        return self._run_read(operation)

    def list_fills(self, basket_id: UUID) -> tuple[TradeFill, ...]:
        def operation(connection: sqlite3.Connection) -> tuple[TradeFill, ...]:
            rows = connection.execute(
                """
                SELECT * FROM trade_fills
                WHERE basket_id = ?
                ORDER BY filled_at_utc, fill_id
                """,
                (str(basket_id),),
            ).fetchall()
            return tuple(fill_from_row(row) for row in rows)

        return self._run_read(operation)

    def count_closed_baskets(self, session_id: UUID) -> int:
        def operation(connection: sqlite3.Connection) -> int:
            row = connection.execute(
                """
                SELECT COUNT(*) AS closed FROM basket_results
                WHERE session_id = ? AND status = ?
                """,
                (str(session_id), BasketStatus.CLOSED.value),
            ).fetchone()
            return int(row["closed"])

        return self._run_read(operation)

    def total_realized_pnl(self) -> Decimal:
        """Every result this installation has realised, across all of it.

        There is no filter because the question has no dimension: not one
        Session, not one day, not one market — the whole ledger.

        Open Baskets are in it. `realized_pnl` is a running total the moment
        an Entry is sold out of a Basket, whether or not the Basket itself
        has closed, so a closed-only sum would drop the profit of a Basket
        that is still holding the rest of its Entries.

        What it does not carry is what is still held: that is unrealised, it
        needs a price, and nothing in the database has one.
        """

        def operation(connection: sqlite3.Connection) -> Decimal:
            rows = connection.execute("SELECT realized_pnl FROM basket_results")
            return _exact_decimal_sum(Decimal(row["realized_pnl"]) for row in rows)

        return self._run_read(operation)

    def list_pair_fills(
        self,
        symbol: str,
        trade_mode: TradeMode,
        market_type: MarketType,
        start_utc: datetime,
        end_utc: datetime,
    ) -> tuple[TradeFill, ...]:
        """Every fill this Pair has taken in a span, whichever Session took it.

        A trade did not stop being true about BTCUSDT because the Session that
        made it was replaced. Read by Session, the chart lost its marks the
        moment a Start retired one — so a Pair is what they are read by, and
        the Basket each fill belongs to is what says which Pair that is.

        The mode and the market are part of the Pair here: paper marks have no
        business on a live chart, nor spot marks on futures.
        """

        def operation(connection: sqlite3.Connection) -> tuple[TradeFill, ...]:
            rows = connection.execute(
                """
                SELECT trade_fills.* FROM trade_fills
                JOIN basket_results
                  ON basket_results.basket_id = trade_fills.basket_id
                WHERE basket_results.symbol = ?
                  AND basket_results.trade_mode = ?
                  AND basket_results.market_type = ?
                  AND trade_fills.filled_at_utc >= ?
                  AND trade_fills.filled_at_utc < ?
                ORDER BY trade_fills.filled_at_utc, trade_fills.fill_id
                """,
                (
                    symbol,
                    trade_mode.value,
                    market_type.value,
                    utc_text(start_utc),
                    utc_text(end_utc),
                ),
            ).fetchall()
            return tuple(fill_from_row(row) for row in rows)

        return self._run_read(operation)

    def _run_read(
        self,
        operation: Callable[[sqlite3.Connection], ReadResult],
    ) -> ReadResult:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._database.connect()
            result = operation(connection)
        except sqlite3.Error as error:
            close_if_open(connection)
            raise TradeHistoryUnavailableError(
                "Trade History SQLite read failed"
            ) from error
        except BaseException:
            close_if_open(connection)
            raise
        _close_or_raise(connection)
        return result

    def _run_write(
        self,
        operation: Callable[[sqlite3.Connection], bool],
    ) -> bool:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._database.connect()
            connection.execute("BEGIN IMMEDIATE")
            result = operation(connection)
            connection.commit()
        except TradeHistoryError:
            rollback_if_open(connection)
            close_if_open(connection)
            raise
        except sqlite3.Error as error:
            rollback_if_open(connection)
            close_if_open(connection)
            raise TradeHistoryUnavailableError(
                "Trade History SQLite write failed"
            ) from error
        except BaseException:
            rollback_if_open(connection)
            close_if_open(connection)
            raise
        _close_or_raise(connection)
        return result


def _basket_facets(
    filters: TradeHistoryFilter,
    *,
    prefix: str,
    closed_only: bool,
) -> tuple[list[str], list[object]]:
    """What a Basket is, as clauses — everything but the range it is read over."""
    clauses: list[str] = []
    parameters: list[object] = []
    fields = (
        ("symbol", filters.symbol),
        ("timeframe", filters.timeframe),
        (
            "market_type",
            filters.market_type.value if filters.market_type is not None else None,
        ),
        (
            "trade_mode",
            filters.trade_mode.value if filters.trade_mode is not None else None,
        ),
    )
    for column, value in fields:
        if value is not None:
            clauses.append(f"{prefix}{column} = ?")
            parameters.append(value)

    status = BasketStatus.CLOSED if closed_only else filters.status
    if status is not None:
        clauses.append(f"{prefix}status = ?")
        parameters.append(status.value)
    return clauses, parameters


def _where(
    clauses: list[str], parameters: list[object]
) -> tuple[str, tuple[object, ...]]:
    if not clauses:
        return "", ()
    return f"WHERE {' AND '.join(clauses)}", tuple(parameters)


def _basket_where(
    filters: TradeHistoryFilter,
    *,
    closed_only: bool = False,
) -> tuple[str, tuple[object, ...]]:
    """The same filters, dated by the day the Basket closed.

    A round that opened before midnight and took profit after it realised its
    result on the new day, and that is the day a trader looks for it under.
    Dated by the day it opened, a take profit would be missing from the window
    it happened in. A Basket still open has no such day yet, and a NULL close
    is excluded by the comparison rather than by a clause of its own — a round
    a trader is still holding is read on the Position tab, not here.
    """
    clauses, parameters = _basket_facets(filters, prefix="", closed_only=closed_only)
    if filters.from_utc is not None:
        clauses.append("closed_at_utc >= ?")
        parameters.append(utc_text(filters.from_utc))
    if filters.before_utc is not None:
        clauses.append("closed_at_utc < ?")
        parameters.append(utc_text(filters.before_utc))
    return _where(clauses, parameters)


def _fill_where(filters: TradeHistoryFilter) -> tuple[str, tuple[object, ...]]:
    """The same filters, dated by the day the Fill landed on.

    A Basket opened before midnight goes on filling after it, and those
    Fills are the new day's trading. Read by the day their Basket opened,
    a whole day of trades would sit hidden behind an older Basket — so the
    range applies to the Fill, and the rest still describes its Basket.
    """
    clauses, parameters = _basket_facets(
        filters, prefix="basket_results.", closed_only=False
    )
    if filters.from_utc is not None:
        clauses.append("trade_fills.filled_at_utc >= ?")
        parameters.append(utc_text(filters.from_utc))
    if filters.before_utc is not None:
        clauses.append("trade_fills.filled_at_utc < ?")
        parameters.append(utc_text(filters.before_utc))
    return _where(clauses, parameters)


def _closed_realized_pnl(
    connection: sqlite3.Connection,
    filters: TradeHistoryFilter,
) -> Decimal:
    if filters.status is BasketStatus.OPEN:
        return Decimal("0")
    where_sql, parameters = _basket_where(filters, closed_only=True)
    rows = connection.execute(
        f"SELECT realized_pnl FROM basket_results {where_sql}",
        parameters,
    )
    return _exact_decimal_sum(Decimal(row["realized_pnl"]) for row in rows)


def _exact_decimal_sum(values: Iterable[Decimal]) -> Decimal:
    coefficient = 0
    exponent = 0
    initialized = False
    for value in values:
        if not value.is_finite():
            raise ValueError("realized_pnl must be finite")
        parts = value.as_tuple()
        if not isinstance(parts.exponent, int):
            raise ValueError("realized_pnl must have a finite exponent")
        item_coefficient = 0
        for digit in parts.digits:
            item_coefficient = item_coefficient * 10 + digit
        if parts.sign:
            item_coefficient = -item_coefficient

        if not initialized:
            coefficient = item_coefficient
            exponent = parts.exponent
            initialized = True
        else:
            common_exponent = min(exponent, parts.exponent)
            coefficient = coefficient * 10 ** (
                exponent - common_exponent
            ) + item_coefficient * 10 ** (parts.exponent - common_exponent)
            exponent = common_exponent

        while coefficient and coefficient % 10 == 0:
            coefficient //= 10
            exponent += 1

    if not initialized or coefficient == 0:
        return Decimal("0")
    digits = tuple(int(character) for character in str(abs(coefficient)))
    return Decimal((int(coefficient < 0), digits, exponent))


def _close_or_raise(connection: sqlite3.Connection) -> None:
    try:
        connection.close()
    except sqlite3.Error as error:
        raise TradeHistoryUnavailableError(
            "Trade History SQLite close failed"
        ) from error
