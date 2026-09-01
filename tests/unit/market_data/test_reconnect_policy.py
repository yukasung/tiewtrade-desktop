"""How long a reconnect waits, and who decided the length."""

from datetime import UTC, datetime, timedelta

import pytest

from tiewtrade.market_data.reconnect_policy import (
    reconnect_backoff_seconds,
    retry_after_seconds,
)
from tiewtrade.market_data.source_errors import MarketDataRateLimitError

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    ("retry_after", "expected"),
    [
        pytest.param(timedelta(seconds=12), 12.0, id="duration"),
        pytest.param(timedelta(seconds=-5), 0.0, id="duration-already-passed"),
        pytest.param(NOW + timedelta(seconds=30), 30.0, id="instant"),
        pytest.param(NOW - timedelta(seconds=30), 0.0, id="instant-already-passed"),
        pytest.param(None, 60.0, id="unstated"),
    ],
)
def test_a_rate_limit_is_waited_out_for_as_long_as_it_asked(
    retry_after: timedelta | datetime | None, expected: float
) -> None:
    """A pause is never negative, and a refusal that names none still earns one."""
    error = MarketDataRateLimitError("slow down", retry_after=retry_after)

    assert retry_after_seconds(error, now=NOW) == expected


@pytest.mark.parametrize(
    ("attempt", "expected"),
    [
        pytest.param(1, 1.0, id="first"),
        pytest.param(2, 2.0, id="second"),
        pytest.param(3, 4.0, id="third"),
        pytest.param(4, 8.0, id="fourth"),
        pytest.param(5, 16.0, id="fifth"),
        pytest.param(6, 30.0, id="capped"),
        pytest.param(20, 30.0, id="capped-long-after"),
    ],
)
def test_a_reconnect_waits_longer_each_try_up_to_a_ceiling(
    attempt: int, expected: float
) -> None:
    """A blip is waited out in a second; an outage is not hammered forever."""
    assert reconnect_backoff_seconds(attempt) == expected


def test_a_reconnect_has_no_try_before_the_first() -> None:
    with pytest.raises(ValueError, match="attempt must be at least 1"):
        reconnect_backoff_seconds(0)
