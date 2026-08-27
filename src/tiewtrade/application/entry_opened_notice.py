"""What is said out loud when the Bot fills an Entry, and what may say it.

An Entry filling is the one thing that happens while nobody is watching the
screen: the Bot decides on a completed candle, and the trader may be nowhere
near the desktop when it does. The notice is the reading of that moment —
enough to know what was bought and where it is heading — held apart from any
one way of delivering it, so `application` never learns a transport.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True, slots=True)
class EntryOpenedNotice:
    """One Entry, as it reads to someone who was not watching it fill."""

    session_id: UUID
    symbol: str
    timeframe: str
    basket_id: UUID
    entry_number: int
    price: Decimal
    quantity: Decimal
    take_profit_price: Decimal | None
    filled_at: datetime

    @property
    def notional(self) -> Decimal:
        """What the Entry paid, which neither price nor quantity says alone."""
        return self.price * self.quantity


type EntryOpenedNotifier = Callable[[EntryOpenedNotice], None]
"""Say that an Entry filled.

Called from the candle loop, so it must return promptly and must never raise:
a message that could not be sent is not a reason for the Bot to stop trading,
and waiting on a network is not something a Session may be made to do.
"""
