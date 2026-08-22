"""What a Start would sell the moment it happened, said in one place.

A Basket left open goes on being marked while the Bot is stopped, so the
price can run past an Entry's target with nothing running to act on it. The
first live tick after a Start sells every Entry the price has reached, each
at its own target rather than at the price now — instantly, and with no way
back. The trader is asked first, and asked in these words wherever the
question is put.
"""

from decimal import Decimal

from tiewtrade.application.open_basket import ReopenedBasket
from tiewtrade.trading.basket import BasketEntry

UNREADABLE_POSITION_MESSAGE = (
    "What the open Position holds cannot be read yet, or no price has "
    "arrived to judge it by. Starting the Bot sells every Entry the price "
    "has already reached, at the target it was given rather than at the "
    "price now."
)
"""Why a Start is worth confirming when nothing can be said about the sale.

The question this feeds exists because a Start can sell at a target the price
has left. Not knowing whether it would is not the same as knowing it would
not — read as "nothing to ask about", it once let a Position go at its target
with no question put at all.
"""


def entries_sold_on_start(
    reopened: ReopenedBasket, current_price: Decimal
) -> tuple[BasketEntry, ...]:
    """The Entries a Start would sell at once, read by the Basket's own rule.

    The price is asked as both the high and the low of the moment, which is
    the question a forming candle puts to the same rule: the price is at this
    level now. Deciding it here instead would be a second reading of the rule
    that sells, free to disagree with it.
    """
    return reopened.basket.entries_reaching_take_profit(current_price, current_price)


def leaves_entries_holding(reopened: ReopenedBasket, current_price: Decimal) -> bool:
    """Whether a Start would leave something in the Basket it did not sell.

    Read from the same rule as the sale itself, so the two cannot disagree
    about which Entries a Start would take.
    """
    return len(entries_sold_on_start(reopened, current_price)) < len(
        reopened.basket.entries
    )


def _reached_their_target(entries: tuple[BasketEntry, ...]) -> str:
    """How many Entries the price has carried to their target, said once."""
    if len(entries) == 1:
        return "1 Entry is already at its Take Profit"
    return f"{len(entries)} Entries are already at their Take Profit"


def sells_on_start_message(
    reopened: ReopenedBasket, current_price: Decimal
) -> str | None:
    """Why a Start is worth confirming, or None when it sells nothing.

    The targets are counted rather than named. What makes this worth asking is
    that the sale books at them and not at the price on screen — one sentence,
    however many Entries are open — and every figure behind it is in the
    Position tab, in columns, where a box of prose cannot beat it.
    """
    entries = entries_sold_on_start(reopened, current_price)
    if not entries:
        return None
    if len(entries) == 1:
        sale = "sells it at once at the target it was given"
    else:
        sale = "sells them at once at the targets they were given"
    return (
        f"{_reached_their_target(entries)}. "
        f"Starting the Bot {sale}, not at the price now."
    )


def position_at_target_message(
    reopened: ReopenedBasket, current_price: Decimal
) -> str | None:
    """What is true of the Position, for a question that offers no Start.

    Where the only way forward on offer is the market price, saying what a
    Start would have booked describes a button that is not there. What is left
    is the state, which is a count.
    """
    entries = entries_sold_on_start(reopened, current_price)
    if not entries:
        return None
    return f"{_reached_their_target(entries)}."


def position_held_message(reopened: ReopenedBasket) -> str:
    """How much is open, for a Start that would run on with it.

    Nothing is bought or sold by resuming, so there is nothing to warn about —
    only the fact that a Position is there, which is a count. An open Basket
    holds at least one Entry, so there is always that much to say. What it is
    worth is read off the Position tab behind the dialog, where it is a row
    rather than a clause that grows one longer with every Entry.
    """
    entries = reopened.basket.entries
    if len(entries) == 1:
        return "1 Entry is open."
    return f"{len(entries)} Entries are open."
