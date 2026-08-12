from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from tiewtrade.trading.session_config import MarketType, TradeMode
from tiewtrade.trading.trade_history import BasketResult, BasketStatus, TradeFill


def _require_utc(value: datetime | None, field: str) -> None:
    if value is not None and (
        value.tzinfo is None or value.utcoffset() != timedelta(0)
    ):
        raise ValueError(f"{field} must use UTC")


@dataclass(frozen=True, slots=True)
class TradeHistoryFilter:
    symbol: str | None = None
    timeframe: str | None = None
    market_type: MarketType | None = None
    trade_mode: TradeMode | None = None
    status: BasketStatus | None = None
    opened_from_utc: datetime | None = None
    opened_before_utc: datetime | None = None

    def __post_init__(self) -> None:
        if self.symbol == "":
            raise ValueError("symbol must not be empty")
        if self.timeframe == "":
            raise ValueError("timeframe must not be empty")
        _require_utc(self.opened_from_utc, "opened_from_utc")
        _require_utc(self.opened_before_utc, "opened_before_utc")
        if (
            self.opened_from_utc is not None
            and self.opened_before_utc is not None
            and self.opened_from_utc >= self.opened_before_utc
        ):
            raise ValueError("opened_from_utc must be before opened_before_utc")


@dataclass(frozen=True, slots=True)
class BasketHistoryResult:
    items: tuple[BasketResult, ...]
    net_realized_pnl: Decimal


@dataclass(frozen=True, slots=True)
class TradeFillHistoryItem:
    fill: TradeFill
    symbol: str
    timeframe: str
    market_type: MarketType
    trade_mode: TradeMode
