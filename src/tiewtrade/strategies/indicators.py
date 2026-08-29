from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from tiewtrade.market_data.candle import Candle

# Wilder smoothing never fully forgets the average it was seeded with, so a
# reading is only the market's once enough candles have passed for the seed to
# wash out. Seeded on the bare period, a quiet stretch teaches the averages
# that nothing ever moves and the next ordinary candle reads as an extreme.
# Everything that reads these indicators — Strategy and chart alike — starts
# from this many candles, so the same candle reads the same either side.
WILDER_WARM_UP_CANDLES = 250


class IndicatorPeriods(Protocol):
    @property
    def rsi_period(self) -> int: ...

    @property
    def atr_period(self) -> int: ...


@dataclass(frozen=True, slots=True)
class IndicatorSnapshot:
    rsi: Decimal
    atr: Decimal


def wilder_average(
    previous_average: Decimal,
    value: Decimal,
    period: int,
) -> Decimal:
    """One Wilder smoothing step over a running average."""
    divisor = Decimal(period)
    return (previous_average * (divisor - Decimal("1")) + value) / divisor


def wilder_rsi(average_gain: Decimal, average_loss: Decimal) -> Decimal:
    if average_gain == 0 and average_loss == 0:
        return Decimal("50")
    if average_loss == 0:
        return Decimal("100")
    if average_gain == 0:
        return Decimal("0")

    relative_strength = average_gain / average_loss
    return Decimal("100") - Decimal("100") / (Decimal("1") + relative_strength)


def price_change_gain_loss(
    previous_close: Decimal,
    close: Decimal,
) -> tuple[Decimal, Decimal]:
    change = close - previous_close
    return max(change, Decimal("0")), max(-change, Decimal("0"))


def true_range(candle: Candle, previous_close: Decimal | None) -> Decimal:
    """How far this candle travelled, gaps from the last close included.

    The first candle of a series has no close behind it, so its own range is
    all there is to measure.
    """
    if previous_close is None:
        return candle.high - candle.low
    return max(
        candle.high - candle.low,
        abs(candle.high - previous_close),
        abs(candle.low - previous_close),
    )


class WilderIndicators:
    def __init__(self, preset: IndicatorPeriods) -> None:
        self._rsi_period = preset.rsi_period
        self._atr_period = preset.atr_period
        self._previous_close: Decimal | None = None
        self._gains: list[Decimal] = []
        self._losses: list[Decimal] = []
        self._true_ranges: list[Decimal] = []
        self._average_gain: Decimal | None = None
        self._average_loss: Decimal | None = None
        self._atr: Decimal | None = None

    def update(self, candle: Candle) -> IndicatorSnapshot | None:
        self._update_atr(candle)
        self._update_rsi(candle.close)
        self._previous_close = candle.close

        if (
            self._average_gain is None
            or self._average_loss is None
            or self._atr is None
        ):
            return None

        return IndicatorSnapshot(
            rsi=wilder_rsi(self._average_gain, self._average_loss),
            atr=self._atr,
        )

    def _update_rsi(self, close: Decimal) -> None:
        if self._previous_close is None:
            return

        gain, loss = price_change_gain_loss(self._previous_close, close)
        period = Decimal(self._rsi_period)

        if self._average_gain is None or self._average_loss is None:
            self._gains.append(gain)
            self._losses.append(loss)
            if len(self._gains) == self._rsi_period:
                self._average_gain = sum(self._gains, Decimal("0")) / period
                self._average_loss = sum(self._losses, Decimal("0")) / period
            return

        self._average_gain = wilder_average(self._average_gain, gain, self._rsi_period)
        self._average_loss = wilder_average(self._average_loss, loss, self._rsi_period)

    def _update_atr(self, candle: Candle) -> None:
        candle_range = true_range(candle, self._previous_close)

        period = Decimal(self._atr_period)
        if self._atr is None:
            self._true_ranges.append(candle_range)
            if len(self._true_ranges) == self._atr_period:
                self._atr = sum(self._true_ranges, Decimal("0")) / period
            return

        self._atr = wilder_average(self._atr, candle_range, self._atr_period)
