"""What a Session's snapshot says, and what the trader is told about it.

Reading a snapshot and messaging a trader are two jobs, and only the second
needs to know a Session has an identity. Both markets fill an Entry into a
Basket at a price, and a message about it says the same thing either way — so
the readings here are market-agnostic, and what only the composition knows,
which symbol and timeframe this Session was pinned to, is joined on at the end.

Four readings rather than one, because deciding what a message is worth saying
is not the sink's job. The sink decides *when* each of them is true, which is
four different moments — and two of them are not on a candle at all.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from tiewtrade.application.paper_session_setup import ConfiguredPaperSession
from tiewtrade.application.session_notice import (
    BasketClosedNotice,
    CooldownCancelledNotice,
    CooldownEndedNotice,
    CooldownStartedNotice,
    EntryOpenedNotice,
    SessionMarket,
    SessionNotice,
    SessionNotifier,
)
from tiewtrade.execution.paper_futures import PaperFuturesEntryFill
from tiewtrade.execution.paper_spot import PaperSpotEntryFill
from tiewtrade.trading.basket import ClosedBasket
from tiewtrade.trading.entry_pair import EntryGate, EntryGateState


class CandleSnapshot(Protocol):
    """What a Session's snapshot tells the sink, whichever market wrote it."""

    @property
    def accepted(self) -> bool: ...

    @property
    def entry_fill(self) -> PaperSpotEntryFill | PaperFuturesEntryFill | None: ...

    @property
    def basket_entry_count(self) -> int: ...

    @property
    def entry_number(self) -> int | None: ...

    @property
    def basket_id(self) -> UUID | None: ...

    @property
    def take_profit_price(self) -> Decimal | None: ...

    @property
    def sold_entry_numbers(self) -> tuple[int, ...]: ...

    @property
    def closed_basket(self) -> ClosedBasket | None: ...

    @property
    def entry_gate(self) -> EntryGate | None: ...

    @property
    def entry_completed_pairs(self) -> int: ...

    @property
    def cooldown_ends_at(self) -> datetime | None: ...

    @property
    def failed_closed(self) -> bool: ...


type SnapshotReading = Callable[[CandleSnapshot], None]
"""Say one thing a snapshot turned out to hold, to whoever is listening."""


@dataclass(frozen=True, slots=True)
class SnapshotNotices:
    """The things the sink says out loud, each read off a snapshot.

    A reading each rather than one between them, because the sink does not
    decide what a message is worth saying — it decides *when* each of them is
    true, which is a different moment in a candle every time and one of them
    not on a candle at all.
    """

    entry_opened: SnapshotReading
    basket_closed: SnapshotReading
    cooldown_started: SnapshotReading
    cooldown_ended: SnapshotReading
    cooldown_cancelled: SnapshotReading


def session_market(session: ConfiguredPaperSession) -> SessionMarket:
    """Which Session a notice is about, and which market it was pinned to."""
    return SessionMarket(
        session_id=session.config.session_id,
        symbol=session.market_data.symbol,
        timeframe=session.market_data.timeframe,
    )


def entry_opened_notice(
    snapshot: CandleSnapshot,
    session: ConfiguredPaperSession,
) -> EntryOpenedNotice | None:
    """Read one filled Entry out of a snapshot, as a notice off the machine.

    The market a Session trades is not part of the reading: both markets fill
    an Entry into a Basket at a price, and a message about it says the same
    thing either way. What only the composition knows — which symbol and
    timeframe this Session was pinned to — is joined on here.
    """
    fill = snapshot.entry_fill
    if fill is None or snapshot.basket_id is None or snapshot.entry_number is None:
        return None
    return EntryOpenedNotice(
        market=session_market(session),
        basket_id=snapshot.basket_id,
        entry_number=snapshot.entry_number,
        price=fill.price,
        quantity=fill.quantity,
        take_profit_price=snapshot.take_profit_price,
        filled_at=fill.filled_at,
    )


def basket_closed_notice(
    snapshot: CandleSnapshot,
    session: ConfiguredPaperSession,
) -> BasketClosedNotice | None:
    """Read a Basket that has just been let go out of a snapshot.

    Everything the message needs is on the `ClosedBasket` itself — a Basket
    that closed knows what it was worth over its whole life, which is the one
    figure a trader who was not watching is waiting for.
    """
    closed = snapshot.closed_basket
    if closed is None:
        return None
    return BasketClosedNotice(
        market=session_market(session),
        basket_id=closed.basket_id,
        entry_count=closed.entry_count,
        average_entry_price=closed.average_entry_price,
        exit_price=closed.exit_price,
        realized_pnl=closed.realized_pnl,
        close_reason=closed.close_reason,
        closed_at=closed.closed_at,
    )


def cooldown_started_notice(
    snapshot: CandleSnapshot,
    session: ConfiguredPaperSession,
    *,
    started_at: datetime,
) -> CooldownStartedNotice | None:
    """Read a gate that has just closed behind a completed Entry Pair.

    Whether it *just* closed is the sink's reading, not this one's: a snapshot
    says what the gate is and never that it changed.

    A Cooldown that has closed the gate always names the day it lifts on, so
    a snapshot carrying no day is not one a Cooldown just started on.
    """
    if snapshot.basket_id is None or snapshot.cooldown_ends_at is None:
        return None
    return CooldownStartedNotice(
        market=session_market(session),
        basket_id=snapshot.basket_id,
        completed_pairs=snapshot.entry_completed_pairs,
        open_entry_count=snapshot.basket_entry_count,
        started_at=started_at,
        ends_at=snapshot.cooldown_ends_at,
    )


def cooldown_ended_notice(
    snapshot: CandleSnapshot,
    session: ConfiguredPaperSession,
    *,
    ended_at: datetime,
) -> CooldownEndedNotice | None:
    """Read a Cooldown that has just run out of the days it was given.

    Whether the gate *just* opened is the sink's reading. What is read here is
    the other half of it: a Pair that still names a day it lifts on is one the
    clock opened, and a Pair a sale broke up names none.
    """
    if snapshot.basket_id is None or snapshot.cooldown_ends_at is None:
        return None
    return CooldownEndedNotice(
        market=session_market(session),
        basket_id=snapshot.basket_id,
        completed_pairs=snapshot.entry_completed_pairs,
        ended_at=ended_at,
    )


def cooldown_cancelled_notice(
    snapshot: CandleSnapshot,
    session: ConfiguredPaperSession,
    *,
    cancelled_at: datetime,
) -> CooldownCancelledNotice | None:
    """Read a Cooldown the trader has just lifted."""
    if snapshot.basket_id is None:
        return None
    return CooldownCancelledNotice(
        market=session_market(session),
        basket_id=snapshot.basket_id,
        completed_pairs=snapshot.entry_completed_pairs,
        cancelled_at=cancelled_at,
    )


def gate_state(snapshot: CandleSnapshot) -> EntryGateState | None:
    """The gate a snapshot is standing on, or nothing where it has none yet."""
    gate = snapshot.entry_gate
    return None if gate is None else gate.state


def snapshot_notices(
    session: ConfiguredPaperSession,
    notify: SessionNotifier | None,
    *,
    clock: Callable[[], datetime],
) -> SnapshotNotices | None:
    """Join a Session's identity onto every reading the sink says out loud.

    Everything a gate does carries a moment the snapshot does not: closing,
    running out, and being lifted are true from now on rather than things that
    happened at a price, so they are stamped where they are noticed.
    """
    if notify is None:
        return None

    def say_entry_opened(snapshot: CandleSnapshot) -> None:
        _say(notify, entry_opened_notice(snapshot, session))

    def say_basket_closed(snapshot: CandleSnapshot) -> None:
        _say(notify, basket_closed_notice(snapshot, session))

    def say_cooldown_started(snapshot: CandleSnapshot) -> None:
        _say(notify, cooldown_started_notice(snapshot, session, started_at=clock()))

    def say_cooldown_ended(snapshot: CandleSnapshot) -> None:
        _say(notify, cooldown_ended_notice(snapshot, session, ended_at=clock()))

    def say_cooldown_cancelled(snapshot: CandleSnapshot) -> None:
        _say(
            notify,
            cooldown_cancelled_notice(snapshot, session, cancelled_at=clock()),
        )

    return SnapshotNotices(
        entry_opened=say_entry_opened,
        basket_closed=say_basket_closed,
        cooldown_started=say_cooldown_started,
        cooldown_ended=say_cooldown_ended,
        cooldown_cancelled=say_cooldown_cancelled,
    )


def _say(notify: SessionNotifier, notice: SessionNotice | None) -> None:
    if notice is not None:
        notify(notice)
