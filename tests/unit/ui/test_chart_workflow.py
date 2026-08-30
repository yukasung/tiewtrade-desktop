from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tests.support.paper_session_setup import configured_spot_session
from tests.support.trade_history_records import trade_fill
from tests.support.workspace_snapshots import basket_entries, basket_targets
from tiewtrade.application.bot_control import (
    BotControlAction,
    BotControlSnapshot,
    workspace_with_runtime_state,
)
from tiewtrade.application.chart_data import (
    ChartFeedState,
    ChartRange,
    ChartReadState,
    ChartSnapshot,
)
from tiewtrade.application.chart_view_selection import ChartViewSelection
from tiewtrade.application.paper_session_setup import ConfiguredPaperSession
from tiewtrade.application.trading_workspace import (
    BasketEntryGate,
    BasketSnapshot,
    BotRuntimeState,
    DataFreshness,
    TradingWorkspaceSnapshot,
    configured_workspace_snapshot,
    ready_position_basket_tab,
)
from tiewtrade.market_data.candle import Candle
from tiewtrade.market_data.config import timeframe_to_interval
from tiewtrade.trading.trade_history import FillSide, TradeFill
from tiewtrade.ui.background_task import BackgroundTask
from tiewtrade.ui.chart_workflow import ChartFactRelay, ChartWorkflow


class ImmediateThreadPool:
    def start(self, task: BackgroundTask) -> None:
        task.run()


class DeferredThreadPool:
    def __init__(self) -> None:
        self.tasks: list[BackgroundTask] = []

    def start(self, task: BackgroundTask) -> None:
        self.tasks.append(task)


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class PartialCompletedCandle:
    symbol = "BTCUSDT"
    timeframe = "5m"
    open_time = datetime(2026, 1, 1, 0, 15, tzinfo=UTC)
    close_time = datetime(2026, 1, 1, 0, 20, tzinfo=UTC)


def test_a_feed_that_comes_back_reloads_the_chart_it_left_behind() -> None:
    """A chart cannot catch up on the candles it was not there for.

    The prices that arrive after a reconnect are the ones the feed happens to
    be handed next; whatever the outage swallowed is only in the history. So
    the way back to a current chart is to read the visible range again, and
    nothing but the feed knows when there is a connection to read it with.
    """
    session = configured_spot_session()
    requests: list[ChartRange] = []
    clock = MutableClock(datetime(2026, 1, 1, 10, 3, tzinfo=UTC))
    workflow = ChartWorkflow(
        load_chart=lambda configured, requested: load_snapshot(
            configured, requested, requests
        ),
        thread_pool=ImmediateThreadPool(),  # type: ignore[arg-type]
        clock=clock,
    )
    workflow.start(session)
    assert len(requests) == 1

    clock.value = datetime(2026, 1, 1, 10, 33, tzinfo=UTC)
    workflow.feed_state(ChartFeedState.RECONNECTING)
    workflow.feed_state(ChartFeedState.LIVE)

    assert len(requests) == 2
    # The window the reader is watching is the one ending now, not the one
    # that ended when the connection went: reloading the old window would
    # read the same candles back and leave the chart just as far behind.
    assert requests[1].end == datetime(2026, 1, 1, 10, 30, tzinfo=UTC)


def test_a_price_from_a_reconnected_feed_is_drawn_without_waiting_for_a_close() -> None:
    """The first price back is proof the connection is, and it is drawable.

    A forming candle can only sit at the end of the range the chart holds,
    and an outage leaves that range boundaries behind. So the price that
    arrives the moment the socket is back has nowhere to go, and the chart
    stays frozen until the next candle closes — a minute, or an hour of it
    on the timeframes that trade slowly. Re-anchoring the range on the first
    price back is what puts the current candle on the screen at once.
    """
    session = configured_spot_session()
    snapshots: list[ChartSnapshot] = []
    clock = MutableClock(datetime(2026, 1, 1, 0, 43, tzinfo=UTC))
    workflow = latest_workflow(session=session, snapshots=snapshots, clock=clock)
    workflow.feed_state(ChartFeedState.RECONNECTING)
    clock.value = datetime(2026, 1, 1, 0, 57, tzinfo=UTC)

    workflow.in_progress_candle(candle("5m", 55))

    assert snapshots[-1].chart_range.end == datetime(2026, 1, 1, 0, 55, tzinfo=UTC)
    assert snapshots[-1].in_progress_candle == candle("5m", 55)


def test_a_reconnected_feed_re_anchors_the_chart_only_once_per_candle() -> None:
    """Prices arrive several times a second; the history behind them does not."""
    session = configured_spot_session()
    snapshots: list[ChartSnapshot] = []
    loads: list[ChartRange] = []
    clock = MutableClock(datetime(2026, 1, 1, 0, 43, tzinfo=UTC))
    workflow = latest_workflow(
        session=session,
        snapshots=snapshots,
        clock=clock,
        load_calls=loads,
    )
    workflow.feed_state(ChartFeedState.RECONNECTING)
    clock.value = datetime(2026, 1, 1, 0, 57, tzinfo=UTC)

    workflow.in_progress_candle(candle("5m", 55, close="101"))
    workflow.in_progress_candle(candle("5m", 55, close="102"))
    workflow.in_progress_candle(candle("5m", 55, close="103"))

    assert len(loads) == 1
    assert snapshots[-1].in_progress_candle == candle("5m", 55, close="103")


def test_a_live_feed_does_not_reload_history_for_every_forming_candle() -> None:
    """In the ordinary case nothing here should reach for the network at all."""
    session = configured_spot_session()
    snapshots: list[ChartSnapshot] = []
    loads: list[ChartRange] = []
    clock = MutableClock(datetime(2026, 1, 1, 0, 43, tzinfo=UTC))
    workflow = latest_workflow(
        session=session,
        snapshots=snapshots,
        clock=clock,
        load_calls=loads,
    )
    workflow.feed_state(ChartFeedState.LIVE)
    clock.value = datetime(2026, 1, 1, 0, 57, tzinfo=UTC)

    workflow.in_progress_candle(candle("5m", 55))

    assert loads == []


def test_a_quiet_market_is_not_read_as_a_broken_feed() -> None:
    """A market with nothing to say is not a connection with nothing to say.

    How often prices arrive is a property of how much a Pair is traded, not
    of whether the feed works. Measured on Binance TH: BTCUSDT goes 8-17
    seconds between prices and over 25 at its widest, and ETHUSDT managed one
    price in a minute. A chart that called any of those a fault would spend
    a thin market announcing an outage that never happened, and reloading
    the range each time it decided the feed had come back.

    Whether the connection is alive is answered where it is known: the
    socket's own heartbeat, and the Runtime's staleness deadline counted
    from the boundary a candle was due to close on.
    """
    session = configured_spot_session()
    loads: list[ChartRange] = []
    clock = MutableClock(datetime(2026, 1, 1, 0, 43, tzinfo=UTC))
    workflow = latest_workflow(
        session=session,
        snapshots=[],
        clock=clock,
        load_calls=loads,
    )
    states: list[object] = []
    workflow.feed_state_changed.connect(states.append)
    workflow.feed_state(ChartFeedState.LIVE)

    clock.value += timedelta(minutes=5)

    assert states == [ChartFeedState.LIVE]
    assert loads == []


def test_a_feed_that_was_never_away_does_not_reload_the_chart() -> None:
    """Going live is what a feed does on every start; only coming back counts."""
    session = configured_spot_session()
    requests: list[ChartRange] = []
    workflow = ChartWorkflow(
        load_chart=lambda configured, requested: load_snapshot(
            configured, requested, requests
        ),
        thread_pool=ImmediateThreadPool(),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 1, 1, 10, 3, tzinfo=UTC),
    )
    workflow.start(session)

    workflow.feed_state(ChartFeedState.LIVE)
    workflow.feed_state(ChartFeedState.LIVE)

    assert len(requests) == 1


def test_the_feed_state_the_chart_shows_is_the_one_the_feed_reported() -> None:
    session = configured_spot_session()
    observed: list[object] = []
    workflow = ChartWorkflow(
        load_chart=lambda configured, requested: load_snapshot(
            configured, requested, []
        ),
        thread_pool=ImmediateThreadPool(),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 1, 1, 10, 3, tzinfo=UTC),
    )
    workflow.feed_state_changed.connect(observed.append)
    workflow.start(session)

    workflow.feed_state(ChartFeedState.RECONNECTING)
    workflow.feed_state(ChartFeedState.RECONNECTING)
    workflow.feed_state(ChartFeedState.LIVE)
    workflow.feed_state(ChartFeedState.STOPPED)

    # Repeats say nothing new, and the screen should not flicker on them.
    assert observed == [
        ChartFeedState.RECONNECTING,
        ChartFeedState.LIVE,
        ChartFeedState.STOPPED,
    ]


def test_a_closed_workflow_ignores_a_feed_that_comes_back() -> None:
    session = configured_spot_session()
    requests: list[ChartRange] = []
    workflow = ChartWorkflow(
        load_chart=lambda configured, requested: load_snapshot(
            configured, requested, requests
        ),
        thread_pool=ImmediateThreadPool(),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 1, 1, 10, 3, tzinfo=UTC),
    )
    workflow.start(session)
    workflow.feed_state(ChartFeedState.RECONNECTING)
    workflow.close()

    workflow.feed_state(ChartFeedState.LIVE)

    assert len(requests) == 1


def test_a_state_from_a_replaced_feed_is_not_shown() -> None:
    """The relay's generation is what tells a stale feed from the live one."""
    relay = ChartFactRelay()
    first = relay.new_generation()
    observed: list[tuple[object, int]] = []
    relay.feed_state_ready.connect(
        lambda state, generation: observed.append((state, generation))
    )
    second = relay.new_generation()

    first.feed_state(ChartFeedState.STOPPED)
    second.feed_state(ChartFeedState.LIVE)

    assert [relay.is_current(generation) for _state, generation in observed] == [
        False,
        True,
    ]


def test_start_loads_application_owned_default_visible_range() -> None:
    session = configured_spot_session()
    requests: list[ChartRange] = []
    workflow = ChartWorkflow(
        load_chart=lambda configured, requested: load_snapshot(
            configured, requested, requests
        ),
        thread_pool=ImmediateThreadPool(),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 1, 1, 10, 3, tzinfo=UTC),
    )

    workflow.start(session)

    assert requests == [
        ChartRange(
            datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
            datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
        )
    ]


def test_select_view_loads_the_selected_market_data_identity() -> None:
    session = configured_spot_session()
    identities: list[tuple[str, str]] = []
    workflow = ChartWorkflow(
        load_chart=lambda configured, requested: load_snapshot_for_identity(
            configured, requested, identities
        ),
        thread_pool=ImmediateThreadPool(),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 1, 1, 10, 3, tzinfo=UTC),
    )
    workflow.start(session)

    workflow.select_view(ChartViewSelection(symbol="ETHUSDT", timeframe="1m"))

    assert identities == [("BTCUSDT", "5m"), ("ETHUSDT", "1m")]


def test_another_timeframe_of_the_same_pair_keeps_its_badges() -> None:
    """The same trade, drawn on whichever candles are on screen.

    The view used to be compared against the Session by symbol *and*
    timeframe, so reading BTC at 3m while the Session ran at 1m counted as
    another market and every badge was wiped.
    """
    session = configured_spot_session()
    snapshots: list[ChartSnapshot] = []
    workflow = ChartWorkflow(
        load_chart=load_snapshot_with_a_fill,
        thread_pool=ImmediateThreadPool(),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 1, 1, 1, 0, tzinfo=UTC),
    )
    workflow.snapshot_changed.connect(snapshots.append)
    workflow.start(session)
    assert session.market_data.timeframe != "1m"

    workflow.select_view(ChartViewSelection(symbol="BTCUSDT", timeframe="1m"))

    assert snapshots[-1].timeframe == "1m"
    assert [marker.fill_id for marker in snapshots[-1].markers] == ["fill-1"]


def test_another_pair_never_shows_this_session_badges() -> None:
    """Fills are read by Session, so the chart of another Pair must drop them."""
    session = configured_spot_session()
    snapshots: list[ChartSnapshot] = []
    workflow = ChartWorkflow(
        load_chart=load_snapshot_with_a_fill,
        thread_pool=ImmediateThreadPool(),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 1, 1, 1, 0, tzinfo=UTC),
    )
    workflow.snapshot_changed.connect(snapshots.append)
    workflow.start(session)

    workflow.select_view(ChartViewSelection(symbol="ETHUSDT", timeframe="1m"))

    assert snapshots[-1].symbol == "ETHUSDT"
    assert snapshots[-1].fills == ()
    assert snapshots[-1].markers == ()


def test_configured_session_displays_valid_forming_candle() -> None:
    session = configured_spot_session()
    snapshots: list[ChartSnapshot] = []
    workflow = ChartWorkflow(
        load_chart=lambda configured, requested: load_snapshot(
            configured, requested, []
        ),
        thread_pool=ImmediateThreadPool(),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 1, 1, 0, 23, tzinfo=UTC),
    )
    workflow.snapshot_changed.connect(snapshots.append)
    workflow.configure(session)
    workflow.load_range(chart_range(0, 20))

    forming = candle("5m", 20)
    workflow.in_progress_candle(forming)

    assert snapshots[-1].in_progress_candle == forming


def test_latest_chart_request_wins() -> None:
    session = configured_spot_session()
    first_range = chart_range(0, 20)
    latest_range = chart_range(20, 40)
    calls: list[ChartRange] = []
    snapshots: list[ChartSnapshot] = []
    pool = DeferredThreadPool()
    workflow = ChartWorkflow(
        load_chart=lambda configured, requested: load_snapshot(
            configured, requested, calls
        ),
        thread_pool=pool,  # type: ignore[arg-type]
    )
    workflow.snapshot_changed.connect(snapshots.append)
    workflow.configure(session)

    workflow.load_range(first_range)
    workflow.load_range(latest_range)
    pool.tasks[0].run()
    pool.tasks[1].run()

    assert calls == [first_range, latest_range]
    assert [snapshot.chart_range for snapshot in snapshots] == [
        first_range,
        latest_range,
        latest_range,
    ]
    assert [snapshot.state for snapshot in snapshots] == [
        ChartReadState.LOADING,
        ChartReadState.LOADING,
        ChartReadState.READY,
    ]


def test_load_publishes_loading_snapshot_before_ready_snapshot() -> None:
    session = configured_spot_session()
    selected_range = chart_range(0, 20)
    snapshots: list[ChartSnapshot] = []
    workflow = ChartWorkflow(
        load_chart=lambda configured, requested: load_snapshot(
            configured, requested, []
        ),
        thread_pool=ImmediateThreadPool(),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 1, 1, 1, 0, tzinfo=UTC),
    )
    workflow.snapshot_changed.connect(snapshots.append)
    workflow.configure(session)

    workflow.load_range(selected_range)

    assert [snapshot.state for snapshot in snapshots] == [
        ChartReadState.LOADING,
        ChartReadState.READY,
    ]
    assert all(snapshot.chart_range == selected_range for snapshot in snapshots)


def test_retry_reloads_last_safe_range_after_chart_failure() -> None:
    session = configured_spot_session()
    selected_range = chart_range(0, 20)
    attempts = 0
    snapshots: list[ChartSnapshot] = []

    async def fail_then_load(
        configured: ConfiguredPaperSession,
        requested: ChartRange,
    ) -> ChartSnapshot:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("public transport unavailable")
        return await load_snapshot(configured, requested, [])

    workflow = ChartWorkflow(
        load_chart=fail_then_load,
        thread_pool=ImmediateThreadPool(),  # type: ignore[arg-type]
    )
    workflow.snapshot_changed.connect(snapshots.append)
    workflow.configure(session)
    workflow.load_range(selected_range)

    workflow.retry()

    assert attempts == 2
    assert snapshots[-1].state is ChartReadState.READY
    assert snapshots[-1].chart_range == selected_range


def test_chart_failure_is_unavailable_and_chart_scoped() -> None:
    session = configured_spot_session()
    first_range = chart_range(0, 20)

    unavailable: list[ChartSnapshot] = []
    failed = ChartWorkflow(
        load_chart=lambda configured, requested: fail_load(),
        thread_pool=ImmediateThreadPool(),  # type: ignore[arg-type]
    )
    failed.snapshot_changed.connect(unavailable.append)
    failed.configure(session)
    failed.load_range(first_range)

    assert unavailable[-1].state is ChartReadState.UNAVAILABLE
    assert unavailable[-1].message == "Chart is unavailable"


def test_completed_candle_ignores_other_session_and_updates_current_snapshot() -> None:
    session = configured_spot_session()
    selected_range = chart_range(0, 20)
    snapshots: list[ChartSnapshot] = []
    workflow = ChartWorkflow(
        load_chart=lambda configured, requested: load_snapshot(
            configured, requested, []
        ),
        thread_pool=ImmediateThreadPool(),  # type: ignore[arg-type]
        clock=clock_at_23,
    )
    workflow.snapshot_changed.connect(snapshots.append)
    workflow.configure(session)
    workflow.load_range(selected_range)

    workflow.completed_candle(candle("15m", 15))
    assert len(snapshots) == 2

    workflow.completed_candle(candle("5m", 15))
    assert [item.open_time.minute for item in snapshots[-1].candles] == [0, 15]


def test_completed_candle_refreshes_shifted_latest_range_and_durable_fills() -> None:
    session = configured_spot_session()
    selected_range = chart_range(0, 20)
    refreshed: list[tuple[ChartSnapshot, Candle]] = []
    snapshots: list[ChartSnapshot] = []

    async def refresh_chart(
        configured: ConfiguredPaperSession,
        current: ChartSnapshot,
        completed: Candle,
    ) -> ChartSnapshot:
        assert configured is session
        refreshed.append((current, completed))
        shifted_range = ChartRange(
            datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
            datetime(2026, 1, 1, 0, 25, tzinfo=UTC),
        )
        return ChartSnapshot(
            session=session,
            chart_range=shifted_range,
            observed_at_utc=completed.close_time,
            candles=(completed,),
            fills=(
                trade_fill(
                    fill_id="runtime-buy",
                    side=FillSide.BUY,
                    session_id=session.config.session_id,
                    filled_at_utc=datetime(2026, 1, 1, 0, 21, tzinfo=UTC),
                ),
            ),
            state=ChartReadState.READY,
        )

    workflow = ChartWorkflow(
        load_chart=lambda configured, requested: load_snapshot(
            configured, requested, []
        ),
        refresh_chart=refresh_chart,
        thread_pool=ImmediateThreadPool(),  # type: ignore[arg-type]
        clock=clock_at_23,
    )
    workflow.snapshot_changed.connect(snapshots.append)
    workflow.configure(session)
    workflow.load_range(selected_range)

    completed = candle("5m", 20)
    workflow.completed_candle(completed)

    assert refreshed == [(snapshots[1], completed)]
    assert snapshots[-1].chart_range.end == completed.close_time
    assert snapshots[-1].candles == (completed,)
    assert [marker.fill_id for marker in snapshots[-1].markers] == ["runtime-buy"]


def test_retry_uses_latest_runtime_refreshed_visible_range() -> None:
    session = configured_spot_session()
    initial_range = chart_range(0, 20)
    refreshed_range = chart_range(5, 25)
    load_requests: list[ChartRange] = []

    async def refresh_chart(
        configured: ConfiguredPaperSession,
        current: ChartSnapshot,
        completed: Candle,
    ) -> ChartSnapshot:
        del current
        return ChartSnapshot(
            session=configured,
            chart_range=refreshed_range,
            observed_at_utc=completed.close_time,
            candles=(completed,),
            fills=(),
            state=ChartReadState.READY,
        )

    workflow = ChartWorkflow(
        load_chart=lambda configured, requested: load_snapshot(
            configured, requested, load_requests
        ),
        refresh_chart=refresh_chart,
        thread_pool=ImmediateThreadPool(),  # type: ignore[arg-type]
        clock=clock_at_23,
    )
    workflow.configure(session)
    workflow.load_range(initial_range)

    workflow.completed_candle(candle("5m", 20))
    workflow.retry()

    assert load_requests == [initial_range, refreshed_range]


def test_completed_candle_refresh_keeps_latest_event_while_worker_is_active() -> None:
    session = configured_spot_session()
    selected_range = chart_range(0, 20)
    pool = DeferredThreadPool()
    refreshed_minutes: list[int] = []
    snapshots: list[ChartSnapshot] = []

    async def refresh_chart(
        configured: ConfiguredPaperSession,
        current: ChartSnapshot,
        completed: Candle,
    ) -> ChartSnapshot:
        refreshed_minutes.append(completed.open_time.minute)
        duration = current.chart_range.end - current.chart_range.start
        next_range = ChartRange(completed.close_time - duration, completed.close_time)
        return ChartSnapshot(
            session=configured,
            chart_range=next_range,
            observed_at_utc=completed.close_time,
            candles=(completed,),
            fills=(),
            state=ChartReadState.READY,
        )

    workflow = ChartWorkflow(
        load_chart=lambda configured, requested: load_snapshot(
            configured, requested, []
        ),
        refresh_chart=refresh_chart,
        thread_pool=pool,  # type: ignore[arg-type]
        clock=clock_at_23,
    )
    workflow.snapshot_changed.connect(snapshots.append)
    workflow.configure(session)
    workflow.load_range(selected_range)
    pool.tasks[0].run()

    workflow.completed_candle(candle("5m", 20))
    workflow.completed_candle(candle("5m", 25))
    pool.tasks[1].run()
    pool.tasks[2].run()

    assert refreshed_minutes == [20, 25]
    assert snapshots[-1].candles[0].open_time.minute == 25
    assert snapshots[-1].chart_range.end.minute == 30


def test_completed_candle_gap_advances_and_keeps_following_runtime_updates() -> None:
    session = configured_spot_session()
    selected_range = chart_range(0, 20)
    refreshed_minutes: list[int] = []
    snapshots: list[ChartSnapshot] = []

    async def refresh_chart(
        configured: ConfiguredPaperSession,
        current: ChartSnapshot,
        completed: Candle,
    ) -> ChartSnapshot:
        refreshed_minutes.append(completed.open_time.minute)
        duration = current.chart_range.end - current.chart_range.start
        next_range = ChartRange(completed.close_time - duration, completed.close_time)
        return ChartSnapshot(
            session=configured,
            chart_range=next_range,
            observed_at_utc=completed.close_time,
            candles=(completed,),
            fills=(),
            state=ChartReadState.READY,
        )

    workflow = ChartWorkflow(
        load_chart=lambda configured, requested: load_snapshot(
            configured, requested, []
        ),
        refresh_chart=refresh_chart,
        thread_pool=ImmediateThreadPool(),  # type: ignore[arg-type]
        clock=clock_at_23,
    )
    workflow.snapshot_changed.connect(snapshots.append)
    workflow.configure(session)
    workflow.load_range(selected_range)

    workflow.completed_candle(candle("5m", 25))
    workflow.completed_candle(candle("5m", 30))

    assert refreshed_minutes == [25, 30]
    assert snapshots[-1].chart_range == chart_range(15, 35)
    assert snapshots[-1].candles == (candle("5m", 30),)


def test_partial_completed_candle_fact_cannot_reach_refresh_boundary() -> None:
    session = configured_spot_session()
    selected_range = chart_range(0, 20)
    refreshed: list[object] = []

    async def refresh_chart(
        configured: ConfiguredPaperSession,
        current: ChartSnapshot,
        completed: Candle,
    ) -> ChartSnapshot:
        del configured
        refreshed.append(completed)
        return current

    workflow = ChartWorkflow(
        load_chart=lambda configured, requested: load_snapshot(
            configured, requested, []
        ),
        refresh_chart=refresh_chart,
        thread_pool=ImmediateThreadPool(),  # type: ignore[arg-type]
    )
    workflow.configure(session)
    workflow.load_range(selected_range)

    workflow.completed_candle(PartialCompletedCandle())

    assert refreshed == []


def test_load_queued_during_refresh_is_drained_and_latest_request_wins() -> None:
    session = configured_spot_session()
    initial_range = chart_range(0, 20)
    queued_range = chart_range(20, 40)
    latest_range = chart_range(40, 55)
    calls: list[ChartRange] = []
    pool = DeferredThreadPool()

    async def refresh_chart(
        configured: ConfiguredPaperSession,
        current: ChartSnapshot,
        completed: Candle,
    ) -> ChartSnapshot:
        del configured, completed
        return current

    workflow = ChartWorkflow(
        load_chart=lambda configured, requested: load_snapshot(
            configured, requested, calls
        ),
        refresh_chart=refresh_chart,
        thread_pool=pool,  # type: ignore[arg-type]
        clock=clock_at_23,
    )
    workflow.configure(session)
    workflow.load_range(initial_range)
    pool.tasks[0].run()

    workflow.completed_candle(candle("5m", 20))
    workflow.load_range(queued_range)
    workflow.load_range(latest_range)
    pool.tasks[1].run()
    pool.tasks[2].run()

    assert calls == [initial_range, latest_range]


def test_open_candle_replaces_forming_fact_while_following_latest() -> None:
    session = configured_spot_session()
    snapshots: list[ChartSnapshot] = []
    workflow = latest_workflow(session=session, snapshots=snapshots)
    workflow.runtime_snapshot(runtime_snapshot(session, BotRuntimeState.RUNNING))

    workflow.in_progress_candle(candle("5m", 40, close="102"))
    workflow.in_progress_candle(candle("5m", 40, close="104"))

    assert snapshots[-1].candles == snapshots[-3].candles
    assert snapshots[-1].in_progress_candle == candle("5m", 40, close="104")
    assert len(snapshots[-1].display_candles) == len(snapshots[-1].candles) + 1


def test_open_candle_buffers_latest_fact_while_latest_range_loads() -> None:
    session = configured_spot_session()
    snapshots: list[ChartSnapshot] = []
    pool = DeferredThreadPool()
    workflow = latest_workflow(
        session=session,
        snapshots=snapshots,
        pool=pool,
        run_load=False,
    )
    workflow.runtime_snapshot(runtime_snapshot(session, BotRuntimeState.RUNNING))

    workflow.in_progress_candle(candle("5m", 40, close="102"))
    workflow.in_progress_candle(candle("5m", 40, close="104"))
    pool.tasks[0].run()

    assert snapshots[-1].in_progress_candle == candle("5m", 40, close="104")


def test_previous_range_freezes_until_next_returns_to_latest() -> None:
    session = configured_spot_session()
    snapshots: list[ChartSnapshot] = []
    refresh_calls: list[Candle] = []
    workflow = latest_workflow(
        session=session,
        snapshots=snapshots,
        refresh_calls=refresh_calls,
    )
    workflow.runtime_snapshot(runtime_snapshot(session, BotRuntimeState.RUNNING))

    previous_range = chart_range(0, 20)
    workflow.load_range(previous_range)
    workflow.in_progress_candle(candle("5m", 20))
    workflow.completed_candle(candle("5m", 20))

    assert snapshots[-1].chart_range == previous_range
    assert snapshots[-1].in_progress_candle is None
    assert refresh_calls == []

    workflow.load_range(chart_range(20, 40))
    workflow.in_progress_candle(candle("5m", 40))

    assert snapshots[-1].chart_range == chart_range(20, 40)
    assert snapshots[-1].in_progress_candle == candle("5m", 40)


def test_new_entry_badges_its_fill_before_the_candle_completes() -> None:
    session = configured_spot_session()
    snapshots: list[ChartSnapshot] = []
    fill_ranges: list[ChartRange] = []
    entry = trade_fill(
        session_id=session.config.session_id,
        filled_at_utc=datetime(2026, 1, 1, 0, 41, tzinfo=UTC),
    )
    workflow = latest_workflow(
        session=session,
        snapshots=snapshots,
        fills=(entry,),
        fill_ranges=fill_ranges,
    )
    workflow.runtime_snapshot(runtime_snapshot(session, BotRuntimeState.RUNNING))
    workflow.in_progress_candle(candle("5m", 40))

    workflow.runtime_snapshot(
        runtime_snapshot(session, BotRuntimeState.RUNNING, entry_count=1)
    )

    # The fill sits in the forming candle, so the range read has to reach past
    # the completed candles the chart holds. The forming candle and the new
    # Entry each ask for it, which is one read more than the answer needs.
    assert set(fill_ranges) == {chart_range(20, 45)}
    assert snapshots[-1].fills == (entry,)
    assert [marker.fill_id for marker in snapshots[-1].markers] == [entry.fill_id]
    assert snapshots[-1].in_progress_candle == candle("5m", 40)


def test_a_forming_candle_asks_for_the_fills_inside_it() -> None:
    """A drawn Position keeps its badge when the chart is loaded afresh.

    Fills are read for the completed range, but the forming candle draws
    past it. Only a Basket that changed used to ask again, so a Position
    that merely sat there lost its badge on every timeframe change until
    the new candle closed — moments on 1m, hours on 4h.
    """
    session = configured_spot_session()
    snapshots: list[ChartSnapshot] = []
    fill_ranges: list[ChartRange] = []
    entry = trade_fill(
        session_id=session.config.session_id,
        filled_at_utc=datetime(2026, 1, 1, 0, 41, tzinfo=UTC),
    )
    workflow = latest_workflow(
        session=session,
        snapshots=snapshots,
        fills=(entry,),
        fill_ranges=fill_ranges,
    )
    workflow.runtime_snapshot(runtime_snapshot(session, BotRuntimeState.RUNNING))

    workflow.in_progress_candle(candle("5m", 40))

    assert fill_ranges == [chart_range(20, 45)]
    assert [marker.fill_id for marker in snapshots[-1].markers] == [entry.fill_id]


def test_unchanged_basket_leaves_the_chart_fills_alone() -> None:
    session = configured_spot_session()
    snapshots: list[ChartSnapshot] = []
    fill_ranges: list[ChartRange] = []
    workflow = latest_workflow(
        session=session,
        snapshots=snapshots,
        fill_ranges=fill_ranges,
    )
    workflow.runtime_snapshot(
        runtime_snapshot(session, BotRuntimeState.RUNNING, entry_count=1)
    )
    reads = len(fill_ranges)
    published = len(snapshots)

    workflow.runtime_snapshot(
        runtime_snapshot(session, BotRuntimeState.RUNNING, entry_count=1)
    )

    assert len(fill_ranges) == reads
    assert len(snapshots) == published


def test_stop_keeps_forming_candle_for_chart_only_feed() -> None:
    session = configured_spot_session()
    snapshots: list[ChartSnapshot] = []
    workflow = latest_workflow(session=session, snapshots=snapshots)
    workflow.runtime_snapshot(runtime_snapshot(session, BotRuntimeState.RUNNING))
    workflow.in_progress_candle(candle("5m", 40))
    with_forming = snapshots[-1]

    workflow.runtime_snapshot(runtime_snapshot(session, BotRuntimeState.STOPPED))

    assert snapshots[-1].in_progress_candle == candle("5m", 40)
    assert snapshots[-1].candles == with_forming.candles


def test_completed_candle_clears_matching_forming_before_latest_refresh() -> None:
    session = configured_spot_session()
    snapshots: list[ChartSnapshot] = []
    refresh_calls: list[Candle] = []
    workflow = latest_workflow(
        session=session,
        snapshots=snapshots,
        refresh_calls=refresh_calls,
    )
    workflow.runtime_snapshot(runtime_snapshot(session, BotRuntimeState.RUNNING))
    forming = candle("5m", 40, close="102")
    completed = candle("5m", 40, close="104")
    workflow.in_progress_candle(forming)

    workflow.completed_candle(completed)

    assert refresh_calls == [completed]
    assert snapshots[-2].in_progress_candle is None
    assert snapshots[-1].in_progress_candle is None


def test_pending_completed_then_forming_survives_latest_load_transition() -> None:
    session = configured_spot_session()
    snapshots: list[ChartSnapshot] = []
    pool = DeferredThreadPool()
    clock = MutableClock(datetime(2026, 1, 1, 0, 43, tzinfo=UTC))
    workflow = latest_workflow(
        session=session,
        snapshots=snapshots,
        pool=pool,
        clock=clock,
        run_load=False,
    )
    workflow.runtime_snapshot(runtime_snapshot(session, BotRuntimeState.RUNNING))

    workflow.completed_candle(candle("5m", 40))
    clock.value = datetime(2026, 1, 1, 0, 48, tzinfo=UTC)
    forming = candle("5m", 45, close="102")
    workflow.in_progress_candle(forming)
    pool.tasks[0].run()
    pool.tasks[1].run()

    assert snapshots[-1].chart_range == chart_range(25, 45)
    assert snapshots[-1].in_progress_candle == forming


def test_pending_completed_then_forming_survives_latest_refresh_transition() -> None:
    session = configured_spot_session()
    snapshots: list[ChartSnapshot] = []
    pool = DeferredThreadPool()
    clock = MutableClock(datetime(2026, 1, 1, 0, 43, tzinfo=UTC))
    workflow = latest_workflow(
        session=session,
        snapshots=snapshots,
        pool=pool,
        clock=clock,
    )
    workflow.runtime_snapshot(runtime_snapshot(session, BotRuntimeState.RUNNING))

    workflow.completed_candle(candle("5m", 40))
    clock.value = datetime(2026, 1, 1, 0, 53, tzinfo=UTC)
    workflow.completed_candle(candle("5m", 45))
    forming = candle("5m", 50, close="102")
    workflow.in_progress_candle(forming)
    pool.tasks[1].run()
    pool.tasks[2].run()

    assert snapshots[-1].chart_range == chart_range(30, 50)
    assert snapshots[-1].in_progress_candle == forming


def test_close_clears_forming_snapshot_but_keeps_completed_history() -> None:
    session = configured_spot_session()
    snapshots: list[ChartSnapshot] = []
    workflow = latest_workflow(session=session, snapshots=snapshots)
    workflow.runtime_snapshot(runtime_snapshot(session, BotRuntimeState.RUNNING))
    workflow.in_progress_candle(candle("5m", 40))
    with_forming = snapshots[-1]

    workflow.close()

    assert workflow._snapshot is not None
    assert workflow._snapshot.in_progress_candle is None
    assert workflow._snapshot.candles == with_forming.candles


def latest_workflow(
    *,
    session: ConfiguredPaperSession,
    snapshots: list[ChartSnapshot],
    refresh_calls: list[Candle] | None = None,
    pool: ImmediateThreadPool | DeferredThreadPool | None = None,
    clock: MutableClock | None = None,
    run_load: bool = True,
    fills: tuple[TradeFill, ...] = (),
    fill_ranges: list[ChartRange] | None = None,
    load_calls: list[ChartRange] | None = None,
) -> ChartWorkflow:
    selected_pool = pool or ImmediateThreadPool()

    async def load_fills(
        configured: ConfiguredPaperSession,
        requested: ChartRange,
    ) -> tuple[TradeFill, ...]:
        assert configured is session
        if fill_ranges is not None:
            fill_ranges.append(requested)
        return fills

    async def refresh_chart(
        configured: ConfiguredPaperSession,
        current: ChartSnapshot,
        completed: Candle,
    ) -> ChartSnapshot:
        assert configured is session
        if refresh_calls is not None:
            refresh_calls.append(completed)
        duration = current.chart_range.end - current.chart_range.start
        return ChartSnapshot(
            session=configured,
            chart_range=ChartRange(
                completed.close_time - duration,
                completed.close_time,
            ),
            observed_at_utc=completed.close_time,
            candles=(completed,),
            fills=(),
            state=ChartReadState.READY,
        )

    workflow = ChartWorkflow(
        load_chart=lambda configured, requested: load_snapshot(
            configured,
            requested,
            [] if load_calls is None else load_calls,
        ),
        refresh_chart=refresh_chart,
        load_fills=load_fills,
        thread_pool=selected_pool,  # type: ignore[arg-type]
        clock=(
            clock
            if clock is not None
            else lambda: datetime(2026, 1, 1, 0, 43, tzinfo=UTC)
        ),
    )
    workflow.snapshot_changed.connect(snapshots.append)
    workflow.configure(session)
    workflow.load_range(chart_range(20, 40))
    if run_load and isinstance(selected_pool, DeferredThreadPool):
        selected_pool.tasks[0].run()
    if load_calls is not None:
        load_calls.clear()
    return workflow


def runtime_snapshot(
    session: ConfiguredPaperSession,
    state: BotRuntimeState,
    *,
    entry_count: int | None = None,
) -> BotControlSnapshot:
    workspace = _configured_workspace(session)
    if entry_count is not None:
        workspace = replace(
            workspace,
            position_basket=ready_position_basket_tab(
                open_basket(session, entry_count),
                observed_at_utc=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
            ),
        )
    configured = workspace_with_runtime_state(
        workspace,
        state,
        data_freshness=(
            DataFreshness.FRESH
            if state is BotRuntimeState.RUNNING
            else DataFreshness.NOT_STARTED
        ),
    )
    return BotControlSnapshot(
        state=state,
        session=session,
        workspace=configured,
        available_actions=(
            frozenset({BotControlAction.STOP})
            if state is BotRuntimeState.RUNNING
            else frozenset()
        ),
    )


def open_basket(session: ConfiguredPaperSession, entry_count: int) -> BasketSnapshot:
    return BasketSnapshot(
        symbol=session.market_data.symbol,
        market_type=session.config.market_type,
        entry_count=entry_count,
        entries=basket_entries(
            *("100",) * entry_count, quantity="1", current_price="101"
        ),
        total_quantity=Decimal(entry_count),
        average_entry_price=Decimal("100"),
        current_price=Decimal("101"),
        take_profit_price=Decimal("110"),
        targets=basket_targets(
            basket_entries(*("100",) * entry_count, quantity="1", current_price="101")
        ),
        unrealized_pnl=Decimal("1"),
        entry_gate=BasketEntryGate.OPEN,
        updated_at_utc=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
    )


def _configured_workspace(
    session: ConfiguredPaperSession,
) -> TradingWorkspaceSnapshot:
    return configured_workspace_snapshot(
        session,
        observed_at_utc=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
    )


async def load_snapshot(
    session: ConfiguredPaperSession,
    chart_range: ChartRange,
    calls: list[ChartRange],
) -> ChartSnapshot:
    calls.append(chart_range)
    return ChartSnapshot(
        session=session,
        chart_range=chart_range,
        observed_at_utc=max(datetime(2026, 1, 1, 1, 0, tzinfo=UTC), chart_range.end),
        # Built from the range it was asked for, so a loader answering a
        # window anywhere in time answers with a candle inside it.
        candles=(candle_opening_at(chart_range.start),),
        fills=(),
        state=ChartReadState.READY,
    )


async def load_snapshot_with_a_fill(
    session: ConfiguredPaperSession,
    chart_range: ChartRange,
) -> ChartSnapshot:
    """What the real loader does: read fills by Session, whatever the view.

    ChartHistory.load asks for fills with the Session id and the range, so
    they arrive on a chart of any timeframe — and of any Pair, which is why
    the workflow has to drop them itself.
    """
    interval = timeframe_to_interval(session.market_data.timeframe)
    last_open = chart_range.end - interval
    return ChartSnapshot(
        session=session,
        chart_range=chart_range,
        observed_at_utc=chart_range.end,
        candles=(candle(session.market_data.timeframe, last_open.minute),),
        fills=(
            trade_fill(session_id=session.config.session_id, filled_at_utc=last_open),
        ),
        state=ChartReadState.READY,
    )


async def load_snapshot_for_identity(
    session: ConfiguredPaperSession,
    chart_range: ChartRange,
    identities: list[tuple[str, str]],
) -> ChartSnapshot:
    identities.append((session.market_data.symbol, session.market_data.timeframe))
    return ChartSnapshot(
        session=session,
        chart_range=chart_range,
        observed_at_utc=max(datetime(2026, 1, 1, 11, tzinfo=UTC), chart_range.end),
        candles=(),
        fills=(),
        state=ChartReadState.EMPTY,
    )


async def fail_load() -> ChartSnapshot:
    raise RuntimeError("transport details must not reach the chart")


def clock_at_23() -> datetime:
    return datetime(2026, 1, 1, 0, 23, tzinfo=UTC)


def chart_range(start_minute: int, end_minute: int) -> ChartRange:
    return ChartRange(
        datetime(2026, 1, 1, 0, start_minute, tzinfo=UTC),
        datetime(2026, 1, 1, 0, end_minute, tzinfo=UTC),
    )


def candle(timeframe: str, minute: int, *, close: str = "103") -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timeframe=timeframe,
        open_time=datetime(2026, 1, 1, 0, minute, tzinfo=UTC),
        open=Decimal("100"),
        high=Decimal("106"),
        low=Decimal("99"),
        close=Decimal(close),
    )


def candle_opening_at(open_time: datetime, *, close: str = "103") -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timeframe="5m",
        open_time=open_time,
        open=Decimal("100"),
        high=Decimal("106"),
        low=Decimal("99"),
        close=Decimal(close),
    )


def test_a_chart_panned_off_the_latest_says_so_and_can_be_sent_back() -> None:
    """A chart that stops taking live prices reads as a fault unless it says.

    The Position tab goes on marking on prices this chart is no longer
    showing, so the two disagree by design — which is only tolerable while
    the screen admits it, and offers the way back.
    """
    session = configured_spot_session()
    requests: list[ChartRange] = []
    following: list[bool] = []
    workflow = ChartWorkflow(
        load_chart=lambda configured, requested: load_snapshot(
            configured, requested, requests
        ),
        thread_pool=ImmediateThreadPool(),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 1, 1, 10, 3, tzinfo=UTC),
    )
    workflow.following_latest_changed.connect(following.append)
    workflow.start(session)
    assert workflow.following_latest
    assert following == []

    workflow.load_range(
        ChartRange(
            datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
            datetime(2026, 1, 1, 5, 0, tzinfo=UTC),
        )
    )

    assert not workflow.following_latest
    assert following == [False]

    workflow.follow_latest()

    assert workflow.following_latest
    assert following == [False, True]
    # Back on the range the chart opens at, which is the one that follows.
    assert requests[-1] == requests[0]


def test_the_same_following_state_is_not_announced_twice() -> None:
    """A signal for every load would flicker the way back on and off."""
    session = configured_spot_session()
    requests: list[ChartRange] = []
    following: list[bool] = []
    workflow = ChartWorkflow(
        load_chart=lambda configured, requested: load_snapshot(
            configured, requested, requests
        ),
        thread_pool=ImmediateThreadPool(),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 1, 1, 10, 3, tzinfo=UTC),
    )
    workflow.start(session)
    workflow.following_latest_changed.connect(following.append)

    for _ in range(2):
        workflow.load_range(
            ChartRange(
                datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
                datetime(2026, 1, 1, 5, 0, tzinfo=UTC),
            )
        )

    assert following == [False]
