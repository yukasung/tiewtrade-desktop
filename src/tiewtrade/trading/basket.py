from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_DOWN, Decimal
from enum import StrEnum
from uuid import UUID

from tiewtrade.trading.entry_policy import EntryPolicy
from tiewtrade.trading.position import PositionSide


class BasketCloseReason(StrEnum):
    TAKE_PROFIT = "take_profit"
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
    """One Entry of a Basket, in the Entry Group it was bought into.

    An Entry does not carry a target of its own: the Group it belongs to
    does, measured from what that Group paid on average. Which Group it joined
    was settled by the price it paid at the moment it filled, and never moves
    afterwards, so it is a fact of the Entry rather than something read off
    the Basket again later. The ATR the Fill was read with is kept here too —
    a Group restates its target whenever an Entry joins or leaves, and the
    measurement it restates it with is the latest one its Entries hold.
    """

    entry_number: int
    price: Decimal
    quantity: Decimal
    filled_at: datetime
    atr: Decimal
    group_id: int


@dataclass(frozen=True, slots=True)
class TakeProfitExit:
    """One Entry a candle carried to a target, and the target it leaves at.

    The target is its Group's, and a Basket holds more than one Group, so a
    single candle can sell two Entries at two different prices. The Entry
    carries the answer rather than leaving the executor to work out which
    Group it was in and what that Group was waiting for.
    """

    entry: "BasketEntry"
    price: Decimal


@dataclass(frozen=True, slots=True)
class BasketEntryTarget:
    """What one Entry paid, which Group it is in, and what that Group leaves at.

    Stated by the Basket rather than worked out again by whatever shows it,
    so a screen and the Session that sells cannot disagree about the price an
    Entry is waiting for. The Group is carried beside the target because two
    rows reading the same price is only legible once it says which Entries
    are leaving together.
    """

    entry_number: int
    group_id: int
    entry_price: Decimal
    take_profit_price: Decimal


@dataclass(frozen=True, slots=True)
class ClosedEntry:
    """What one Entry leaves behind when it alone exits.

    The Group it left is carried with it, together with where that Group's
    target stood once it had gone — None where it was the last Entry and the
    Group went with it. Both are facts only the Basket holds at that instant,
    and what a Basket taken back from the database is rebuilt from.
    """

    entry_number: int
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    realized_pnl: Decimal
    closed_at: datetime
    group_id: int
    group_take_profit_price: Decimal | None = None


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
        settled: SettledEntries | None = None,
    ) -> None:
        _require_positive(
            take_profit_atr_multiplier,
            "take_profit_atr_multiplier",
        )
        self._basket_id = basket_id
        self._policy = policy
        self._take_profit_atr_multiplier = take_profit_atr_multiplier
        self._position_side = position_side
        self._entries: list[BasketEntry] = []
        self._is_closed = False
        self._entry_sequence = 0
        # One target per Entry Group, keyed by the Group's number. A Group
        # exists for exactly as long as it holds an Entry, so this is also the
        # list of Groups the Basket has open.
        self._group_take_profit_prices: dict[int, Decimal] = {}
        self._group_sequence = 0
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
        """The nearest target any of the Basket's Groups is waiting for.

        For reading, not for selling. A Basket does not have a target of its
        own: each Group leaves at the price measured from what that Group
        paid, and a Group the market has left behind goes on holding rather
        than being carried out by a Group that did reach its price. What this
        answers is which sale is closest, which is what a notice or a summary
        line has room to say.
        """
        if not self._group_take_profit_prices:
            return None
        targets = self._group_take_profit_prices.values()
        return max(targets) if self._is_short else min(targets)

    @property
    def group_take_profit_prices(self) -> dict[int, Decimal]:
        """Every open Group and the price it leaves at, for recording it."""
        return dict(self._group_take_profit_prices)

    def reaches_take_profit(self, high: Decimal, low: Decimal) -> bool:
        """Whether a price range carried any Group to its target.

        Stated here because an executor, a Session and a confirmation dialog
        all have to answer it, and a rule read in three places is a rule free
        to disagree with itself about whether a Position has been sold.
        """
        return any(
            self._reached(target, high, low)
            for target in self._group_take_profit_prices.values()
        )

    def _entry(self, entry_number: int) -> BasketEntry | None:
        return next(
            (entry for entry in self._entries if entry.entry_number == entry_number),
            None,
        )

    def entry_take_profit_price(self, entry_number: int) -> Decimal | None:
        """The price this Entry leaves at, which is its Group's.

        An Entry buys into a Group and is sold out with it, so every Entry of
        one Group reads the same figure. What that figure is measured from is
        what the Group paid on average — an Entry that averaged the Group down
        brings the price the whole Group is waiting for down with it, and an
        Entry that did not averaged nothing because it opened a Group of its
        own instead.
        """
        entry = self._entry(entry_number)
        if entry is None:
            return None
        return self._group_take_profit_prices.get(entry.group_id)

    def entry_group_id(self, entry_number: int) -> int | None:
        """The Group this Entry was bought into, for recording it."""
        entry = self._entry(entry_number)
        return None if entry is None else entry.group_id

    def open_entry_targets(self) -> tuple[BasketEntryTarget, ...]:
        """Every Entry still open, with the figures it is judged by."""
        views: list[BasketEntryTarget] = []
        for entry in self._entries:
            target = self._group_take_profit_prices.get(entry.group_id)
            if target is None:
                continue
            views.append(
                BasketEntryTarget(
                    entry_number=entry.entry_number,
                    group_id=entry.group_id,
                    entry_price=entry.price,
                    take_profit_price=target,
                )
            )
        return tuple(views)

    def take_profit_exits(
        self, high: Decimal, low: Decimal
    ) -> tuple[TakeProfitExit, ...]:
        """Every Entry this candle sells, and the price each one sells at.

        A Group is the unit that leaves: reaching its target sells every Entry
        in it, at that one price, and leaves every other Group where it is. A
        candle wide enough to reach two Groups sells the nearer one first,
        because a limit order at the nearer price would have filled before the
        market ever came as far as the other — reading it the other way round
        would book the better of two prices in an order the market did not
        offer them in.
        """
        exits: list[TakeProfitExit] = []
        reached = [
            (group_id, target)
            for group_id, target in self._group_take_profit_prices.items()
            if self._reached(target, high, low)
        ]
        # Nearest target first, which is the order the market reached them in.
        reached.sort(key=lambda item: item[1], reverse=self._is_short)
        for group_id, target in reached:
            for entry in self._group_entries(group_id):
                exits.append(TakeProfitExit(entry=entry, price=target))
        return tuple(exits)

    def _reached(self, target: Decimal, high: Decimal, low: Decimal) -> bool:
        if self._is_short:
            return low <= target
        return high >= target

    @property
    def _is_short(self) -> bool:
        return self.position_side is PositionSide.SHORT

    def _group_entries(self, group_id: int) -> list[BasketEntry]:
        return [entry for entry in self._entries if entry.group_id == group_id]

    def _group_average_entry_price(self, group_id: int) -> Decimal:
        entries = self._group_entries(group_id)
        quantity = sum((entry.quantity for entry in entries), Decimal("0"))
        if quantity == 0:
            raise ValueError("group holds no Entry")
        notional = sum(
            (entry.price * entry.quantity for entry in entries), Decimal("0")
        )
        return notional / quantity

    def _averages_down(self, price: Decimal, average_entry_price: Decimal) -> bool:
        """Whether paying this moves a Group's average the way profit lies.

        A Long averages down by paying less than the Group has paid so far and
        a Short by selling higher, and either way the Group's target comes
        closer to the price on screen. Anything else — the higher price, and
        the price that matches to the tick — is averaging up, which is what
        opens a Group instead of joining one.
        """
        if self._is_short:
            return price > average_entry_price
        return price < average_entry_price

    def _group_for_new_entry(self, price: Decimal) -> int:
        """The Group this price joins, or the number of the one it opens.

        Only the latest open Group is asked. An Entry that averages an older
        Group down but not the latest one is still averaging up on the price
        the Basket last paid, and the Group it would join has a target it has
        been waiting at while the market moved away — pulling it back would
        make the Basket keep waiting for a price it had already reached.
        """
        if self._group_take_profit_prices:
            latest = max(self._group_take_profit_prices)
            if self._averages_down(price, self._group_average_entry_price(latest)):
                return latest
        return self._group_sequence + 1

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
        group_id: int | None = None,
    ) -> None:
        """Add an Entry, numbering it and grouping it unless history did."""
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
        if group_id is not None and group_id <= 0:
            raise ValueError("group_id must be positive")

        if group_id is None:
            group_id = self._group_for_new_entry(price)
        group_entries = self._group_entries(group_id)
        group_quantity = sum((entry.quantity for entry in group_entries), Decimal("0"))
        average_entry_price = average_entry_price_after_fill(
            current_average_entry_price=(
                None if not group_entries else self._group_average_entry_price(group_id)
            ),
            current_quantity=group_quantity,
            fill_price=price,
            fill_quantity=quantity,
        )
        # Measured before anything is added, so a target that cannot be stated
        # leaves the Basket exactly as it found it. This Fill is the latest one
        # by definition, so its ATR is the ATR the Group now measures with.
        take_profit_price = self._held_or_closer(
            group_id,
            self._measure_take_profit(
                measured_from=average_entry_price,
                atr=atr,
                tick_size=tick_size,
            ),
        )

        if entry_number is None:
            entry_number = self._entry_sequence + 1
        elif entry_number <= 0:
            raise ValueError("entry_number must be positive")
        self._entry_sequence = max(self._entry_sequence, entry_number)
        # A Group number is never handed back either: a Group that was sold
        # out of is gone, and a later Entry taking its number would read as
        # the Entries having left together.
        self._group_sequence = max(self._group_sequence, group_id)
        self._entries.append(
            BasketEntry(
                entry_number=entry_number,
                price=price,
                quantity=quantity,
                filled_at=filled_at,
                atr=atr,
                group_id=group_id,
            )
        )
        self._tick_size = tick_size
        self._group_take_profit_prices[group_id] = take_profit_price

    def restore_group_take_profit_prices(
        self, take_profit_prices: dict[int, Decimal]
    ) -> None:
        """Put back the targets a previous Runtime's Groups were waiting at.

        A Group's target is history, not a reading: it only ever moved closer,
        and where it stopped depends on Entries that have since been sold out
        of it. Replaying the Entries still open cannot arrive back at it, so a
        Basket taken out of the database is told instead.
        """
        open_groups = {entry.group_id for entry in self._entries}
        if set(take_profit_prices) != open_groups:
            raise ValueError("every open Group has exactly one target")
        for target in take_profit_prices.values():
            _require_positive(target, "take_profit_price")
        self._group_take_profit_prices = dict(take_profit_prices)

    def _measure_take_profit(
        self,
        *,
        measured_from: Decimal,
        atr: Decimal,
        tick_size: Decimal,
    ) -> Decimal:
        """An exit level for a price paid and the ATR it is measured against.

        One formula for both levels: an Entry measures from the price it paid
        and the Basket from what it paid on average. Long rounds its target
        down and Short rounds it up — the rounding is against the Basket
        either way, so a Paper Fill can never flatter itself.
        """
        if self.position_side is PositionSide.LONG:
            raw_target = measured_from + (atr * self._take_profit_atr_multiplier)
            target_rounding = ROUND_DOWN
        else:
            raw_target = measured_from - (atr * self._take_profit_atr_multiplier)
            target_rounding = ROUND_CEILING
        if raw_target <= 0:
            raise ValueError("take profit price must be positive")
        take_profit_price = (raw_target / tick_size).to_integral_value(
            rounding=target_rounding
        ) * tick_size
        return take_profit_price

    def _held_or_closer(self, group_id: int, take_profit_price: Decimal) -> Decimal:
        """A Group's target, allowed to move closer and never further away.

        A rising ATR would otherwise push a target the market had all but
        reached back out of reach, so the Group would go on holding a Position
        that had already made what it was waiting for. A Group being opened
        has nothing to hold against, which is why averaging up is untouched by
        this: the new Group states its target outright.
        """
        held = self._group_take_profit_prices.get(group_id)
        if held is None:
            return take_profit_price
        if self._is_short:
            return max(take_profit_price, held)
        return min(take_profit_price, held)

    def _refresh_group_take_profit(self, group_id: int) -> None:
        """State a Group's target again, for the Entries it still holds.

        An Entry leaving moves its Group's average as surely as one arriving
        does, so the target has to be restated then too — subject to the same
        rule, so a Group that has lost the Entry which brought its target down
        does not have it pushed back out again. A Group with nothing left in
        it is not a Group any more.
        """
        if not self._group_entries(group_id):
            self._group_take_profit_prices.pop(group_id, None)
            return
        assert self._tick_size is not None
        latest = max(
            self._group_entries(group_id),
            key=lambda entry: (entry.filled_at, entry.entry_number),
        )
        self._group_take_profit_prices[group_id] = self._held_or_closer(
            group_id,
            self._measure_take_profit(
                measured_from=self._group_average_entry_price(group_id),
                atr=latest.atr,
                tick_size=self._tick_size,
            ),
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
        self._entries.remove(entry)
        self._refresh_group_take_profit(entry.group_id)
        closed = ClosedEntry(
            entry_number=entry.entry_number,
            quantity=entry.quantity,
            entry_price=entry.price,
            exit_price=exit_price,
            realized_pnl=realized_pnl,
            closed_at=closed_at,
            group_id=entry.group_id,
            group_take_profit_price=self._group_take_profit_prices.get(entry.group_id),
        )
        self._settled.record(entry, closed)
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
