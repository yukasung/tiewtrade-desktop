"""Candle builders shared by the chart and indicator suites.

RSI fixtures only care about closes, so these keep the OHLC invariants in one
place instead of restating them in every suite.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from tiewtrade.market_data.candle import Candle

FIVE_MINUTES = timedelta(minutes=5)


def flat_candle(
    open_time: datetime,
    close: str | int | Decimal,
    *,
    symbol: str = "BTCUSDT",
    timeframe: str = "5m",
) -> Candle:
    """A candle that opens and closes at the same price, with a ±1 wick."""
    price = Decimal(close)
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time,
        open=price,
        high=price + Decimal("1"),
        low=price - Decimal("1"),
        close=price,
    )


def candle_run(
    count: int,
    *,
    last_open_time: datetime,
    closes: tuple[int, ...] = (100, 101, 102, 103, 104, 103, 102),
    symbol: str = "BTCUSDT",
) -> tuple[Candle, ...]:
    """`count` 5m candles ending at `last_open_time`, cycling through `closes`.

    Long enough runs leave the RSI warm-up, and the closes rise and fall so
    the resulting RSI is not pinned at 0 or 100.
    """
    start = last_open_time - FIVE_MINUTES * (count - 1)
    return tuple(
        flat_candle(
            start + FIVE_MINUTES * index,
            closes[index % len(closes)],
            symbol=symbol,
        )
        for index in range(count)
    )
