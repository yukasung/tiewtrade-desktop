from datetime import UTC, datetime, timedelta

import pytest

from tiewtrade.market_data.candle_pipeline import (
    CandlePipelineInputError,
    CandlePipelineSinkError,
)
from tiewtrade.market_data.runtime import (
    _retry_after_seconds,
    _source_failure,
    _WarmUpSinkError,
)
from tiewtrade.market_data.runtime_state import MarketDataRuntimeReason
from tiewtrade.market_data.source_errors import (
    MarketDataFailureKind,
    MarketDataFatalError,
    MarketDataRateLimitError,
    MarketDataRetryableError,
    MarketDataTimeoutError,
)

BACKFILL_TIMEOUT = MarketDataRuntimeReason.SOURCE_ERROR
WARM_UP_TIMEOUT = MarketDataRuntimeReason.WARM_UP_TIMEOUT
NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    ("error", "expected_reason", "expected_kind"),
    [
        pytest.param(
            MarketDataFatalError("gone", kind=MarketDataFailureKind.PAYLOAD),
            MarketDataRuntimeReason.SOURCE_FATAL,
            MarketDataFailureKind.PAYLOAD,
            id="fatal",
        ),
        pytest.param(
            MarketDataRateLimitError("slow down", retry_after=None),
            MarketDataRuntimeReason.RATE_LIMIT_EXHAUSTED,
            MarketDataFailureKind.PROTOCOL,
            id="rate-limited",
        ),
        pytest.param(
            MarketDataRetryableError("flaky", kind=MarketDataFailureKind.TRANSPORT),
            MarketDataRuntimeReason.SOURCE_ERROR,
            MarketDataFailureKind.TRANSPORT,
            id="retryable",
        ),
        pytest.param(
            _WarmUpSinkError(),
            MarketDataRuntimeReason.SINK_ERROR,
            None,
            id="warm-up-sink",
        ),
        pytest.param(
            CandlePipelineSinkError(),
            MarketDataRuntimeReason.SINK_ERROR,
            None,
            id="pipeline-sink",
        ),
        pytest.param(
            CandlePipelineInputError(),
            MarketDataRuntimeReason.SOURCE_ERROR,
            None,
            id="pipeline-input",
        ),
        pytest.param(
            RuntimeError("something else entirely"),
            MarketDataRuntimeReason.SOURCE_ERROR,
            None,
            id="unrecognised",
        ),
    ],
)
def test_a_failure_reads_the_same_wherever_it_happens(
    error: BaseException,
    expected_reason: MarketDataRuntimeReason,
    expected_kind: MarketDataFailureKind | None,
) -> None:
    """Only a timeout depends on the stage; every other kind is stage-agnostic."""
    for on_timeout in (WARM_UP_TIMEOUT, BACKFILL_TIMEOUT):
        failure = _source_failure(error, on_timeout=on_timeout)
        assert failure.reason is expected_reason
        assert failure.kind is expected_kind


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(MarketDataTimeoutError("too slow"), id="source-timeout"),
        pytest.param(TimeoutError(), id="asyncio-timeout"),
    ],
)
def test_a_timeout_is_named_by_the_stage_that_ran_out(error: BaseException) -> None:
    """Warm-up has a deadline of its own; a backfill just did not get an answer."""
    assert (
        _source_failure(error, on_timeout=WARM_UP_TIMEOUT).reason
        is MarketDataRuntimeReason.WARM_UP_TIMEOUT
    )
    assert (
        _source_failure(error, on_timeout=BACKFILL_TIMEOUT).reason
        is MarketDataRuntimeReason.SOURCE_ERROR
    )


def test_a_timeout_is_not_swallowed_by_the_retryable_base_it_inherits() -> None:
    """MarketDataTimeoutError is a MarketDataRetryableError, so order matters."""
    assert issubclass(MarketDataTimeoutError, MarketDataRetryableError)

    assert (
        _source_failure(
            MarketDataTimeoutError("too slow"), on_timeout=WARM_UP_TIMEOUT
        ).reason
        is MarketDataRuntimeReason.WARM_UP_TIMEOUT
    )


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

    assert _retry_after_seconds(error, now=NOW) == expected
