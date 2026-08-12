from datetime import datetime, timedelta, timezone

# Every stored instant stays UTC: candles, fills and ranges are compared and
# persisted in UTC, and only the screen speaks the trader's wall clock.
# Thailand has held a single +07:00 offset since 1920 and keeps no daylight
# saving, so a fixed offset needs no tz database to stay correct.
DISPLAY_TIMEZONE = timezone(timedelta(hours=7), "UTC+7")


def in_display_timezone(value: datetime) -> datetime:
    """Read a UTC instant on the Bangkok wall clock."""
    if value.tzinfo is None:
        raise ValueError("display time requires an aware datetime")
    return value.astimezone(DISPLAY_TIMEZONE)


def display_timestamp(value: datetime) -> str:
    """A stamp to the second, as every table and log line in the app writes it."""
    return in_display_timezone(value).strftime("%Y-%m-%d %H:%M:%S")
