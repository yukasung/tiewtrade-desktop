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
from tiewtrade.ui.money_text import price_text


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


def sells_on_start_message(
    reopened: ReopenedBasket, current_price: Decimal
) -> str | None:
    """Why a Start is worth confirming, or None when it sells nothing.

    The targets are named rather than summed: they are the prices the sale
    books at, and the whole point of asking is that they are not the price on
    screen.
    """
    entries = entries_sold_on_start(reopened, current_price)
    if not entries:
        return None
    targets = " and ".join(price_text(entry.take_profit_price) for entry in entries)
    if len(entries) == 1:
        opening = "1 Entry is already at its Take Profit"
        sale = f"sells it at once at {targets}, the target it was given"
    else:
        opening = f"{len(entries)} Entries are already at their Take Profit"
        sale = f"sells them at once at {targets}, the targets they were given"
    return (
        f"{opening}. Starting the Bot {sale}, "
        f"not at the current {price_text(current_price)}."
    )
