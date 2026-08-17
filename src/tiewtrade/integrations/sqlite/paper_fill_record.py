"""A Fill an executor produced, written as the record Trade History keeps.

Both markets record Fills, and every one of them is the same translation: the
executor says what was bought or sold and at what price, and the record adds
what the ledger needs — which Basket it belongs to, which Session, and the
money the two of them come to.

Written once because the money is derived here. `notional` is price times
quantity in every record there has ever been, and a second place computing it
is a second place it could stop being true; a Basket's invested total is the
sum of these, so a Fill that disagrees is a balance that cannot be traced
back to the trades under it.
"""

from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from tiewtrade.trading.trade_history import FillSide, FillSource, TradeFill


class PaperFill(Protocol):
    """What every Paper Fill says about itself, whichever market made it.

    Spot and Futures Fills carry more than this — a side, a close reason, the
    Entry they sold — but those are what the caller decides the record from.
    These five are the ones read straight off the Fill.
    """

    @property
    def fill_id(self) -> str: ...

    @property
    def order_id(self) -> str: ...

    @property
    def price(self) -> Decimal: ...

    @property
    def quantity(self) -> Decimal: ...

    @property
    def filled_at(self) -> datetime: ...


def paper_fill_record(
    fill: PaperFill,
    *,
    basket_id: UUID,
    session_id: UUID,
    side: FillSide,
    entry_number: int | None = None,
    realized_pnl: Decimal = Decimal("0"),
    entry_atr: Decimal | None = None,
    closes_entry_number: int | None = None,
) -> TradeFill:
    """The Trade History record for one Paper Fill.

    `exchange_trade_id` is None and the source is the Paper executor, because
    nothing here came back from an Exchange. An Entry realises nothing yet, so
    a caller that names no result records zero rather than leaving it open.
    """
    return TradeFill(
        fill_id=fill.fill_id,
        basket_id=basket_id,
        session_id=session_id,
        order_id=fill.order_id,
        exchange_trade_id=None,
        side=side,
        entry_number=entry_number,
        filled_at_utc=fill.filled_at,
        price=fill.price,
        quantity=fill.quantity,
        notional=fill.price * fill.quantity,
        realized_pnl=realized_pnl,
        source=FillSource.PAPER_EXECUTOR,
        entry_atr=entry_atr,
        closes_entry_number=closes_entry_number,
    )
