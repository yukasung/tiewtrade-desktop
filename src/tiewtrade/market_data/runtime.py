from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum, auto
from typing import Protocol, TypeVar

from tiewtrade.market_data.candle import Candle
from tiewtrade.market_data.candle_pipeline import (
    CandlePipelineIncompleteBackfillError,
    CandlePipelineInputError,
    CandlePipelineSinkError,
    CompletedCandlePipeline,
)
from tiewtrade.market_data.candle_pipeline import (
    MarketDataCandleSink as MarketDataCandleSink,
)
from tiewtrade.market_data.candle_source import (
    MarketDataCandleSource,
    MarketDataKlineUpdate,
)
from tiewtrade.market_data.completed_candle_stream import (
    CandleAcceptance,
    CandleGapError,
)
from tiewtrade.market_data.config import MarketDataConfig
from tiewtrade.market_data.runtime_logging import MarketDataRuntimeLog
from tiewtrade.market_data.runtime_state import (
    MarketDataRuntimeReason,
    MarketDataRuntimeSnapshot,
    MarketDataRuntimeState,
    MarketDataRuntimeStatus,
)
from tiewtrade.market_data.source_errors import (
    MarketDataFailureKind,
    MarketDataFatalError,
    MarketDataRateLimitError,
    MarketDataRetryableError,
    MarketDataSourceError,
    MarketDataTimeoutError,
)

_T = TypeVar("_T")
_WARM_UP_TIMEOUT_SECONDS = 30.0
_BACKFILL_TIMEOUT_SECONDS = 30.0
_STALE_GRACE = timedelta(seconds=30)
_RECONNECT_BACKOFF_BASE_SECONDS = 1.0
_RECONNECT_BACKOFF_CAP_SECONDS = 30.0
_RATE_LIMIT_FALLBACK_SECONDS = 60.0
# Binance marks a kline closed (k.x=true) authoritatively; local delivery can
# still arrive a moment before our wall clock reaches the boundary due to
# ordinary network/scheduling jitter. Tolerate a small window so that jitter
# doesn't get misread as clock skew and discarded.
_LIVE_CANDLE_CLOSE_TOLERANCE = timedelta(seconds=2)
_LOGGER = logging.getLogger("tiewtrade.market_data.runtime")


class _BackfillOutcome(Enum):
    """What a backfill left behind: candles, another attempt, or a dead run."""

    DELIVERED = auto()
    RECOVERABLE = auto()
    FAILED = auto()


class _LiveOutcome(Enum):
    """What the live loop does next with the stream it is reading."""

    CONTINUE = auto()
    RECOVER = auto()
    STOP = auto()


class _WarmUpSourceError(Exception):
    pass


class _WarmUpSinkError(Exception):
    pass


class RuntimeScheduler(Protocol):
    def now(self) -> datetime: ...

    async def sleep(self, seconds: float) -> None: ...

    async def wait_for(self, awaitable: Awaitable[_T], timeout: float) -> _T: ...


class AsyncioRuntimeScheduler:
    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    async def wait_for(self, awaitable: Awaitable[_T], timeout: float) -> _T:
        return await asyncio.wait_for(awaitable, timeout)


class MarketDataRuntime:
    def __init__(
        self,
        *,
        config: MarketDataConfig,
        warm_up_count: int,
        source: MarketDataCandleSource,
        sink: MarketDataCandleSink,
        scheduler: RuntimeScheduler | None = None,
        on_transition: Callable[[MarketDataRuntimeSnapshot], None] | None = None,
        on_open_candle: Callable[[Candle], None] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        if warm_up_count <= 0:
            raise ValueError("warm_up_count must be positive")

        self._config = config
        self._warm_up_count = warm_up_count
        self._source = source
        self._on_open_candle = on_open_candle
        self._scheduler = scheduler or AsyncioRuntimeScheduler()
        self._status = MarketDataRuntimeStatus(
            self._scheduler.now,
            on_transition=on_transition,
        )
        self._runtime_log = MarketDataRuntimeLog(
            logger or _LOGGER,
            symbol=config.symbol,
            timeframe=config.timeframe,
        )
        self._pipeline = CompletedCandlePipeline(
            config=config,
            sink=sink,
            on_delivery=self._status.record_delivery,
            not_closed_tolerance=_LIVE_CANDLE_CLOSE_TOLERANCE,
        )
        self._run_task: asyncio.Task[None] | None = None
        self._shutdown_task: asyncio.Task[None] | None = None
        self._stop_requested = False
        self._source_close_lock = asyncio.Lock()
        self._source_closed = False
        self._terminal_failure_kind: MarketDataFailureKind | None = None

    @property
    def snapshot(self) -> MarketDataRuntimeSnapshot:
        return self._status.snapshot

    async def run(self) -> None:
        if self._status.snapshot.state in {
            MarketDataRuntimeState.FAILED_CLOSED,
            MarketDataRuntimeState.STOPPED,
        }:
            return
        current_task = asyncio.current_task()
        if current_task is None:
            raise RuntimeError("runtime requires an asyncio task")
        if self._run_task is not None:
            raise RuntimeError("runtime is already running")
        self._run_task = current_task

        try:
            if not await self._warm_up():
                return
            await self._consume_live()
        except asyncio.CancelledError:
            if not self._stop_requested:
                self._fail_closed(MarketDataRuntimeReason.SOURCE_ERROR)
                raise
        finally:
            try:
                if (
                    not self._stop_requested
                    and self._status.snapshot.state
                    is MarketDataRuntimeState.FAILED_CLOSED
                ):
                    primary_reason = self._status.snapshot.reason
                    primary_failure_kind = self._terminal_failure_kind
                    try:
                        await self._close_source_once()
                    except Exception:
                        self._fail_closed(
                            primary_reason,
                            failure_kind=primary_failure_kind,
                        )
            finally:
                self._run_task = None

    async def stop(self) -> None:
        self._stop_requested = True
        if self._shutdown_task is None or self._shutdown_failed():
            self._shutdown_task = asyncio.create_task(self._shutdown())
        await asyncio.shield(self._shutdown_task)

    async def _shutdown(self) -> None:
        run_task = self._run_task
        if run_task is not None and not run_task.done():
            run_task.cancel()

        close_error: Exception | None = None
        try:
            await self._close_source_once()
        except Exception as error:
            close_error = error
            if self._status.snapshot.state is not MarketDataRuntimeState.FAILED_CLOSED:
                self._fail_closed(
                    MarketDataRuntimeReason.SOURCE_ERROR,
                    failure_kind=_failure_kind(error),
                )

        if run_task is not None:
            await asyncio.gather(run_task, return_exceptions=True)

        if close_error is not None:
            raise close_error
        self._transition_to_stopped()

    def _shutdown_failed(self) -> bool:
        assert self._shutdown_task is not None
        return (
            self._shutdown_task.done()
            and not self._shutdown_task.cancelled()
            and self._shutdown_task.exception() is not None
        )

    async def _warm_up(self) -> bool:
        self._transition(
            MarketDataRuntimeState.WARMING_UP,
            MarketDataRuntimeReason.START_REQUESTED,
        )
        completed_before = self._scheduler.now()
        try:
            await self._perform_warm_up(completed_before)
        except (
            MarketDataSourceError,
            _WarmUpSourceError,
            _WarmUpSinkError,
            TimeoutError,
        ) as error:
            failure = _source_failure(
                error,
                on_timeout=MarketDataRuntimeReason.WARM_UP_TIMEOUT,
            )
            self._fail_closed(failure.reason, failure_kind=failure.kind)
            return False

        self._transition(
            MarketDataRuntimeState.LIVE,
            MarketDataRuntimeReason.WARM_UP_COMPLETED,
        )
        return True

    async def _perform_warm_up(self, completed_before: datetime) -> None:
        try:
            candles = await self._run_source_operation(
                lambda: self._source.load_recent(
                    self._config,
                    count=self._warm_up_count,
                    completed_before=completed_before,
                ),
                timeout=_WARM_UP_TIMEOUT_SECONDS,
            )
        except MarketDataFatalError:
            raise
        except Exception as error:
            raise _WarmUpSourceError from error

        try:
            await self._scheduler.wait_for(
                self._pipeline.warm_up(
                    candles,
                    expected_count=self._warm_up_count,
                    received_at=completed_before,
                ),
                _WARM_UP_TIMEOUT_SECONDS,
            )
        except CandlePipelineInputError as error:
            raise _WarmUpSourceError from error
        except CandlePipelineSinkError as error:
            raise _WarmUpSinkError from error

    async def _consume_live(self) -> None:
        try:
            stream = self._source.stream_klines(self._config)
        except MarketDataFatalError as error:
            self._fail_closed(
                MarketDataRuntimeReason.SOURCE_FATAL,
                failure_kind=_failure_kind(error),
            )
            return
        except MarketDataRateLimitError as error:
            recovered_stream = await self._recover_stream(
                MarketDataRuntimeReason.RATE_LIMITED,
                pending_rate_limit=error,
            )
            if recovered_stream is None:
                return
            stream = recovered_stream
        except Exception:
            recovered_stream = await self._recover_stream(
                MarketDataRuntimeReason.SOURCE_DISCONNECTED
            )
            if recovered_stream is None:
                return
            stream = recovered_stream
        while not self._stop_requested:
            disconnect_reason: MarketDataRuntimeReason
            try:
                update = await self._next_before_stale(stream)
            except TimeoutError:
                disconnect_reason = MarketDataRuntimeReason.DATA_STALE
            except MarketDataFatalError as error:
                self._fail_closed(
                    MarketDataRuntimeReason.SOURCE_FATAL,
                    failure_kind=_failure_kind(error),
                )
                return
            except MarketDataRateLimitError as error:
                recovered_stream = await self._recover_stream(
                    MarketDataRuntimeReason.RATE_LIMITED,
                    pending_rate_limit=error,
                )
                if recovered_stream is None:
                    return
                stream = recovered_stream
                continue
            except Exception:
                disconnect_reason = MarketDataRuntimeReason.SOURCE_DISCONNECTED
            else:
                if not update.is_closed:
                    self._publish_open_candle(update.candle)
                    if not await self._deliver_open_candle(update.candle):
                        return
                    continue
                outcome = await self._accept_live_candle(update.candle)
                if outcome is _LiveOutcome.STOP:
                    return
                if outcome is _LiveOutcome.CONTINUE:
                    continue
                # A source that answered the gap short is one to ask again
                # from a fresh stream, at the boundary that holds by then.
                await _close_stream(stream)
                disconnect_reason = MarketDataRuntimeReason.SOURCE_DISCONNECTED

            recovered_stream = await self._recover_stream(disconnect_reason)
            if recovered_stream is None:
                return
            stream = recovered_stream

    async def _next_before_stale(
        self,
        stream: AsyncIterator[MarketDataKlineUpdate],
    ) -> MarketDataKlineUpdate:
        timeout = self._seconds_until(self._freshness_deadline())
        return await self._scheduler.wait_for(stream.__anext__(), timeout)

    async def _next_reconnect_candle(
        self,
        stream: AsyncIterator[MarketDataKlineUpdate],
        *,
        deadline: datetime,
    ) -> MarketDataKlineUpdate:
        timeout = self._seconds_until(deadline)
        return await self._scheduler.wait_for(stream.__anext__(), timeout)

    def _reconnect_deadline(self) -> datetime:
        now = self._scheduler.now()
        next_boundary = (
            _latest_completed_boundary(now, interval=self._config.interval)
            + self._config.interval
        )
        return next_boundary + _STALE_GRACE

    async def _deliver_open_candle(self, candle: Candle) -> bool:
        """Let the sink act on the forming candle; a sink failure fails closed.

        The chart callback above swallows its own errors because a read model
        cannot stop trading. This delivery can place an order, so it is held
        to the same terms as a completed candle.
        """
        try:
            await self._pipeline.process_in_progress(
                candle,
                received_at=self._scheduler.now(),
            )
        except CandlePipelineSinkError:
            self._fail_closed(MarketDataRuntimeReason.SINK_ERROR)
            return False
        return True

    def _publish_open_candle(self, candle: Candle) -> None:
        callback = self._on_open_candle
        if callback is None:
            return
        try:
            callback(candle)
        except Exception:
            pass

    def _seconds_until(self, deadline: datetime) -> float:
        return max(
            0.0,
            (deadline - self._scheduler.now()).total_seconds(),
        )

    def _freshness_deadline(self) -> datetime:
        last_accepted_open_time = self._status.snapshot.last_accepted_open_time
        if last_accepted_open_time is None:
            raise RuntimeError("freshness requires an accepted candle")
        expected_open_time = last_accepted_open_time + self._config.interval
        expected_close_boundary = expected_open_time + self._config.interval
        return expected_close_boundary + _STALE_GRACE

    async def _accept_live_candle(self, candle: Candle) -> _LiveOutcome:
        received_at = self._scheduler.now()
        try:
            decision = await self._pipeline.process_live(
                candle,
                received_at=received_at,
            )
        except CandleGapError:
            outcome = await self._backfill_through(candle, received_at=received_at)
            return _LIVE_OUTCOMES[outcome]
        except CandlePipelineInputError:
            self._fail_closed(MarketDataRuntimeReason.SOURCE_ERROR)
            return _LiveOutcome.STOP
        except CandlePipelineSinkError:
            self._fail_closed(MarketDataRuntimeReason.SINK_ERROR)
            return _LiveOutcome.STOP
        if decision is not CandleAcceptance.ACCEPTED:
            self._runtime_log.candle_discarded(
                open_time=candle.open_time,
                received_at=received_at,
                discard_reason=decision,
            )
            if decision is CandleAcceptance.NOT_CLOSED:
                self._runtime_log.clock_skew_detected(
                    open_time=candle.open_time,
                    close_time=candle.close_time,
                    received_at=received_at,
                )
            return _LiveOutcome.CONTINUE

        self._transition(
            MarketDataRuntimeState.LIVE,
            MarketDataRuntimeReason.LIVE_CANDLE_ACCEPTED,
        )
        return _LiveOutcome.CONTINUE

    async def _backfill_through(
        self,
        observed: Candle,
        *,
        received_at: datetime,
    ) -> _BackfillOutcome:
        return await self._backfill_to_boundary(
            observed.open_time + self._config.interval,
            received_at=received_at,
            observed=observed,
        )

    async def _backfill_to_boundary(
        self,
        end: datetime,
        *,
        received_at: datetime,
        observed: Candle,
    ) -> _BackfillOutcome:
        last_accepted_open_time = self._status.snapshot.last_accepted_open_time
        if last_accepted_open_time is None:
            return self._fail_backfill(
                start=end,
                end=end,
                failure=_SourceFailure(
                    MarketDataRuntimeReason.SOURCE_ERROR,
                    None,
                ),
            )

        start = last_accepted_open_time + self._config.interval
        self._transition(
            MarketDataRuntimeState.BACKFILLING,
            MarketDataRuntimeReason.GAP_DETECTED,
        )
        candles: tuple[Candle, ...] = ()
        if start < end:
            try:
                candles = await self._run_source_operation(
                    lambda: self._source.load_range(
                        self._config,
                        start=start,
                        end=end,
                    ),
                    timeout=_BACKFILL_TIMEOUT_SECONDS,
                )
            except Exception as error:
                # A backfill that times out is a source that did not answer.
                return self._fail_backfill(
                    start=start,
                    end=end,
                    failure=_source_failure(
                        error,
                        on_timeout=MarketDataRuntimeReason.SOURCE_ERROR,
                    ),
                )

        def publish_backfill_ready() -> None:
            self._transition(
                MarketDataRuntimeState.LIVE,
                MarketDataRuntimeReason.BACKFILL_COMPLETED,
            )

        try:
            await self._pipeline.process_backfill(
                candles,
                start=start,
                end=end,
                observed=observed,
                received_at=received_at,
                on_ready_for_delivery=publish_backfill_ready,
            )
        except CandlePipelineIncompleteBackfillError:
            # The range came back short, so there is nothing yet to hand the
            # Session. The source has not caught up rather than broken; the
            # caller asks again from a fresh stream and a fresh boundary.
            self._runtime_log.backfill_failed(
                start=start,
                end=end,
                reason=MarketDataRuntimeReason.SOURCE_ERROR,
                failure_kind=None,
            )
            return _BackfillOutcome.RECOVERABLE
        except (CandlePipelineInputError, CandlePipelineSinkError) as error:
            return self._fail_backfill(
                start=start,
                end=end,
                failure=_source_failure(
                    error,
                    on_timeout=MarketDataRuntimeReason.SOURCE_ERROR,
                ),
            )
        self._runtime_log.backfill_completed(
            start=start,
            end=end,
            candle_count=len(candles),
        )
        return _BackfillOutcome.DELIVERED

    def _fail_backfill(
        self,
        *,
        start: datetime,
        end: datetime,
        failure: _SourceFailure,
    ) -> _BackfillOutcome:
        self._runtime_log.backfill_failed(
            start=start,
            end=end,
            reason=failure.reason,
            failure_kind=failure.kind,
        )
        self._fail_closed(failure.reason, failure_kind=failure.kind)
        return _BackfillOutcome.FAILED

    async def _recover_stream(
        self,
        reason: MarketDataRuntimeReason,
        *,
        pending_rate_limit: MarketDataRateLimitError | None = None,
    ) -> AsyncIterator[MarketDataKlineUpdate] | None:
        if pending_rate_limit is None:
            if reason is MarketDataRuntimeReason.DATA_STALE:
                self._runtime_log.stale_detected(
                    reason=reason,
                    last_accepted_open_time=(
                        self._status.snapshot.last_accepted_open_time
                    ),
                )
            self._transition(MarketDataRuntimeState.STALE, reason)
        attempt = 0
        while True:
            attempt += 1
            if pending_rate_limit is None:
                attempt_delay = _reconnect_backoff_seconds(attempt)
                await self._scheduler.sleep(attempt_delay)
                self._transition(MarketDataRuntimeState.RECONNECTING, reason)
            else:
                if (
                    self._status.snapshot.state
                    is not MarketDataRuntimeState.RATE_LIMITED
                ):
                    self._transition(
                        MarketDataRuntimeState.RATE_LIMITED,
                        MarketDataRuntimeReason.RATE_LIMITED,
                    )
                attempt_delay = _retry_after_seconds(
                    pending_rate_limit, now=self._scheduler.now()
                )
                await self._scheduler.sleep(attempt_delay)
                self._transition(MarketDataRuntimeState.RECONNECTING, reason)
            if self._stop_requested:
                return None
            self._runtime_log.reconnect_attempted(
                attempt=attempt,
                delay_seconds=attempt_delay,
                reason=reason,
            )
            stream: AsyncIterator[MarketDataKlineUpdate] | None = None
            try:
                stream = self._source.stream_klines(self._config)
                deadline = self._reconnect_deadline()
                while True:
                    update = await self._next_reconnect_candle(
                        stream,
                        deadline=deadline,
                    )
                    if update.is_closed:
                        observed = update.candle
                        break
                    self._publish_open_candle(update.candle)
            except MarketDataFatalError as error:
                await _close_stream(stream)
                self._fail_closed(
                    MarketDataRuntimeReason.SOURCE_FATAL,
                    failure_kind=_failure_kind(error),
                )
                return None
            except MarketDataRateLimitError as error:
                await _close_stream(stream)
                pending_rate_limit = error
                self._transition(
                    MarketDataRuntimeState.RATE_LIMITED,
                    MarketDataRuntimeReason.RATE_LIMITED,
                )
                continue
            except MarketDataSourceError:
                await _close_stream(stream)
                pending_rate_limit = None
                continue
            except Exception:
                await _close_stream(stream)
                pending_rate_limit = None
                continue

            received_at = self._scheduler.now()
            latest_boundary = _latest_completed_boundary(
                received_at,
                interval=self._config.interval,
            )
            end = max(
                observed.open_time + self._config.interval,
                latest_boundary,
            )
            outcome = await self._backfill_to_boundary(
                end,
                received_at=received_at,
                observed=observed,
            )
            if outcome is _BackfillOutcome.RECOVERABLE:
                # The connection is back but the history behind it is not all
                # there yet. Another attempt costs a backoff delay; giving up
                # costs the Session, which nothing but a restart brings back.
                await _close_stream(stream)
                pending_rate_limit = None
                continue
            if outcome is _BackfillOutcome.FAILED:
                return None
            return stream

    async def _run_source_operation(
        self,
        operation: Callable[[], Awaitable[_T]],
        *,
        timeout: float,
    ) -> _T:
        attempt = 0
        while True:
            attempt += 1
            # The wait below has no end, so a Stop has to be able to reach it.
            # CancelledError is what run() already answers a Stop with, and
            # being a BaseException it passes the callers' except clauses
            # untouched rather than reading to them as a source that failed.
            if self._stop_requested:
                raise asyncio.CancelledError
            try:
                return await self._scheduler.wait_for(operation(), timeout)
            except MarketDataFatalError:
                raise
            except MarketDataRateLimitError as error:
                self._transition(
                    MarketDataRuntimeState.RATE_LIMITED,
                    MarketDataRuntimeReason.RATE_LIMITED,
                )
                await self._scheduler.sleep(
                    _retry_after_seconds(error, now=self._scheduler.now())
                )
                self._transition(
                    MarketDataRuntimeState.RECONNECTING,
                    MarketDataRuntimeReason.RATE_LIMITED,
                )
            except (MarketDataRetryableError, TimeoutError):
                await self._scheduler.sleep(_reconnect_backoff_seconds(attempt))

    async def _close_source_once(self) -> None:
        async with self._source_close_lock:
            if self._source_closed:
                return
            await self._source.close()
            self._source_closed = True

    def _fail_closed(
        self,
        reason: MarketDataRuntimeReason,
        *,
        failure_kind: MarketDataFailureKind | None = None,
    ) -> None:
        if self._status.snapshot.state is not MarketDataRuntimeState.FAILED_CLOSED:
            self._terminal_failure_kind = failure_kind
        self._runtime_log.failed_closed(
            reason=reason,
            failure_kind=failure_kind,
        )
        self._transition(MarketDataRuntimeState.FAILED_CLOSED, reason)

    def _transition_to_stopped(self) -> None:
        if self._status.snapshot.state in {
            MarketDataRuntimeState.FAILED_CLOSED,
            MarketDataRuntimeState.STOPPED,
        }:
            return
        self._transition(
            MarketDataRuntimeState.STOPPED,
            MarketDataRuntimeReason.STOP_REQUESTED,
        )

    def _transition(
        self,
        state: MarketDataRuntimeState,
        reason: MarketDataRuntimeReason,
    ) -> None:
        self._status.transition(state, reason)


def _latest_completed_boundary(value: datetime, *, interval: timedelta) -> datetime:
    interval_seconds = int(interval.total_seconds())
    boundary_timestamp = int(value.timestamp()) // interval_seconds * interval_seconds
    return datetime.fromtimestamp(boundary_timestamp, UTC)


_LIVE_OUTCOMES = {
    _BackfillOutcome.DELIVERED: _LiveOutcome.CONTINUE,
    _BackfillOutcome.RECOVERABLE: _LiveOutcome.RECOVER,
    _BackfillOutcome.FAILED: _LiveOutcome.STOP,
}


async def _close_stream(stream: AsyncIterator[MarketDataKlineUpdate] | None) -> None:
    """Let go of a stream this run is done with, without dying over it.

    An abandoned kline stream holds a half-open connection until it is
    closed, and an outage abandons one per attempt. Closing is housekeeping,
    so a source that raises on the way out changes nothing here.
    """
    close = getattr(stream, "aclose", None)
    if close is None:
        return
    try:
        await close()
    except Exception:
        pass


def _reconnect_backoff_seconds(attempt: int) -> float:
    """How long to wait before the attempt-th try at reaching the source.

    Doubles from a second and then holds — 1, 2, 4, 8, 16, 30, 30 — so a blip
    is waited out in about the time it takes to notice one, while an outage
    that lasts is not asked about every few seconds for as long as it lasts.

    There is no last delay here, because there is no last attempt: a source
    that cannot be reached is waited for, not given up on.
    """
    if attempt < 1:
        raise ValueError("attempt must be at least 1")
    # Capped before it is doubled as well as after: an outage lasting days
    # would otherwise raise two to the power of every attempt it took.
    doublings = min(attempt - 1, 30)
    return min(
        _RECONNECT_BACKOFF_BASE_SECONDS * float(2**doublings),
        _RECONNECT_BACKOFF_CAP_SECONDS,
    )


def _retry_after_seconds(error: MarketDataRateLimitError, *, now: datetime) -> float:
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
    return _RATE_LIMIT_FALLBACK_SECONDS


@dataclass(frozen=True, slots=True)
class _SourceFailure:
    """Why a source operation failed, in the terms the runtime reports."""

    reason: MarketDataRuntimeReason
    kind: MarketDataFailureKind | None


def _source_failure(
    error: BaseException,
    *,
    on_timeout: MarketDataRuntimeReason,
) -> _SourceFailure:
    """Read a failure once, so every stage reports the same one the same way.

    Only the meaning of a timeout differs between stages: warm-up has a
    deadline of its own to name, while a backfill that times out is just a
    source that did not answer. Every other kind reads the same wherever it
    happens, and stating that in one place is what keeps it so.
    """
    kind = _failure_kind(error)
    if isinstance(error, MarketDataFatalError):
        return _SourceFailure(MarketDataRuntimeReason.SOURCE_FATAL, kind)
    if isinstance(error, _WarmUpSinkError | CandlePipelineSinkError):
        return _SourceFailure(MarketDataRuntimeReason.SINK_ERROR, kind)
    # Checked before the retryable base it inherits from, which would swallow it.
    if isinstance(error, MarketDataTimeoutError | TimeoutError):
        return _SourceFailure(on_timeout, kind)
    return _SourceFailure(MarketDataRuntimeReason.SOURCE_ERROR, kind)


def _failure_kind(error: BaseException) -> MarketDataFailureKind | None:
    if isinstance(error, MarketDataSourceError):
        return error.kind
    if isinstance(error, _WarmUpSourceError) and isinstance(
        error.__cause__, MarketDataSourceError
    ):
        return error.__cause__.kind
    return None
