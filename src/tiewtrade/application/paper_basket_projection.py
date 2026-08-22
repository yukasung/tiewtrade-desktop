"""How an open Basket is read out of a Paper Session, candle by candle.

The runtime owns threads, market data and publishing; none of that is needed
to answer what a Basket looks like right now. These are the plain readings in
between: what a Session's snapshot says about the position, and what the
workspace shows for it.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from tiewtrade.application.open_basket import ReopenedBasket
from tiewtrade.application.paper_futures_session import PaperFuturesSessionSnapshot
from tiewtrade.application.paper_spot_session import PaperSpotSessionSnapshot
from tiewtrade.application.trading_workspace import (
    BasketEntryGate,
    BasketEntrySnapshot,
    BasketSnapshot,
)
from tiewtrade.execution.paper_futures import PaperFuturesEntryFill
from tiewtrade.execution.paper_spot import PaperSpotEntryFill
from tiewtrade.market_data.candle import Candle
from tiewtrade.trading.basket import BasketEntry, average_entry_price_after_fill
from tiewtrade.trading.entry_pair import EntryGate
from tiewtrade.trading.position import PositionSide, unrealized_pnl
from tiewtrade.trading.session_config import MarketType


@dataclass(frozen=True, slots=True)
class FilledEntry:
    """An Entry as it filled, kept unaveraged so the tab can show it."""

    entry_number: int
    filled_at_utc: datetime
    price: Decimal
    quantity: Decimal
    take_profit_price: Decimal


@dataclass(frozen=True, slots=True)
class OpenBasketPosition:
    total_quantity: Decimal
    average_entry_price: Decimal
    entries: tuple[FilledEntry, ...]


def position_without_entries(
    current: OpenBasketPosition | None,
    entry_numbers: Iterable[int],
) -> OpenBasketPosition | None:
    """Take the Entries that sold out of the position they were part of."""
    sold = set(entry_numbers)
    if current is None or not sold:
        return current
    remaining = tuple(
        entry for entry in current.entries if entry.entry_number not in sold
    )
    return reopened_position(remaining)


def position_after_snapshot(
    current: OpenBasketPosition | None,
    *,
    entry_fill: PaperSpotEntryFill | PaperFuturesEntryFill | None,
    basket_entry_count: int,
    entry_number: int | None = None,
    take_profit_price: Decimal | None = None,
) -> OpenBasketPosition | None:
    if basket_entry_count == 0:
        return None
    if entry_fill is None:
        if current is None:
            raise ValueError("open Basket requires authoritative position facts")
        return current
    if entry_number is None or take_profit_price is None:
        raise ValueError("an Entry Fill carries its number and its target")
    current_entries = () if current is None else current.entries
    current_quantity = Decimal("0") if current is None else current.total_quantity
    return OpenBasketPosition(
        total_quantity=current_quantity + entry_fill.quantity,
        average_entry_price=average_entry_price_after_fill(
            current_average_entry_price=(
                None if current is None else current.average_entry_price
            ),
            current_quantity=current_quantity,
            fill_price=entry_fill.price,
            fill_quantity=entry_fill.quantity,
        ),
        # The Basket counts this Entry, so the list has to grow with it.
        entries=(
            *current_entries,
            FilledEntry(
                entry_number=entry_number,
                filled_at_utc=entry_fill.filled_at,
                price=entry_fill.price,
                quantity=entry_fill.quantity,
                take_profit_price=take_profit_price,
            ),
        ),
    )


def reopened_position(entries: tuple[FilledEntry, ...]) -> OpenBasketPosition | None:
    """What an open Basket's Entries add up to, read in one go rather than live.

    A Runtime that takes back a Basket it did not open has every Entry at once
    instead of one per candle, and adds them up by the same rule.
    """
    position: OpenBasketPosition | None = None
    for entry in entries:
        current_quantity = Decimal("0") if position is None else position.total_quantity
        position = OpenBasketPosition(
            total_quantity=current_quantity + entry.quantity,
            average_entry_price=average_entry_price_after_fill(
                current_average_entry_price=(
                    None if position is None else position.average_entry_price
                ),
                current_quantity=current_quantity,
                fill_price=entry.price,
                fill_quantity=entry.quantity,
            ),
            entries=((*position.entries, entry) if position is not None else (entry,)),
        )
    return position


def open_basket_position(
    entries: tuple[BasketEntry, ...],
) -> OpenBasketPosition | None:
    """The position a Basket's open Entries stand at, target by target.

    The Basket is the authority on what each Entry is sold at, so the targets
    are read back off it rather than recomputed beside it.
    """
    return reopened_position(
        tuple(
            FilledEntry(
                entry_number=entry.entry_number,
                filled_at_utc=entry.filled_at,
                price=entry.price,
                quantity=entry.quantity,
                take_profit_price=entry.take_profit_price,
            )
            for entry in entries
        )
    )


def reopened_basket_snapshot(
    reopened: ReopenedBasket,
    *,
    symbol: str,
    current_price: Decimal | None,
    observed_at_utc: datetime,
) -> BasketSnapshot:
    """Show a Basket no Runtime holds yet, marked on the price of the moment.

    The Entry Pair gate is read against now rather than against a candle,
    because no candle has reached this Basket since it was left.

    A price of None is a Basket read before one arrived. Everything the
    database already answered — what was bought, when, how much, and the
    target it is sold at — is shown at once; only what it is worth waits.
    Holding the whole tab back for that one figure read as no Position at all.
    """
    return _updated_basket_snapshot(
        open_basket_position(reopened.basket.entries),
        symbol=symbol,
        market_type=MarketType.SPOT,
        entry_count=reopened.basket.entry_count,
        current_price=current_price,
        take_profit_price=reopened.basket.take_profit_price,
        position_side=reopened.basket.position_side,
        entry_gate=reopened.lifecycle.entry_gate(),
        observed_at_utc=observed_at_utc,
    )


def spot_basket_snapshot(
    snapshot: PaperSpotSessionSnapshot,
    position: OpenBasketPosition | None,
    candle: Candle,
    observed_at_utc: datetime,
) -> BasketSnapshot | None:
    if snapshot.basket_entry_count == 0:
        return None
    return _updated_basket_snapshot(
        position,
        symbol=candle.symbol,
        market_type=MarketType.SPOT,
        entry_count=snapshot.basket_entry_count,
        current_price=candle.close,
        take_profit_price=snapshot.take_profit_price,
        position_side=PositionSide.LONG,
        entry_gate=snapshot.entry_gate,
        observed_at_utc=observed_at_utc,
    )


def futures_basket_snapshot(
    snapshot: PaperFuturesSessionSnapshot,
    position: OpenBasketPosition | None,
    candle: Candle,
    observed_at_utc: datetime,
) -> BasketSnapshot | None:
    if snapshot.basket_entry_count == 0:
        return None
    if snapshot.position_side is None:
        raise ValueError("open Futures Basket requires position_side")
    return _updated_basket_snapshot(
        position,
        symbol=candle.symbol,
        market_type=MarketType.FUTURES,
        entry_count=snapshot.basket_entry_count,
        current_price=candle.close,
        take_profit_price=snapshot.take_profit_price,
        position_side=snapshot.position_side,
        entry_gate=snapshot.entry_gate,
        observed_at_utc=observed_at_utc,
    )


def _updated_basket_snapshot(
    position: OpenBasketPosition | None,
    *,
    symbol: str,
    market_type: MarketType,
    entry_count: int,
    current_price: Decimal | None,
    take_profit_price: Decimal | None,
    position_side: PositionSide,
    entry_gate: EntryGate | None,
    observed_at_utc: datetime,
) -> BasketSnapshot:
    if position is None:
        raise ValueError("open Basket requires authoritative position facts")
    if entry_gate is None:
        raise ValueError("open Basket requires an entry_gate")
    total_quantity = position.total_quantity
    average_entry_price = position.average_entry_price
    if take_profit_price is None:
        raise ValueError("open Basket requires take_profit_price")
    marked_pnl = (
        None
        if current_price is None
        else unrealized_pnl(
            side=position_side,
            average_entry_price=average_entry_price,
            quantity=total_quantity,
            current_price=current_price,
        )
    )
    return BasketSnapshot(
        symbol=symbol,
        market_type=market_type.value,
        entry_count=entry_count,
        entries=_marked_entries(
            position.entries,
            position_side=position_side,
            current_price=current_price,
        ),
        total_quantity=total_quantity,
        average_entry_price=average_entry_price,
        current_price=current_price,
        take_profit_price=take_profit_price,
        unrealized_pnl=marked_pnl,
        entry_gate=BasketEntryGate(entry_gate.state),
        updated_at_utc=observed_at_utc,
    )


def _marked_entries(
    entries: tuple[FilledEntry, ...],
    *,
    position_side: PositionSide,
    current_price: Decimal | None,
) -> tuple[BasketEntrySnapshot, ...]:
    """Each Entry marked on its own entry price, by the same rule as the Basket.

    With no price to mark on, every Entry says so the same way the Basket
    does: what it paid stands, what it is worth is not answered yet.
    """
    return tuple(
        BasketEntrySnapshot(
            entry_number=entry.entry_number,
            filled_at_utc=entry.filled_at_utc,
            price=entry.price,
            quantity=entry.quantity,
            take_profit_price=entry.take_profit_price,
            notional=entry.price * entry.quantity,
            unrealized_pnl=(
                None
                if current_price is None
                else unrealized_pnl(
                    side=position_side,
                    average_entry_price=entry.price,
                    quantity=entry.quantity,
                    current_price=current_price,
                )
            ),
        )
        for entry in entries
    )
