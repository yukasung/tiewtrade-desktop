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
from tiewtrade.trading.entry_policy import EntryPolicy


def _state(*, waived_pair: int | None) -> OpenBasketState:
    return OpenBasketState(
        basket_id=uuid4(),
        entries=(
            OpenBasketEntry(
                entry_number=1,
                price=Decimal("100"),
                quantity=Decimal("1"),
                filled_at=datetime(2026, 1, 10, tzinfo=UTC),
                atr=Decimal("1"),
            ),
            OpenBasketEntry(
                entry_number=2,
                price=Decimal("99"),
                quantity=Decimal("1"),
                filled_at=datetime(2026, 1, 20, tzinfo=UTC),
                atr=Decimal("1"),
            ),
        ),
        closed_basket_count=0,
        waived_pair=waived_pair,
    )


def _reopen(state: OpenBasketState) -> ReopenedBasket:
    return reopen_basket(
        state,
        entry_policy=EntryPolicy(max_entries=10),
        take_profit_atr_multiplier=Decimal("1"),
        stop_loss_atr_multiplier=None,
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
    return OpenBasketState(
        basket_id=state.basket_id,
        entries=state.entries
        + (
            OpenBasketEntry(
                entry_number=3,
                price=Decimal("98"),
                quantity=Decimal("1"),
                filled_at=datetime(2026, 1, 30, tzinfo=UTC),
                atr=Decimal("1"),
            ),
            OpenBasketEntry(
                entry_number=4,
                price=Decimal("97"),
                quantity=Decimal("1"),
                filled_at=datetime(2026, 2, 10, tzinfo=UTC),
                atr=Decimal("1"),
            ),
        ),
        closed_basket_count=state.closed_basket_count,
        waived_pair=state.waived_pair,
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


def test_reopen_basket_refuses_more_open_entries_than_the_quota_allows() -> None:
    """Replay skips the Cooldown, never the quota.

    Putting a Fill back does not ask whether an Entry may open — that was
    decided when it filled. The quota is a different claim: a Basket holding
    more Entries than its Session ever allowed is not a Basket to trade
    beside. The Basket itself is what refuses, so replay counts nothing the
    Basket has not already accepted.
    """
    state = _state(waived_pair=None)
    over_quota = OpenBasketState(
        basket_id=state.basket_id,
        entries=tuple(
            OpenBasketEntry(
                entry_number=number,
                price=Decimal("100"),
                quantity=Decimal("1"),
                filled_at=datetime(2026, 1, 1, number, tzinfo=UTC),
                atr=Decimal("1"),
            )
            for number in range(1, 7)
        ),
        closed_basket_count=0,
        waived_pair=None,
    )

    with pytest.raises(ValueError, match="maximum entries"):
        reopen_basket(
            over_quota,
            entry_policy=EntryPolicy(max_entries=4),
            take_profit_atr_multiplier=Decimal("1"),
            stop_loss_atr_multiplier=None,
            tick_size=Decimal("0.01"),
        )
