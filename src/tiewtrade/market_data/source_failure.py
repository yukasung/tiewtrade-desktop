"""Reading what a failure means, once, so every stage reports it the same way.

A source can fail at warm-up, mid-stream, or during a backfill, and the same
fault means the same thing at each of them: fatal is fatal, a sink error is the
Session's own doing, a rate limit is a pause. Stated at each stage instead, the
three drifted — which is how the same dropped connection came to be reported
under two different reasons depending on when it happened.

Only the meaning of a timeout differs between stages, so that is the one thing
the caller says: warm-up has a deadline of its own to name, while a backfill
that times out is just a source that did not answer.
"""

from dataclasses import dataclass

from tiewtrade.market_data.candle_pipeline import CandlePipelineSinkError
from tiewtrade.market_data.runtime_state import MarketDataRuntimeReason
from tiewtrade.market_data.source_errors import (
    MarketDataFailureKind,
    MarketDataFatalError,
    MarketDataSourceError,
    MarketDataTimeoutError,
)


class WarmUpSourceError(Exception):
    """Warm-up could not get its candles, whatever the source blamed."""


class WarmUpSinkError(Exception):
    """Warm-up got its candles and the Session would not take them."""


@dataclass(frozen=True, slots=True)
class SourceFailure:
    """Why a source operation failed, in the terms the runtime reports."""

    reason: MarketDataRuntimeReason
    kind: MarketDataFailureKind | None


def source_failure(
    error: BaseException,
    *,
    on_timeout: MarketDataRuntimeReason,
) -> SourceFailure:
    """Classify one failure into the reason and kind the runtime reports."""
    kind = failure_kind(error)
    if isinstance(error, MarketDataFatalError):
        return SourceFailure(MarketDataRuntimeReason.SOURCE_FATAL, kind)
    if isinstance(error, WarmUpSinkError | CandlePipelineSinkError):
        return SourceFailure(MarketDataRuntimeReason.SINK_ERROR, kind)
    # Checked before the retryable base it inherits from, which would swallow it.
    if isinstance(error, MarketDataTimeoutError | TimeoutError):
        return SourceFailure(on_timeout, kind)
    return SourceFailure(MarketDataRuntimeReason.SOURCE_ERROR, kind)


def failure_kind(error: BaseException) -> MarketDataFailureKind | None:
    """The diagnostic kind the source named, through a warm-up wrapper if any."""
    if isinstance(error, MarketDataSourceError):
        return error.kind
    if isinstance(error, WarmUpSourceError) and isinstance(
        error.__cause__, MarketDataSourceError
    ):
        return error.__cause__.kind
    return None
