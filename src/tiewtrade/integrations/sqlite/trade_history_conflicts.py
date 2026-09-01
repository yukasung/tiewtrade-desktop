"""What must already be true before a Trade History row is written.

Every rule here answers the same question in a different place: would this
write leave two accounts of the same trade? A Fill that names another Basket,
an identity field edited after the fact, a partial Fill counted as a new Entry,
the same `fill_id` arriving twice with a different payload — each of them ends
with the ledger disagreeing with itself, which is the one state no reader can
recover from.

So they refuse rather than reconcile. A refused write leaves the record as it
was, which is a state someone can still act on.
"""

import sqlite3

from tiewtrade.integrations.sqlite.trade_history_errors import (
    TradeHistoryConflictError,
)
from tiewtrade.integrations.sqlite.trade_history_rows import find_fill
from tiewtrade.trading.session_config import MarketType, TradeMode
from tiewtrade.trading.trade_history import BasketResult, TradeFill


def validate_fill_ownership(basket: BasketResult, fill: TradeFill) -> None:
    if basket.basket_id != fill.basket_id:
        raise TradeHistoryConflictError("Fill belongs to a different Basket")
    if basket.session_id != fill.session_id:
        raise TradeHistoryConflictError("Fill belongs to a different Session")


def validate_immutable_identity(
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


def validate_paper_spot_identity(
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


def validate_entry_count(
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


def entry_count_after_fill(
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


def check_duplicate_fill(
    connection: sqlite3.Connection,
    fill: TradeFill,
) -> bool:
    existing = find_fill(connection, fill.fill_id)
    if existing is None:
        return False
    if existing != fill:
        raise TradeHistoryConflictError(
            f"fill_id {fill.fill_id!r} has conflicting payload"
        )
    return True
