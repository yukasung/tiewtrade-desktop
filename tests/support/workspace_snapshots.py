from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from tiewtrade.application.trading_workspace import (
    BasketEntryGate,
    BasketEntrySnapshot,
    BasketPairSnapshot,
    BasketSnapshot,
)
from tiewtrade.trading.entry_pair import entry_pair_index

OBSERVED_AT = datetime(2026, 8, 1, 12, tzinfo=UTC)


def basket_entries(
    *prices: str,
    quantity: str = "0.5",
    current_price: str = "65000",
    first_filled_at: datetime = OBSERVED_AT,
) -> tuple[BasketEntrySnapshot, ...]:
    """Entries numbered as they were taken, each with the price it paid."""
    mark = Decimal(current_price)
    return tuple(
        BasketEntrySnapshot(
            entry_number=number,
            filled_at_utc=first_filled_at + timedelta(hours=number - 1),
            price=Decimal(price),
            quantity=Decimal(quantity),
            notional=Decimal(price) * Decimal(quantity),
            unrealized_pnl=(mark - Decimal(price)) * Decimal(quantity),
        )
        for number, price in enumerate(prices, start=1)
    )


def basket_pairs(
    entries: tuple[BasketEntrySnapshot, ...],
    *,
    take_profit_offset: str = "1000",
) -> tuple[BasketPairSnapshot, ...]:
    """The Pairs those Entries make, averaged the way a Basket averages them.

    Counted by the rule's own module so a fixture cannot pair Entries up one
    way while the code pairs them another. The target is rounded to the scale
    the Entries were quoted at, because a real one is rounded to the Symbol's
    tick before anything is asked to show it — a fixture that skipped that
    would have a screen answering for a price no Basket can produce.
    """
    offset = Decimal(take_profit_offset)
    grouped: dict[int, list[BasketEntrySnapshot]] = {}
    for entry in entries:
        grouped.setdefault(entry_pair_index(entry.entry_number), []).append(entry)
    pairs: list[BasketPairSnapshot] = []
    for pair, members in sorted(grouped.items()):
        quantity = sum((member.quantity for member in members), Decimal("0"))
        notional = sum((member.notional for member in members), Decimal("0"))
        average = notional / quantity
        scale = min(member.price.as_tuple().exponent for member in members)
        assert isinstance(scale, int)
        pairs.append(
            BasketPairSnapshot(
                pair=pair,
                average_entry_price=average,
                take_profit_price=(average + offset).quantize(Decimal(1).scaleb(scale)),
            )
        )
    return tuple(pairs)


def basket_snapshot(**overrides: Any) -> BasketSnapshot:
    """An open Spot Basket, with only the fields a test cares about spelled out."""
    entries = basket_entries("63000", "65000")
    basket = BasketSnapshot(
        symbol="BTCUSDT",
        market_type="spot",
        entry_count=2,
        entries=entries,
        total_quantity=Decimal("1"),
        average_entry_price=Decimal("64000"),
        current_price=Decimal("65000"),
        take_profit_price=Decimal("66000"),
        pairs=basket_pairs(entries),
        unrealized_pnl=Decimal("1000"),
        entry_gate=BasketEntryGate.OPEN,
        completed_pairs=1,
        updated_at_utc=OBSERVED_AT,
    )
    if not overrides:
        return basket
    updated = replace(basket, **overrides)
    if "entries" in overrides and "pairs" not in overrides:
        # A fixture that changes the Entries means the Pairs with them, and
        # leaving the old ones behind reads as Entries whose Pair vanished.
        updated = replace(updated, pairs=basket_pairs(updated.entries))
    return updated
