from datetime import UTC, datetime

from tiewtrade.application.trading_workspace import BasketEntryGate
from tiewtrade.ui.entry_gate_display import blocked_entry_reason

LIFTS_AT = datetime(2026, 8, 29, 3, 0, tzinfo=UTC)


def test_blocked_entry_reason_names_each_gate() -> None:
    assert blocked_entry_reason(BasketEntryGate.OPEN) == ""
    assert blocked_entry_reason(BasketEntryGate.COOLDOWN) == "Cooldown"
    assert (
        blocked_entry_reason(BasketEntryGate.MAX_ENTRIES_REACHED)
        == "Max Entries reached"
    )


def test_a_cooldown_that_lifts_by_itself_names_the_day_it_lifts_on() -> None:
    """Read on the trader's own clock, like every other time on screen."""
    reason = blocked_entry_reason(BasketEntryGate.COOLDOWN, cooldown_ends_at=LIFTS_AT)

    assert reason == "Cooldown until 2026-08-29 10:00:00"


def test_only_a_cooldown_reads_a_day_it_lifts_on() -> None:
    """A gate out of Entries never opens again, whatever a stale date says."""
    assert (
        blocked_entry_reason(
            BasketEntryGate.MAX_ENTRIES_REACHED, cooldown_ends_at=LIFTS_AT
        )
        == "Max Entries reached"
    )
    assert blocked_entry_reason(BasketEntryGate.OPEN, cooldown_ends_at=LIFTS_AT) == ""
