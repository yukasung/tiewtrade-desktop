"""How one Entry sold out of a Basket reaches the database, in either market.

A Basket is one record in history however many times it was sold out of, so
the two markets have to agree about what a single sale does to it: the Fill
row keeps only what that Entry realised, the Basket's running total grows by
the same figure, and the Basket closes only when the sale was of its last
Entry. Read one way in Spot and another in Futures, the same money would show
up twice in one market's ledger and not at all in the other's.

The pairing rule lives here for the same reason: when a candle sells several
Entries at once, exactly one of those sales — the last — is the one that
emptied the Basket, and it is the only one that may carry the closed Basket.
"""

from collections.abc import Iterator, Sequence
from dataclasses import replace
from typing import Protocol
from uuid import UUID

from tiewtrade.integrations.sqlite.paper_fill_record import PaperFill, paper_fill_record
from tiewtrade.integrations.sqlite.trade_history import SQLiteTradeHistory
from tiewtrade.trading.basket import ClosedBasket, ClosedEntry
from tiewtrade.trading.trade_history import BasketStatus, FillSide


class PaperEntryExitFill(PaperFill, Protocol):
    """A Paper Fill that sold one Entry rather than a whole Basket.

    `entry_number` is optional because a Spot Fill carries None when it took
    everything — a stop loss or a manual close — which is exactly the Fill
    that must never reach this path.
    """

    @property
    def entry_number(self) -> int | None: ...


def record_paper_entry_exit(
    store: SQLiteTradeHistory,
    *,
    basket_id: UUID,
    session_id: UUID,
    fill: PaperEntryExitFill,
    side: FillSide,
    leverage: int | None,
    closed: ClosedEntry,
    closed_basket: ClosedBasket | None,
) -> bool:
    """Record one Entry sold at a target, whichever target took it.

    The Basket row keeps the running total rather than the Basket's own: a
    Basket taken back from the database counts only what it has sold since it
    was taken back, so closing the last Entry after a restart would otherwise
    write a total that left out every Entry sold before it.
    """
    if closed.entry_number != fill.entry_number:
        raise ValueError("closed Entry and exit Fill name different Entries")

    existing = store.get_basket(basket_id)
    if existing is None:
        raise ValueError("Basket does not exist")

    normalized_fill = paper_fill_record(
        fill,
        basket_id=basket_id,
        session_id=session_id,
        side=side,
        # What this sale sold, which is one Entry. An Entry sold earlier
        # already has a Fill of its own carrying its result, so the Basket row
        # keeps the round while each Fill keeps only its own sale.
        realized_pnl=closed.realized_pnl,
        closes_entry_number=closed.entry_number,
    )
    realized_pnl = existing.realized_pnl + closed.realized_pnl
    if closed_basket is not None:
        basket = replace(
            existing,
            leverage=leverage,
            closed_at_utc=closed_basket.closed_at,
            realized_pnl=realized_pnl,
            status=BasketStatus.CLOSED,
        )
        return store.record_closed_basket(basket, normalized_fill)

    basket = replace(existing, leverage=leverage, realized_pnl=realized_pnl)
    return store.record_entry_exit_fill(basket, normalized_fill)


def entry_exits_with_closing_basket[Exit](
    exits: Sequence[Exit],
    *,
    basket_id: UUID | None,
    closed_basket: ClosedBasket | None,
) -> Iterator[tuple[Exit, UUID, ClosedBasket | None]]:
    """Each sale in the order the Basket made them, and what it closed.

    Only the last sale can be the one that emptied the Basket, so the closed
    Basket travels with that one and with no other — writing it beside an
    earlier sale would close a Basket that was still holding Entries.
    """
    if basket_id is None:
        raise ValueError("Entry Exit requires a Basket ID")
    if closed_basket is not None and closed_basket.basket_id != basket_id:
        raise ValueError("closed Basket requires a matching Basket ID")
    last = len(exits) - 1
    for index, exit in enumerate(exits):
        yield exit, basket_id, closed_basket if index == last else None
