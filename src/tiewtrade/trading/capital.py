from dataclasses import dataclass
from decimal import Decimal

from tiewtrade.trading.futures_policy import FuturesTradingPolicy


def _require_capital_per_entry(capital_per_entry: Decimal) -> None:
    """The one thing both markets ask of the capital they are handed.

    Every Entry either market sizes is this figure, so a plan built from
    nothing, a negative, or a NaN would carry that on into every Entry rather
    than failing where it started.
    """
    if not capital_per_entry.is_finite() or capital_per_entry <= 0:
        raise ValueError("capital per Entry must be finite and positive")


@dataclass(frozen=True, slots=True)
class SpotCapitalPlan:
    """What one Spot Entry spends.

    The trader states it rather than a wallet to divide up: the quotient was
    the only figure anything ever read, and how many Entries a Basket may take
    is a gate on the Basket, not a divisor on the money.
    """

    entry_notional: Decimal

    @classmethod
    def from_capital_per_entry(cls, capital_per_entry: Decimal) -> "SpotCapitalPlan":
        _require_capital_per_entry(capital_per_entry)
        return cls(entry_notional=capital_per_entry)


@dataclass(frozen=True, slots=True)
class FuturesCapitalPlan:
    """What one Futures Entry commits, and the position that commitment opens.

    The figure the trader states is the margin: it is what leaves the wallet
    per Entry whatever the leverage, which is the number a position is sized
    against rather than the one it is worth.
    """

    initial_margin_per_entry: Decimal
    target_notional_per_entry: Decimal

    @classmethod
    def from_capital_per_entry(
        cls,
        capital_per_entry: Decimal,
        futures_policy: FuturesTradingPolicy,
    ) -> "FuturesCapitalPlan":
        _require_capital_per_entry(capital_per_entry)
        return cls(
            initial_margin_per_entry=capital_per_entry,
            target_notional_per_entry=(
                capital_per_entry * Decimal(futures_policy.leverage)
            ),
        )
