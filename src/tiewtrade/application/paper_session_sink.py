"""How a candle reaches a Paper Session, and which Session it reaches.

The Runtime drives candles; what they are driven into is built here. Spot and
Futures differ in what a Basket looks like, not in how a candle reaches one,
so the market is read once — at the moment the pieces are chosen — and never
again while candles are arriving.

Composing a Session is not the same job as running one, and keeping it apart
is what lets the rule that Paper Futures cannot take back a Basket sit beside
the composition it forbids, rather than inside a controller that also owns a
thread, a loop, and a stream of snapshots.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from threading import Lock
from typing import Protocol
from uuid import UUID

from tiewtrade.application.open_basket import OpenBasketEntry, OpenBasketState
from tiewtrade.application.paper_basket_projection import (
    OpenBasketPosition,
    futures_basket_snapshot,
    open_basket_position,
    position_after_snapshot,
    position_without_entries,
    spot_basket_snapshot,
)
from tiewtrade.application.paper_futures_session import (
    PaperFuturesSession,
    PaperFuturesSessionSnapshot,
)
from tiewtrade.application.paper_session_setup import ConfiguredPaperSession
from tiewtrade.application.paper_spot_session import (
    PaperSpotSession,
    PaperSpotSessionSnapshot,
)
from tiewtrade.application.session_notice import (
    BasketClosedNotice,
    CooldownCancelledNotice,
    CooldownStartedNotice,
    EntryOpenedNotice,
    SessionMarket,
    SessionNotice,
    SessionNotifier,
)
from tiewtrade.application.session_persistence import (
    PersistentSessionSnapshot,
    SessionPersistenceCoordinator,
)
from tiewtrade.application.trading_workspace import BasketSnapshot
from tiewtrade.execution.paper_futures import PaperFuturesEntryFill
from tiewtrade.execution.paper_spot import PaperSpotEntryFill
from tiewtrade.integrations.sqlite.paper_futures_history import (
    PaperFuturesHistoryContext,
    PaperFuturesSQLiteHistory,
)
from tiewtrade.integrations.sqlite.paper_spot_history import (
    PaperSpotHistoryContext,
    PaperSpotSQLiteHistory,
)
from tiewtrade.integrations.sqlite.persistent_paper_futures_session import (
    create_persistent_paper_futures_session,
)
from tiewtrade.integrations.sqlite.persistent_paper_spot_session import (
    create_persistent_paper_spot_session,
)
from tiewtrade.integrations.sqlite.trade_history import SQLiteTradeHistory
from tiewtrade.market_data.candle import Candle
from tiewtrade.strategies.protocols import StrategyPreset
from tiewtrade.trading.basket import ClosedBasket, SettledEntries
from tiewtrade.trading.entry_pair import EntryGate, EntryGateState
from tiewtrade.trading.session_config import MarketType
from tiewtrade.trading.symbol_rules import SymbolRules

_logger = logging.getLogger(__name__)


class PaperPositionRestoreError(RuntimeError):
    """An open Basket cannot be put back the way it was left."""


class PaperSessionFailedClosedError(RuntimeError):
    """The Session has stopped for good, so nothing more may be fed to it."""


class _CandleSnapshot(Protocol):
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
    def failed_closed(self) -> bool: ...


type CloseOneEntry[Snapshot] = Callable[
    [int, Decimal, datetime], PersistentSessionSnapshot[Snapshot]
]
"""Sell one Entry at a price, recorded before it is published."""

type WaiveCooldown[Snapshot] = Callable[[datetime], PersistentSessionSnapshot[Snapshot]]
"""Lift the Cooldown holding this Basket now, recorded before it is published."""


type SnapshotReading = Callable[[_CandleSnapshot], None]
"""Say one thing a snapshot turned out to hold, to whoever is listening."""


@dataclass(frozen=True, slots=True)
class SnapshotNotices:
    """The things the sink says out loud, each read off a snapshot.

    Four readings rather than one, because the sink does not decide what a
    message is worth saying — it decides *when* each of them is true, which is
    four different moments in a candle and two of them not on a candle at all.
    """

    entry_opened: SnapshotReading
    basket_closed: SnapshotReading
    cooldown_started: SnapshotReading
    cooldown_cancelled: SnapshotReading


class PaperRuntimeSink[Snapshot: _CandleSnapshot]:
    """Feeds candles to one Paper Session and publishes what it decided.

    Spot and Futures differ in what a Basket looks like, not in how a candle
    reaches one, so the market is a reading handed in rather than a class of
    its own: how to warm up, how to persist, and how to read a Basket out of
    the snapshot that comes back.
    """

    def __init__(
        self,
        *,
        warm_up: Callable[[tuple[Candle, ...], datetime], None],
        persistent: SessionPersistenceCoordinator[Snapshot],
        basket: Callable[
            [Snapshot, OpenBasketPosition | None, Candle, datetime],
            BasketSnapshot | None,
        ],
        publish: Callable[[BasketSnapshot | None, Candle, datetime], None],
        entry_lock: Lock,
        entries_enabled: Callable[[], bool],
        current_snapshot: Callable[[], Snapshot],
        restored: Callable[[BasketSnapshot, datetime], None] | None = None,
        close_entry: CloseOneEntry[Snapshot] | None = None,
        waive_cooldown: WaiveCooldown[Snapshot] | None = None,
        position: OpenBasketPosition | None = None,
        notices: SnapshotNotices | None = None,
    ) -> None:
        self._warm_up = warm_up
        self._persistent = persistent
        self._basket = basket
        self._publish = publish
        self._entry_lock = entry_lock
        self._entries_enabled = entries_enabled
        self._current_snapshot = current_snapshot
        self._restored = restored
        self._close_entry = close_entry
        self._waive_cooldown = waive_cooldown
        self._notices = notices
        # The last completed price this sink has seen. A sale the trader asks
        # for arrives between candles, and what is left of the Basket is still
        # marked on the price the tab is already showing.
        self._candle: Candle | None = None
        # A Basket taken back arrives with its Entries already filled, so the
        # position starts where they left it rather than at nothing.
        self._position = position
        # The Entry gate as it stood when this sink last looked. A Cooldown
        # starting is a change rather than a fact a snapshot carries, so the
        # sink is what remembers where the gate was to change from.
        self._entry_gate: EntryGateState | None = None

    async def warm_up(
        self,
        candles: tuple[Candle, ...],
        *,
        received_at: datetime,
    ) -> None:
        self._warm_up(candles, received_at)
        if candles:
            self._candle = candles[-1]
        # A Basket taken back may already be cooling, and a Cooldown that was
        # standing before this Runtime started is not one it just started.
        self._entry_gate = _gate_state(self._current_snapshot())
        self._publish_what_was_taken_back(candles, received_at)

    def _publish_what_was_taken_back(
        self,
        candles: tuple[Candle, ...],
        observed_at_utc: datetime,
    ) -> None:
        """Say what the Session is holding before the market says anything.

        A Basket is only published when a candle completes, and the Session
        that took one back is holding it from the moment it starts — a whole
        timeframe before the next candle closes, which on an hourly Session is
        an hour of the Position tab reading as though nothing were open.

        It is marked on the last warm-up candle, which is the last completed
        price the Session has seen and the one it would be marked on anyway.
        """
        if self._restored is None or self._position is None or not candles:
            return
        basket = self._basket(
            self._current_snapshot(), self._position, candles[-1], observed_at_utc
        )
        if basket is None:
            return
        self._restored(basket, observed_at_utc)

    async def process_completed(
        self,
        candle: Candle,
        *,
        received_at: datetime,
        backfilled: bool,
    ) -> None:
        with self._entry_lock:
            if not self._entries_enabled():
                return
            snapshot = self._persistent.process_completed_candle(
                candle, received_at=received_at, backfilled=backfilled
            ).session
            self._require_running(snapshot)
            self._candle = candle
            self._publish_snapshot(snapshot, candle, candle.close_time)
            cooling = self._gate_just_closed(snapshot)
        # Outside the lock: whoever is told about this is somewhere else on
        # the network, and the next candle is due whether or not they have
        # answered yet.
        #
        # In the order they happened: an Entry fills, the Basket it filled
        # into may then leave, and only what the candle left behind can be
        # holding a gate shut.
        self._say(lambda notices: notices.entry_opened, snapshot, snapshot.entry_fill)
        self._say(
            lambda notices: notices.basket_closed, snapshot, snapshot.closed_basket
        )
        self._say(lambda notices: notices.cooldown_started, snapshot, cooling)

    async def process_in_progress(
        self,
        candle: Candle,
        *,
        received_at: datetime,
    ) -> None:
        """Close the Basket the moment the forming candle reaches its exit.

        Whether a market acts between candles is the Session's rule to state,
        not this sink's: Paper Futures answers an unaccepted snapshot here and
        so passes through without publishing.
        """
        with self._entry_lock:
            if not self._entries_enabled():
                return
            snapshot = self._persistent.process_in_progress_candle(
                candle, observed_at=received_at
            ).session
            self._require_running(snapshot)
            if not snapshot.accepted:
                return
            self._candle = candle
            self._publish_snapshot(snapshot, candle, received_at)
        # A forming candle opens no Entry, so the only thing it can have to
        # say is that the Basket reached its exit before the candle closed.
        self._say(
            lambda notices: notices.basket_closed, snapshot, snapshot.closed_basket
        )

    def close_entry(
        self,
        entry_number: int,
        *,
        price: Decimal,
        observed_at: datetime,
    ) -> bool:
        """Sell one Entry because the trader asked, between candles.

        It takes the same lock a candle does, so a sale and a Fill are never
        half of each other, and it goes through the same coordinator, so it is
        written before it is published or the Session is blocked.

        False is the answer where there was nothing to sell — a market that
        cannot take an Entry out on its own, a Bot that is stopping, or a row
        naming an Entry the Session has already let go.
        """
        close = self._close_entry
        if close is None:
            return False
        with self._entry_lock:
            if not self._entries_enabled():
                return False
            candle = self._candle
            if candle is None:
                return False
            snapshot = close(entry_number, price, observed_at).session
            if not snapshot.accepted:
                return False
            self._publish_snapshot(snapshot, candle, observed_at)
        # Selling the last Entry of a Basket closes it, and the trader who
        # asked is not the only one who wants to know what the round came to.
        self._say(
            lambda notices: notices.basket_closed, snapshot, snapshot.closed_basket
        )
        return True

    def waive_cooldown(self, observed_at: datetime) -> bool:
        """Lift the Cooldown holding this Basket, because the trader asked.

        Locked and recorded the same way a demand sale is, so the gate a
        candle reads next is never half of what the trader just accepted.

        False is the answer where there was nothing to waive — a market that
        cannot be asked, a Bot that is stopping, or a gate that was not a
        Cooldown to begin with.
        """
        waive = self._waive_cooldown
        if waive is None:
            return False
        with self._entry_lock:
            if not self._entries_enabled():
                return False
            candle = self._candle
            if candle is None:
                return False
            snapshot = waive(observed_at).session
            if not snapshot.accepted:
                return False
            self._publish_snapshot(snapshot, candle, observed_at)
            # The gate is open again, so nothing here may read as a Cooldown
            # this sink has yet to say anything about.
            self._entry_gate = _gate_state(snapshot)
        self._say(lambda notices: notices.cooldown_cancelled, snapshot, snapshot)
        return True

    def _say(
        self,
        reading: Callable[[SnapshotNotices], SnapshotReading],
        snapshot: Snapshot,
        happened: object,
    ) -> None:
        """Tell whoever is listening about one thing this snapshot holds.

        `happened` is whatever proves it happened — a Fill, a closed Basket —
        so each caller states its own test once and this states the rest: the
        decision is already persisted by now, nothing said here can change it,
        and a listener that fails is not allowed to take the Session with it.
        """
        notices = self._notices
        if notices is None or not happened:
            return
        try:
            reading(notices)(snapshot)
        except Exception:
            _logger.exception("saying what the Session did failed")

    def _gate_just_closed(self, snapshot: Snapshot) -> bool:
        """Whether this snapshot is where a Cooldown started.

        A snapshot says what the gate is, never that it changed, and a
        Cooldown is worth saying once — when it closes — rather than on every
        candle it goes on standing through.
        """
        was = self._entry_gate
        self._entry_gate = _gate_state(snapshot)
        return (
            self._entry_gate is EntryGateState.COOLDOWN
            and was is not EntryGateState.COOLDOWN
        )

    @staticmethod
    def _require_running(snapshot: Snapshot) -> None:
        """Stop feeding a Session that has stopped deciding.

        A Session that failed closed answers an unaccepted snapshot to every
        candle after it, which reads from outside exactly like a market with
        nothing happening in it. Said out loud here, the Runtime fails closed
        on it and the Bot goes Blocked instead of looking alive.
        """
        if snapshot.failed_closed:
            raise PaperSessionFailedClosedError(
                "the Paper Session has stopped and cannot take this candle"
            )

    def _publish_snapshot(
        self,
        snapshot: Snapshot,
        candle: Candle,
        observed_at_utc: datetime,
    ) -> None:
        self._position = position_after_snapshot(
            position_without_entries(self._position, snapshot.sold_entry_numbers),
            entry_fill=snapshot.entry_fill,
            basket_entry_count=snapshot.basket_entry_count,
            entry_number=snapshot.entry_number,
        )
        if self._entries_enabled():
            self._publish(
                self._basket(snapshot, self._position, candle, observed_at_utc),
                candle,
                observed_at_utc,
            )


def _warm_up_of(
    session: PaperSpotSession | PaperFuturesSession,
) -> Callable[[tuple[Candle, ...], datetime], None]:
    """The one thing the sink asks of a Session before candles start arriving."""

    def warm_up(candles: tuple[Candle, ...], received_at: datetime) -> None:
        session.warm_up_completed_candles(candles, received_at=received_at)

    return warm_up


def session_market(session: ConfiguredPaperSession) -> SessionMarket:
    """Which Session a notice is about, and which market it was pinned to."""
    return SessionMarket(
        session_id=session.config.session_id,
        symbol=session.market_data.symbol,
        timeframe=session.market_data.timeframe,
    )


def entry_opened_notice(
    snapshot: _CandleSnapshot,
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
    snapshot: _CandleSnapshot,
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
    snapshot: _CandleSnapshot,
    session: ConfiguredPaperSession,
    *,
    started_at: datetime,
) -> CooldownStartedNotice | None:
    """Read a gate that has just closed behind a completed Entry Pair.

    Whether it *just* closed is the sink's reading, not this one's: a snapshot
    says what the gate is and never that it changed.
    """
    if snapshot.basket_id is None:
        return None
    return CooldownStartedNotice(
        market=session_market(session),
        basket_id=snapshot.basket_id,
        completed_pairs=snapshot.entry_completed_pairs,
        open_entry_count=snapshot.basket_entry_count,
        started_at=started_at,
    )


def cooldown_cancelled_notice(
    snapshot: _CandleSnapshot,
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


def _gate_state(snapshot: _CandleSnapshot) -> EntryGateState | None:
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

    Two of the four carry a moment the snapshot does not: a gate closing and a
    gate lifting are things that are true from now on rather than things that
    happened at a price, so they are stamped where they are noticed.
    """
    if notify is None:
        return None

    def say_entry_opened(snapshot: _CandleSnapshot) -> None:
        _say(notify, entry_opened_notice(snapshot, session))

    def say_basket_closed(snapshot: _CandleSnapshot) -> None:
        _say(notify, basket_closed_notice(snapshot, session))

    def say_cooldown_started(snapshot: _CandleSnapshot) -> None:
        _say(notify, cooldown_started_notice(snapshot, session, started_at=clock()))

    def say_cooldown_cancelled(snapshot: _CandleSnapshot) -> None:
        _say(
            notify,
            cooldown_cancelled_notice(snapshot, session, cancelled_at=clock()),
        )

    return SnapshotNotices(
        entry_opened=say_entry_opened,
        basket_closed=say_basket_closed,
        cooldown_started=say_cooldown_started,
        cooldown_cancelled=say_cooldown_cancelled,
    )


def _say(notify: SessionNotifier, notice: SessionNotice | None) -> None:
    if notice is not None:
        notify(notice)


def paper_session_sink(
    session: ConfiguredPaperSession,
    preset: StrategyPreset,
    *,
    trade_history: SQLiteTradeHistory,
    symbol_rules: SymbolRules,
    publish: Callable[[BasketSnapshot | None, Candle, datetime], None],
    entry_lock: Lock,
    entries_enabled: Callable[[], bool],
    restored: Callable[[BasketSnapshot, datetime], None] | None = None,
    notify: SessionNotifier | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> (
    PaperRuntimeSink[PaperSpotSessionSnapshot]
    | PaperRuntimeSink[PaperFuturesSessionSnapshot]
):
    """Build the sink one Session's candles are fed to.

    Which market it is decides three things and nothing else: what a Session
    is, where its Fills are written, and how a Basket is read out of the
    snapshot that comes back. They are chosen here, together, so the Runtime
    that drives candles never has to know which market it is driving.
    """
    notices = snapshot_notices(session, notify, clock=clock)
    if session.config.market_type is MarketType.SPOT:
        open_basket = read_open_basket_state(trade_history, session.config.session_id)
        spot_application = PaperSpotSession(
            session.config,
            session.market_data,
            symbol_rules,
            preset,
            open_basket=open_basket,
        )
        spot_history = PaperSpotSQLiteHistory(
            PaperSpotHistoryContext(
                session_id=session.config.session_id,
                symbol=session.market_data.symbol,
                timeframe=session.market_data.timeframe,
                preset_version=session.config.preset_version,
            ),
            trade_history,
        )
        spot_persistent = create_persistent_paper_spot_session(
            spot_application, spot_history
        )
        return PaperRuntimeSink(
            warm_up=_warm_up_of(spot_application),
            persistent=spot_persistent,
            basket=spot_basket_snapshot,
            publish=publish,
            entry_lock=entry_lock,
            entries_enabled=entries_enabled,
            current_snapshot=lambda: spot_application.snapshot,
            restored=restored,
            close_entry=lambda entry_number, price, observed_at: (
                spot_persistent.close_entry(
                    entry_number, price=price, observed_at=observed_at
                )
            ),
            waive_cooldown=lambda observed_at: spot_persistent.waive_cooldown(
                observed_at
            ),
            position=open_basket_position(spot_application.open_entries),
            notices=notices,
        )

    # Paper Futures has no way back into a Basket it did not open: what it
    # was holding is margin and equity against a wallet, none of which the
    # Entries alone say. Rather than trade beside one, it says so and stops.
    if trade_history.find_open_basket(session.config.session_id) is not None:
        raise PaperPositionRestoreError("Paper Futures cannot take back an open Basket")
    futures_application = PaperFuturesSession(
        session.config,
        session.market_data,
        symbol_rules,
        preset,
    )
    futures_policy = session.config.futures_policy
    if futures_policy is None:
        raise ValueError("Paper Futures requires futures_policy")
    futures_history = PaperFuturesSQLiteHistory(
        PaperFuturesHistoryContext(
            session_id=session.config.session_id,
            symbol=session.market_data.symbol,
            timeframe=session.market_data.timeframe,
            preset_version=session.config.preset_version,
            leverage=futures_policy.leverage,
        ),
        trade_history,
    )
    futures_persistent = create_persistent_paper_futures_session(
        futures_application, futures_history
    )
    return PaperRuntimeSink(
        warm_up=_warm_up_of(futures_application),
        persistent=futures_persistent,
        basket=futures_basket_snapshot,
        publish=publish,
        entry_lock=entry_lock,
        entries_enabled=entries_enabled,
        current_snapshot=lambda: futures_application.snapshot,
        # Nothing is ever taken back here, so there is nothing to say early,
        # and no way to take one Entry out of a position on its own.
        restored=None,
        close_entry=None,
        # A Cooldown is the Entry Pair rule, which this market shares. Nothing
        # is taken back here, so a Runtime holding the Basket is the only
        # place the Cooldown exists to be lifted.
        waive_cooldown=lambda observed_at: futures_persistent.waive_cooldown(
            observed_at
        ),
        notices=notices,
    )


def close_one_entry_at_rest(
    session: ConfiguredPaperSession,
    entry_number: int,
    *,
    trade_history: SQLiteTradeHistory,
    symbol_rules: SymbolRules,
    preset: StrategyPreset,
    price: Decimal,
    observed_at: datetime,
) -> bool:
    """Sell one Entry of a Basket no Runtime is holding.

    A stopped Bot leaves its Basket in the database, and the trader can still
    see it and still ask for one Entry to go. The Session is composed here
    exactly as the Runtime composes it and asked the same question, so a sale
    made with the Bot stopped cannot book differently from the same sale made
    while it runs.

    False is the answer where there was nothing to sell: no open Basket, or no
    Entry by that number.
    """
    if session.config.market_type is not MarketType.SPOT:
        raise ValueError("only a Paper Spot Basket can let one Entry go")
    open_basket = read_open_basket_state(trade_history, session.config.session_id)
    if open_basket is None:
        return False
    application = PaperSpotSession(
        session.config,
        session.market_data,
        symbol_rules,
        preset,
        open_basket=open_basket,
    )
    history = PaperSpotSQLiteHistory(
        PaperSpotHistoryContext(
            session_id=session.config.session_id,
            symbol=session.market_data.symbol,
            timeframe=session.market_data.timeframe,
            preset_version=session.config.preset_version,
        ),
        trade_history,
    )
    persistent = create_persistent_paper_spot_session(application, history)
    return persistent.close_entry(
        entry_number, price=price, observed_at=observed_at
    ).session.accepted


def waive_cooldown_at_rest(
    session: ConfiguredPaperSession,
    *,
    trade_history: SQLiteTradeHistory,
    symbol_rules: SymbolRules,
    preset: StrategyPreset,
    observed_at: datetime,
) -> bool:
    """Lift a Cooldown holding a Basket no Runtime is holding.

    Composed exactly as the Runtime composes it and asked the same question,
    so a waiver granted with the Bot stopped cannot book differently from the
    same waiver granted while it runs.

    False is the answer where there was nothing to waive: no open Basket, or a
    gate that is not a Cooldown right now.
    """
    if session.config.market_type is not MarketType.SPOT:
        raise ValueError("only a Paper Spot Basket can have its Cooldown waived")
    open_basket = read_open_basket_state(trade_history, session.config.session_id)
    if open_basket is None:
        return False
    application = PaperSpotSession(
        session.config,
        session.market_data,
        symbol_rules,
        preset,
        open_basket=open_basket,
    )
    history = PaperSpotSQLiteHistory(
        PaperSpotHistoryContext(
            session_id=session.config.session_id,
            symbol=session.market_data.symbol,
            timeframe=session.market_data.timeframe,
            preset_version=session.config.preset_version,
        ),
        trade_history,
    )
    persistent = create_persistent_paper_spot_session(application, history)
    return persistent.waive_cooldown(observed_at).session.accepted


def read_open_basket_state(
    trade_history: SQLiteTradeHistory,
    session_id: UUID,
) -> OpenBasketState | None:
    """Read back the Basket a previous Runtime left open, if there is one.

    A Basket nobody holds is a Position nobody watches: its take profit goes
    unchecked and its Entries go uncounted. Rather than trade beside one, a
    Basket that cannot be put back says so.
    """
    basket = trade_history.find_open_basket(session_id)
    if basket is None:
        return None
    fills = trade_history.list_fills(basket.basket_id)
    # An Entry that was sold at its own target is not part of the position
    # left open, however the Basket it belonged to still is.
    sold = {
        fill.closes_entry_number
        for fill in fills
        if fill.closes_entry_number is not None
    }
    entries: list[OpenBasketEntry] = []
    settled = SettledEntries(
        count=len(sold),
        realized_pnl=sum(
            (
                fill.realized_pnl
                for fill in fills
                if fill.closes_entry_number is not None
            ),
            Decimal("0"),
        ),
        quantity=sum(
            (
                fill.quantity
                for fill in fills
                if fill.entry_number is not None and fill.entry_number in sold
            ),
            Decimal("0"),
        ),
        notional=sum(
            (
                fill.price * fill.quantity
                for fill in fills
                if fill.entry_number is not None and fill.entry_number in sold
            ),
            Decimal("0"),
        ),
    )
    for fill in fills:
        if fill.entry_number is None or fill.entry_number in sold:
            continue
        if fill.entry_atr is None:
            raise PaperPositionRestoreError(
                "open Basket has an Entry recorded without its ATR"
            )
        entries.append(
            OpenBasketEntry(
                entry_number=fill.entry_number,
                price=fill.price,
                quantity=fill.quantity,
                filled_at=fill.filled_at_utc,
                atr=fill.entry_atr,
            )
        )
    if not entries:
        raise PaperPositionRestoreError("open Basket has no Entry Fills")
    return OpenBasketState(
        basket_id=basket.basket_id,
        entries=tuple(entries),
        closed_basket_count=trade_history.count_closed_baskets(session_id),
        settled=settled,
        waived_pair=trade_history.find_cooldown_waiver(basket.basket_id),
    )
