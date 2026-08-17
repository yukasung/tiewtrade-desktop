from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from tiewtrade.trading.entry_policy import EntryPolicy
from tiewtrade.trading.futures_policy import FuturesTradingPolicy


class TradeMode(StrEnum):
    PAPER = "paper"
    LIVE = "live"


class MarketType(StrEnum):
    SPOT = "spot"
    FUTURES = "futures"


@dataclass(frozen=True, slots=True)
class SessionConfig:
    session_id: UUID
    preset_version: str
    market_type: MarketType
    trade_mode: TradeMode
    capital_per_entry: Decimal
    entry_policy: EntryPolicy
    # Spot needs no policy of its own: it spends the same on every Entry, as
    # many as the Entry policy allows. Futures is the market that has more to
    # say — leverage, margin mode and a collateral buffer.
    futures_policy: FuturesTradingPolicy | None = None

    def __post_init__(self) -> None:
        if self.capital_per_entry <= 0:
            raise ValueError("capital_per_entry must be positive")
        if self.market_type is MarketType.SPOT and self.futures_policy is not None:
            raise ValueError("futures_policy is not valid for Spot sessions")
        if self.market_type is MarketType.FUTURES and self.futures_policy is None:
            raise ValueError("futures_policy is required for Futures sessions")

    @property
    def session_capital(self) -> Decimal:
        """The most this Session ever commits: every Entry it may take.

        The trader states what one Entry spends, so this is the only total
        the Session has — and it is the one a Futures wallet is measured
        against, since nothing else here says how much money is at risk.
        """
        return self.capital_per_entry * Decimal(self.entry_policy.max_entries)
