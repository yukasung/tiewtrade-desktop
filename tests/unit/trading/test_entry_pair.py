import pytest

from tests.support.entry_pair_walk import reachable_lifecycles
from tiewtrade.trading.entry_pair import (
    EntryGate,
    EntryGateState,
    EntryPairLifecycle,
    entry_pair_index,
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


def test_entry_pair_index_counts_two_entries_to_a_pair() -> None:
    assert entry_pair_index(1) == 1
    assert entry_pair_index(2) == 1
    assert entry_pair_index(3) == 2
    assert entry_pair_index(4) == 2


def test_entry_gate_for_reads_the_same_as_entry_gate_when_open() -> None:
    lifecycle = EntryPairLifecycle(policy())
    lifecycle.record_fill()

    assert lifecycle.entry_gate_for(1) == EntryGate(EntryGateState.OPEN)


def test_entry_gate_for_blocks_the_pair_that_just_completed() -> None:
    lifecycle = completed_pair()

    assert lifecycle.entry_gate_for(1) == EntryGate(EntryGateState.COOLDOWN)
    assert lifecycle.entry_gate_for(2) == EntryGate(EntryGateState.COOLDOWN)


def test_entry_gate_for_opens_an_earlier_pair_the_basket_has_moved_past() -> None:
    """Entries 1 and 2 are Pair 1, already waived; 3 and 4 just formed Pair 2."""
    lifecycle = completed_pair()
    lifecycle.waive_cooldown()
    lifecycle.record_fill()
    lifecycle.record_fill()

    assert lifecycle.entry_gate() == EntryGate(EntryGateState.COOLDOWN)
    assert lifecycle.entry_gate_for(1) == EntryGate(EntryGateState.OPEN)
    assert lifecycle.entry_gate_for(2) == EntryGate(EntryGateState.OPEN)
    assert lifecycle.entry_gate_for(3) == EntryGate(EntryGateState.COOLDOWN)
    assert lifecycle.entry_gate_for(4) == EntryGate(EntryGateState.COOLDOWN)


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


def test_selling_an_entry_of_the_cooling_pair_lifts_its_cooldown() -> None:
    """A Cooldown is the Basket holding both Entries of a Pair at once.

    Sell either of them and it is no longer holding both, so the rule the
    Cooldown states has nothing left to hold shut.
    """
    lifecycle = completed_pair()

    lifecycle.release_fill(1)

    assert lifecycle.entry_count == 1
    assert lifecycle.can_enter()


def test_selling_from_an_earlier_pair_leaves_a_later_cooldown_standing() -> None:
    """Entries 1 and 2 are Pair 1, already waived; 3 and 4 just formed Pair 2."""
    lifecycle = completed_pair()
    lifecycle.waive_cooldown()
    lifecycle.record_fill()
    lifecycle.record_fill()

    lifecycle.release_fill(1)

    assert lifecycle.entry_gate() == EntryGate(EntryGateState.COOLDOWN)


def test_a_lifted_cooldown_does_not_carry_to_the_next_pair() -> None:
    """The Pair that comes after the one that was broken up cools down as usual."""
    lifecycle = completed_pair()
    lifecycle.release_fill(1)
    lifecycle.record_fill()
    lifecycle.record_fill()

    assert lifecycle.entry_gate() == EntryGate(EntryGateState.COOLDOWN)


def test_release_requires_an_entry_to_release() -> None:
    lifecycle = EntryPairLifecycle(policy())

    with pytest.raises(ValueError, match="no Entry is open to release"):
        lifecycle.release_fill(1)


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

    lifecycle.release_fill(1)

    assert lifecycle.can_enter()


def test_a_fresh_fill_after_a_waived_sale_opens_a_pair_of_its_own() -> None:
    """A refill does not rejoin the Pair it replaces — it starts the next one.

    Selling one Entry of a waived Pair and taking a fresh Fill does not
    instantly recomplete a Pair: the Fill is counted by when it happened, not
    by how many Entries merely happen to be open, so it is the first half of
    a new Pair and still needs its own partner.
    """
    lifecycle = completed_pair()
    lifecycle.waive_cooldown()
    lifecycle.release_fill(1)

    lifecycle.record_fill()

    assert lifecycle.entry_gate() == EntryGate(EntryGateState.OPEN)


def test_a_rebuilt_pair_cools_down_once_its_own_partner_fills() -> None:
    """The refill's own partner is what actually completes its Pair."""
    lifecycle = completed_pair()
    lifecycle.waive_cooldown()
    lifecycle.release_fill(1)
    lifecycle.record_fill()

    lifecycle.record_fill()

    assert lifecycle.entry_gate() == EntryGate(EntryGateState.COOLDOWN)


def test_a_replayed_waiver_naming_a_stale_pair_is_ignored() -> None:
    lifecycle = EntryPairLifecycle.replayed(
        policy(), open_entry_numbers=(1, 2), waived_pair=0
    )

    assert not lifecycle.can_enter()


def test_a_replayed_waiver_naming_the_pair_it_lifted_opens_the_gate() -> None:
    waived = completed_pair()
    waived.waive_cooldown()

    lifecycle = EntryPairLifecycle.replayed(
        policy(), open_entry_numbers=(1, 2), waived_pair=waived.completed_pairs
    )

    assert lifecycle.can_enter()


def test_reset_clears_the_waiver() -> None:
    lifecycle = completed_pair()
    lifecycle.waive_cooldown()

    lifecycle.reset()
    lifecycle.record_fill()
    lifecycle.record_fill()

    assert not lifecycle.can_enter()


def test_replay_puts_back_fills_a_cooldown_would_have_blocked() -> None:
    """Fills that already happened are counted, not re-decided.

    The third of these could never have passed the gate: the Pair before it
    closed one. The trader lifted that Cooldown at the time, and storage no
    longer holds the waiver they lifted it with.
    """
    lifecycle = EntryPairLifecycle.replayed(
        policy(), open_entry_numbers=(1, 2, 3), waived_pair=None
    )

    assert lifecycle.entry_count == 3
    assert lifecycle.completed_pairs == 1


def test_replayed_fills_still_complete_their_pairs() -> None:
    lifecycle = EntryPairLifecycle.replayed(
        policy(), open_entry_numbers=(1, 2, 3, 4), waived_pair=None
    )

    assert lifecycle.completed_pairs == 2
    assert lifecycle.entry_gate() == EntryGate(EntryGateState.COOLDOWN)


def test_replay_reads_a_lifted_cooldown_off_the_entries_still_open() -> None:
    """A Pair broken up before a restart is still broken up after one.

    Nothing records that a Cooldown was lifted this way, because nothing has
    to: the Entries the Basket still holds say which Pair is short of one.
    """
    lifecycle = EntryPairLifecycle.replayed(
        policy(),
        open_entry_numbers=(2,),
        settled_entry_count=1,
        waived_pair=None,
    )

    assert lifecycle.completed_pairs == 1
    assert lifecycle.can_enter()


def test_replay_holds_a_cooldown_an_earlier_pair_s_sale_never_lifted() -> None:
    """Entry 1 of Pair 1 sold; Pair 2 is whole, so its Cooldown still stands."""
    lifecycle = EntryPairLifecycle.replayed(
        policy(),
        open_entry_numbers=(2, 3, 4),
        settled_entry_count=1,
        waived_pair=None,
    )

    assert lifecycle.completed_pairs == 2
    assert lifecycle.entry_gate() == EntryGate(EntryGateState.COOLDOWN)


def test_a_pair_never_held_whole_does_not_cool_down() -> None:
    """A Cooldown is the Basket holding both Entries of a Pair at once.

    Entry 3 sells before Entry 4 ever fills, so the Pair the two of them make
    is complete by the count and was never held whole. The gate turns on what
    the Basket holds, not on the order the holding and the selling happened
    in, so there is no Cooldown here to lift.
    """
    lifecycle = completed_pair()
    lifecycle.release_fill(1)
    lifecycle.record_fill()
    lifecycle.release_fill(3)
    lifecycle.record_fill()

    assert lifecycle.completed_pairs == 2
    assert lifecycle.entry_gate() == EntryGate(EntryGateState.OPEN)


def test_replay_reads_a_pair_never_held_whole_the_same_way() -> None:
    """Replay has only the Entries still open, and reaches the same answer.

    The live lifecycle and replay state one rule between them: neither asks
    when a sale happened, both ask which Entries of the Pair are still here.
    """
    lifecycle = EntryPairLifecycle.replayed(
        policy(),
        open_entry_numbers=(2, 4),
        settled_entry_count=2,
        waived_pair=None,
    )

    assert lifecycle.completed_pairs == 2
    assert lifecycle.entry_gate() == EntryGate(EntryGateState.OPEN)


def test_at_most_one_pair_ever_reads_as_cooling_down() -> None:
    """One Cooldown at a time, in every Basket a trader can reach.

    A Cooldown is the Pair the Basket has just completed and is still holding
    both of. Two Pairs answering to one at once would leave a trader a
    Cooldown that "Cancel Cooldown" — which lifts exactly one — could not
    clear, so this is the shape of the rule rather than a happy accident.
    """
    for reached in reachable_lifecycles(policy()):
        cooling = reached.cooling_pairs()

        assert len(cooling) <= 1, (
            f"{reached.open_entry_numbers} left Pairs {sorted(cooling)} cooling"
        )


def test_a_basket_that_cools_down_says_so_on_one_of_its_own_entries() -> None:
    """A gate nobody can see is a gate the trader cannot act on."""
    for reached in reachable_lifecycles(policy()):
        if not reached.open_entry_numbers:
            continue
        gate = reached.lifecycle.entry_gate()
        if gate.state is not EntryGateState.COOLDOWN:
            continue

        assert reached.cooling_pairs(), (
            f"{reached.open_entry_numbers} cools down on no Entry of its own"
        )


def test_replay_reaches_the_same_gate_as_the_lifecycle_it_replays() -> None:
    """A restart is not a decision: it puts back what was already there.

    The live rule and the replayed one are one rule, asked of a sale as it
    happens and of the Entries a sale left behind. Where they disagree, a
    Basket changes its mind about a Cooldown by being reopened.
    """
    for reached in reachable_lifecycles(policy()):
        replayed = reached.replayed(policy())

        assert replayed.completed_pairs == reached.lifecycle.completed_pairs
        assert replayed.entry_gate() == reached.lifecycle.entry_gate(), (
            f"{reached.open_entry_numbers} replays as a different gate"
        )
        for number in reached.open_entry_numbers:
            assert replayed.entry_gate_for(number) == reached.lifecycle.entry_gate_for(
                number
            )
