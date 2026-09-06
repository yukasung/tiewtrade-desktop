"""The range a filter bar asks for, market-agnostic.

Trade History and Notifications both ask the same question of a different
record — "what happened between these two days?" — so the quick ranges, the
day picked, and how a day becomes the UTC bound a query reads are shared here
rather than kept once per screen.
"""

from calendar import monthrange
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import Enum

from tiewtrade.ui.display_time import DISPLAY_TIMEZONE


@dataclass(frozen=True, slots=True)
class DateRangeValues:
    """What a filter bar asks for: a range of days, either end open."""

    from_date: date | None = None
    to_date: date | None = None


class QuickRange(Enum):
    """The periods a filter bar offers as one click each."""

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


def default_date_range_values(today: date) -> DateRangeValues:
    """What a filter bar asks for before anyone has touched it.

    Both the bar and its workflow read the default from here, because a
    filter bar that says one thing while the query asks another is wrong in
    a way nothing on screen would show.
    """
    from_date, to_date = quick_range_dates(DEFAULT_QUICK_RANGE, today)
    return DateRangeValues(from_date=from_date, to_date=to_date)


def _months_before(value: date, months: int) -> date:
    """The same day of an earlier month, or its last day where that is short."""
    month_index = value.year * 12 + value.month - 1 - months
    year, month = divmod(month_index, 12)
    last_day = monthrange(year, month + 1)[1]
    return date(year, month + 1, min(value.day, last_day))


def validate_range(values: DateRangeValues) -> None:
    if (
        values.from_date is not None
        and values.to_date is not None
        and values.from_date > values.to_date
    ):
        raise ValueError("From Date must not be after To Date")


def utc_bounds(values: DateRangeValues) -> tuple[datetime | None, datetime | None]:
    """The window a query reads over: the picked days, read on the clock they
    were shown in, as the half-open `[from_utc, before_utc)` a record's own
    UTC stamp is compared against.
    """
    return (
        _utc_start(values.from_date),
        _utc_start(values.to_date + timedelta(days=1))
        if values.to_date is not None
        else None,
    )


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
