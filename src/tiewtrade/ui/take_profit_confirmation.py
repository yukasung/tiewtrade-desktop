"""What a Start would sell the moment it happened, said in one place.

A Basket left open goes on being marked while the Bot is stopped, so the
price can run past a target with nothing running to act on it. The first live
tick after a Start sells every Entry that is past its own target at that
target rather than at the price now — instantly, and with no way back. The
trader is asked first, and asked in these words wherever the question is put.
"""

from decimal import Decimal

from tiewtrade.application.open_basket import ReopenedBasket

UNREADABLE_POSITION_MESSAGE = (
    "The open Position cannot be read yet, or no price has arrived to judge "
    "it by. If any Entry is already at its Take Profit, starting the Bot "
    "sells it at once at that target, not at the price now."
)
"""Why a Start is worth confirming when nothing can be said about the sale.

The question this feeds exists because a Start can sell at a target the price
has left. Not knowing whether it would is not the same as knowing it would
not — read as "nothing to ask about", it once let a Position go at its target
with no question put at all.
"""


def sells_on_start(reopened: ReopenedBasket, current_price: Decimal) -> bool:
    """Whether a Start would sell anything at once, by the Basket's own rule.

    Anything, not everything: an Entry past its own target goes on the first
    tick whether or not the Basket's net was reached, so a question asked
    only about the net would leave that sale unannounced.

    The price is asked as both the high and the low of the moment, which is
    the question a forming candle puts to the same rule: the price is at this
    level now. Deciding it here instead would be a second reading of the rule
    that sells, free to disagree with it.
    """
    return bool(reopened.basket.take_profit_exits(current_price, current_price))


def sells_on_start_message(
    reopened: ReopenedBasket, current_price: Decimal
) -> str | None:
    """Why a Start is worth confirming, or None when it sells nothing.

    The target is not named. What makes this worth asking is that the sale
    books at it and not at the price on screen — and every figure behind it is
    in the Position tab, in columns, where a box of prose cannot beat it.
    """
    if not sells_on_start(reopened, current_price):
        return None
    return (
        "The Position is already at its Take Profit. Starting the Bot sells "
        "every Entry that has reached its target at once at that target, not "
        "at the price now."
    )


def position_at_target_message(
    reopened: ReopenedBasket, current_price: Decimal
) -> str | None:
    """What is true of the Position, for a question that offers no Start.

    Where the only way forward on offer is the market price, saying what a
    Start would have booked describes a button that is not there. What is left
    is the state.
    """
    if not sells_on_start(reopened, current_price):
        return None
    return "The Position is already at its Take Profit."


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
