"""How long to wait before reaching for the source again.

Two different questions wear the same shape. A provider that refused us names
its own pause and we honour it; everything else — a dropped socket, a stall, a
timeout — gets a backoff of ours. Both answer in seconds, and the caller waits
that long either way, so they belong together.

Neither has a last attempt in it. A source that cannot be reached is waited
for, not given up on: the schedule climbs to a ceiling and then holds there for
as long as the outage lasts.
"""

from datetime import datetime, timedelta

from tiewtrade.market_data.source_errors import MarketDataRateLimitError

BACKOFF_BASE_SECONDS = 1.0
BACKOFF_CAP_SECONDS = 30.0
RATE_LIMIT_FALLBACK_SECONDS = 60.0


def reconnect_backoff_seconds(attempt: int) -> float:
    """How long to wait before the attempt-th try at reaching the source.

    Doubles from a second and then holds — 1, 2, 4, 8, 16, 30, 30 — so a blip
    is waited out in about the time it takes to notice one, while an outage
    that lasts is not asked about every few seconds for as long as it lasts.
    """
    if attempt < 1:
        raise ValueError("attempt must be at least 1")
    # Capped before it is doubled as well as after: an outage lasting days
    # would otherwise raise two to the power of every attempt it took.
    doublings = min(attempt - 1, 30)
    return min(BACKOFF_BASE_SECONDS * float(2**doublings), BACKOFF_CAP_SECONDS)


def retry_after_seconds(error: MarketDataRateLimitError, *, now: datetime) -> float:
    """How long the provider asked to be left alone, never less than nothing.

    Retry-After arrives either as a duration or as the instant to resume at,
    and a provider whose instant has already passed is asking for no wait at
    all rather than for a negative one. A refusal that names neither still
    earns a pause: retrying a rate limit immediately is how it is earned again.
    """
    retry_after = error.retry_after
    if isinstance(retry_after, timedelta):
        return max(0.0, retry_after.total_seconds())
    if isinstance(retry_after, datetime):
        return max(0.0, (retry_after - now).total_seconds())
    return RATE_LIMIT_FALLBACK_SECONDS
