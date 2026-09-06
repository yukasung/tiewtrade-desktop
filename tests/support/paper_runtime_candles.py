"""A run of candles a Paper Session opens exactly one Entry on.

The shape is tuned, not arbitrary: the warm-up runs the price down to the
close the live candles then climb back from, which is what the RSI Step Grid
Preset needs to see before it will call an Entry. A test that wants a Bot to
actually trade wants this run, and two copies of it would be two runs that
could drift into meaning different things.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tiewtrade.market_data.candle import Candle
from tiewtrade.strategies.rsi_step_grid.preset import RsiStepGridPreset

WARM_UP_COUNT = RsiStepGridPreset.v1().minimum_warm_up_candles
LIVE_FROM = 15


def candle(index: int, *, close: Decimal) -> Candle:
    opened = close - Decimal("0.5")
    return Candle(
        symbol="BTCUSDT",
        timeframe="5m",
        open_time=datetime(2026, 8, 2, tzinfo=UTC) + timedelta(minutes=index * 5),
        open=opened,
        high=close + Decimal("1"),
        low=opened - Decimal("1"),
        close=close,
    )


def warm_up_candles() -> tuple[Candle, ...]:
    """A run down to the close the live candles then climb back from.

    As many candles as the Preset asks for, and it is the *start* that moves
    when the Preset asks for more: the warm-up still ends on the candle before
    the live ones, closing at 86, so every price and time asserted off them
    stays where it was.
    """
    return tuple(
        candle(index, close=Decimal(86 + LIVE_FROM - 1 - index))
        for index in range(LIVE_FROM - WARM_UP_COUNT, LIVE_FROM)
    )


def live_candles() -> tuple[Candle, ...]:
    close = Decimal("86")
    candles: list[Candle] = []
    for index in range(LIVE_FROM, LIVE_FROM + 11):
        close += Decimal("1")
        candles.append(candle(index, close=close))
    return tuple(candles)
