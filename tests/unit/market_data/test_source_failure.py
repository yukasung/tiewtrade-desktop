from datetime import UTC, datetime

import pytest

from tiewtrade.market_data.candle_pipeline import (
    CandlePipelineInputError,
    CandlePipelineSinkError,
)
from tiewtrade.market_data.runtime_state import MarketDataRuntimeReason
from tiewtrade.market_data.source_errors import (
    MarketDataFailureKind,
    MarketDataFatalError,
    MarketDataRetryableError,
    MarketDataTimeoutError,
)
from tiewtrade.market_data.source_failure import (
    WarmUpSinkError,
    WarmUpSourceError,
    failure_kind,
    source_failure,
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
            MarketDataRetryableError("flaky", kind=MarketDataFailureKind.TRANSPORT),
            MarketDataRuntimeReason.SOURCE_ERROR,
            MarketDataFailureKind.TRANSPORT,
            id="retryable",
        ),
        pytest.param(
            WarmUpSinkError(),
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
        failure = source_failure(error, on_timeout=on_timeout)
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
        source_failure(error, on_timeout=WARM_UP_TIMEOUT).reason
        is MarketDataRuntimeReason.WARM_UP_TIMEOUT
    )
    assert (
        source_failure(error, on_timeout=BACKFILL_TIMEOUT).reason
        is MarketDataRuntimeReason.SOURCE_ERROR
    )


def test_a_timeout_is_not_swallowed_by_the_retryable_base_it_inherits() -> None:
    """MarketDataTimeoutError is a MarketDataRetryableError, so order matters."""
    assert issubclass(MarketDataTimeoutError, MarketDataRetryableError)

    assert (
        source_failure(
            MarketDataTimeoutError("too slow"), on_timeout=WARM_UP_TIMEOUT
        ).reason
        is MarketDataRuntimeReason.WARM_UP_TIMEOUT
    )


def test_a_warm_up_wrapper_still_names_the_kind_the_source_gave() -> None:
    """Warm-up wraps what the source raised, and the diagnostic has to survive it.

    A wrapper that swallowed the kind reported every warm-up failure as
    unclassified, which is the one thing an operator cannot act on.
    """
    wrapped = WarmUpSourceError()
    wrapped.__cause__ = MarketDataRetryableError(
        "flaky", kind=MarketDataFailureKind.TRANSPORT
    )

    assert failure_kind(wrapped) is MarketDataFailureKind.TRANSPORT


def test_a_wrapper_over_something_that_named_no_kind_names_none() -> None:
    wrapped = WarmUpSourceError()
    wrapped.__cause__ = RuntimeError("something else entirely")

    assert failure_kind(wrapped) is None
