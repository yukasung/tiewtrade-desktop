import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import TypeGuard
from uuid import UUID

from PySide6.QtCore import QObject, QThreadPool, Signal, Slot

from tiewtrade.application.bot_control import BotControlSnapshot
from tiewtrade.application.chart_data import (
    ChartCandle,
    ChartFeedState,
    ChartRange,
    ChartReadState,
    ChartSnapshot,
    append_completed_candle,
    default_chart_range,
    latest_completed_boundary,
)
from tiewtrade.application.chart_view_selection import ChartViewSelection
from tiewtrade.application.paper_session_setup import ConfiguredPaperSession
from tiewtrade.trading.trade_history import TradeFill
from tiewtrade.ui.background_task import BackgroundTask
from tiewtrade.ui.chart_snapshots import (
    answers_the_view,
    blank_snapshot,
    fills_inside,
    unavailable_snapshot,
    with_in_progress_candle,
    without_fills,
)

LoadChart = Callable[[ConfiguredPaperSession, ChartRange], Awaitable[ChartSnapshot]]
RefreshChart = Callable[
    [ConfiguredPaperSession, ChartSnapshot, ChartCandle],
    Awaitable[ChartSnapshot],
]
_NO_BASKET: tuple[object, ...] = (None,)
LoadChartFills = Callable[
    [ConfiguredPaperSession, ChartRange],
    Awaitable[tuple[TradeFill, ...]],
]
_LOGGER = logging.getLogger("tiewtrade.ui.chart_workflow")


class ChartWorkflow(QObject):
    """Loads chart facts off the Qt thread and discards stale callbacks."""

    snapshot_changed = Signal(object)
    loading_changed = Signal(bool)
    # A chart panned off the latest candle takes no live price, while the
    # Position tab goes on marking on them. Said out loud, the two readings
    # stop looking like a fault.
    following_latest_changed = Signal(bool)
    # What the chart's own connection reports it is doing. How often prices
    # arrive says nothing about it: that is how much the Pair is traded.
    feed_state_changed = Signal(object)

    def __init__(
        self,
        *,
        load_chart: LoadChart,
        refresh_chart: RefreshChart | None = None,
        load_fills: LoadChartFills | None = None,
        thread_pool: QThreadPool | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._load_chart = load_chart
        self._refresh_chart = refresh_chart
        self._load_fills = load_fills
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._clock = clock
        self._session: ConfiguredPaperSession | None = None
        self._selection: ChartViewSelection | None = None
        self._snapshot: ChartSnapshot | None = None
        self._generation = 0
        self._active_task: BackgroundTask | None = None
        self._pending_request: tuple[int, ChartRange] | None = None
        self._pending_completed_candle: ChartCandle | None = None
        self._pending_in_progress_candle: ChartCandle | None = None
        self._last_safe_range: ChartRange | None = None
        self._following_latest = True
        self._closed = False
        self._basket_facts: tuple[object, ...] | None = None
        self._fills_task: BackgroundTask | None = None
        self._fills_refresh_pending = False
        self._re_anchored_for: datetime | None = None
        self._feed_state: ChartFeedState | None = None

    def configure(self, session: ConfiguredPaperSession) -> None:
        if self._closed:
            return
        self._generation += 1
        self._session = session
        self._selection = ChartViewSelection(
            symbol=session.market_data.symbol,
            timeframe=session.market_data.timeframe,
        )
        self._snapshot = None
        self._pending_request = None
        self._pending_completed_candle = None
        self._pending_in_progress_candle = None
        self._last_safe_range = None
        self._set_following_latest(True)
        self._basket_facts = None
        self._fills_refresh_pending = False
        self._re_anchored_for = None

    def start(self, session: ConfiguredPaperSession) -> None:
        self.configure(session)
        self.load_range(default_chart_range(self._view_session(), self._clock()))

    def select_view(self, selection: ChartViewSelection) -> None:
        if self._closed or self._session is None or selection == self._selection:
            return
        self._selection = selection
        self._generation += 1
        self._pending_request = None
        self._pending_completed_candle = None
        self._pending_in_progress_candle = None
        self._last_safe_range = None
        self._set_following_latest(True)
        self.load_range(default_chart_range(self._view_session(), self._clock()))

    @property
    def following_latest(self) -> bool:
        """Whether prices as they arrive still reach the chart."""
        return self._following_latest

    def follow_latest(self) -> None:
        """Put the chart back on the candle forming now."""
        if self._closed or self._session is None or self._selection is None:
            return
        self.load_range(default_chart_range(self._view_session(), self._clock()))

    def _set_following_latest(self, following: bool) -> None:
        if following == self._following_latest:
            return
        self._following_latest = following
        self.following_latest_changed.emit(following)

    def load_range(self, chart_range: ChartRange) -> None:
        if self._closed or self._session is None or self._selection is None:
            return
        self._set_following_latest(
            chart_range.end
            == latest_completed_boundary(self._selection.timeframe, self._clock())
        )
        self._pending_in_progress_candle = None
        self._clear_in_progress_candle()
        self._generation += 1
        generation = self._generation
        self._last_safe_range = chart_range
        self._publish_loading(chart_range)
        if self._active_task is not None:
            self._pending_request = (generation, chart_range)
            return
        self._start_load(generation, chart_range)

    def retry(self) -> None:
        if self._closed or self._active_task is not None:
            return
        chart_range = self._last_safe_range
        if chart_range is not None:
            self.load_range(chart_range)

    def feed_state(self, state: object) -> None:
        """Take what the chart's own connection is doing, and act on it.

        Coming back is the half that matters: the candles an outage swallowed
        are only in the history now, so the visible range is read again. Going
        live for the first time is just the feed starting, and the load that
        starts with it has already asked for the same range — which is why the
        reload turns on the state being replaced, not the one arriving.

        Repeats say nothing new, and the screen should not flicker on them.
        """
        if self._closed or not isinstance(state, ChartFeedState):
            return
        previous = self._feed_state
        if state is previous:
            return
        self._feed_state = state
        self.feed_state_changed.emit(state)
        if state is ChartFeedState.LIVE and previous is ChartFeedState.RECONNECTING:
            self._reload_after_reconnect()

    def _log_dropped_candle(
        self,
        drop_reason: str,
        *,
        chart_state: str | None = None,
    ) -> None:
        """Say when a live candle did not reach the chart, and why.

        A chart that stops moving looks the same whether the prices stopped
        arriving or arrived and were turned away here. Only one of the two is
        a fault, and until this was written down there was no way to tell
        which one an idle chart was.
        """
        selection = self._selection
        _LOGGER.info(
            "chart.candle.dropped",
            extra={
                "event_name": "chart.candle.dropped",
                "drop_reason": drop_reason,
                "chart_state": chart_state,
                "symbol": None if selection is None else selection.symbol,
                "timeframe": None if selection is None else selection.timeframe,
            },
        )

    def _reload_after_reconnect(self) -> None:
        """Read the visible range again, now that there is a feed to read with.

        A chart following the latest candle is watching the window that ends
        now, which is boundaries past the one it was left holding — reloading
        the old window would read the same candles back and leave the chart
        just as far behind. A chart panned away is watching a window that has
        not moved, and gets that one back.
        """
        session = self._session
        selection = self._selection
        if session is None or selection is None:
            return
        chart_range = (
            default_chart_range(self._view_session(), self._clock())
            if self._following_latest
            else self._last_safe_range
        )
        if chart_range is None:
            return
        _LOGGER.info(
            "chart.reload.after_reconnect",
            extra={
                "event_name": "chart.reload.after_reconnect",
                "symbol": selection.symbol,
                "timeframe": selection.timeframe,
            },
        )
        self.load_range(chart_range)

    def runtime_snapshot(self, snapshot: object) -> None:
        if (
            self._closed
            or not isinstance(snapshot, BotControlSnapshot)
            or self._session is None
            or snapshot.session.config.session_id != self._session.config.session_id
        ):
            return
        # Runtime snapshots arrive on every tick, but only a basket that grew
        # or closed means a fill the chart has not drawn yet.
        facts = _basket_facts(snapshot)
        if facts == self._basket_facts:
            return
        first_sighting = self._basket_facts is None
        self._basket_facts = facts
        if facts == _NO_BASKET and first_sighting:
            # Nothing has traded yet, so the loaded fills are already current.
            return
        self.refresh_fills()

    def refresh_fills(self) -> None:
        """Re-read the fills of the visible range, off the Qt thread."""
        snapshot = self._snapshot
        session = self._session
        selection = self._selection
        if (
            self._closed
            or self._load_fills is None
            or session is None
            or selection is None
            or snapshot is None
            or snapshot.state not in {ChartReadState.READY, ChartReadState.EMPTY}
            # Fills belong to a Pair, not to a timeframe: the same trade is
            # drawn on whatever candles are on screen. Only a chart of some
            # other Pair has no business showing this Session's trades.
            or selection.symbol != session.market_data.symbol
        ):
            return
        if self._fills_task is not None:
            self._fills_refresh_pending = True
            return
        load_fills = self._load_fills
        generation = self._generation
        chart_range = snapshot.chart_range
        visible_range = snapshot.visible_range
        task = BackgroundTask(
            lambda: asyncio.run(
                self._fills_in_background(
                    load_fills,
                    self._view_session(),
                    visible_range,
                )
            )
        )
        task.signals.succeeded.connect(
            lambda result: self._fills_loaded(generation, chart_range, result)
        )
        task.signals.finished.connect(lambda: self._fills_finished(task))
        self._fills_task = task
        self._thread_pool.start(task)

    def _fills_loaded(
        self,
        generation: int,
        chart_range: ChartRange,
        result: object,
    ) -> None:
        snapshot = self._snapshot
        if (
            self._closed
            or generation != self._generation
            or not isinstance(result, tuple)
            or snapshot is None
            or snapshot.chart_range != chart_range
        ):
            return
        fills = fills_inside(result, snapshot.visible_range)
        if fills == snapshot.fills:
            return
        try:
            updated = self._copy_snapshot(
                snapshot,
                in_progress_candle=snapshot.in_progress_candle,
                fills=fills,
            )
        except ValueError:
            return
        self._snapshot = updated
        self.snapshot_changed.emit(updated)

    def _fills_finished(self, task: BackgroundTask) -> None:
        if task is not self._fills_task:
            return
        self._fills_task = None
        if not self._fills_refresh_pending or self._closed:
            return
        self._fills_refresh_pending = False
        self.refresh_fills()

    def in_progress_candle(self, candle: object) -> None:
        if self._closed or not self._is_current_session_candle_fact(candle):
            return
        if not self._following_latest or not self._is_observed_inside_candle(candle):
            return
        snapshot = self._snapshot
        if self._active_task is not None or (
            snapshot is not None and snapshot.state is ChartReadState.LOADING
        ):
            pending = self._pending_in_progress_candle
            if pending is None or candle.open_time >= pending.open_time:
                self._pending_in_progress_candle = candle
            return
        if snapshot is None or snapshot.state not in {
            ChartReadState.READY,
            ChartReadState.EMPTY,
        }:
            return
        if self._re_anchors_for(candle, snapshot):
            self._re_anchored_for = candle.open_time
            self._reload_after_reconnect()
            # Through the front door again: the reload either landed already
            # and the candle now fits, or is in flight and holds it as the
            # pending fact it drains when it lands.
            self.in_progress_candle(candle)
            return
        self._publish_in_progress_candle(snapshot, candle)

    def _re_anchors_for(self, candle: ChartCandle, snapshot: ChartSnapshot) -> bool:
        """Whether this price is the first one back from a dropped feed.

        A forming candle sits at the end of the range the chart holds, and an
        outage leaves that range behind, so the price that proves the socket
        is back cannot be drawn on the chart it arrives at. Reading the range
        again is what makes room for it. Once per candle: prices arrive many
        times a second and the history behind them changes once a boundary.
        """
        # Not `is LIVE`: a feed nothing has reported on yet has no outage to
        # come back from either, and belongs on the same side as a live one.
        if self._feed_state is not ChartFeedState.RECONNECTING:
            return False
        if candle.open_time == snapshot.chart_range.end:
            return False
        return self._re_anchored_for is None or candle.open_time > self._re_anchored_for

    def completed_candle(self, candle: object) -> None:
        if not self._is_current_session_candle_fact(candle):
            self._log_dropped_candle("not_this_view")
            return
        self._clear_in_progress_candle(matching_open_time=candle.open_time)
        snapshot = self._snapshot
        if not self._following_latest:
            self._log_dropped_candle("not_following_latest")
            return
        if self._active_task is not None or (
            snapshot is not None and snapshot.state is ChartReadState.LOADING
        ):
            pending = self._pending_completed_candle
            typed_candle = candle
            if pending is None or typed_candle.close_time > pending.close_time:
                self._pending_completed_candle = typed_candle
            return
        if (
            self._closed
            or self._session is None
            or snapshot is None
            or snapshot.state
            not in (
                {ChartReadState.READY, ChartReadState.EMPTY}
                if self._refresh_chart is not None
                else {ChartReadState.READY}
            )
            or not self._is_current_session_candle(candle, snapshot)
        ):
            self._log_dropped_candle(
                "chart_not_ready",
                chart_state=None if snapshot is None else snapshot.state.value,
            )
            return
        if self._refresh_chart is not None:
            refresh_chart = self._refresh_chart
            if self._session is None or self._selection is None:
                return
            self._generation += 1
            generation = self._generation
            task = BackgroundTask(
                lambda: asyncio.run(
                    self._refresh_in_background(
                        refresh_chart,
                        self._view_session(),
                        snapshot,
                        candle,
                    )
                )
            )
            task.signals.succeeded.connect(
                lambda result: self._refresh_succeeded(generation, result)
            )
            task.signals.failed.connect(
                lambda error: self._refresh_failed(generation, snapshot, error)
            )
            task.signals.finished.connect(lambda: self._refresh_finished(task))
            self._active_task = task
            self.loading_changed.emit(True)
            self._thread_pool.start(task)
            return
        try:
            updated = append_completed_candle(snapshot, candle)
        except ValueError:
            return
        self._snapshot = updated
        self.snapshot_changed.emit(updated)

    def _refresh_succeeded(self, generation: int, result: object) -> None:
        session = self._session
        selection = self._selection
        if (
            generation != self._generation
            or not isinstance(result, ChartSnapshot)
            or session is None
            or selection is None
            or result.session_id != session.config.session_id
            or result.symbol != selection.symbol
            or result.timeframe != selection.timeframe
        ):
            return
        normalized = self._normalize_snapshot(result)
        self._snapshot = normalized
        self._last_safe_range = normalized.chart_range
        self.snapshot_changed.emit(normalized)

    def _refresh_failed(
        self,
        generation: int,
        snapshot: ChartSnapshot,
        error: object,
    ) -> None:
        del error
        if generation != self._generation:
            return
        unavailable = self._unavailable_snapshot(snapshot.chart_range)
        if unavailable is None:
            return
        self._pending_completed_candle = None
        self._pending_in_progress_candle = None
        self._snapshot = unavailable
        self.snapshot_changed.emit(unavailable)

    def _refresh_finished(self, task: BackgroundTask) -> None:
        if task is not self._active_task:
            return
        self._active_task = None
        pending = self._pending_request
        self._pending_request = None
        if self._closed:
            return
        if pending is not None:
            self._start_load(*pending)
            return
        self.loading_changed.emit(False)
        self._drain_pending_completed_candle()
        self._drain_pending_in_progress_candle()

    @Slot()
    def close(self) -> None:
        if self._closed:
            return
        self._clear_in_progress_candle()
        self._closed = True
        self._generation += 1
        self._pending_request = None
        self._pending_completed_candle = None
        self._pending_in_progress_candle = None
        self._session = None
        self._set_following_latest(False)

    def _start_load(self, generation: int, chart_range: ChartRange) -> None:
        if self._session is None or self._selection is None:
            return
        task = BackgroundTask(
            lambda: asyncio.run(
                self._load_in_background(self._view_session(), chart_range)
            )
        )
        task.signals.succeeded.connect(
            lambda result: self._load_succeeded(generation, chart_range, result)
        )
        task.signals.failed.connect(
            lambda error: self._load_failed(generation, chart_range, error)
        )
        task.signals.finished.connect(lambda: self._load_finished(task))
        self._active_task = task
        self.loading_changed.emit(True)
        self._thread_pool.start(task)

    def _publish_loading(self, chart_range: ChartRange) -> None:
        loading = self._blank_snapshot(chart_range, state=ChartReadState.LOADING)
        if loading is None:
            return
        self._snapshot = loading
        self.snapshot_changed.emit(loading)

    def _load_succeeded(
        self, generation: int, chart_range: ChartRange, result: object
    ) -> None:
        if generation != self._generation or not self._valid_snapshot(
            result, chart_range
        ):
            return
        normalized = self._normalize_snapshot(result)
        self._snapshot = normalized
        self.snapshot_changed.emit(normalized)

    def _load_failed(
        self, generation: int, chart_range: ChartRange, error: object
    ) -> None:
        del error
        if generation != self._generation:
            return
        unavailable = self._unavailable_snapshot(chart_range)
        if unavailable is None:
            return
        self._pending_in_progress_candle = None
        self._snapshot = unavailable
        self.snapshot_changed.emit(unavailable)

    def _load_finished(self, task: BackgroundTask) -> None:
        if task is not self._active_task:
            return
        self._active_task = None
        pending = self._pending_request
        self._pending_request = None
        if self._closed:
            return
        if pending is not None:
            self._start_load(*pending)
            return
        self.loading_changed.emit(False)
        self._drain_pending_completed_candle()
        self._drain_pending_in_progress_candle()

    async def _load_in_background(
        self, session: ConfiguredPaperSession, chart_range: ChartRange
    ) -> ChartSnapshot:
        return await self._load_chart(session, chart_range)

    async def _fills_in_background(
        self,
        load_fills: LoadChartFills,
        session: ConfiguredPaperSession,
        visible_range: ChartRange,
    ) -> tuple[TradeFill, ...]:
        return await load_fills(session, visible_range)

    async def _refresh_in_background(
        self,
        refresh_chart: RefreshChart,
        session: ConfiguredPaperSession,
        snapshot: ChartSnapshot,
        candle: ChartCandle,
    ) -> ChartSnapshot:
        return await refresh_chart(session, snapshot, candle)

    def _valid_snapshot(
        self, result: object, chart_range: ChartRange
    ) -> TypeGuard[ChartSnapshot]:
        return answers_the_view(
            result, facts=self._view_facts(), chart_range=chart_range
        )

    def _is_current_session_candle(
        self, candle: object, snapshot: ChartSnapshot
    ) -> TypeGuard[ChartCandle]:
        return (
            self._is_current_session_candle_fact(candle)
            and candle.open_time >= snapshot.chart_range.start
        )

    def _is_current_session_candle_fact(
        self,
        candle: object,
    ) -> TypeGuard[ChartCandle]:
        return (
            isinstance(candle, ChartCandle)
            and self._selection is not None
            and candle.symbol == self._selection.symbol
            and candle.timeframe == self._selection.timeframe
        )

    def _is_observed_inside_candle(self, candle: ChartCandle) -> bool:
        observed_at_utc = self._clock()
        return candle.open_time <= observed_at_utc < candle.close_time

    def _publish_in_progress_candle(
        self,
        snapshot: ChartSnapshot,
        candle: ChartCandle,
    ) -> None:
        try:
            updated = self._copy_snapshot(
                snapshot,
                in_progress_candle=candle,
            )
        except ValueError:
            return
        self._snapshot = updated
        self.snapshot_changed.emit(updated)
        # Fills were read for the completed range, but the forming candle
        # stretches the drawn span past it, so a fill inside that candle has
        # never been asked for. Without this the marker for a fresh Entry
        # stays missing until the candle closes — a moment on 1m, but hours
        # on 4h, and every timeframe change starts the wait over.
        forming = snapshot.in_progress_candle
        if forming is None or forming.open_time != candle.open_time:
            self.refresh_fills()

    def _clear_in_progress_candle(
        self,
        *,
        matching_open_time: datetime | None = None,
    ) -> None:
        pending = self._pending_in_progress_candle
        if pending is not None and (
            matching_open_time is None or pending.open_time == matching_open_time
        ):
            self._pending_in_progress_candle = None
        snapshot = self._snapshot
        if snapshot is None or snapshot.in_progress_candle is None:
            return
        forming = snapshot.in_progress_candle
        if matching_open_time is not None and forming.open_time != matching_open_time:
            return
        updated = self._copy_snapshot(snapshot, in_progress_candle=None)
        self._snapshot = updated
        self.snapshot_changed.emit(updated)

    def _copy_snapshot(
        self,
        snapshot: ChartSnapshot,
        *,
        in_progress_candle: ChartCandle | None,
        fills: tuple[TradeFill, ...] | None = None,
    ) -> ChartSnapshot:
        return with_in_progress_candle(
            snapshot,
            now=self._clock(),
            in_progress_candle=in_progress_candle,
            fills=fills,
        )

    def _view_facts(self) -> tuple[UUID, str, str] | None:
        """Whose Session this chart is on, and which Pair at which timeframe.

        None before there is a view to answer for, which is every caller's
        reason to have nothing to draw.
        """
        session = self._session
        selection = self._selection
        if session is None or selection is None:
            return None
        return (session.config.session_id, selection.symbol, selection.timeframe)

    def _unavailable_snapshot(self, chart_range: ChartRange) -> ChartSnapshot | None:
        """A chart with nothing on it, because the read behind it did not land."""
        facts = self._view_facts()
        if facts is None:
            return None
        return unavailable_snapshot(facts, chart_range, now=self._clock())

    def _blank_snapshot(
        self,
        chart_range: ChartRange,
        *,
        state: ChartReadState,
        message: str | None = None,
    ) -> ChartSnapshot | None:
        """A chart of this view with nothing drawn on it yet, or nothing left.

        None before there is a view to draw one for, which is every caller's
        reason to have nothing to publish.
        """
        facts = self._view_facts()
        if facts is None:
            return None
        return blank_snapshot(
            facts, chart_range, now=self._clock(), state=state, message=message
        )

    def _view_session(self) -> ConfiguredPaperSession:
        session = self._session
        selection = self._selection
        if session is None or selection is None:
            raise RuntimeError("Chart view has not been configured")
        if selection.market_data_config() == session.market_data:
            return session
        return replace(session, market_data=selection.market_data_config())

    def _normalize_snapshot(self, snapshot: ChartSnapshot) -> ChartSnapshot:
        """Drop the Session's fills from a chart of some other Pair.

        The fills are read by Session, not by Pair, so a chart of another
        Pair would otherwise badge trades that never happened on it. A
        different timeframe of the same Pair keeps them — it is the same
        trade seen at another resolution, and ChartMarker already rounds
        each fill to the candle it belongs to.
        """
        session = self._session
        selection = self._selection
        facts = self._view_facts()
        if (
            session is None
            or selection is None
            or facts is None
            or selection.symbol == session.market_data.symbol
        ):
            return snapshot
        return without_fills(snapshot, facts=facts)

    def _drain_pending_in_progress_candle(self) -> None:
        pending = self._pending_in_progress_candle
        self._pending_in_progress_candle = None
        if pending is not None:
            self.in_progress_candle(pending)

    def _drain_pending_completed_candle(self) -> None:
        pending = self._pending_completed_candle
        self._pending_completed_candle = None
        if pending is not None:
            self.completed_candle(pending)


def _basket_facts(snapshot: BotControlSnapshot) -> tuple[object, ...]:
    """What identifies a basket across ticks, ignoring its moving price."""
    basket = snapshot.workspace.basket
    if basket is None:
        return _NO_BASKET
    return (
        basket.symbol,
        basket.entry_count,
        basket.total_quantity,
        basket.entry_gate,
    )
