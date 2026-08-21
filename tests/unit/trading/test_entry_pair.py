import pytest

from tiewtrade.trading.entry_pair import (
    EntryGate,
    EntryGateState,
    EntryPairLifecycle,
)
from tiewtrade.trading.entry_policy import EntryPolicy


def policy(max_entries: int = 10) -> EntryPolicy:
    return EntryPolicy(max_entries=max_entries)


def completed_pair(max_entries: int = 10) -> EntryPairLifecycle:
    """A Basket holding one completed Pair, which is a Basket in Cooldown."""
    lifecycle = EntryPairLifecycle(policy(max_entries))
    lifecycle.record_fill()
    lifecycle.record_fill()
    return lifecycle


def test_a_basket_that_has_never_traded_may_enter() -> None:
    lifecycle = EntryPairLifecycle(policy())

    assert lifecycle.can_enter()


def test_one_entry_may_complete_its_pair() -> None:
    lifecycle = EntryPairLifecycle(policy())
    lifecycle.record_fill()

    assert lifecycle.can_enter()


def test_completed_pair_holds_its_cooldown_with_no_time_limit() -> None:
    """A Cooldown does not lapse: only the trader lifts it."""
    lifecycle = completed_pair()

    assert lifecycle.entry_gate() == EntryGate(EntryGateState.COOLDOWN)
    assert not lifecycle.can_enter()


def test_entry_pair_stops_at_configured_maximum() -> None:
    lifecycle = completed_pair(4)
    lifecycle.waive_cooldown()
    lifecycle.record_fill()
    lifecycle.record_fill()

    assert lifecycle.entry_gate() == EntryGate(EntryGateState.MAX_ENTRIES_REACHED)


def test_spent_entries_and_cooldown_are_told_apart() -> None:
    lifecycle = completed_pair(4)
    lifecycle.waive_cooldown()
    lifecycle.record_fill()
    lifecycle.record_fill()

    gate = lifecycle.entry_gate()

    assert gate.state is EntryGateState.MAX_ENTRIES_REACHED
    assert not gate.is_open


def test_entry_pair_resets_after_basket_closes() -> None:
    lifecycle = completed_pair()

    lifecycle.reset()

    assert lifecycle.entry_count == 0
    assert lifecycle.can_enter()


def test_an_entry_that_sold_hands_its_place_back_to_the_pair() -> None:
    lifecycle = completed_pair()

    lifecycle.release_fill()

    assert lifecycle.entry_count == 1
    assert lifecycle.can_enter()


def test_release_requires_an_entry_to_release() -> None:
    lifecycle = EntryPairLifecycle(policy())

    with pytest.raises(ValueError, match="no Entry is open to release"):
        lifecycle.release_fill()


def test_waive_cooldown_opens_the_gate() -> None:
    lifecycle = completed_pair()

    lifecycle.waive_cooldown()

    assert lifecycle.can_enter()


def test_waive_cooldown_raises_when_gate_is_not_cooldown() -> None:
    lifecycle = EntryPairLifecycle(policy())

    with pytest.raises(ValueError, match="no Cooldown"):
        lifecycle.waive_cooldown()


def test_waived_cooldown_reapplies_to_the_next_pair() -> None:
    lifecycle = completed_pair()
    lifecycle.waive_cooldown()

    lifecycle.record_fill()
    lifecycle.record_fill()

    assert lifecycle.entry_gate() == EntryGate(EntryGateState.COOLDOWN)


def test_a_waived_cooldown_survives_an_entry_of_its_pair_selling() -> None:
    """The same Cooldown, still waived: selling did not make it a new one."""
    lifecycle = completed_pair()
    lifecycle.waive_cooldown()
    lifecycle.record_fill()

    lifecycle.release_fill()

    assert lifecycle.can_enter()


def test_a_pair_rebuilt_after_a_sale_cools_down_on_its_own_account() -> None:
    """Selling out of a waived Pair and refilling it is a Cooldown of its own."""
    lifecycle = completed_pair()
    lifecycle.waive_cooldown()
    lifecycle.release_fill()

    lifecycle.record_fill()

    assert lifecycle.entry_gate() == EntryGate(EntryGateState.COOLDOWN)


def test_restore_waiver_ignores_a_stale_pair() -> None:
    lifecycle = completed_pair()

    lifecycle.restore_waiver(0)

    assert not lifecycle.can_enter()


def test_restore_waiver_reapplies_a_matching_pair() -> None:
    waived = completed_pair()
    waived.waive_cooldown()

    replayed = completed_pair()
    replayed.restore_waiver(waived.completed_pairs)

    assert replayed.can_enter()


def test_reset_clears_the_waiver() -> None:
    lifecycle = completed_pair()
    lifecycle.waive_cooldown()

    lifecycle.reset()
    lifecycle.record_fill()
    lifecycle.record_fill()

    assert not lifecycle.can_enter()
