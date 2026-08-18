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

from collections.abc import Callable
from datetime import datetime
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
from tiewtrade.application.session_persistence import SessionPersistenceCoordinator
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
from tiewtrade.trading.session_config import MarketType
from tiewtrade.trading.symbol_rules import SymbolRules


class PaperPositionRestoreError(RuntimeError):
    """An open Basket cannot be put back the way it was left."""


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
    def entry_take_profit_price(self) -> Decimal | None: ...

    @property
    def sold_entry_numbers(self) -> tuple[int, ...]: ...


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
        position: OpenBasketPosition | None = None,
    ) -> None:
        self._warm_up = warm_up
        self._persistent = persistent
        self._basket = basket
        self._publish = publish
        self._entry_lock = entry_lock
        self._entries_enabled = entries_enabled
        self._current_snapshot = current_snapshot
        self._restored = restored
        # A Basket taken back arrives with its Entries already filled, so the
        # position starts where they left it rather than at nothing.
        self._position = position

    async def warm_up(
        self,
        candles: tuple[Candle, ...],
        *,
        received_at: datetime,
    ) -> None:
        self._warm_up(candles, received_at)
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
    ) -> None:
        with self._entry_lock:
            if not self._entries_enabled():
                return
            snapshot = self._persistent.process_completed_candle(
                candle, received_at=received_at
            ).session
            self._publish_snapshot(snapshot, candle, candle.close_time)

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
            if not snapshot.accepted:
                return
            self._publish_snapshot(snapshot, candle, received_at)

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
            take_profit_price=snapshot.entry_take_profit_price,
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
        return PaperRuntimeSink(
            warm_up=_warm_up_of(spot_application),
            persistent=create_persistent_paper_spot_session(
                spot_application, spot_history
            ),
            basket=spot_basket_snapshot,
            publish=publish,
            entry_lock=entry_lock,
            entries_enabled=entries_enabled,
            current_snapshot=lambda: spot_application.snapshot,
            restored=restored,
            position=open_basket_position(spot_application.open_entries),
        )

    # Paper Futures has no way back into a Basket it did not open — a
    # Liquidation Price is not derived from the Entries alone — so rather
    # than trade beside one, it says so and stops.
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
    return PaperRuntimeSink(
        warm_up=_warm_up_of(futures_application),
        persistent=create_persistent_paper_futures_session(
            futures_application, futures_history
        ),
        basket=futures_basket_snapshot,
        publish=publish,
        entry_lock=entry_lock,
        entries_enabled=entries_enabled,
        current_snapshot=lambda: futures_application.snapshot,
        # Nothing is ever taken back here, so there is nothing to say early.
        restored=None,
    )


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
    )
