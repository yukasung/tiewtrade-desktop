from calendar import monthrange
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import Enum

from tiewtrade.application.trade_history import TradeFillHistoryItem, TradeHistoryFilter
from tiewtrade.trading.trade_history import BasketResult, FillSide
from tiewtrade.ui.display_time import DISPLAY_TIMEZONE, display_timestamp
from tiewtrade.ui.money_text import money_text, price_text, realized_text
from tiewtrade.ui.pair_text import pair_text
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


DEFAULT_QUICK_RANGE = QuickRange.ONE_DAY


def default_filter_values(today: date) -> TradeHistoryFilterValues:
    """What the filter asks for before anyone has touched it.

    Both the bar and the workflow read the default from here, because a
    filter bar that says one thing while the query asks another is wrong in
    a way nothing on screen would show.
    """
    from_date, to_date = quick_range_dates(DEFAULT_QUICK_RANGE, today)
    return TradeHistoryFilterValues(from_date=from_date, to_date=to_date)


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
    Column("Date", LEFT, lambda basket: display_timestamp(basket.opened_at_utc)),
    Column("Market", CENTER, lambda basket: display_enum(basket.market_type.value)),
    Column("Pair", LEFT, lambda basket: pair_text(basket.symbol)),
    Column("Timeframe", CENTER, lambda basket: basket.timeframe),
    # How many Entries the round took, which is also part of why it ended: a
    # completed Entry Pair is what starts a Cooldown, and a Session out
    # of Entries never opens again. The Total alone cannot tell one large
    # Entry from four that scaled in.
    Column("Entries", CENTER, lambda basket: str(basket.entry_count)),
    # "Total" is what an exchange calls price × quantity, and what the Fills
    # table beside this one already calls it. One word for one idea.
    Column("Total", RIGHT, lambda basket: money_text(basket.invested_notional)),
    Column(
        "Profit / Loss",
        RIGHT,
        lambda basket: realized_text(basket.realized_pnl),
        lambda basket: sign_colour(basket.realized_pnl),
    ),
    # Mode and Status are on the record but off the screen: a Paper Bot writes
    # only Paper, and a round listed here is read by what it realised.
)

FILL_COLUMNS: tuple[Column[TradeFillHistoryItem], ...] = (
    Column("Date", LEFT, lambda item: display_timestamp(item.fill.filled_at_utc)),
    Column("Pair", LEFT, lambda item: pair_text(item.symbol)),
    Column(
        "Side",
        CENTER,
        lambda item: display_enum(item.fill.side.value),
        lambda item: _side_colour(item.fill.side),
    ),
    Column("Price", RIGHT, lambda item: price_text(item.fill.price)),
    Column("Quantity", RIGHT, lambda item: decimal_text(item.fill.quantity)),
    Column("Total", RIGHT, lambda item: money_text(item.fill.notional)),
    Column(
        "Profit / Loss",
        RIGHT,
        lambda item: realized_text(item.fill.realized_pnl),
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


def decimal_text(value: Decimal) -> str:
    """A quantity, which is counted in the asset rather than in money."""
    if value == 0:
        return "0.00"
    return format(value, "f")


def display_enum(value: str) -> str:
    return value.replace("_", " ").title()
