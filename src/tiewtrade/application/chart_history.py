from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from tiewtrade.application.chart_data import (
    ChartRange,
    ChartReadState,
    ChartSnapshot,
    follows,
)
from tiewtrade.application.chart_indicators import (
    CHART_RSI_WARM_UP_CANDLES,
    AtrSeries,
    RsiSeries,
)
from tiewtrade.application.paper_session_setup import ConfiguredPaperSession
from tiewtrade.market_data.candle import Candle
from tiewtrade.trading.trade_history import TradeFill

LoadChartCandles = Callable[
    [ConfiguredPaperSession, ChartRange], Awaitable[tuple[Candle, ...]]
]
ListChartFills = Callable[[ConfiguredPaperSession, ChartRange], tuple[TradeFill, ...]]
"""The marks a chart draws: read by the Pair a Session trades, not by its id.

The Session is handed over whole because the Pair is what is being asked for —
its symbol, its mode, and its market — and a Session id alone would name only
the run that happens to be current."""


class ChartHistory:
    def __init__(
        self,
        *,
        load_candles: LoadChartCandles,
        list_fills: ListChartFills,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._load_candles = load_candles
        self._list_fills = list_fills
        self._clock = clock

    async def load(
        self,
        session: ConfiguredPaperSession,
        chart_range: ChartRange,
    ) -> ChartSnapshot:
        candles, rsi, atr = await self._load_with_indicators(session, chart_range)
        return ChartSnapshot(
            session=session,
            chart_range=chart_range,
            observed_at_utc=self._clock(),
            candles=candles,
            fills=self._list_fills(session, chart_range),
            state=ChartReadState.READY if candles else ChartReadState.EMPTY,
            rsi=rsi,
            atr=atr,
        )

    async def refresh_completed(
        self,
        session: ConfiguredPaperSession,
        snapshot: ChartSnapshot,
        candle: Candle,
    ) -> ChartSnapshot:
        if snapshot.session_id != session.config.session_id:
            raise ValueError("ChartSnapshot Session must match Session")
        chart_range = snapshot.chart_range
        candles = snapshot.candles
        rsi = snapshot.rsi
        atr = snapshot.atr
        if candle.open_time >= chart_range.end:
            reload_history = candle.open_time > chart_range.end
            duration = chart_range.end - chart_range.start
            chart_range = ChartRange(
                start=candle.close_time - duration,
                end=candle.close_time,
            )
            if reload_history:
                # The gap means the carried indicator state no longer sits at
                # the candle before this one, so the series are rebuilt.
                candles, rsi, atr = await self._load_with_indicators(
                    session, chart_range
                )
        elif not (
            chart_range.start <= candle.open_time
            and candle.close_time <= chart_range.end
        ):
            return snapshot

        if follows(candles, candle):
            rsi = rsi.advanced(candle)
            atr = atr.advanced(candle)

        candles_by_open_time = {item.open_time: item for item in candles}
        candles_by_open_time[candle.open_time] = candle
        candles = tuple(
            candles_by_open_time[open_time]
            for open_time in sorted(candles_by_open_time)
            if chart_range.start <= open_time
            and candles_by_open_time[open_time].close_time <= chart_range.end
        )
        return ChartSnapshot(
            session=session,
            chart_range=chart_range,
            observed_at_utc=max(self._clock(), candle.close_time, chart_range.end),
            candles=candles,
            fills=self._list_fills(session, chart_range),
            state=ChartReadState.READY if candles else ChartReadState.EMPTY,
            rsi=rsi.restricted_to(item.open_time for item in candles),
            atr=atr.restricted_to(item.open_time for item in candles),
        )

    async def _load_with_indicators(
        self,
        session: ConfiguredPaperSession,
        chart_range: ChartRange,
    ) -> tuple[tuple[Candle, ...], RsiSeries, AtrSeries]:
        """Load the visible candles, seeding indicators from candles before them.

        Wilder smoothing is path dependent, so a reading computed from the
        visible window alone would change every time the window moves. Reading
        extra candles before the range makes each candle's RSI and ATR stable
        across zoom and pan without ever showing those candles.
        """
        interval = session.market_data.interval
        loaded = await self._load_candles(
            session,
            ChartRange(
                start=chart_range.start - interval * CHART_RSI_WARM_UP_CANDLES,
                end=chart_range.end,
            ),
        )
        candles = tuple(
            candle for candle in loaded if candle.open_time >= chart_range.start
        )
        # The warm-up range ends where the visible range ends, so the series
        # state already sits at the last visible candle.
        visible = tuple(item.open_time for item in candles)
        rsi = RsiSeries.over(loaded) if candles else RsiSeries()
        atr = AtrSeries.over(loaded) if candles else AtrSeries()
        return candles, rsi.restricted_to(visible), atr.restricted_to(visible)
