"""The questions a Position tab puts, and nothing about what is done with them.

Only the wording lives here — the shape they all share, down to which answer a
stray Enter finds, is `destructive_question.ask_before_acting`.
"""

from PySide6.QtWidgets import QWidget

from tiewtrade.ui.destructive_question import ask_before_acting


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
            f"Market sells Entry {entry_number} at the price now, not at the Take "
            "Profit it was given. The rest of the Position is left as it is, and "
            "the Bot goes on running.\n\n"
            "Cancel leaves the Entry open."
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
            "Market sells every remaining Entry at the price now, not at the "
            "Take Profit each was given, and closes the Basket.\n\n"
            "Cancel leaves the Position open."
        ),
        confirm_label="Close All",
    )


def ask_about_cancelling_cooldown(parent: QWidget | None) -> bool:
    """Ask before a Cooldown the strategy itself set is lifted.

    A Cooldown does not lapse, so lifting it is the trader stepping around a
    risk policy rather than hurrying one along — the question says so in as
    many words.
    """
    return ask_before_acting(
        parent,
        title="Cancel Cooldown",
        question="Lift this Cooldown and open a new Entry now?",
        detail=(
            "This Basket completed an Entry Pair, so the strategy is holding it "
            "back from taking another Entry. Lifting the Cooldown lets one open "
            "immediately. The next Pair it completes is held back again.\n\n"
            "Cancel leaves the Cooldown as it is."
        ),
        confirm_label="Cancel Cooldown",
    )
