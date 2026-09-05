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
    # The window a trader asked for, not a moment on any one record. Each
    # table dates a row by the moment that matters for it: a Fill by when it
    # landed, a round by when it closed — or, while it is still open, by when
    # it opened, which is the only moment it has.
    from_utc: datetime | None = None
    before_utc: datetime | None = None

    def __post_init__(self) -> None:
        if self.symbol == "":
            raise ValueError("symbol must not be empty")
        if self.timeframe == "":
            raise ValueError("timeframe must not be empty")
        _require_utc(self.from_utc, "from_utc")
        _require_utc(self.before_utc, "before_utc")
        if (
            self.from_utc is not None
            and self.before_utc is not None
            and self.from_utc >= self.before_utc
        ):
            raise ValueError("from_utc must be before before_utc")


@dataclass(frozen=True, slots=True)
class BasketHistoryResult:
    items: tuple[BasketResult, ...]
    realized_pnl: Decimal


@dataclass(frozen=True, slots=True)
class TradeFillHistoryItem:
    fill: TradeFill
    symbol: str
    timeframe: str
    market_type: MarketType
    trade_mode: TradeMode
