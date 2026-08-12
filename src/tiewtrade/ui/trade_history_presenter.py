from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from tiewtrade.application.trade_history import TradeFillHistoryItem, TradeHistoryFilter
from tiewtrade.trading.session_config import MarketType, TradeMode
from tiewtrade.trading.trade_history import BasketResult, BasketStatus, FillSide
from tiewtrade.ui.display_time import DISPLAY_TIMEZONE, display_timestamp
from tiewtrade.ui.table_column import CENTER, LEFT, RIGHT, Column
from tiewtrade.ui.theme import NEGATIVE_COLOR, POSITIVE_COLOR


@dataclass(frozen=True, slots=True)
class TradeHistoryFilterValues:
    symbol: str | None = None
    timeframe: str | None = None
    market_type: str | None = None
    trade_mode: str | None = None
    status: str | None = None
    from_date: date | None = None
    to_date: date | None = None


def trade_history_filter(values: TradeHistoryFilterValues) -> TradeHistoryFilter:
    if values.from_date is not None and values.to_date is not None:
        if values.from_date > values.to_date:
            raise ValueError("From Date must not be after To Date")

    return TradeHistoryFilter(
        symbol=values.symbol,
        timeframe=values.timeframe,
        market_type=(
            MarketType(values.market_type) if values.market_type is not None else None
        ),
        trade_mode=(
            TradeMode(values.trade_mode) if values.trade_mode is not None else None
        ),
        status=BasketStatus(values.status) if values.status is not None else None,
        opened_from_utc=_utc_start(values.from_date),
        opened_before_utc=(
            _utc_start(values.to_date + timedelta(days=1))
            if values.to_date is not None
            else None
        ),
    )


BASKET_COLUMNS: tuple[Column[BasketResult], ...] = (
    Column("Opened At", LEFT, lambda basket: local_text(basket.opened_at_utc)),
    Column("Mode", CENTER, lambda basket: display_enum(basket.trade_mode.value)),
    Column("Market", CENTER, lambda basket: display_enum(basket.market_type.value)),
    Column("Symbol", LEFT, lambda basket: basket.symbol),
    Column("Timeframe", CENTER, lambda basket: basket.timeframe),
    Column("Entries", CENTER, lambda basket: str(basket.entry_count)),
    Column("Notional", RIGHT, lambda basket: usdt_text(basket.invested_notional)),
    Column("Gross PnL", RIGHT, lambda basket: usdt_text(basket.gross_realized_pnl)),
    Column("Fees", RIGHT, lambda basket: usdt_text(basket.trading_fees)),
    Column("Funding Fee", RIGHT, lambda basket: usdt_text(basket.funding_fee)),
    Column(
        "Net PnL",
        RIGHT,
        lambda basket: pnl_text(basket.net_realized_pnl),
        lambda basket: sign_colour(basket.net_realized_pnl),
    ),
    Column("Status", LEFT, lambda basket: display_enum(basket.status.value)),
)

FILL_COLUMNS: tuple[Column[TradeFillHistoryItem], ...] = (
    Column("Filled At", LEFT, lambda item: local_text(item.fill.filled_at_utc)),
    Column("Symbol", LEFT, lambda item: item.symbol),
    Column(
        "Side",
        CENTER,
        lambda item: display_enum(item.fill.side.value),
        lambda item: _side_colour(item.fill.side),
    ),
    Column("Price", RIGHT, lambda item: decimal_text(item.fill.price)),
    Column("Quantity", RIGHT, lambda item: decimal_text(item.fill.quantity)),
    Column("Fee", RIGHT, lambda item: usdt_text(item.fill.commission)),
    Column("Total", RIGHT, lambda item: usdt_text(item.fill.notional)),
    Column(
        "Realized PnL",
        RIGHT,
        lambda item: pnl_text(item.fill.realized_pnl),
        lambda item: sign_colour(item.fill.realized_pnl),
    ),
    Column("Source", LEFT, lambda item: display_enum(item.fill.source.value)),
)


def sign_colour(value: Decimal) -> str | None:
    """No colour at break-even: neither reading is true, so the cell stays plain."""
    if value > 0:
        return POSITIVE_COLOR
    if value < 0:
        return NEGATIVE_COLOR
    return None


def _side_colour(side: FillSide) -> str:
    """Buying and selling read as the two directions a price can move."""
    return POSITIVE_COLOR if side is FillSide.BUY else NEGATIVE_COLOR


def pnl_text(value: Decimal) -> str:
    if value > 0:
        label = "Profit"
    elif value < 0:
        label = "Loss"
    else:
        label = "Break-even"
    return f"{decimal_text(value)} USDT · {label}"


def _utc_start(value: date | None) -> datetime | None:
    """Midnight of a picked day, read on the clock the rows are shown in."""
    if value is None:
        return None
    local = datetime(
        value.year,
        value.month,
        value.day,
        tzinfo=DISPLAY_TIMEZONE,
    )
    return local.astimezone(UTC)


def local_text(value: datetime) -> str:
    return display_timestamp(value)


def usdt_text(value: Decimal) -> str:
    return f"{decimal_text(value)} USDT"


def decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0.00"
    return format(value, "f")


def display_enum(value: str) -> str:
    return value.replace("_", " ").title()
