"""What the whole installation is up or down, read as one figure.

Two halves make it. What has been realised is in the database and is true of
every Session the installation has ever run — money that was made or lost and
is not coming back. What is unrealised belongs to the one Basket still open
and only exists against a price, so it moves with every candle and is not
written anywhere.

A Basket that is still open contributes to both halves without either
counting the other twice: its stored result covers the Entries already sold
out of it, and its unrealised figure covers the Entries it is still holding.

One case this cannot see: a Basket left open by a Session that has since been
retired — only reachable by killing the process mid-Basket — keeps its
realised half in the sum while nothing marks the rest, so the figure reads
low until that Basket is closed.
"""

from decimal import Decimal

from tiewtrade.application.trading_workspace import BasketSnapshot
from tiewtrade.trading.position import PositionSide, unrealized_pnl


def total_pnl(
    *,
    realized: Decimal | None,
    open_basket: BasketSnapshot | None,
    current_price: Decimal | None = None,
) -> Decimal | None:
    """Realised across the database plus what the open Basket is worth now.

    No answer at all rather than half of one: a realised total with nothing to
    read it from is not zero, and a realised total standing alone while a
    Basket is open and unpriced is a figure that is simply wrong — it would
    settle to the right one on the first candle, which is exactly long enough
    for a trader to have read it.

    A `current_price` is the live price the card is already showing, and it
    takes precedence over the mark the Basket arrived with: the Basket is
    marked once a candle, while the price moves all the way through it.
    """
    if realized is None:
        return None
    if open_basket is None:
        return realized
    if current_price is not None:
        return realized + marked_on(open_basket, current_price)
    if open_basket.unrealized_pnl is None:
        return None
    return realized + open_basket.unrealized_pnl


def marked_on(basket: BasketSnapshot, current_price: Decimal) -> Decimal:
    """What the open Basket is worth against a price newer than its own.

    A Basket arrives marked on the candle that produced it, and the price goes
    on moving inside that candle. Marking it again here is the same sum the
    projection did — the Basket's own average against a later price — and it
    answers for a Basket the database handed back before any candle marked it.
    """
    return unrealized_pnl(
        side=PositionSide(basket.position_side),
        average_entry_price=basket.average_entry_price,
        quantity=basket.total_quantity,
        current_price=current_price,
    )
