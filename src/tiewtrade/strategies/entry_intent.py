from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from tiewtrade.market_data.candle import Candle
from tiewtrade.trading.position import PositionSide


@dataclass(frozen=True, slots=True)
class EntryIntent:
    intent_id: str
    session_id: UUID
    preset_version: str
    entry_number: int
    signal_candle: Candle
    atr: Decimal
    side: PositionSide = PositionSide.LONG
