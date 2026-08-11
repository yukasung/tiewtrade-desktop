from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tiewtrade.market_data.candle import Candle
from tiewtrade.market_data.completed_candle_stream import (
    CandleAcceptance,
    CandleGapError,
    CompletedCandleStream,
)
from tiewtrade.market_data.config import MarketDataConfig


def candle_at(minute: int, *, symbol: str = "BTCUSDT", timeframe: str = "5m") -> Candle:
    opened = datetime(2026, 1, 1, 0, minute, tzinfo=UTC)
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=opened,
        open=Decimal("100"),
        high=Decimal("102"),
        low=Decimal("99"),
        close=Decimal("101"),
    )


def test_accepts_only_closed_candles_and_deduplicates() -> None:
    config = MarketDataConfig(symbol="BTCUSDT", timeframe="5m")
    stream = CompletedCandleStream(config)
    first = candle_at(0)

    assert (
        stream.accept(
            first,
            datetime(2026, 1, 1, 0, 4, 59, tzinfo=UTC),
        )
        is CandleAcceptance.NOT_CLOSED
    )
    assert (
        stream.accept(
            first,
            datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
        )
        is CandleAcceptance.ACCEPTED
    )
    assert (
        stream.accept(
            first,
            datetime(2026, 1, 1, 0, 6, tzinfo=UTC),
        )
        is CandleAcceptance.DUPLICATE_OR_OUT_OF_ORDER
    )


def test_not_closed_tolerance_defaults_to_zero_for_exact_replay_semantics() -> None:
    config = MarketDataConfig(symbol="BTCUSDT", timeframe="5m")
    stream = CompletedCandleStream(config)
    first = candle_at(0)

    assert (
        stream.accept(
            first,
            first.close_time - timedelta(milliseconds=1),
        )
        is CandleAcceptance.NOT_CLOSED
    )


def test_not_closed_tolerance_accepts_a_candle_that_arrives_slightly_early() -> None:
    config = MarketDataConfig(symbol="BTCUSDT", timeframe="5m")
    stream = CompletedCandleStream(config, not_closed_tolerance=timedelta(seconds=2))
    first = candle_at(0)

    assert (
        stream.accept(
            first,
            first.close_time - timedelta(milliseconds=69),
        )
        is CandleAcceptance.ACCEPTED
    )


def test_not_closed_tolerance_still_rejects_a_candle_earlier_than_the_window() -> None:
    config = MarketDataConfig(symbol="BTCUSDT", timeframe="5m")
    stream = CompletedCandleStream(config, not_closed_tolerance=timedelta(seconds=2))
    first = candle_at(0)

    assert (
        stream.accept(
            first,
            first.close_time - timedelta(seconds=3),
        )
        is CandleAcceptance.NOT_CLOSED
    )


def test_not_closed_tolerance_rejects_a_negative_window() -> None:
    config = MarketDataConfig(symbol="BTCUSDT", timeframe="5m")

    with pytest.raises(ValueError, match="not_closed_tolerance"):
        CompletedCandleStream(config, not_closed_tolerance=timedelta(seconds=-1))


def test_rejects_a_gap_until_backfill_supplies_missing_candle() -> None:
    config = MarketDataConfig(symbol="BTCUSDT", timeframe="5m")
    stream = CompletedCandleStream(config)
    assert (
        stream.accept(candle_at(0), datetime(2026, 1, 1, 0, 5, tzinfo=UTC))
        is CandleAcceptance.ACCEPTED
    )

    with pytest.raises(CandleGapError, match="2026-01-01T00:05:00"):
        stream.accept(candle_at(10), datetime(2026, 1, 1, 0, 15, tzinfo=UTC))

    assert (
        stream.accept(candle_at(5), datetime(2026, 1, 1, 0, 10, tzinfo=UTC))
        is CandleAcceptance.ACCEPTED
    )
    assert (
        stream.accept(candle_at(10), datetime(2026, 1, 1, 0, 15, tzinfo=UTC))
        is CandleAcceptance.ACCEPTED
    )


def test_candle_requires_utc_and_valid_ohlc() -> None:
    with pytest.raises(ValueError, match="UTC"):
        Candle(
            symbol="BTCUSDT",
            timeframe="5m",
            open_time=datetime(2026, 1, 1),
            open=Decimal("100"),
            high=Decimal("102"),
            low=Decimal("99"),
            close=Decimal("101"),
        )


def test_candle_is_an_ohlc_fact_without_volume() -> None:
    candle = Candle(
        symbol="BTCUSDT",
        timeframe="5m",
        open_time=datetime(2026, 1, 1, tzinfo=UTC),
        open=Decimal("100"),
        high=Decimal("102"),
        low=Decimal("99"),
        close=Decimal("101"),
    )

    assert not hasattr(candle, "volume")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("open_time", datetime(2026, 1, 1, 0, 1, tzinfo=UTC), "timeframe boundary"),
        ("high", Decimal("100"), "OHLC range"),
    ],
)
def test_candle_rejects_invalid_market_data(
    field: str, value: object, message: str
) -> None:
    values: dict[str, object] = {
        "symbol": "BTCUSDT",
        "timeframe": "5m",
        "open_time": datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        "open": Decimal("100"),
        "high": Decimal("102"),
        "low": Decimal("99"),
        "close": Decimal("101"),
    }
    values[field] = value

    with pytest.raises(ValueError, match=message):
        Candle(**values)  # type: ignore[arg-type]


def test_stream_rejects_candle_that_does_not_match_configuration() -> None:
    config = MarketDataConfig(symbol="ETHUSDT", timeframe="15m")
    stream = CompletedCandleStream(config)

    with pytest.raises(ValueError, match="symbol"):
        stream.accept(candle_at(0), datetime(2026, 1, 1, 0, 5, tzinfo=UTC))


def test_stream_accepts_a_non_five_minute_timeframe() -> None:
    config = MarketDataConfig(symbol="ETHUSDT", timeframe="15m")
    stream = CompletedCandleStream(config)
    first = candle_at(0, symbol="ETHUSDT", timeframe="15m")
    second = candle_at(15, symbol="ETHUSDT", timeframe="15m")

    assert (
        stream.accept(first, datetime(2026, 1, 1, 0, 15, tzinfo=UTC))
        is CandleAcceptance.ACCEPTED
    )
    assert (
        stream.accept(second, datetime(2026, 1, 1, 0, 30, tzinfo=UTC))
        is CandleAcceptance.ACCEPTED
    )
