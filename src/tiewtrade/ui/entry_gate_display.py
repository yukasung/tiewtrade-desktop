from tiewtrade.application.trading_workspace import BasketEntryGate, BasketSnapshot


def entry_pair_index(entry_number: int) -> int:
    """Which Entry Pair this Entry belongs to, counted across the Basket's life."""
    return (entry_number - 1) // 2 + 1


def effective_entry_gate(basket: BasketSnapshot, entry_number: int) -> BasketEntryGate:
    """The gate as it stands for one Entry, not the Basket read as a whole.

    A Cooldown blocks the next Entry, not the ones an earlier, already-resolved
    Pair left open — those were waived past their own Cooldown long before this
    one started, or they would not be here for a later Pair to have formed. So
    an Entry from a Pair before the one that just completed reads Open even
    while the Basket itself is cooling down.
    """
    if basket.entry_gate is not BasketEntryGate.COOLDOWN:
        return basket.entry_gate
    if entry_pair_index(entry_number) < basket.completed_pairs:
        return BasketEntryGate.OPEN
    return basket.entry_gate


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
