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

from tiewtrade.application.paper_basket_at_rest import (
    PaperPositionRestoreError,
    read_open_basket_state,
)
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
from tiewtrade.application.paper_session_notices import (
    CandleSnapshot,
    SnapshotNotices,
    SnapshotReading,
    gate_state,
    snapshot_notices,
)
from tiewtrade.application.paper_session_setup import ConfiguredPaperSession
from tiewtrade.application.paper_spot_session import (
    PaperSpotSession,
    PaperSpotSessionSnapshot,
)
from tiewtrade.application.session_notice import (
    SessionNotifier,
)
from tiewtrade.application.session_persistence import (
    PersistentSessionSnapshot,
    SessionPersistenceCoordinator,
)
from tiewtrade.application.trading_workspace import BasketSnapshot
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
from tiewtrade.trading.entry_pair import EntryGateState
from tiewtrade.trading.session_config import MarketType
from tiewtrade.trading.symbol_rules import SymbolRules

_logger = logging.getLogger(__name__)


class PaperSessionFailedClosedError(RuntimeError):
    """The Session has stopped for good, so nothing more may be fed to it."""


type CloseOneEntry[Snapshot] = Callable[
    [int, Decimal, datetime], PersistentSessionSnapshot[Snapshot]
]
"""Sell one Entry at a price, recorded before it is published."""

type WaiveCooldown[Snapshot] = Callable[[datetime], PersistentSessionSnapshot[Snapshot]]
"""Lift the Cooldown holding this Basket now, recorded before it is published."""


@dataclass(frozen=True, slots=True)
class _GateChange:
    """What one candle did to the Entry gate, read once and answered from twice.

    `cooled` is a Cooldown that just started; `lapsed` is one that just ended
    without the trader asking — the days it was given ran out. Neither is a
    state the snapshot carries: they are the difference between two snapshots.
    """

    cooled: bool
    lapsed: bool


class PaperRuntimeSink[Snapshot: CandleSnapshot]:
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
        self._entry_gate = gate_state(self._current_snapshot())
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
            gate = self._take_gate_change(snapshot)
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
        self._say(lambda notices: notices.cooldown_started, snapshot, gate.cooled)
        self._say(lambda notices: notices.cooldown_ended, snapshot, gate.lapsed)

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
            self._entry_gate = gate_state(snapshot)
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

    def _take_gate_change(self, snapshot: Snapshot) -> _GateChange:
        """What the gate did between the last candle and this one.

        A snapshot says what the gate is, never that it changed, so the sink
        remembers the last one and compares. A Cooldown is worth saying once —
        when it closes, and again when it runs out — rather than on every
        candle it goes on standing through.

        Both readings come back together because both answer for the same
        replacement: the gate this sink remembers is taken and replaced once
        per candle, and there is no moment in between for either to be read
        against the wrong one.
        """
        was = self._entry_gate
        now = gate_state(snapshot)
        self._entry_gate = now
        return _GateChange(
            cooled=now is EntryGateState.COOLDOWN
            and was is not EntryGateState.COOLDOWN,
            lapsed=was is EntryGateState.COOLDOWN
            and now is not EntryGateState.COOLDOWN,
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
