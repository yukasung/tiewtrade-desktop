from dataclasses import dataclass
from decimal import Decimal

_V1_VERSION = "fast-test-v1"
_V1_RSI_PERIOD = 2
_V1_ATR_PERIOD = 2
_V1_TAKE_PROFIT_ATR_MULTIPLIER = Decimal("3")


@dataclass(frozen=True, slots=True)
class FastTestPreset:
    """The shortest warm-up the pipeline will accept, and a target that outlives
    the candle that opened the Basket.

    Two-candle indicators end warm-up after three candles, which is what makes
    a first Entry appear in a minute rather than in a quarter of an hour.

    The take profit is deliberately *not* shortened with them. An Entry fills
    at the open of a candle already complete, so by the time the Basket exists
    the market has moved a whole candle past its Entry Price, while the exit is
    checked on every tick in between. A target inside one candle's range is
    therefore hit before the next candle can add a second Entry, and the tab
    under test never shows more than one row. Three ATR keeps the Basket open
    across the few candles it takes to fill an Entry Pair.
    """

    version: str
    rsi_period: int
    atr_period: int
    take_profit_atr_multiplier: Decimal
    stop_loss_atr_multiplier: Decimal | None = None

    def __post_init__(self) -> None:
        if self.stop_loss_atr_multiplier is not None:
            raise ValueError("fast-test does not support stop_loss_atr_multiplier")
        if not self.version:
            raise ValueError("version must not be empty")
        if self.rsi_period <= 0:
            raise ValueError("rsi_period must be positive")
        if self.atr_period <= 0:
            raise ValueError("atr_period must be positive")
        if self.take_profit_atr_multiplier <= 0:
            raise ValueError("take_profit_atr_multiplier must be positive")
        if self.version != _V1_VERSION:
            raise ValueError("unsupported preset version")
        if (
            self.rsi_period != _V1_RSI_PERIOD
            or self.atr_period != _V1_ATR_PERIOD
            or self.take_profit_atr_multiplier != _V1_TAKE_PROFIT_ATR_MULTIPLIER
        ):
            raise ValueError("fast-test-v1 parameters must remain canonical")

    @property
    def minimum_warm_up_candles(self) -> int:
        return max(self.rsi_period + 1, self.atr_period)

    @classmethod
    def v1(cls) -> "FastTestPreset":
        return cls(
            version=_V1_VERSION,
            rsi_period=_V1_RSI_PERIOD,
            atr_period=_V1_ATR_PERIOD,
            take_profit_atr_multiplier=_V1_TAKE_PROFIT_ATR_MULTIPLIER,
        )
