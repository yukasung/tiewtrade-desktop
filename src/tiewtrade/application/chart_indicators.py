"""Chart-only RSI series over completed candles.

The Strategy indicators are stream-stateful and expose only the latest value,
so the chart needs its own read-model. Both share the Wilder recurrence from
`tiewtrade.strategies.indicators`, which keeps the displayed RSI and the RSI a
Strategy acts on from ever drifting apart.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType

from tiewtrade.market_data.candle import Candle
from tiewtrade.strategies.indicators import (
    price_change_gain_loss,
    wilder_average,
    wilder_rsi,
)

CHART_RSI_PERIOD = 14
# Wilder smoothing never fully forgets its seed, so the chart reads extra
# candles before the visible range. Without them the same candle would show a
# different RSI after every zoom or pan.
CHART_RSI_WARM_UP_CANDLES = 250

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class RsiState:
    """Tail of an RSI series, enough to extend it one candle at a time."""

    period: int
    previous_close: Decimal
    # Wilder's average gain and loss, or None while still seeding them from
    # the first `period` price changes.
    averages: tuple[Decimal, Decimal] | None = None
    seed_gain: Decimal = _ZERO
    seed_loss: Decimal = _ZERO
    seed_count: int = 0

    def __post_init__(self) -> None:
        if self.period <= 0:
            raise ValueError("period must be positive")

    @property
    def value(self) -> Decimal | None:
        """RSI at this candle, or None while the series is still warming up."""
        if self.averages is None:
            return None
        return wilder_rsi(*self.averages)

    def advance(self, close: Decimal) -> "RsiState":
        """Return the state one candle later; never mutates this one."""
        gain, loss = price_change_gain_loss(self.previous_close, close)
        if self.averages is None:
            return self._seeded(close, gain, loss)
        average_gain, average_loss = self.averages
        return replace(
            self,
            previous_close=close,
            averages=(
                wilder_average(average_gain, gain, self.period),
                wilder_average(average_loss, loss, self.period),
            ),
        )

    def _seeded(self, close: Decimal, gain: Decimal, loss: Decimal) -> "RsiState":
        seed_gain = self.seed_gain + gain
        seed_loss = self.seed_loss + loss
        count = self.seed_count + 1
        if count < self.period:
            return replace(
                self,
                previous_close=close,
                seed_gain=seed_gain,
                seed_loss=seed_loss,
                seed_count=count,
            )
        divisor = Decimal(self.period)
        return RsiState(
            period=self.period,
            previous_close=close,
            averages=(seed_gain / divisor, seed_loss / divisor),
        )


@dataclass(frozen=True, slots=True)
class RsiSeries:
    """RSI per candle open time, plus the state at the last candle.

    Candles inside the warm-up window are simply absent from `values`. The
    state is what lets a later candle extend the series without re-reading
    the warm-up candles behind it.
    """

    period: int = CHART_RSI_PERIOD
    values: Mapping[datetime, Decimal] = field(default_factory=dict)
    state: RsiState | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))

    @classmethod
    def over(
        cls,
        candles: Sequence[Candle],
        *,
        period: int = CHART_RSI_PERIOD,
    ) -> "RsiSeries":
        """Compute the series from scratch. The first candle only seeds it."""
        if not candles:
            return cls(period=period)
        state = RsiState(period=period, previous_close=candles[0].close)
        values: dict[datetime, Decimal] = {}
        for candle in candles[1:]:
            state = state.advance(candle.close)
            if (value := state.value) is not None:
                values[candle.open_time] = value
        return cls(period=period, values=values, state=state)

    def advanced(self, candle: Candle) -> "RsiSeries":
        """Extend by one candle that follows every candle already measured."""
        if self.state is None:
            return self
        state = self.state.advance(candle.close)
        values = dict(self.values)
        if (value := state.value) is not None:
            values[candle.open_time] = value
        return replace(self, values=values, state=state)

    def restricted_to(self, open_times: Iterable[datetime]) -> "RsiSeries":
        """Drop values for candles that scrolled out of the visible range."""
        retained = set(open_times)
        if retained.issuperset(self.values):
            return self
        return replace(
            self,
            values={
                open_time: value
                for open_time, value in self.values.items()
                if open_time in retained
            },
        )

    def value_at(self, open_time: datetime) -> Decimal | None:
        return self.values.get(open_time)

    def value_for_close(self, close: Decimal) -> Decimal | None:
        """RSI the next candle would have if it closed at this price.

        Derived rather than stored, so reading a forming candle never
        consumes the state committed at the last completed one.
        """
        if self.state is None:
            return None
        return self.state.advance(close).value
