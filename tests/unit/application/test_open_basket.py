from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

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
