from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Protocol

from tiewtrade.market_data.candle import Candle
from tiewtrade.market_data.completed_candle_stream import (
    CandleAcceptance,
    CandleGapError,
    CompletedCandleStream,
)
from tiewtrade.market_data.config import MarketDataConfig
from tiewtrade.market_data.warm_up import require_warm_up_candle


class CandlePipelineInputError(ValueError):
    pass


class CandlePipelineIncompleteBackfillError(CandlePipelineInputError):
    """The answer did not cover the range that was missing.

    An empty, short, or still-gapped answer is a source that has not caught
    up yet — a kline published a moment after its boundary, or a connection
    just restored that is still being let through — rather than a source that
    is broken. Asking again is the answer to that. Malformed candles stay a
    plain CandlePipelineInputError, because asking again cannot fix them.
    """


class CandlePipelineSinkError(RuntimeError):
    pass


class _IncompleteBackfill(Exception):
    """Raised by the coverage checks below, reported as the error above."""


class MarketDataCandleSink(Protocol):
    async def warm_up(
        self,
        candles: tuple[Candle, ...],
        *,
        received_at: datetime,
    ) -> None: ...

    async def process_completed(
        self,
        candle: Candle,
        *,
        received_at: datetime,
        backfilled: bool,
    ) -> None:
        """Take one completed candle, and whether it was recovered rather than live.

        A Candle fetched back after a disconnect arrives here exactly as a
        live one does, so a sink that must treat the two differently — a
        Session that may not open an Entry at a price the market has already
        left behind — has no way to tell them apart unless it is told.
        """

    async def process_in_progress(
        self,
        candle: Candle,
        *,
        received_at: datetime,
    ) -> None: ...


class CompletedCandlePipeline:
    def __init__(
        self,
        *,
        config: MarketDataConfig,
        sink: MarketDataCandleSink,
        on_delivery: Callable[[datetime], None],
        not_closed_tolerance: timedelta = timedelta(0),
    ) -> None:
        self._config = config
        self._sink = sink
        self._on_delivery = on_delivery
        self._candles = CompletedCandleStream(
            config, not_closed_tolerance=not_closed_tolerance
        )

    async def warm_up(
        self,
        candles: tuple[Candle, ...],
        *,
        expected_count: int,
        received_at: datetime,
    ) -> None:
        try:
            if expected_count <= 0:
                raise ValueError("expected_count must be positive")
            if len(candles) < expected_count:
                raise ValueError("insufficient warm-up candles")
            for candle in candles:
                require_warm_up_candle(self._candles, candle, received_at)
        except Exception as error:
            raise CandlePipelineInputError from error
        try:
            await self._sink.warm_up(candles, received_at=received_at)
        except Exception as error:
            raise CandlePipelineSinkError from error
        self._on_delivery(candles[-1].open_time)

    async def process_in_progress(
        self,
        candle: Candle,
        *,
        received_at: datetime,
    ) -> None:
        """Hand the forming candle to the sink, which may act on its price."""
        try:
            await self._sink.process_in_progress(candle, received_at=received_at)
        except Exception as error:
            raise CandlePipelineSinkError from error

    async def process_live(
        self,
        candle: Candle,
        *,
        received_at: datetime,
    ) -> CandleAcceptance:
        try:
            decision = self._candles.accept(candle, received_at)
        except CandleGapError:
            raise
        except Exception as error:
            raise CandlePipelineInputError from error
        if decision is not CandleAcceptance.ACCEPTED:
            return decision
        await self._deliver(candle, received_at=received_at, backfilled=False)
        return decision

    async def process_backfill(
        self,
        candles: tuple[Candle, ...],
        *,
        start: datetime,
        end: datetime,
        observed: Candle,
        received_at: datetime,
        on_ready_for_delivery: Callable[[], None],
    ) -> None:
        try:
            if start < end and not candles:
                raise _IncompleteBackfill("backfill must not be empty")
            if start >= end and candles:
                raise ValueError("backfill must be empty when no range is missing")
            validation_candles = deepcopy(self._candles)
            accepted: list[Candle] = []
            for candle in candles:
                try:
                    decision = validation_candles.accept(candle, received_at)
                except CandleGapError as error:
                    raise _IncompleteBackfill("backfill left a gap behind") from error
                if decision is not CandleAcceptance.ACCEPTED:
                    raise _IncompleteBackfill("backfill requires new completed candles")
                accepted.append(candle)
            if accepted:
                reached = accepted[-1].open_time + self._config.interval
                if reached != end:
                    raise _IncompleteBackfill(
                        "backfill did not reach requested boundary"
                    )
            if (
                validation_candles.accept(observed, received_at)
                is CandleAcceptance.ACCEPTED
            ):
                raise _IncompleteBackfill(
                    "buffered observation was not covered by backfill"
                )
        except _IncompleteBackfill as error:
            raise CandlePipelineIncompleteBackfillError(str(error)) from error
        except Exception as error:
            raise CandlePipelineInputError from error

        try:
            for candle in accepted:
                # Deep-copy validation guarantees acceptance by unchanged real state.
                if (
                    self._candles.accept(candle, received_at)
                    is not CandleAcceptance.ACCEPTED
                ):
                    raise ValueError("backfill commit diverged from validation")
        except Exception as error:
            raise CandlePipelineInputError from error
        on_ready_for_delivery()
        for candle in accepted:
            await self._deliver(candle, received_at=received_at, backfilled=True)

    async def _deliver(
        self,
        candle: Candle,
        *,
        received_at: datetime,
        backfilled: bool,
    ) -> None:
        # A candle accepted here already passed the (possibly tolerant)
        # not-closed gate above, so it is authoritatively closed. Downstream
        # consumers keep their own zero-tolerance completed-candle stream and
        # would otherwise reject a received_at that is still a moment before
        # close_time, silently dropping the delivery.
        delivered_at = max(received_at, candle.close_time)
        try:
            await self._sink.process_completed(
                candle, received_at=delivered_at, backfilled=backfilled
            )
        except Exception as error:
            raise CandlePipelineSinkError from error
        self._on_delivery(candle.open_time)
