from tests.support.workspace_snapshots import basket_snapshot
from tiewtrade.application.trading_workspace import BasketEntryGate
from tiewtrade.ui.entry_gate_display import (
    blocked_entry_reason,
    effective_entry_gate,
    entry_pair_index,
)


def test_entry_pair_index_counts_two_entries_to_a_pair() -> None:
    assert entry_pair_index(1) == 1
    assert entry_pair_index(2) == 1
    assert entry_pair_index(3) == 2
    assert entry_pair_index(4) == 2


def test_an_open_basket_leaves_every_entry_at_its_own_gate() -> None:
    basket = basket_snapshot(entry_gate=BasketEntryGate.OPEN, completed_pairs=1)

    assert effective_entry_gate(basket, 1) is BasketEntryGate.OPEN
    assert effective_entry_gate(basket, 2) is BasketEntryGate.OPEN


def test_max_entries_reached_reads_the_same_for_every_entry() -> None:
    basket = basket_snapshot(
        entry_gate=BasketEntryGate.MAX_ENTRIES_REACHED, completed_pairs=2
    )

    assert effective_entry_gate(basket, 1) is BasketEntryGate.MAX_ENTRIES_REACHED
    assert effective_entry_gate(basket, 4) is BasketEntryGate.MAX_ENTRIES_REACHED


def test_a_cooldown_blocks_only_the_pair_that_just_completed() -> None:
    """Entries 3 and 4 just closed Pair 2; Entries 1 and 2 already moved past it."""
    basket = basket_snapshot(entry_gate=BasketEntryGate.COOLDOWN, completed_pairs=2)

    assert effective_entry_gate(basket, 1) is BasketEntryGate.OPEN
    assert effective_entry_gate(basket, 2) is BasketEntryGate.OPEN
    assert effective_entry_gate(basket, 3) is BasketEntryGate.COOLDOWN
    assert effective_entry_gate(basket, 4) is BasketEntryGate.COOLDOWN


def test_a_single_pair_cooldown_blocks_both_of_its_own_entries() -> None:
    basket = basket_snapshot(entry_gate=BasketEntryGate.COOLDOWN, completed_pairs=1)

    assert effective_entry_gate(basket, 1) is BasketEntryGate.COOLDOWN
    assert effective_entry_gate(basket, 2) is BasketEntryGate.COOLDOWN


def test_blocked_entry_reason_names_each_gate() -> None:
    assert blocked_entry_reason(BasketEntryGate.OPEN) == ""
    assert blocked_entry_reason(BasketEntryGate.COOLDOWN) == "Cooldown"
    assert (
        blocked_entry_reason(BasketEntryGate.MAX_ENTRIES_REACHED)
        == "Max Entries reached"
    )
