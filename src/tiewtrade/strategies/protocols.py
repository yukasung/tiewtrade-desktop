from decimal import Decimal
from typing import Protocol

from tiewtrade.market_data.candle import Candle
from tiewtrade.strategies.entry_intent import EntryIntent
from tiewtrade.strategies.indicators import IndicatorSnapshot


class StrategyPreset(Protocol):
    @property
    def version(self) -> str: ...

    @property
    def rsi_period(self) -> int: ...

    @property
    def atr_period(self) -> int: ...

    @property
    def take_profit_atr_multiplier(self) -> Decimal: ...

    @property
    def stop_loss_atr_multiplier(self) -> Decimal | None: ...

    @property
    def minimum_warm_up_candles(self) -> int: ...


class EntryStrategy(Protocol):
    def evaluate(
        self,
        candle: Candle,
        indicators: IndicatorSnapshot,
        entry_number: int,
        can_enter: bool,
    ) -> EntryIntent | None: ...

    def on_entry_filled(self, intent_id: str) -> None: ...

    def on_entry_rejected(self, intent_id: str) -> None: ...
