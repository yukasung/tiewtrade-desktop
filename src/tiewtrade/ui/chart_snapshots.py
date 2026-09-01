"""Shaping one chart snapshot into another, for the view the chart is on.

Nothing here decides anything. Which Session the chart is on, whether a read
landed, whether a forming candle still belongs — those are the workflow's, and
they are what it hands in. What is left is the shaping itself, and three rules
that had been written out at each of the half-dozen places that needed them.

A snapshot is never observed earlier than the range it covers, so a window
ending in the future is not read as one the clock has already passed. Fills are
clipped to what is actually visible, because dropping the forming candle
shortens the span and a fill inside it has to go with it. And a chart of some
other Pair carries no fills at all — they are read by Session rather than by
Pair, so they would otherwise badge trades that never happened on it.
"""

from datetime import datetime
from typing import TypeGuard
from uuid import UUID

from tiewtrade.application.chart_data import (
    ChartCandle,
    ChartRange,
    ChartReadState,
    ChartSnapshot,
)
from tiewtrade.trading.trade_history import TradeFill

ViewFacts = tuple[UUID, str, str]
"""Whose Session this chart is on, and which Pair at which timeframe."""


def observed_at_for(chart_range: ChartRange, *, now: datetime) -> datetime:
    """No earlier than the range it covers, whatever the clock says.

    A window ending in the future would otherwise read as one the clock has
    already passed, which is a chart claiming to have seen candles that have
    not happened.
    """
    return max(now, chart_range.end)


def fills_inside(
    fills: tuple[object, ...],
    visible_range: ChartRange,
) -> tuple[TradeFill, ...]:
    """The fills that fall inside what the chart is actually showing."""
    return tuple(
        fill
        for fill in fills
        if isinstance(fill, TradeFill) and visible_range.contains(fill.filled_at_utc)
    )


def blank_snapshot(
    facts: ViewFacts,
    chart_range: ChartRange,
    *,
    now: datetime,
    state: ChartReadState,
    message: str | None = None,
) -> ChartSnapshot:
    """A chart of this view with nothing drawn on it yet, or nothing left."""
    return ChartSnapshot(
        chart_range=chart_range,
        observed_at_utc=observed_at_for(chart_range, now=now),
        candles=(),
        fills=(),
        state=state,
        message=message,
        _session_facts=facts,
    )


def unavailable_snapshot(
    facts: ViewFacts,
    chart_range: ChartRange,
    *,
    now: datetime,
) -> ChartSnapshot:
    """A chart with nothing on it, because the read behind it did not land."""
    return blank_snapshot(
        facts,
        chart_range,
        now=now,
        state=ChartReadState.UNAVAILABLE,
        message="Chart is unavailable",
    )


def with_in_progress_candle(
    snapshot: ChartSnapshot,
    *,
    now: datetime,
    in_progress_candle: ChartCandle | None,
    fills: tuple[TradeFill, ...] | None = None,
) -> ChartSnapshot:
    """The same chart with the forming candle put on it, or taken off.

    Taking it off shortens the visible span, so the fills are clipped again
    against whichever end the chart now reaches.
    """
    visible_end = (
        snapshot.chart_range.end
        if in_progress_candle is None
        else in_progress_candle.close_time
    )
    return ChartSnapshot(
        chart_range=snapshot.chart_range,
        observed_at_utc=observed_at_for(snapshot.chart_range, now=now),
        candles=snapshot.candles,
        fills=fills_inside(
            snapshot.fills if fills is None else fills,
            ChartRange(snapshot.chart_range.start, visible_end),
        ),
        state=snapshot.state,
        in_progress_candle=in_progress_candle,
        message=snapshot.message,
        rsi=snapshot.rsi,
        atr=snapshot.atr,
        _session_facts=(snapshot.session_id, snapshot.symbol, snapshot.timeframe),
    )


def without_fills(snapshot: ChartSnapshot, *, facts: ViewFacts) -> ChartSnapshot:
    """The same chart with the Session's fills taken off it.

    For a chart of another Pair: the fills are read by Session, not by Pair,
    so they would badge trades that never happened on it. A different
    timeframe of the same Pair keeps them — it is the same trade seen at
    another resolution, and ChartMarker already rounds each fill to the
    candle it belongs to.
    """
    return ChartSnapshot(
        chart_range=snapshot.chart_range,
        observed_at_utc=snapshot.observed_at_utc,
        candles=snapshot.candles,
        fills=(),
        state=snapshot.state,
        in_progress_candle=snapshot.in_progress_candle,
        message=snapshot.message,
        rsi=snapshot.rsi,
        atr=snapshot.atr,
        _session_facts=facts,
    )


def answers_the_view(
    result: object,
    *,
    facts: ViewFacts | None,
    chart_range: ChartRange,
) -> TypeGuard[ChartSnapshot]:
    """Whether a read that landed is a read of what is on screen now.

    A view changed while a read was out leaves the reply describing a Pair,
    a timeframe or a window nobody is looking at any more.
    """
    if facts is None or not isinstance(result, ChartSnapshot):
        return False
    session_id, symbol, timeframe = facts
    return (
        result.session_id == session_id
        and result.symbol == symbol
        and result.timeframe == timeframe
        and result.chart_range == chart_range
    )
