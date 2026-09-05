from datetime import datetime

from tiewtrade.application.trading_workspace import BasketEntryGate
from tiewtrade.ui.display_time import display_timestamp


def blocked_entry_reason(
    gate: BasketEntryGate, *, cooldown_ends_at: datetime | None = None
) -> str:
    """Why this gate takes no further Entry, or empty when it still can.

    One phrase for every screen that reports a gate, so a Basket cannot read
    as cooling down in one place and open in another.

    `cooldown_ends_at` is the day the domain stamped on this Cooldown, for a
    Session whose Cooldown waits out days. It is formatted, never compared:
    what the gate is has already been decided against the market's own clock,
    and a screen working it out a second time could disagree with the Bot.
    """
    if gate is BasketEntryGate.MAX_ENTRIES_REACHED:
        return "Max Entries reached"
    if gate is BasketEntryGate.COOLDOWN:
        if cooldown_ends_at is None:
            # No day to name: this Cooldown waits on the trader, not the clock.
            return "Cooldown"
        return f"Cooldown until {display_timestamp(cooldown_ends_at)}"
    return ""
