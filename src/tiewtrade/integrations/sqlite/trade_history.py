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
from tiewtrade.trading.session_config import MarketType, TradeMode
from tiewtrade.trading.trade_history import (
    BasketResult,
    BasketStatus,
    FillSide,
    FillSource,
    TradeFill,
)


class TradeHistoryError(RuntimeError):
    pass


class TradeHistoryConflictError(TradeHistoryError):
    pass


class TradeHistoryUnavailableError(TradeHistoryError):
    pass


ReadResult = TypeVar("ReadResult")


class SQLiteTradeHistory:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def record_open_basket(self, basket: BasketResult, fill: TradeFill) -> bool:
        _validate_fill_ownership(basket, fill)
        if basket.status is not BasketStatus.OPEN:
            raise TradeHistoryConflictError("new Basket must have OPEN status")

        def operation(connection: sqlite3.Connection) -> bool:
            if _check_duplicate_fill(connection, fill):
                return False
            if _find_basket(connection, basket.basket_id) is not None:
                raise TradeHistoryConflictError("Basket already exists")
            _insert_basket(connection, basket)
            _insert_fill(connection, fill)
            return True

        return self._run_write(operation)

    def record_entry_fill(self, basket: BasketResult, fill: TradeFill) -> bool:
        _validate_fill_ownership(basket, fill)

        def operation(connection: sqlite3.Connection) -> bool:
            if _check_duplicate_fill(connection, fill):
                return False
            current = _require_basket(connection, basket.basket_id)
            _validate_immutable_identity(current, basket)
            if current.status is not BasketStatus.OPEN:
                raise TradeHistoryConflictError("closed Basket cannot receive a Fill")
            if basket.status is not BasketStatus.OPEN:
                raise TradeHistoryConflictError("Entry Fill requires an OPEN Basket")
            _validate_entry_count(connection, current, basket, fill)
            _insert_fill(connection, fill)
            _update_basket(connection, basket)
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
            if _check_duplicate_fill(connection, fill):
                return False
            current = _find_basket(connection, fill.basket_id)
            if current is None:
                if fill.entry_number != 1:
                    raise TradeHistoryConflictError(
                        "new Entry Fill has an unexpected entry_number"
                    )
                _insert_basket(
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
                _insert_fill(connection, fill)
                return True
            _validate_paper_spot_identity(
                current,
                fill,
                symbol=symbol,
                timeframe=timeframe,
                strategy_preset_version=strategy_preset_version,
            )
            if current.status is not BasketStatus.OPEN:
                raise TradeHistoryConflictError("closed Basket cannot receive a Fill")
            entry_count = _entry_count_after_fill(connection, current, fill)
            _insert_fill(connection, fill)
            _update_basket(
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
        _validate_fill_ownership(basket, fill)
        if fill.closes_entry_number is None:
            raise TradeHistoryConflictError("Entry Exit Fill must name its Entry")

        def operation(connection: sqlite3.Connection) -> bool:
            if _check_duplicate_fill(connection, fill):
                return False
            current = _require_basket(connection, basket.basket_id)
            _validate_immutable_identity(current, basket)
            if current.status is not BasketStatus.OPEN:
                raise TradeHistoryConflictError("closed Basket cannot receive a Fill")
            _insert_fill(connection, fill)
            _update_basket(connection, basket)
            return True

        return self._run_write(operation)

    def record_closed_basket(self, basket: BasketResult, fill: TradeFill) -> bool:
        _validate_fill_ownership(basket, fill)

        def operation(connection: sqlite3.Connection) -> bool:
            if _check_duplicate_fill(connection, fill):
                return False
            current = _require_basket(connection, basket.basket_id)
            _validate_immutable_identity(current, basket)
            if current.status is not BasketStatus.OPEN:
                raise TradeHistoryConflictError("Basket is already closed")
            if basket.status is not BasketStatus.CLOSED:
                raise TradeHistoryConflictError("Close Fill requires a CLOSED Basket")
            _insert_fill(connection, fill)
            _update_basket(connection, basket)
            return True

        return self._run_write(operation)

    def get_basket(self, basket_id: UUID) -> BasketResult | None:
        return self._run_read(lambda connection: _find_basket(connection, basket_id))

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
                (str(basket_id), waived_pair, _utc_text(waived_at)),
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
            return _basket_from_row(rows[0]) if rows else None

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
                items=tuple(_basket_from_row(row) for row in rows),
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
            return tuple(_trade_fill_history_item_from_row(row) for row in rows)

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
            return tuple(_fill_from_row(row) for row in rows)

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
                    _utc_text(start_utc),
                    _utc_text(end_utc),
                ),
            ).fetchall()
            return tuple(_fill_from_row(row) for row in rows)

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
    clauses, parameters = _basket_facets(filters, prefix="", closed_only=closed_only)
    if filters.opened_from_utc is not None:
        clauses.append("opened_at_utc >= ?")
        parameters.append(_utc_text(filters.opened_from_utc))
    if filters.opened_before_utc is not None:
        clauses.append("opened_at_utc < ?")
        parameters.append(_utc_text(filters.opened_before_utc))
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
    if filters.opened_from_utc is not None:
        clauses.append("trade_fills.filled_at_utc >= ?")
        parameters.append(_utc_text(filters.opened_from_utc))
    if filters.opened_before_utc is not None:
        clauses.append("trade_fills.filled_at_utc < ?")
        parameters.append(_utc_text(filters.opened_before_utc))
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


def _validate_fill_ownership(basket: BasketResult, fill: TradeFill) -> None:
    if basket.basket_id != fill.basket_id:
        raise TradeHistoryConflictError("Fill belongs to a different Basket")
    if basket.session_id != fill.session_id:
        raise TradeHistoryConflictError("Fill belongs to a different Session")


def _validate_immutable_identity(
    current: BasketResult,
    proposed: BasketResult,
) -> None:
    fields = (
        "basket_id",
        "session_id",
        "trade_mode",
        "market_type",
        "leverage",
        "symbol",
        "timeframe",
        "strategy_preset_version",
        "opened_at_utc",
    )
    for field in fields:
        if getattr(current, field) != getattr(proposed, field):
            raise TradeHistoryConflictError(
                f"Basket identity field {field} cannot change"
            )


def _validate_paper_spot_identity(
    current: BasketResult,
    fill: TradeFill,
    *,
    symbol: str,
    timeframe: str,
    strategy_preset_version: str,
) -> None:
    fields = (
        ("basket_id", current.basket_id, fill.basket_id),
        ("session_id", current.session_id, fill.session_id),
        ("trade_mode", current.trade_mode, TradeMode.PAPER),
        ("market_type", current.market_type, MarketType.SPOT),
        ("leverage", current.leverage, None),
        ("symbol", current.symbol, symbol),
        ("timeframe", current.timeframe, timeframe),
        (
            "strategy_preset_version",
            current.strategy_preset_version,
            strategy_preset_version,
        ),
    )
    for field, current_value, expected_value in fields:
        if current_value != expected_value:
            raise TradeHistoryConflictError(
                f"Basket identity field {field} cannot change"
            )


def _validate_entry_count(
    connection: sqlite3.Connection,
    current: BasketResult,
    proposed: BasketResult,
    fill: TradeFill,
) -> None:
    row = connection.execute(
        """
        SELECT entry_number
        FROM trade_fills
        WHERE basket_id = ? AND order_id = ?
        ORDER BY filled_at_utc, fill_id
        LIMIT 1
        """,
        (str(fill.basket_id), fill.order_id),
    ).fetchone()
    prior_entry_number = row["entry_number"] if row is not None else None
    if prior_entry_number is not None:
        if fill.entry_number != prior_entry_number:
            raise TradeHistoryConflictError(
                "Partial Fills for one Order must use the same entry_number"
            )
        if proposed.entry_count != current.entry_count:
            raise TradeHistoryConflictError(
                "Partial Fill must not increment Basket entry_count"
            )
        return

    expected_entry_number = current.entry_count + 1
    if fill.entry_number != expected_entry_number:
        raise TradeHistoryConflictError("new Entry Fill has an unexpected entry_number")
    if proposed.entry_count != expected_entry_number:
        raise TradeHistoryConflictError(
            "new Entry Fill must increment Basket entry_count once"
        )


def _entry_count_after_fill(
    connection: sqlite3.Connection,
    current: BasketResult,
    fill: TradeFill,
) -> int:
    row = connection.execute(
        """
        SELECT entry_number
        FROM trade_fills
        WHERE basket_id = ? AND order_id = ?
        ORDER BY filled_at_utc, fill_id
        LIMIT 1
        """,
        (str(fill.basket_id), fill.order_id),
    ).fetchone()
    if row is not None:
        if fill.entry_number != row["entry_number"]:
            raise TradeHistoryConflictError(
                "Partial Fills for one Order must use the same entry_number"
            )
        return current.entry_count
    expected_entry_number = current.entry_count + 1
    if fill.entry_number != expected_entry_number:
        raise TradeHistoryConflictError("new Entry Fill has an unexpected entry_number")
    return expected_entry_number


def _find_basket(
    connection: sqlite3.Connection,
    basket_id: UUID,
) -> BasketResult | None:
    row = connection.execute(
        "SELECT * FROM basket_results WHERE basket_id = ?",
        (str(basket_id),),
    ).fetchone()
    return _basket_from_row(row) if row is not None else None


def _require_basket(
    connection: sqlite3.Connection,
    basket_id: UUID,
) -> BasketResult:
    basket = _find_basket(connection, basket_id)
    if basket is None:
        raise TradeHistoryConflictError("Basket does not exist")
    return basket


def _find_fill(
    connection: sqlite3.Connection,
    fill_id: str,
) -> TradeFill | None:
    row = connection.execute(
        "SELECT * FROM trade_fills WHERE fill_id = ?",
        (fill_id,),
    ).fetchone()
    return _fill_from_row(row) if row is not None else None


def _check_duplicate_fill(
    connection: sqlite3.Connection,
    fill: TradeFill,
) -> bool:
    existing = _find_fill(connection, fill.fill_id)
    if existing is None:
        return False
    if existing != fill:
        raise TradeHistoryConflictError(
            f"fill_id {fill.fill_id!r} has conflicting payload"
        )
    return True


def _update_basket(
    connection: sqlite3.Connection,
    basket: BasketResult,
) -> None:
    cursor = connection.execute(
        """
        UPDATE basket_results
        SET session_id = ?, trade_mode = ?, market_type = ?, leverage = ?, symbol = ?,
            timeframe = ?, strategy_preset_version = ?, opened_at_utc = ?,
            closed_at_utc = ?, entry_count = ?, invested_notional = ?,
            realized_pnl = ?, status = ?
        WHERE basket_id = ?
        """,
        (*_basket_values(basket)[1:], str(basket.basket_id)),
    )
    if cursor.rowcount != 1:
        raise TradeHistoryConflictError("Basket does not exist")


def _insert_basket(
    connection: sqlite3.Connection,
    basket: BasketResult,
) -> None:
    connection.execute(
        """
        INSERT INTO basket_results (
            basket_id, session_id, trade_mode, market_type, leverage, symbol,
            timeframe, strategy_preset_version, opened_at_utc,
            closed_at_utc, entry_count, invested_notional,
            realized_pnl, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _basket_values(basket),
    )


def _basket_values(basket: BasketResult) -> tuple[object, ...]:
    return (
        str(basket.basket_id),
        str(basket.session_id),
        basket.trade_mode.value,
        basket.market_type.value,
        basket.leverage,
        basket.symbol,
        basket.timeframe,
        basket.strategy_preset_version,
        _utc_text(basket.opened_at_utc),
        _utc_text(basket.closed_at_utc) if basket.closed_at_utc is not None else None,
        basket.entry_count,
        _stored_decimal(basket.invested_notional),
        _stored_decimal(basket.realized_pnl),
        basket.status.value,
    )


def _insert_fill(connection: sqlite3.Connection, fill: TradeFill) -> None:
    connection.execute(
        """
        INSERT INTO trade_fills (
            fill_id, basket_id, session_id, order_id, exchange_trade_id, side,
            entry_number, filled_at_utc, price, quantity, notional,
            realized_pnl, source, entry_atr,
            closes_entry_number, entry_group_id, group_take_profit_price
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fill.fill_id,
            str(fill.basket_id),
            str(fill.session_id),
            fill.order_id,
            fill.exchange_trade_id,
            fill.side.value,
            fill.entry_number,
            _utc_text(fill.filled_at_utc),
            _stored_decimal(fill.price),
            _stored_decimal(fill.quantity),
            _stored_decimal(fill.notional),
            _stored_decimal(fill.realized_pnl),
            fill.source.value,
            None if fill.entry_atr is None else _stored_decimal(fill.entry_atr),
            fill.closes_entry_number,
            fill.entry_group_id,
            (
                None
                if fill.group_take_profit_price is None
                else _stored_decimal(fill.group_take_profit_price)
            ),
        ),
    )


def _stored_decimal(value: Decimal) -> str:
    """A figure as the database keeps it, to every place it was given.

    Nothing is normalized away here. What a Fill was priced at is the
    record, and trimming it would change what the row says happened.
    """
    return format(value, "f")


def _utc_text(value: datetime) -> str:
    return value.isoformat()


def _basket_from_row(row: sqlite3.Row) -> BasketResult:
    return BasketResult(
        basket_id=UUID(row["basket_id"]),
        session_id=UUID(row["session_id"]),
        trade_mode=TradeMode(row["trade_mode"]),
        market_type=MarketType(row["market_type"]),
        leverage=row["leverage"],
        symbol=row["symbol"],
        timeframe=row["timeframe"],
        strategy_preset_version=row["strategy_preset_version"],
        opened_at_utc=datetime.fromisoformat(row["opened_at_utc"]),
        closed_at_utc=(
            datetime.fromisoformat(row["closed_at_utc"])
            if row["closed_at_utc"] is not None
            else None
        ),
        entry_count=row["entry_count"],
        invested_notional=Decimal(row["invested_notional"]),
        realized_pnl=Decimal(row["realized_pnl"]),
        status=BasketStatus(row["status"]),
    )


def _fill_from_row(row: sqlite3.Row) -> TradeFill:
    return TradeFill(
        fill_id=row["fill_id"],
        basket_id=UUID(row["basket_id"]),
        session_id=UUID(row["session_id"]),
        order_id=row["order_id"],
        exchange_trade_id=row["exchange_trade_id"],
        side=FillSide(row["side"]),
        entry_number=row["entry_number"],
        filled_at_utc=datetime.fromisoformat(row["filled_at_utc"]),
        price=Decimal(row["price"]),
        quantity=Decimal(row["quantity"]),
        notional=Decimal(row["notional"]),
        realized_pnl=Decimal(row["realized_pnl"]),
        source=FillSource(row["source"]),
        entry_atr=(None if row["entry_atr"] is None else Decimal(row["entry_atr"])),
        closes_entry_number=row["closes_entry_number"],
        entry_group_id=row["entry_group_id"],
        group_take_profit_price=(
            None
            if row["group_take_profit_price"] is None
            else Decimal(row["group_take_profit_price"])
        ),
    )


def _trade_fill_history_item_from_row(row: sqlite3.Row) -> TradeFillHistoryItem:
    return TradeFillHistoryItem(
        fill=_fill_from_row(row),
        symbol=row["symbol"],
        timeframe=row["timeframe"],
        market_type=MarketType(row["market_type"]),
        trade_mode=TradeMode(row["trade_mode"]),
    )
