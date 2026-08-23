from tiewtrade.application.trading_workspace import BasketEntryGate


def blocked_entry_reason(gate: BasketEntryGate) -> str:
    """Why this gate takes no further Entry, or empty when it still can.

    One phrase for every screen that reports a gate, so a Basket cannot read
    as cooling down in one place and open in another.
    """
    if gate is BasketEntryGate.MAX_ENTRIES_REACHED:
        return "Max Entries reached"
    if gate is BasketEntryGate.COOLDOWN:
        # No date to name: a Cooldown waits on the trader, not on the calendar.
        return "Cooldown"
    return ""
