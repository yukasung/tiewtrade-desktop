"""A Basket that outlived the Runtime that opened it, and how it comes back.

Nothing about a Basket is remembered as a total: the take profit, the average
Entry Price and the Entry Pair count are all read out of its Entries. Putting
one back is therefore replaying those Entries, which is the only way the
numbers come back the same rather than close, and the reason both the Runtime
that trades it and the tab that shows it ask here rather than each other.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from tiewtrade.trading.basket import Basket, SettledEntries
from tiewtrade.trading.entry_pair import EntryPairLifecycle
from tiewtrade.trading.entry_policy import EntryPolicy


@dataclass(frozen=True, slots=True)
class OpenBasketEntry:
    """One Entry of a Basket, as much of it as putting the Basket back needs."""

    entry_number: int
    price: Decimal
    quantity: Decimal
    filled_at: datetime
    atr: Decimal


@dataclass(frozen=True, slots=True)
class OpenBasketState:
    basket_id: UUID
    entries: tuple[OpenBasketEntry, ...]
    closed_basket_count: int
    # What the Basket's own Entries left when they sold, which its result is
    # owed however long ago they went. Nothing settled is the default: a
    # Basket that has never been sold out of has none.
    settled: SettledEntries = field(default_factory=SettledEntries)
    # A persisted Cooldown waiver, restored into the replayed lifecycle when
    # it still names the Pair replay counts. No Fill carries this.
    waived_pair: int | None = None

    def __post_init__(self) -> None:
        if not self.entries:
            raise ValueError("an open Basket has at least one Entry")
        if self.closed_basket_count < 0:
            raise ValueError("closed_basket_count must not be negative")
        filled_at = [entry.filled_at for entry in self.entries]
        if filled_at != sorted(filled_at):
            raise ValueError("Entries must be ordered by the time they filled")


@dataclass(frozen=True, slots=True)
class ReopenedBasket:
    """A replayed Basket, beside the Entries and Pair count it was built from."""

    state: OpenBasketState
    basket: Basket
    lifecycle: EntryPairLifecycle


def reopen_basket(
    state: OpenBasketState,
    *,
    entry_policy: EntryPolicy,
    take_profit_atr_multiplier: Decimal,
    stop_loss_atr_multiplier: Decimal | None,
    tick_size: Decimal,
) -> ReopenedBasket:
    """Replay the Entries, letting the Basket state its own numbers again."""
    basket = Basket(
        state.basket_id,
        entry_policy,
        take_profit_atr_multiplier,
        stop_loss_atr_multiplier=stop_loss_atr_multiplier,
        # Replaying the open Entries states everything the Basket holds; what
        # it already sold is not there to be replayed, and is told to it.
        settled=state.settled,
    )
    for entry in state.entries:
        basket.add_entry(
            price=entry.price,
            quantity=entry.quantity,
            filled_at=entry.filled_at,
            atr=entry.atr,
            tick_size=tick_size,
            entry_number=entry.entry_number,
        )
    # Counted off the Basket rather than off the Entries handed in, so the
    # Pair count can only ever describe Entries the Basket actually took.
    lifecycle = EntryPairLifecycle.replayed(
        entry_policy,
        fill_count=basket.entry_count,
        waived_pair=state.waived_pair,
    )
    return ReopenedBasket(state=state, basket=basket, lifecycle=lifecycle)
