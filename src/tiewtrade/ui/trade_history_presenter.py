from calendar import monthrange
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import Enum

from tiewtrade.application.trade_history import TradeFillHistoryItem, TradeHistoryFilter
from tiewtrade.trading.trade_history import BasketResult, FillSide
from tiewtrade.ui.display_time import DISPLAY_TIMEZONE, display_timestamp
from tiewtrade.ui.money_text import money_text, price_text
from tiewtrade.ui.sign_state import sign_colour
from tiewtrade.ui.table_column import CENTER, LEFT, RIGHT, Column
from tiewtrade.ui.theme import NEGATIVE_COLOR, POSITIVE_COLOR


@dataclass(frozen=True, slots=True)
class TradeHistoryFilterValues:
    """What the filter bar asks for: a range of days, either end open."""

    from_date: date | None = None
    to_date: date | None = None


class QuickRange(Enum):
    """The periods the filter bar offers as one click each."""

    ONE_DAY = "1 Day"
    ONE_WEEK = "1 Week"
    ONE_MONTH = "1 Month"
    THREE_MONTHS = "3 Months"


def quick_range_dates(kind: QuickRange, today: date) -> tuple[date, date]:
    """The window a quick range asks for, counted back from today.

    Every window ends today and includes it, so "1 Week" returns seven days
    of trading rather than eight.
    """
    if kind is QuickRange.ONE_DAY:
        return today, today
    if kind is QuickRange.ONE_WEEK:
        return today - timedelta(days=6), today
    months = 1 if kind is QuickRange.ONE_MONTH else 3
    return _months_before(today, months) + timedelta(days=1), today


def _months_before(value: date, months: int) -> date:
    """The same day of an earlier month, or its last day where that is short."""
    month_index = value.year * 12 + value.month - 1 - months
    year, month = divmod(month_index, 12)
    last_day = monthrange(year, month + 1)[1]
    return date(year, month + 1, min(value.day, last_day))


def trade_history_filter(values: TradeHistoryFilterValues) -> TradeHistoryFilter:
    if values.from_date is not None and values.to_date is not None:
        if values.from_date > values.to_date:
            raise ValueError("From Date must not be after To Date")

    return TradeHistoryFilter(
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
    # "Total" is what an exchange calls price × quantity, and what the Fills
    # table beside this one already calls it. One word for one idea.
    Column("Total", RIGHT, lambda basket: usdt_text(basket.invested_notional)),
    # Gross PnL, Fees and Funding Fee are the three terms Net PnL is made of,
    # and Net PnL is the figure that is read. The record still holds all three.
    Column(
        "Net PnL",
        RIGHT,
        lambda basket: usdt_text(basket.net_realized_pnl),
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
    Column("Price", RIGHT, lambda item: price_text(item.fill.price)),
    Column("Quantity", RIGHT, lambda item: decimal_text(item.fill.quantity)),
    Column("Fee", RIGHT, lambda item: usdt_text(item.fill.commission)),
    Column("Total", RIGHT, lambda item: usdt_text(item.fill.notional)),
    Column(
        "Realized PnL",
        RIGHT,
        lambda item: usdt_text(item.fill.realized_pnl),
        lambda item: sign_colour(item.fill.realized_pnl),
    ),
    # Source is on the record but off the screen: every Fill a Paper Bot makes
    # says the same thing, so the column reads the same on every row.
)


def _side_colour(side: FillSide) -> str:
    """Buying and selling read as the two directions a price can move."""
    return POSITIVE_COLOR if side is FillSide.BUY else NEGATIVE_COLOR


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
    return money_text(value)


def decimal_text(value: Decimal) -> str:
    """A quantity, which is counted in the asset rather than in money."""
    if value == 0:
        return "0.00"
    return format(value, "f")


def display_enum(value: str) -> str:
    return value.replace("_", " ").title()
