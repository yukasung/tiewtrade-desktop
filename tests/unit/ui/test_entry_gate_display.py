from tiewtrade.application.trading_workspace import BasketEntryGate
from tiewtrade.ui.entry_gate_display import blocked_entry_reason


def test_blocked_entry_reason_names_each_gate() -> None:
    assert blocked_entry_reason(BasketEntryGate.OPEN) == ""
    assert blocked_entry_reason(BasketEntryGate.COOLDOWN) == "Cooldown"
    assert (
        blocked_entry_reason(BasketEntryGate.MAX_ENTRIES_REACHED)
        == "Max Entries reached"
    )
