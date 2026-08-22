from tiewtrade.application.trading_workspace import BasketEntryGate, BasketSnapshot


def blocked_entry_reason(basket: BasketSnapshot) -> str:
    """Why this Basket takes no further Entry, or empty when it still can.

    One phrase for every screen that reports the gate, so a Basket cannot read
    as cooling down in one place and open in another.
    """
    if basket.entry_gate is BasketEntryGate.MAX_ENTRIES_REACHED:
        return "Max Entries reached"
    if basket.entry_gate is BasketEntryGate.COOLDOWN:
        # No date to name: a Cooldown waits on the trader, not on the calendar.
        return "Cooldown"
    return ""
