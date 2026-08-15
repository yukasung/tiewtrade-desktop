from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from tests.support.chart_candles import FIVE_MINUTES, flat_candle
from tiewtrade.application.chart_indicators import (
    CHART_ATR_PERIOD,
    CHART_RSI_PERIOD,
    AtrSeries,
    AtrState,
    RsiSeries,
    RsiState,
)
from tiewtrade.market_data.candle import Candle
from tiewtrade.market_data.config import MarketDataConfig
from tiewtrade.replay.csv_candles import load_candles_csv
from tiewtrade.strategies.indicators import WilderIndicators
from tiewtrade.strategies.rsi_step_grid.preset import RsiStepGridPreset

FIXTURE = Path("tests/fixtures/btcusdt_5m_tracer.csv")


def test_chart_rsi_matches_the_rsi_a_strategy_acts_on() -> None:
    """The displayed RSI must never diverge from the traded RSI."""
    candles = load_candles_csv(
        FIXTURE,
        MarketDataConfig(symbol="BTCUSDT", timeframe="5m"),
    )
    indicators = WilderIndicators(RsiStepGridPreset.v1())
    expected = {
        candle.open_time: snapshot.rsi
        for candle in candles
        if (snapshot := indicators.update(candle)) is not None
    }

    series = RsiSeries.over(candles)

    assert expected
    assert dict(series.values) == expected


def test_chart_atr_matches_the_atr_a_strategy_acts_on() -> None:
    """The displayed ATR must never diverge from the ATR a target is measured from."""
    candles = load_candles_csv(
        FIXTURE,
        MarketDataConfig(symbol="BTCUSDT", timeframe="5m"),
    )
    indicators = WilderIndicators(RsiStepGridPreset.v1())
    expected = {
        candle.open_time: snapshot.atr
        for candle in candles
        if (snapshot := indicators.update(candle)) is not None
    }

    series = AtrSeries.over(candles)

    assert expected
    # The Strategy publishes a reading only once RSI is warm too, so ATR
    # values before that are the chart's alone.
    assert {open_time: series.values[open_time] for open_time in expected} == expected


def test_atr_stays_absent_until_the_period_has_enough_candles() -> None:
    candles = rising_candles(CHART_ATR_PERIOD + 2)

    series = AtrSeries.over(candles)

    # A candle's own range needs no candle before it, so the first value
    # lands on the period rather than one after it.
    assert [candle.open_time in series.values for candle in candles] == (
        [False] * (CHART_ATR_PERIOD - 1) + [True, True, True]
    )


def test_advancing_atr_one_candle_matches_recomputing_the_whole_series() -> None:
    candles = rising_candles(30)

    advanced = AtrSeries.over(candles[:-1]).advanced(candles[-1])
    full = AtrSeries.over(candles)

    assert dict(advanced.values) == dict(full.values)
    assert advanced.state == full.state


def test_reading_a_forming_candle_leaves_the_atr_series_usable_again() -> None:
    candles = rising_candles(CHART_ATR_PERIOD + 5)
    series = AtrSeries.over(candles[:-1])
    forming = candles[-1]

    forming_value = series.value_for_candle(forming)

    assert forming_value is not None
    # Reading the forming candle must not consume the state behind it.
    assert series.advanced(forming).value_at(forming.open_time) == forming_value


def test_empty_atr_series_reads_as_absent_rather_than_failing() -> None:
    series = AtrSeries.over(())

    assert series.state is None
    assert series.value_at(datetime(2026, 1, 1, tzinfo=UTC)) is None
    assert (
        series.value_for_candle(flat_candle(datetime(2026, 1, 1, tzinfo=UTC), 100))
        is None
    )
    assert series.advanced(flat_candle(datetime(2026, 1, 1, tzinfo=UTC), 100)) == series


def test_atr_state_rejects_a_non_positive_period() -> None:
    with pytest.raises(ValueError, match="period must be positive"):
        AtrState(period=0)


def test_rsi_stays_absent_until_the_period_has_enough_price_changes() -> None:
    candles = rising_candles(CHART_RSI_PERIOD + 2)

    series = RsiSeries.over(candles)

    # A change needs two candles, so the first value lands one candle after
    # the period rather than on it.
    assert [candle.open_time in series.values for candle in candles] == (
        [False] * CHART_RSI_PERIOD + [True, True]
    )
    assert series.state is not None
    assert series.state.value == Decimal("100")


def test_advancing_one_candle_matches_recomputing_the_whole_series() -> None:
    candles = rising_candles(30)

    advanced = RsiSeries.over(candles[:-1]).advanced(candles[-1])
    full = RsiSeries.over(candles)

    assert dict(advanced.values) == dict(full.values)
    assert advanced.state == full.state


def test_reading_a_forming_close_leaves_the_series_usable_again() -> None:
    """The forming candle must not consume the committed state."""
    series = RsiSeries.over(rising_candles(30))

    higher = series.value_for_close(Decimal("120"))
    lower = series.value_for_close(Decimal("80"))

    assert higher != lower
    assert series.value_for_close(Decimal("120")) == higher


def test_restricting_drops_only_candles_that_scrolled_away() -> None:
    candles = rising_candles(30)
    series = RsiSeries.over(candles)
    retained = [candle.open_time for candle in candles[-5:]]

    restricted = series.restricted_to(retained)

    assert set(restricted.values) == set(retained) & set(series.values)
    # Trimming the visible window must not disturb the tail state that lets
    # the next candle extend the series.
    assert restricted.state == series.state


def test_empty_series_reads_as_absent_rather_than_failing() -> None:
    series = RsiSeries.over(())

    assert series.state is None
    assert series.values == {}
    assert series.value_at(datetime(2026, 1, 1, tzinfo=UTC)) is None
    assert series.value_for_close(Decimal("100")) is None


def test_rsi_state_rejects_a_non_positive_period() -> None:
    with pytest.raises(ValueError, match="period must be positive"):
        RsiState(period=0, previous_close=Decimal("100"))


def rising_candles(count: int) -> tuple[Candle, ...]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return tuple(
        flat_candle(start + FIVE_MINUTES * index, 100 + index) for index in range(count)
    )
