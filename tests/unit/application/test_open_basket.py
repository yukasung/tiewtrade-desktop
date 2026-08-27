from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from tiewtrade.application.open_basket import (
    OpenBasketEntry,
    OpenBasketState,
    ReopenedBasket,
    reopen_basket,
)
from tiewtrade.trading.basket import SettledEntries
from tiewtrade.trading.entry_pair import EntryGateState
from tiewtrade.trading.entry_policy import EntryPolicy


def _entry(number: int, price: str, *, day: int) -> OpenBasketEntry:
    return OpenBasketEntry(
        entry_number=number,
        price=Decimal(price),
        quantity=Decimal("1"),
        filled_at=datetime(2026, 1, day, tzinfo=UTC),
        atr=Decimal("1"),
    )


def _state(*, waived_pair: int | None) -> OpenBasketState:
    return OpenBasketState(
        basket_id=uuid4(),
        entries=(_entry(1, "100", day=10), _entry(2, "99", day=20)),
        closed_basket_count=0,
        waived_pair=waived_pair,
    )


def _reopen(state: OpenBasketState, *, max_entries: int = 10) -> ReopenedBasket:
    return reopen_basket(
        state,
        entry_policy=EntryPolicy(max_entries=max_entries),
        take_profit_atr_multiplier=Decimal("1"),
        tick_size=Decimal("0.01"),
    )


def test_reopen_basket_restores_a_waiver_matching_the_completed_pair() -> None:
    # The two Entries replayed below complete this Basket's first Pair.
    reopened = _reopen(_state(waived_pair=1))

    assert reopened.lifecycle.can_enter()


def test_reopen_basket_ignores_a_waiver_for_a_different_pair() -> None:
    reopened = _reopen(_state(waived_pair=2))

    assert not reopened.lifecycle.can_enter()


def test_reopen_basket_without_a_waiver_stays_in_cooldown() -> None:
    reopened = _reopen(_state(waived_pair=None))

    assert not reopened.lifecycle.can_enter()


def _four_entry_state(*, waived_pair: int | None) -> OpenBasketState:
    """A Basket the trader carried past its first Cooldown into a second Pair."""
    state = _state(waived_pair=waived_pair)
    return replace(
        state,
        entries=state.entries + (_entry(3, "98", day=25), _entry(4, "97", day=30)),
    )


def test_reopen_basket_replays_entries_taken_after_a_waived_cooldown() -> None:
    """What the market already took comes back, whatever the gate says now.

    Storage keeps the latest waiver alone, so a Cooldown lifted two Pairs ago
    can never be answered for. Asking the gate to approve Entries that have
    already filled left such a Basket unreadable — and a Session holding it
    could neither show it nor start a Bot to close it.
    """
    reopened = _reopen(_four_entry_state(waived_pair=1))

    assert reopened.basket.entry_count == 4
    assert reopened.lifecycle.entry_count == 4


def test_reopen_basket_leaves_the_pair_a_stale_waiver_no_longer_names() -> None:
    """A waiver lifts the Cooldown it named, never the one that replaced it."""
    reopened = _reopen(_four_entry_state(waived_pair=1))

    assert not reopened.lifecycle.can_enter()


def test_reopen_basket_counts_a_sale_that_already_happened_toward_its_pair() -> None:
    """A restart must not forget a Fill just because its Entry already sold.

    Pair 1 (Entries 1-2) filled, was waived, and Pair 2 (Entries 3-4) filled
    and is cooling down. Entry 1 sold before the Runtime stopped, so only
    Entries 2-4 come back open — but all four Fills still happened, and
    Pair 2's Cooldown must still stand.
    """
    state = replace(
        _four_entry_state(waived_pair=1),
        entries=(
            _entry(2, "99", day=20),
            _entry(3, "98", day=25),
            _entry(4, "97", day=30),
        ),
        settled=SettledEntries(count=1, quantity=Decimal("1"), notional=Decimal("100")),
    )

    reopened = _reopen(state)

    assert reopened.lifecycle.entry_count == 3
    assert reopened.lifecycle.completed_pairs == 2
    assert reopened.lifecycle.entry_gate().state is EntryGateState.COOLDOWN


def test_reopen_basket_refuses_more_open_entries_than_the_quota_allows() -> None:
    """Replay skips the Cooldown, never the quota.

    Putting a Fill back does not ask whether an Entry may open — that was
    decided when it filled. The quota is a different claim: a Basket holding
    more Entries than its Session ever allowed is not a Basket to trade
    beside. The Basket itself is what refuses, so replay counts nothing the
    Basket has not already accepted.
    """
    over_quota = replace(
        _state(waived_pair=None),
        entries=tuple(_entry(number, "100", day=number) for number in range(1, 7)),
    )

    with pytest.raises(ValueError, match="maximum entries"):
        _reopen(over_quota, max_entries=4)
