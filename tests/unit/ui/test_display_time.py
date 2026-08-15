from datetime import UTC, date, datetime, timedelta

import pytest

from tiewtrade.ui.display_time import display_today, in_display_timezone


def test_display_time_reads_utc_on_the_bangkok_wall_clock() -> None:
    stored = datetime(2026, 8, 11, 15, 46, tzinfo=UTC)

    shown = in_display_timezone(stored)

    assert shown.hour == 22
    assert shown.minute == 46
    assert shown.utcoffset() == timedelta(hours=7)
    # The same instant, only read differently: nothing is re-stamped.
    assert shown == stored


def test_display_time_crosses_the_date_at_seventeen_hundred_utc() -> None:
    """Bangkok is already tomorrow while UTC still reads the old date."""
    shown = in_display_timezone(datetime(2026, 8, 11, 17, 30, tzinfo=UTC))

    assert shown.date() == datetime(2026, 8, 12).date()
    assert shown.hour == 0
    assert shown.minute == 30


def test_today_is_the_traders_day_not_the_machines() -> None:
    """A trader in Bangkok picking "today" means the Bangkok day."""
    late_utc_evening = datetime(2026, 8, 11, 18, 30, tzinfo=UTC)

    assert display_today(now=late_utc_evening) == date(2026, 8, 12)
    assert display_today(now=datetime(2026, 8, 11, 16, 30, tzinfo=UTC)) == date(
        2026, 8, 11
    )


def test_display_time_refuses_a_datetime_without_a_zone() -> None:
    with pytest.raises(ValueError, match="aware datetime"):
        in_display_timezone(datetime(2026, 8, 11, 15, 46))
