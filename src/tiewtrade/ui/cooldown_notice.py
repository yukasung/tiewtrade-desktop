"""What is said beside the Bot while a Cooldown holds the next Entry back.

A Cooldown already reads on every row of the Position tab, but a trader
watching the Bot panel on the right is looking at the Session, not at the
table — and a Bot that is running and taking no Entry reads as a Bot that
has stopped working. The card says why, in the same words the tab uses, and
takes itself back the moment the gate opens again.

It is said at the quieter of the two levels: a Cooldown is the strategy doing
what the Session told it to, not a fault standing between the trader and
their Bot.
"""

from PySide6.QtWidgets import QWidget

from tiewtrade.application.trading_workspace import BasketEntryGate, BasketSnapshot
from tiewtrade.ui.entry_gate_display import blocked_entry_reason
from tiewtrade.ui.notice_card import NoticeCard, NoticeLevel

# What the wait means for the Session, said after the phrase the Position tab
# reads — the reason alone names a state, not what it costs the trader.
_HELD_BACK = "no new Entry until it lifts"


class CooldownNoticeCard(NoticeCard):
    """The standing Cooldown as a card in the column beside the chart."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(NoticeLevel.INFO, parent=parent)

    def show_basket(self, basket: BasketSnapshot | None) -> None:
        """Say what the open Basket is waiting on, or nothing if it is not.

        The gate is read as the domain stamped it, never worked out here: a
        screen deciding for itself whether the wait is over could disagree
        with the Bot that is enforcing it.
        """
        if basket is None or basket.entry_gate is not BasketEntryGate.COOLDOWN:
            self.say("")
            return
        reason = blocked_entry_reason(
            basket.entry_gate, cooldown_ends_at=basket.cooldown_ends_at_utc
        )
        self.say(f"{reason} — {_HELD_BACK}")
