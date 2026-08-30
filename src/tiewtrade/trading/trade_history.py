from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from tiewtrade.trading.session_config import MarketType, TradeMode


class BasketStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class FillSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class FillSource(StrEnum):
    PAPER_EXECUTOR = "paper_executor"
    BINANCE = "binance"


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must use UTC")


def _require_non_negative(value: Decimal, field: str) -> None:
    if not value.is_finite() or value < 0:
        raise ValueError(f"{field} must be a finite non-negative decimal")


def _require_finite(value: Decimal, field: str) -> None:
    if not value.is_finite():
        raise ValueError(f"{field} must be finite")


def _require_positive(value: Decimal, field: str) -> None:
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{field} must be a finite positive decimal")


def _require_non_empty(value: str, field: str) -> None:
    if not value:
        raise ValueError(f"{field} must not be empty")


@dataclass(frozen=True, slots=True)
class TradeFill:
    fill_id: str
    basket_id: UUID
    session_id: UUID
    order_id: str
    exchange_trade_id: str | None
    side: FillSide
    entry_number: int | None
    filled_at_utc: datetime
    price: Decimal
    quantity: Decimal
    notional: Decimal
    realized_pnl: Decimal
    source: FillSource
    # The ATR the Entry was read against. A Basket's take profit is derived
    # from it, so without it a Basket that outlives its Runtime cannot be put
    # back together. An Exit is not measured against one, and Fills recorded
    # before this was kept do not have one.
    entry_atr: Decimal | None = None
    # The Entry number this Fill sold. An Exit that took the whole Basket
    # sold no single Entry and leaves it None.
    closes_entry_number: int | None = None
    # The Entry Group this Fill bought into or sold out of. A Group's target
    # only ever moved closer, and where it stopped depends on Entries that
    # have since gone, so it cannot be worked out again from the Entries left
    # open — every Fill that moved it records where it moved to instead. The
    # target is None where the Fill's sale left the Group with nothing, and
    # both are None for a Fill that took the whole Basket at once and for
    # Fills recorded before Groups were kept.
    entry_group_id: int | None = None
    group_take_profit_price: Decimal | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.fill_id, "fill_id")
        _require_non_empty(self.order_id, "order_id")
        if self.exchange_trade_id is not None:
            _require_non_empty(self.exchange_trade_id, "exchange_trade_id")
        _require_utc(self.filled_at_utc, "filled_at_utc")
        _require_positive(self.price, "price")
        _require_positive(self.quantity, "quantity")
        _require_finite(self.notional, "notional")
        if self.notional != self.price * self.quantity:
            raise ValueError("notional must equal price * quantity")
        _require_finite(self.realized_pnl, "realized_pnl")
        if self.entry_number is not None and self.entry_number <= 0:
            raise ValueError("entry_number must be positive when present")
        if self.entry_atr is not None:
            if self.entry_number is None:
                raise ValueError("entry_atr belongs to an Entry Fill")
            _require_non_negative(self.entry_atr, "entry_atr")
        if self.closes_entry_number is not None:
            if self.entry_number is not None:
                raise ValueError("closes_entry_number belongs to an Exit Fill")
            if self.closes_entry_number <= 0:
                raise ValueError("closes_entry_number must be positive")
        if self.entry_group_id is not None and self.entry_group_id <= 0:
            raise ValueError("entry_group_id must be positive when present")
        if self.group_take_profit_price is not None:
            if self.entry_group_id is None:
                raise ValueError("a take profit price belongs to a Group")
            _require_positive(self.group_take_profit_price, "group_take_profit_price")


@dataclass(frozen=True, slots=True)
class BasketResult:
    basket_id: UUID
    session_id: UUID
    trade_mode: TradeMode
    market_type: MarketType
    symbol: str
    timeframe: str
    strategy_preset_version: str
    opened_at_utc: datetime
    closed_at_utc: datetime | None
    entry_count: int
    invested_notional: Decimal
    realized_pnl: Decimal
    status: BasketStatus
    leverage: int | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.symbol, "symbol")
        _require_non_empty(self.timeframe, "timeframe")
        _require_non_empty(self.strategy_preset_version, "strategy_preset_version")
        if self.market_type is MarketType.FUTURES:
            if (
                isinstance(self.leverage, bool)
                or not isinstance(self.leverage, int)
                or not 1 <= self.leverage <= 5
            ):
                raise ValueError("Futures Basket requires leverage between 1 and 5")
        elif self.leverage is not None:
            raise ValueError("Spot Basket must not have leverage")
        _require_utc(self.opened_at_utc, "opened_at_utc")
        if self.closed_at_utc is not None:
            _require_utc(self.closed_at_utc, "closed_at_utc")
        if self.entry_count < 1:
            raise ValueError("entry_count must be at least 1")
        _require_non_negative(self.invested_notional, "invested_notional")
        _require_finite(self.realized_pnl, "realized_pnl")
        if self.status is BasketStatus.OPEN and self.closed_at_utc is not None:
            raise ValueError("open Basket must not have closed_at_utc")
        if self.status is BasketStatus.CLOSED and self.closed_at_utc is None:
            raise ValueError("closed Basket requires closed_at_utc")
