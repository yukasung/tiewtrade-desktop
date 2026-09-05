"""The questions a Position tab puts, and nothing about what is done with them.

Only the wording lives here — the shape they all share, down to which answer a
stray Enter finds, is `destructive_question.ask_before_acting`.
"""

from datetime import datetime

from PySide6.QtWidgets import QWidget

from tiewtrade.ui.destructive_question import ask_before_acting
from tiewtrade.ui.display_time import display_timestamp


def ask_about_selling_one_entry(parent: QWidget | None, entry_number: int) -> bool:
    """Ask before one Entry goes at the price the market is at.

    A Fill has no way back, and this button sits in a row that redraws under
    the hand on every tick, so the Entry it would sell is named in the
    question.
    """
    return ask_before_acting(
        parent,
        title="Close Position",
        question=f"Sell Entry {entry_number} at the market price?",
        detail=(
            f"Entry {entry_number} sells at the price now, not at its Take Profit. "
            "The rest of the Position and the Bot are left alone."
        ),
        confirm_label="Market",
    )


def ask_about_closing_all_at_market(parent: QWidget | None) -> bool:
    """Ask before every remaining Entry goes at the price the market is at.

    A Fill has no way back, and this closes the whole Position — not just the
    row under the hand — so the question says what it does instead of what it
    is beside.
    """
    return ask_before_acting(
        parent,
        title="Close All at Market",
        question="Sell the whole Position at the market price now?",
        detail=(
            "Every remaining Entry sells at the price now, not at its Take "
            "Profit, and the Basket closes."
        ),
        confirm_label="Close All",
    )


def ask_about_cancelling_cooldown(
    parent: QWidget | None, *, cooldown_ends_at: datetime | None = None
) -> bool:
    """Ask before a Cooldown the strategy itself set is lifted.

    A Cooldown that waits on the trader does not lapse at all, so lifting it
    is stepping around a risk policy rather than hurrying one along. One that
    waits out days is the opposite: the question names the day it would lift
    on by itself, because that is what the trader is choosing not to wait for.
    """
    detail = (
        "The strategy is holding this Basket back after an Entry Pair. "
        "Lifting the Cooldown lets one Entry open now; the next Pair is "
        "held back again."
    )
    if cooldown_ends_at is not None:
        detail = (
            "This Cooldown lifts on its own at "
            f"{display_timestamp(cooldown_ends_at)}. Lifting it now opens one "
            "Entry immediately; the next Pair is held back again."
        )
    return ask_before_acting(
        parent,
        title="Cancel Cooldown",
        question="Lift this Cooldown and open a new Entry now?",
        detail=detail,
        confirm_label="Cancel Cooldown",
    )
