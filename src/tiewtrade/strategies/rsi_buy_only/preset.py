from dataclasses import dataclass
from decimal import Decimal

_V1_VERSION = "rsi-buy-only-v1"
_V1_RSI_PERIOD = 14
_V1_RSI_BUY_THRESHOLD = Decimal("30")
_V1_ATR_PERIOD = 14
_V1_TAKE_PROFIT_ATR_MULTIPLIER = Decimal("1.5")
_V1_STOP_LOSS_ATR_MULTIPLIER = Decimal("1.5")


@dataclass(frozen=True, slots=True)
class RsiBuyOnlyPreset:
    version: str
    rsi_period: int
    rsi_buy_threshold: Decimal
    atr_period: int
    take_profit_atr_multiplier: Decimal
    stop_loss_atr_multiplier: Decimal | None

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("version must not be empty")
        if self.rsi_period <= 0:
            raise ValueError("rsi_period must be positive")
        if self.atr_period <= 0:
            raise ValueError("atr_period must be positive")
        if not Decimal("0") <= self.rsi_buy_threshold <= Decimal("100"):
            raise ValueError("rsi_buy_threshold must be between 0 and 100")
        if self.take_profit_atr_multiplier <= 0:
            raise ValueError("take_profit_atr_multiplier must be positive")
        if (
            self.stop_loss_atr_multiplier is not None
            and self.stop_loss_atr_multiplier <= 0
        ):
            raise ValueError("stop_loss_atr_multiplier must be positive")
        if self.version != _V1_VERSION:
            raise ValueError("unsupported preset version")
        if (
            self.rsi_period != _V1_RSI_PERIOD
            or self.rsi_buy_threshold != _V1_RSI_BUY_THRESHOLD
            or self.atr_period != _V1_ATR_PERIOD
            or self.take_profit_atr_multiplier != _V1_TAKE_PROFIT_ATR_MULTIPLIER
            or self.stop_loss_atr_multiplier != _V1_STOP_LOSS_ATR_MULTIPLIER
        ):
            raise ValueError("rsi-buy-only-v1 parameters must remain canonical")

    @property
    def minimum_warm_up_candles(self) -> int:
        return max(self.rsi_period + 1, self.atr_period)

    @classmethod
    def v1(cls) -> "RsiBuyOnlyPreset":
        return cls(
            version=_V1_VERSION,
            rsi_period=_V1_RSI_PERIOD,
            rsi_buy_threshold=_V1_RSI_BUY_THRESHOLD,
            atr_period=_V1_ATR_PERIOD,
            take_profit_atr_multiplier=_V1_TAKE_PROFIT_ATR_MULTIPLIER,
            stop_loss_atr_multiplier=_V1_STOP_LOSS_ATR_MULTIPLIER,
        )
