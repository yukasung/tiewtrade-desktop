from datetime import UTC, date, datetime

import pytest

from tiewtrade.trading.entry_pair import (
    EntryGate,
    EntryGateState,
    EntryPairLifecycle,
)
from tiewtrade.trading.entry_policy import EntryPolicy


def policy(max_entries: int = 10) -> EntryPolicy:
    return EntryPolicy(max_entries=max_entries)


def test_one_entry_may_cross_month_to_complete_its_pair() -> None:
    lifecycle = EntryPairLifecycle(policy())
    lifecycle.record_fill(datetime(2026, 1, 31, 23, 55, tzinfo=UTC))

    assert lifecycle.can_enter(datetime(2026, 2, 1, tzinfo=UTC))


def test_completed_pair_blocks_remainder_then_cooldown_month() -> None:
    lifecycle = EntryPairLifecycle(policy())
    lifecycle.record_fill(datetime(2026, 1, 10, tzinfo=UTC))
    lifecycle.record_fill(datetime(2026, 1, 20, tzinfo=UTC))

    assert not lifecycle.can_enter(datetime(2026, 1, 25, tzinfo=UTC))
    assert not lifecycle.can_enter(datetime(2026, 2, 15, tzinfo=UTC))
    assert lifecycle.can_enter(datetime(2026, 3, 1, tzinfo=UTC))


def test_completed_pair_requires_completion_month() -> None:
    lifecycle = EntryPairLifecycle(policy())
    lifecycle.record_fill(datetime(2026, 1, 10, tzinfo=UTC))
    lifecycle.record_fill(datetime(2026, 1, 20, tzinfo=UTC))
    lifecycle._completed_pair_month = None

    with pytest.raises(
        RuntimeError,
        match="completed Entry Pair is missing its completion month",
    ):
        lifecycle.can_enter(datetime(2026, 3, 1, tzinfo=UTC))


def test_entry_pair_stops_at_configured_maximum() -> None:
    lifecycle = EntryPairLifecycle(policy(4))
    for pair_start_month in (1, 3):
        lifecycle.record_fill(datetime(2026, pair_start_month, 1, tzinfo=UTC))
        lifecycle.record_fill(datetime(2026, pair_start_month, 2, tzinfo=UTC))

    assert not lifecycle.can_enter(datetime(2026, 5, 1, tzinfo=UTC))


def test_cooldown_gate_names_the_month_entries_reopen() -> None:
    lifecycle = EntryPairLifecycle(policy())
    lifecycle.record_fill(datetime(2026, 1, 10, tzinfo=UTC))
    lifecycle.record_fill(datetime(2026, 1, 20, tzinfo=UTC))

    for at in (datetime(2026, 1, 25, tzinfo=UTC), datetime(2026, 2, 15, tzinfo=UTC)):
        assert lifecycle.entry_gate(at) == EntryGate(
            EntryGateState.COOLDOWN, date(2026, 3, 1)
        )
    assert lifecycle.entry_gate(datetime(2026, 3, 1, tzinfo=UTC)).state is (
        EntryGateState.OPEN
    )


def test_cooldown_reopening_crosses_the_year() -> None:
    lifecycle = EntryPairLifecycle(policy())
    lifecycle.record_fill(datetime(2026, 12, 10, tzinfo=UTC))
    lifecycle.record_fill(datetime(2026, 12, 20, tzinfo=UTC))

    gate = lifecycle.entry_gate(datetime(2027, 1, 5, tzinfo=UTC))

    assert gate.reopens_at == date(2027, 2, 1)


def test_spent_entries_and_cooldown_are_told_apart() -> None:
    lifecycle = EntryPairLifecycle(policy(4))
    for pair_start_month in (1, 3):
        lifecycle.record_fill(datetime(2026, pair_start_month, 1, tzinfo=UTC))
        lifecycle.record_fill(datetime(2026, pair_start_month, 2, tzinfo=UTC))

    gate = lifecycle.entry_gate(datetime(2026, 5, 1, tzinfo=UTC))

    assert gate.state is EntryGateState.MAX_ENTRIES_REACHED
    assert gate.reopens_at is None
    assert not gate.is_open


def test_open_gate_rejects_a_reopening_month() -> None:
    with pytest.raises(ValueError, match="Cooldown Month"):
        EntryGate(EntryGateState.OPEN, date(2026, 3, 1))


def test_cooldown_gate_requires_a_reopening_month() -> None:
    with pytest.raises(ValueError, match="Cooldown Month"):
        EntryGate(EntryGateState.COOLDOWN)


def test_entry_pair_requires_utc_timestamp() -> None:
    lifecycle = EntryPairLifecycle(policy())

    with pytest.raises(ValueError, match="UTC"):
        lifecycle.can_enter(datetime(2026, 1, 1))


def test_entry_pair_resets_after_basket_closes() -> None:
    lifecycle = EntryPairLifecycle(policy())
    lifecycle.record_fill(datetime(2026, 1, 10, tzinfo=UTC))
    lifecycle.record_fill(datetime(2026, 1, 20, tzinfo=UTC))

    lifecycle.reset()

    assert lifecycle.entry_count == 0
    assert lifecycle.can_enter(datetime(2026, 1, 25, tzinfo=UTC))
