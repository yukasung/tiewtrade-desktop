from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from tiewtrade.application.trading_workspace import (
    BasketEntryGate,
    BasketEntrySnapshot,
    BasketEntryTargetSnapshot,
    BasketSnapshot,
)

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


def basket_targets(
    entries: tuple[BasketEntrySnapshot, ...],
    *,
    take_profit_offset: str = "1000",
) -> tuple[BasketEntryTargetSnapshot, ...]:
    """What each of those Entries leaves at, one Group per Entry.

    A Group of its own for each, so a fixture with two Entries has two
    targets and the column varies down the page. A Basket really does this
    whenever it averages up, and it is the arrangement that puts the most in
    front of whatever is being tested.

    The target is rounded to the scale the Entry was quoted at, because a
    real one is rounded to the Symbol's tick before anything is asked to show
    it — a fixture that skipped that would have a screen answering for a
    price no Basket can produce.
    """
    offset = Decimal(take_profit_offset)
    targets: list[BasketEntryTargetSnapshot] = []
    for group_id, entry in enumerate(entries, start=1):
        scale = entry.price.as_tuple().exponent
        assert isinstance(scale, int)
        targets.append(
            BasketEntryTargetSnapshot(
                entry_number=entry.entry_number,
                group_id=group_id,
                entry_price=entry.price,
                # One Entry per Group, so the Group paid what that Entry paid.
                group_average_entry_price=entry.price,
                take_profit_price=(entry.price + offset).quantize(
                    Decimal(1).scaleb(scale)
                ),
            )
        )
    return tuple(targets)


def basket_snapshot(**overrides: Any) -> BasketSnapshot:
    """An open Spot Basket, with only the fields a test cares about spelled out."""
    entries = basket_entries("63000", "65000")
    basket = BasketSnapshot(
        symbol="BTCUSDT",
        market_type="spot",
        position_side="long",
        entry_count=2,
        entries=entries,
        total_quantity=Decimal("1"),
        average_entry_price=Decimal("64000"),
        current_price=Decimal("65000"),
        take_profit_price=Decimal("66000"),
        targets=basket_targets(entries),
        unrealized_pnl=Decimal("1000"),
        entry_gate=BasketEntryGate.OPEN,
        completed_pairs=1,
        updated_at_utc=OBSERVED_AT,
    )
    if not overrides:
        return basket
    updated = replace(basket, **overrides)
    if "entries" in overrides and "targets" not in overrides:
        # A fixture that changes the Entries means their targets with them,
        # and leaving the old ones behind reads as Entries with no target.
        updated = replace(updated, targets=basket_targets(updated.entries))
    return updated
