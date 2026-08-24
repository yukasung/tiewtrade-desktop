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
    # The trader sold it, at the price the market was at when they asked.
    MANUAL = "manual"


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
    """One Entry of a Basket, carrying the ATR it was measured against.

    The target belongs to the Basket, not to the Entry — but the measurement
    it is made with arrived with a Fill, and the Basket has to be able to
    state it again after any Entry leaves. Keeping the ATR here rather than as
    one number on the Basket is what lets a Basket rebuilt from its open
    Entries alone reach the same target the Runtime had.
    """

    entry_number: int
    price: Decimal
    quantity: Decimal
    filled_at: datetime
    atr: Decimal


@dataclass(frozen=True, slots=True)
class ClosedEntry:
    """What one Entry leaves behind when it alone exits."""

    entry_number: int
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    realized_pnl: Decimal
    closed_at: datetime


@dataclass(slots=True)
class SettledEntries:
    """Running totals of the Entries a Basket has already sold out of.

    A Basket is one record in history however many times it was sold out of,
    so what its sold Entries left has to outlive them — and outlive the
    Runtime that held them, which is why a Basket taken back from the
    database is handed this rather than starting it at nothing.
    """

    count: int = 0
    quantity: Decimal = Decimal("0")
    notional: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.count < 0:
            raise ValueError("count must not be negative")
        if self.count == 0 and (self.quantity or self.notional or self.realized_pnl):
            raise ValueError("no settled Entry leaves figures behind")

    def record(self, entry: "BasketEntry", closed: "ClosedEntry") -> None:
        self.count += 1
        self.quantity += entry.quantity
        self.notional += entry.price * entry.quantity
        self.realized_pnl += closed.realized_pnl


@dataclass(frozen=True, slots=True)
class ClosedBasket:
    """What a Basket leaves behind, over its whole life and at this exit.

    `realized_pnl` is the round: every Entry the Basket ever held, including
    ones sold out of it long before. `exit_realized_pnl` is only what this
    exit itself sold, which is what the Fill it books is worth — an Entry sold
    earlier already has a Fill of its own carrying its result, and counting it
    twice makes a row that says the Basket earned it again.
    """

    basket_id: UUID
    entry_count: int
    average_entry_price: Decimal
    exit_price: Decimal
    realized_pnl: Decimal
    exit_realized_pnl: Decimal
    closed_at: datetime
    position_side: PositionSide = PositionSide.LONG
    close_reason: BasketCloseReason = BasketCloseReason.TAKE_PROFIT


class Basket:
    def __init__(
        self,
        basket_id: UUID,
        policy: EntryPolicy,
        take_profit_atr_multiplier: Decimal,
        position_side: PositionSide = PositionSide.LONG,
        stop_loss_atr_multiplier: Decimal | None = None,
        settled: SettledEntries | None = None,
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
        self._take_profit_price: Decimal | None = None
        self._stop_loss_price: Decimal | None = None
        # Taken from the first Fill and pinned there. The Basket has to round
        # its target again whenever an Entry leaves, and it may not go asking
        # an exchange rule for the tick to do it — Symbol identity is pinned
        # for the whole Session, so the first Fill's tick is the Session's.
        self._tick_size: Decimal | None = None
        # What Entries that already exited leave behind, taken as a copy so
        # what this Basket goes on to settle is its own.
        self._settled = (
            SettledEntries()
            if settled is None
            else SettledEntries(
                count=settled.count,
                quantity=settled.quantity,
                notional=settled.notional,
                realized_pnl=settled.realized_pnl,
            )
        )

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
        """The one price the whole Basket leaves at.

        Measured from what the Basket paid on average rather than from any one
        Entry's price, which is what makes an Entry bought high leave with the
        rest instead of being left behind by the Entries that were cheaper.
        """
        return self._take_profit_price

    def reaches_take_profit(self, high: Decimal, low: Decimal) -> bool:
        """Whether a price range carried the Basket to its target.

        Stated here because an executor, a Session and a confirmation dialog
        all have to answer it, and a rule read in three places is a rule free
        to disagree with itself about whether a Position has been sold.
        """
        if self._take_profit_price is None:
            return False
        if self.position_side is PositionSide.LONG:
            return high >= self._take_profit_price
        return low <= self._take_profit_price

    @property
    def stop_loss_price(self) -> Decimal | None:
        return self._stop_loss_price

    @property
    def total_quantity(self) -> Decimal:
        return sum((entry.quantity for entry in self._entries), Decimal("0"))

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
        _require_non_negative(atr, "atr")
        _require_positive(tick_size, "tick_size")
        if self._tick_size is not None and tick_size != self._tick_size:
            raise ValueError("tick_size is pinned for the Session")

        average_entry_price = average_entry_price_after_fill(
            current_average_entry_price=(
                None if self.is_empty else self.average_entry_price
            ),
            current_quantity=self.total_quantity,
            fill_price=price,
            fill_quantity=quantity,
        )
        # Measured before anything is added, so a target that cannot be stated
        # leaves the Basket exactly as it found it. This Fill is the latest one
        # by definition, so its ATR is the ATR the Basket now measures with.
        take_profit_price, stop_loss_price = self._measure_targets(
            average_entry_price=average_entry_price,
            atr=atr,
            tick_size=tick_size,
        )

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
                filled_at=filled_at,
                atr=atr,
            )
        )
        self._tick_size = tick_size
        self._take_profit_price = take_profit_price
        self._stop_loss_price = stop_loss_price

    def _measure_targets(
        self,
        *,
        average_entry_price: Decimal,
        atr: Decimal,
        tick_size: Decimal,
    ) -> tuple[Decimal, Decimal | None]:
        """The Basket's exit levels for an average and the ATR it measures with.

        Both levels come from the same average, so the Basket has one place it
        can be right or wrong about what it paid. Long rounds its target down
        and its stop up; Short does the reverse — every rounding is against the
        Basket, so a Paper Fill can never flatter itself.
        """
        if self.position_side is PositionSide.LONG:
            raw_target = average_entry_price + (atr * self._take_profit_atr_multiplier)
            target_rounding = ROUND_DOWN
        else:
            raw_target = average_entry_price - (atr * self._take_profit_atr_multiplier)
            target_rounding = ROUND_CEILING
        if raw_target <= 0:
            raise ValueError("take profit price must be positive")
        take_profit_price = (raw_target / tick_size).to_integral_value(
            rounding=target_rounding
        ) * tick_size

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
        return take_profit_price, stop_loss_price

    def _remeasure_targets(self) -> None:
        """State the exit levels again for the Entries the Basket still holds.

        An Entry leaving moves the average as surely as one arriving does, so
        the levels have to be restated then too. Left alone, they would go on
        describing a Basket that no longer exists — which is what let a stop
        outlive the Entries whose average put it there.
        """
        if self.is_empty:
            self._take_profit_price = None
            self._stop_loss_price = None
            return
        assert self._tick_size is not None
        latest = max(
            self._entries, key=lambda entry: (entry.filled_at, entry.entry_number)
        )
        self._take_profit_price, self._stop_loss_price = self._measure_targets(
            average_entry_price=self.average_entry_price,
            atr=latest.atr,
            tick_size=self._tick_size,
        )

    def close_entry(
        self,
        entry_number: int,
        *,
        exit_price: Decimal,
        closed_at: datetime,
    ) -> ClosedEntry:
        """Take one Entry out of the Basket, leaving the others where they are."""
        _require_utc(closed_at, "closed_at")
        if self.is_closed:
            raise ValueError("basket is closed")
        _require_positive(exit_price, "exit_price")
        entry = next(
            (item for item in self._entries if item.entry_number == entry_number),
            None,
        )
        if entry is None:
            raise ValueError("basket has no open Entry with that number")

        if self.position_side is PositionSide.LONG:
            realized_pnl = (exit_price - entry.price) * entry.quantity
        else:
            realized_pnl = (entry.price - exit_price) * entry.quantity
        closed = ClosedEntry(
            entry_number=entry.entry_number,
            quantity=entry.quantity,
            entry_price=entry.price,
            exit_price=exit_price,
            realized_pnl=realized_pnl,
            closed_at=closed_at,
        )
        self._entries.remove(entry)
        self._settled.record(entry, closed)
        self._remeasure_targets()
        if self.is_empty:
            self._is_closed = True
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
        taken while the totals are the sum of them all. This record sells
        nothing itself — the sale that emptied the Basket was booked by the
        `close_entry` that made it — so there is no exit result to claim here.
        """
        if not self.is_closed or not self.is_empty:
            raise ValueError("basket still holds an Entry")
        settled = self._settled
        return ClosedBasket(
            basket_id=self.basket_id,
            entry_count=settled.count,
            average_entry_price=settled.notional / settled.quantity,
            exit_price=exit_price,
            realized_pnl=settled.realized_pnl,
            exit_realized_pnl=Decimal("0"),
            closed_at=closed_at,
            position_side=self.position_side,
            close_reason=close_reason,
        )

    def close(
        self,
        *,
        exit_price: Decimal,
        closed_at: datetime,
        close_reason: BasketCloseReason = BasketCloseReason.TAKE_PROFIT,
    ) -> ClosedBasket:
        _require_utc(closed_at, "closed_at")
        if self.is_closed:
            raise ValueError("basket is closed")
        if self.is_empty:
            raise ValueError("basket is empty")
        _require_positive(exit_price, "exit_price")

        # What this exit itself sells: every Entry still open, swept out at the
        # one price together, whether it is in profit or still under water.
        if self.position_side is PositionSide.LONG:
            exit_realized_pnl = sum(
                (
                    (exit_price - entry.price) * entry.quantity
                    for entry in self._entries
                ),
                Decimal("0"),
            )
        else:
            exit_realized_pnl = sum(
                (
                    (entry.price - exit_price) * entry.quantity
                    for entry in self._entries
                ),
                Decimal("0"),
            )
        # Entries that already sold are part of this Basket's result too.
        settled = self._settled
        realized_pnl = exit_realized_pnl + settled.realized_pnl
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
            realized_pnl=realized_pnl,
            exit_realized_pnl=exit_realized_pnl,
            closed_at=closed_at,
            position_side=self.position_side,
            close_reason=close_reason,
        )
        self._is_closed = True
        return closed
