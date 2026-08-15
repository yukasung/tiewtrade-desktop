from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_DOWN, Decimal
from enum import StrEnum
from uuid import UUID

from tiewtrade.trading.entry_policy import EntryPolicy
from tiewtrade.trading.position import PositionSide


class BasketCloseReason(StrEnum):
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    LIQUIDATION = "liquidation"


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must use UTC")


def _require_positive(value: Decimal, field: str) -> None:
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{field} must be positive")


def _require_non_negative(value: Decimal, field: str) -> None:
    if not value.is_finite() or value < 0:
        raise ValueError(f"{field} must not be negative")


def average_entry_price_after_fill(
    *,
    current_average_entry_price: Decimal | None,
    current_quantity: Decimal,
    fill_price: Decimal,
    fill_quantity: Decimal,
) -> Decimal:
    _require_non_negative(current_quantity, "current_quantity")
    _require_positive(fill_price, "fill_price")
    _require_positive(fill_quantity, "fill_quantity")
    if current_quantity == 0:
        if current_average_entry_price is not None:
            raise ValueError("current_average_entry_price requires a current position")
        return fill_price
    if current_average_entry_price is None:
        raise ValueError("current position requires current_average_entry_price")
    _require_positive(
        current_average_entry_price,
        "current_average_entry_price",
    )
    total_quantity = current_quantity + fill_quantity
    return (
        current_average_entry_price * current_quantity + fill_price * fill_quantity
    ) / total_quantity


@dataclass(frozen=True, slots=True)
class BasketEntry:
    """One Entry of a Basket, carrying the target it was measured for.

    An Entry pays its own price, so it takes profit at its own target rather
    than at one averaged across Entries that were paid for at other prices.
    """

    entry_number: int
    price: Decimal
    quantity: Decimal
    fee: Decimal
    filled_at: datetime
    take_profit_price: Decimal


@dataclass(frozen=True, slots=True)
class ClosedEntry:
    """What one Entry leaves behind when it alone exits."""

    entry_number: int
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    gross_realized_pnl: Decimal
    trading_fees: Decimal
    net_realized_pnl: Decimal
    closed_at: datetime


@dataclass(slots=True)
class _SettledEntries:
    """Running totals of the Entries a Basket has already sold out of."""

    count: int = 0
    quantity: Decimal = Decimal("0")
    notional: Decimal = Decimal("0")
    gross_realized_pnl: Decimal = Decimal("0")
    trading_fees: Decimal = Decimal("0")

    def record(self, entry: "BasketEntry", closed: "ClosedEntry") -> None:
        self.count += 1
        self.quantity += entry.quantity
        self.notional += entry.price * entry.quantity
        self.gross_realized_pnl += closed.gross_realized_pnl
        self.trading_fees += closed.trading_fees


@dataclass(frozen=True, slots=True)
class ClosedBasket:
    basket_id: UUID
    entry_count: int
    average_entry_price: Decimal
    exit_price: Decimal
    gross_realized_pnl: Decimal
    trading_fees: Decimal
    funding_fee: Decimal
    net_realized_pnl: Decimal
    closed_at: datetime
    position_side: PositionSide = PositionSide.LONG
    close_reason: BasketCloseReason = BasketCloseReason.TAKE_PROFIT

    @property
    def realized_pnl(self) -> Decimal:
        return self.net_realized_pnl


class Basket:
    def __init__(
        self,
        basket_id: UUID,
        policy: EntryPolicy,
        take_profit_atr_multiplier: Decimal,
        position_side: PositionSide = PositionSide.LONG,
        stop_loss_atr_multiplier: Decimal | None = None,
    ) -> None:
        _require_positive(
            take_profit_atr_multiplier,
            "take_profit_atr_multiplier",
        )
        if stop_loss_atr_multiplier is not None:
            _require_positive(stop_loss_atr_multiplier, "stop_loss_atr_multiplier")
        self._basket_id = basket_id
        self._policy = policy
        self._take_profit_atr_multiplier = take_profit_atr_multiplier
        self._stop_loss_atr_multiplier = stop_loss_atr_multiplier
        self._position_side = position_side
        self._entries: list[BasketEntry] = []
        self._is_closed = False
        self._entry_sequence = 0
        self._stop_loss_price: Decimal | None = None
        # What Entries that already exited leave behind. A Basket is one
        # record in history however many times it was sold out of, so its
        # totals have to outlive the Entries they came from.
        self._settled = _SettledEntries()

    @property
    def basket_id(self) -> UUID:
        return self._basket_id

    @property
    def position_side(self) -> PositionSide:
        return self._position_side

    @property
    def entry_count(self) -> int:
        """How many Entries are open now, which is what a quota counts."""
        return len(self._entries)

    @property
    def entry_sequence(self) -> int:
        """How many Entries this Basket has ever taken, closed ones included.

        Entry numbers are an audit trail, so an Entry that exits does not hand
        its number back even though it hands its place in the Basket back.
        """
        return self._entry_sequence

    @property
    def entries(self) -> tuple[BasketEntry, ...]:
        return tuple(self._entries)

    @property
    def is_empty(self) -> bool:
        return not self._entries

    @property
    def is_closed(self) -> bool:
        return self._is_closed

    @property
    def take_profit_price(self) -> Decimal | None:
        """The nearest target among the open Entries.

        Every Entry carries its own, so the one that answers "what does the
        market have to reach next" is the closest of them.
        """
        if self.is_empty:
            return None
        targets = [entry.take_profit_price for entry in self._entries]
        if self.position_side is PositionSide.LONG:
            return min(targets)
        return max(targets)

    @property
    def stop_loss_price(self) -> Decimal | None:
        return self._stop_loss_price

    @property
    def total_quantity(self) -> Decimal:
        return sum((entry.quantity for entry in self._entries), Decimal("0"))

    @property
    def entry_fees(self) -> Decimal:
        return sum((entry.fee for entry in self._entries), Decimal("0"))

    @property
    def average_entry_price(self) -> Decimal:
        if self.is_empty:
            raise ValueError("basket is empty")
        total_notional = sum(
            (entry.price * entry.quantity for entry in self._entries),
            Decimal("0"),
        )
        return total_notional / self.total_quantity

    def add_entry(
        self,
        *,
        price: Decimal,
        quantity: Decimal,
        fee: Decimal,
        filled_at: datetime,
        atr: Decimal,
        tick_size: Decimal,
        position_side: PositionSide = PositionSide.LONG,
        entry_number: int | None = None,
    ) -> None:
        """Add an Entry, numbering it unless history already numbered it."""
        _require_utc(filled_at, "filled_at")
        if self.is_closed:
            raise ValueError("basket is closed")
        if self.entry_count >= self._policy.max_entries:
            raise ValueError("basket has reached maximum entries")
        if position_side is not self.position_side:
            raise ValueError("opposite-side Entry is not allowed in One-way Mode")
        _require_positive(price, "price")
        _require_positive(quantity, "quantity")
        _require_non_negative(fee, "fee")
        _require_non_negative(atr, "atr")
        _require_positive(tick_size, "tick_size")

        # The target is measured from the price this Entry paid, not from the
        # Basket's average: Entries that bought at different prices have
        # different distances to run, and one averaged target hides that.
        if self.position_side is PositionSide.LONG:
            raw_target = price + (atr * self._take_profit_atr_multiplier)
            rounding = ROUND_DOWN
        else:
            raw_target = price - (atr * self._take_profit_atr_multiplier)
            rounding = ROUND_CEILING
        if raw_target <= 0:
            raise ValueError("take profit price must be positive")
        take_profit_price = (raw_target / tick_size).to_integral_value(
            rounding=rounding
        ) * tick_size

        average_entry_price = average_entry_price_after_fill(
            current_average_entry_price=(
                None if self.is_empty else self.average_entry_price
            ),
            current_quantity=self.total_quantity,
            fill_price=price,
            fill_quantity=quantity,
        )
        stop_loss_price: Decimal | None = None
        if self._stop_loss_atr_multiplier is not None:
            if self.position_side is PositionSide.LONG:
                raw_stop = average_entry_price - (atr * self._stop_loss_atr_multiplier)
                stop_rounding = ROUND_CEILING
            else:
                raw_stop = average_entry_price + (atr * self._stop_loss_atr_multiplier)
                stop_rounding = ROUND_DOWN
            if raw_stop <= 0:
                raise ValueError("stop loss price must be positive")
            stop_loss_price = (raw_stop / tick_size).to_integral_value(
                rounding=stop_rounding
            ) * tick_size

        if entry_number is None:
            entry_number = self._entry_sequence + 1
        elif entry_number <= 0:
            raise ValueError("entry_number must be positive")
        self._entry_sequence = max(self._entry_sequence, entry_number)
        self._entries.append(
            BasketEntry(
                entry_number=entry_number,
                price=price,
                quantity=quantity,
                fee=fee,
                filled_at=filled_at,
                take_profit_price=take_profit_price,
            )
        )
        self._stop_loss_price = stop_loss_price

    def entries_reaching_take_profit(
        self, high: Decimal, low: Decimal
    ) -> tuple[BasketEntry, ...]:
        """The open Entries a candle reached the target of, nearest first."""
        if self.position_side is PositionSide.LONG:
            reached = [
                entry for entry in self._entries if high >= entry.take_profit_price
            ]
            return tuple(sorted(reached, key=lambda entry: entry.take_profit_price))
        reached = [entry for entry in self._entries if low <= entry.take_profit_price]
        return tuple(
            sorted(reached, key=lambda entry: entry.take_profit_price, reverse=True)
        )

    def close_entry(
        self,
        entry_number: int,
        *,
        exit_price: Decimal,
        exit_fee: Decimal,
        closed_at: datetime,
    ) -> ClosedEntry:
        """Take one Entry out of the Basket, leaving the others where they are."""
        _require_utc(closed_at, "closed_at")
        if self.is_closed:
            raise ValueError("basket is closed")
        _require_positive(exit_price, "exit_price")
        _require_non_negative(exit_fee, "exit_fee")
        entry = next(
            (item for item in self._entries if item.entry_number == entry_number),
            None,
        )
        if entry is None:
            raise ValueError("basket has no open Entry with that number")

        if self.position_side is PositionSide.LONG:
            gross_realized_pnl = (exit_price - entry.price) * entry.quantity
        else:
            gross_realized_pnl = (entry.price - exit_price) * entry.quantity
        trading_fees = entry.fee + exit_fee
        closed = ClosedEntry(
            entry_number=entry.entry_number,
            quantity=entry.quantity,
            entry_price=entry.price,
            exit_price=exit_price,
            gross_realized_pnl=gross_realized_pnl,
            trading_fees=trading_fees,
            net_realized_pnl=gross_realized_pnl - trading_fees,
            closed_at=closed_at,
        )
        self._entries.remove(entry)
        self._settled.record(entry, closed)
        if self.is_empty:
            self._is_closed = True
            self._stop_loss_price = None
        return closed

    def settled_result(
        self,
        *,
        exit_price: Decimal,
        closed_at: datetime,
        close_reason: BasketCloseReason = BasketCloseReason.TAKE_PROFIT,
    ) -> ClosedBasket:
        """The Basket's own record, once its last Entry has been sold.

        Every Entry left on its own terms, so the exit price is the last one
        taken while the totals are the sum of them all.
        """
        if not self.is_closed or not self.is_empty:
            raise ValueError("basket still holds an Entry")
        settled = self._settled
        net_realized_pnl = settled.gross_realized_pnl - settled.trading_fees
        return ClosedBasket(
            basket_id=self.basket_id,
            entry_count=settled.count,
            average_entry_price=settled.notional / settled.quantity,
            exit_price=exit_price,
            gross_realized_pnl=settled.gross_realized_pnl,
            trading_fees=settled.trading_fees,
            funding_fee=Decimal("0"),
            net_realized_pnl=net_realized_pnl,
            closed_at=closed_at,
            position_side=self.position_side,
            close_reason=close_reason,
        )

    def close(
        self,
        *,
        exit_price: Decimal,
        exit_fee: Decimal,
        closed_at: datetime,
        close_reason: BasketCloseReason = BasketCloseReason.TAKE_PROFIT,
    ) -> ClosedBasket:
        _require_utc(closed_at, "closed_at")
        if self.is_closed:
            raise ValueError("basket is closed")
        if self.is_empty:
            raise ValueError("basket is empty")
        _require_positive(exit_price, "exit_price")
        _require_non_negative(exit_fee, "exit_fee")

        if self.position_side is PositionSide.LONG:
            gross_realized_pnl = sum(
                (
                    (exit_price - entry.price) * entry.quantity
                    for entry in self._entries
                ),
                Decimal("0"),
            )
        else:
            gross_realized_pnl = sum(
                (
                    (entry.price - exit_price) * entry.quantity
                    for entry in self._entries
                ),
                Decimal("0"),
            )
        # Entries that already sold are part of this Basket's result too.
        settled = self._settled
        gross_realized_pnl += settled.gross_realized_pnl
        trading_fees = self.entry_fees + exit_fee + settled.trading_fees
        funding_fee = Decimal("0")
        net_realized_pnl = gross_realized_pnl - trading_fees - funding_fee
        total_quantity = self.total_quantity + settled.quantity
        total_notional = (
            sum(
                (entry.price * entry.quantity for entry in self._entries),
                Decimal("0"),
            )
            + settled.notional
        )
        closed = ClosedBasket(
            basket_id=self.basket_id,
            entry_count=self.entry_count + settled.count,
            average_entry_price=total_notional / total_quantity,
            exit_price=exit_price,
            gross_realized_pnl=gross_realized_pnl,
            trading_fees=trading_fees,
            funding_fee=funding_fee,
            net_realized_pnl=net_realized_pnl,
            closed_at=closed_at,
            position_side=self.position_side,
            close_reason=close_reason,
        )
        self._is_closed = True
        return closed
