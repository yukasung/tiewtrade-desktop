from dataclasses import dataclass
from decimal import Decimal

from tiewtrade.strategies.preset_rules import (
    minimum_warm_up_candles,
    require_shared_preset_rules,
)

_V1_VERSION = "fast-test-v1"
_V1_RSI_PERIOD = 14
_V1_ATR_PERIOD = 14
_V1_TAKE_PROFIT_ATR_MULTIPLIER = Decimal("3")
_V1_STOP_LOSS_ATR_MULTIPLIER = Decimal("3")


@dataclass(frozen=True, slots=True)
class FastTestPreset:
    """A preset that enters fast but sizes its exits like a real one.

    What this preset shortens is the wait for a *signal*, which is the
    strategy's business, not the wait for a *measurement*. The RSI it carries
    is the shortest the pipeline accepts because FastTestStrategy reads no
    market condition at all, so the number only has to exist.

    The ATR is another matter, and is the fourteen every traded preset uses.
    A two-candle ATR reads the last two candles and little else: in a quiet
    minute it fell to a hundredth, which put the take profit three cents above
    the Entry Price and closed the Basket on the first tick up. Targets read
    off two candles are not a faster test, they are a different one.

    Warm-up is loaded, not waited for — the Runtime backfills
    minimum_warm_up_candles completed candles before the first live one — so a
    fourteen-candle ATR still has an Entry on screen within a minute.
    """

    version: str
    rsi_period: int
    atr_period: int
    take_profit_atr_multiplier: Decimal
    stop_loss_atr_multiplier: Decimal | None

    def __post_init__(self) -> None:
        require_shared_preset_rules(
            version=self.version,
            rsi_period=self.rsi_period,
            atr_period=self.atr_period,
            take_profit_atr_multiplier=self.take_profit_atr_multiplier,
            stop_loss_atr_multiplier=self.stop_loss_atr_multiplier,
        )
        if self.version != _V1_VERSION:
            raise ValueError("unsupported preset version")
        if (
            self.rsi_period != _V1_RSI_PERIOD
            or self.atr_period != _V1_ATR_PERIOD
            or self.take_profit_atr_multiplier != _V1_TAKE_PROFIT_ATR_MULTIPLIER
            or self.stop_loss_atr_multiplier != _V1_STOP_LOSS_ATR_MULTIPLIER
        ):
            raise ValueError("fast-test-v1 parameters must remain canonical")

    @property
    def minimum_warm_up_candles(self) -> int:
        return minimum_warm_up_candles(self.rsi_period, self.atr_period)

    @classmethod
    def v1(cls) -> "FastTestPreset":
        return cls(
            version=_V1_VERSION,
            rsi_period=_V1_RSI_PERIOD,
            atr_period=_V1_ATR_PERIOD,
            take_profit_atr_multiplier=_V1_TAKE_PROFIT_ATR_MULTIPLIER,
            stop_loss_atr_multiplier=_V1_STOP_LOSS_ATR_MULTIPLIER,
        )
