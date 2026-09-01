"""Reading and writing the Trade History rows themselves.

Nothing here decides whether a write is allowed — it is the translation
either way across the boundary: a `sqlite3.Row` into the Basket or Fill it
stands for, and a Basket or Fill back into the columns that hold it.

Decimals are stored as text and read back as text, so a figure comes out of
the database as the same figure that went in. A float would round a price the
moment it was written, and a trade booked at a price nobody quoted is worse
than one that will not save.
"""

import sqlite3
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from tiewtrade.application.trade_history import TradeFillHistoryItem
from tiewtrade.integrations.sqlite.trade_history_errors import (
    TradeHistoryConflictError,
)
from tiewtrade.trading.session_config import MarketType, TradeMode
from tiewtrade.trading.trade_history import (
    BasketResult,
    BasketStatus,
    FillSide,
    FillSource,
    TradeFill,
)


def find_basket(
    connection: sqlite3.Connection,
    basket_id: UUID,
) -> BasketResult | None:
    row = connection.execute(
        "SELECT * FROM basket_results WHERE basket_id = ?",
        (str(basket_id),),
    ).fetchone()
    return basket_from_row(row) if row is not None else None


def require_basket(
    connection: sqlite3.Connection,
    basket_id: UUID,
) -> BasketResult:
    basket = find_basket(connection, basket_id)
    if basket is None:
        raise TradeHistoryConflictError("Basket does not exist")
    return basket


def find_fill(
    connection: sqlite3.Connection,
    fill_id: str,
) -> TradeFill | None:
    row = connection.execute(
        "SELECT * FROM trade_fills WHERE fill_id = ?",
        (fill_id,),
    ).fetchone()
    return fill_from_row(row) if row is not None else None


def update_basket(
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
        (*basket_values(basket)[1:], str(basket.basket_id)),
    )
    if cursor.rowcount != 1:
        raise TradeHistoryConflictError("Basket does not exist")


def insert_basket(
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
        basket_values(basket),
    )


def basket_values(basket: BasketResult) -> tuple[object, ...]:
    return (
        str(basket.basket_id),
        str(basket.session_id),
        basket.trade_mode.value,
        basket.market_type.value,
        basket.leverage,
        basket.symbol,
        basket.timeframe,
        basket.strategy_preset_version,
        utc_text(basket.opened_at_utc),
        utc_text(basket.closed_at_utc) if basket.closed_at_utc is not None else None,
        basket.entry_count,
        stored_decimal(basket.invested_notional),
        stored_decimal(basket.realized_pnl),
        basket.status.value,
    )


def insert_fill(connection: sqlite3.Connection, fill: TradeFill) -> None:
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
            utc_text(fill.filled_at_utc),
            stored_decimal(fill.price),
            stored_decimal(fill.quantity),
            stored_decimal(fill.notional),
            stored_decimal(fill.realized_pnl),
            fill.source.value,
            None if fill.entry_atr is None else stored_decimal(fill.entry_atr),
            fill.closes_entry_number,
            fill.entry_group_id,
            (
                None
                if fill.group_take_profit_price is None
                else stored_decimal(fill.group_take_profit_price)
            ),
        ),
    )


def stored_decimal(value: Decimal) -> str:
    """A figure as the database keeps it, to every place it was given.

    Nothing is normalized away here. What a Fill was priced at is the
    record, and trimming it would change what the row says happened.
    """
    return format(value, "f")


def utc_text(value: datetime) -> str:
    return value.isoformat()


def basket_from_row(row: sqlite3.Row) -> BasketResult:
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


def fill_from_row(row: sqlite3.Row) -> TradeFill:
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


def trade_fill_history_item_from_row(row: sqlite3.Row) -> TradeFillHistoryItem:
    return TradeFillHistoryItem(
        fill=fill_from_row(row),
        symbol=row["symbol"],
        timeframe=row["timeframe"],
        market_type=MarketType(row["market_type"]),
        trade_mode=TradeMode(row["trade_mode"]),
    )
