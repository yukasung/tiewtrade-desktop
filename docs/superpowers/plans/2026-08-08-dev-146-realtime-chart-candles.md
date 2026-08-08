# Real-time Chart Candles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** แสดงแท่ง Candlestick ที่กำลังก่อตัวทันทีหลัง Start Bot โดยใช้ WebSocket สายเดียวกับ Paper Runtime และไม่ให้แท่งที่ยังไม่ปิดเข้าถึง trading path

**Architecture:** เพิ่ม immutable MarketDataKlineUpdate เพื่อแยก open/closed kline ให้ชัดเจนตั้งแต่ adapter. MarketDataRuntime ส่ง open candle ไปยัง presentation observer ที่แยกจาก CompletedCandlePipeline; Paper Runtime relay ไป ChartWorkflow แบบ generation-bound. Chart เก็บ forming candle แยกจาก completed candles และแสดงเฉพาะตอนตาม latest range.

**Tech Stack:** Python 3.12, asyncio, aiohttp, PySide6, pytest, ruff, mypy

## Global Constraints

- ใช้ Paper Trading และ deterministic fake adapters/schedulers ใน tests เท่านั้น; ห้าม Live order หรือ credentials.
- ห้ามเพิ่ม API key, secret, volume field หรือ column Candle.volume.
- strategy, indicator, risk, persistence และ execution รับเฉพาะ closed/completed Candle.
- ใช้ Binance WebSocket connection เดียวกับ Paper Runtime; ห้ามสร้าง chart WebSocket หรือ REST polling ใหม่.
- ChartSnapshot.candles เป็น completed candles ภายใน half-open ChartRange เท่านั้น; forming candle อยู่ใน optional in_progress_candle.
- ไม่มี LIVE label, opacity พิเศษ, volume panel หรือการเปลี่ยน header range semantics.
- Previous ต้องคง historical view; Next กลับ latest จึงรับ forming update ใหม่.
- Stop, Blocked, เปลี่ยน session, loading, unavailable, retry และ close ต้องล้าง forming candle โดยคง completed history.
- chart observer exception ต้องไม่ทำให้ Paper Runtime fail closed.

---

## File Structure

| File | Responsibility |
| --- | --- |
| src/tiewtrade/market_data/candle_source.py | immutable WebSocket update contract |
| src/tiewtrade/integrations/binance/kline_parser.py | parse Binance open/closed kline โดยไม่อ่าน volume |
| src/tiewtrade/integrations/binance/public_market_data.py | yield ทุก update จาก WebSocket เดิม |
| src/tiewtrade/market_data/runtime.py | route open ไป presentation, closed ไป completed pipeline |
| src/tiewtrade/application/paper_runtime.py | best-effort bridge ไป UI |
| src/tiewtrade/ui/bot_lifecycle_workflow.py, src/tiewtrade/desktop_main.py, src/tiewtrade/ui/main_window.py | Qt generation relay และ lifecycle wiring |
| src/tiewtrade/application/chart_data.py, src/tiewtrade/ui/chart_workflow.py | forming read-model และ follow-latest state |
| src/tiewtrade/ui/candlestick_chart.py | render display candles บน visual range ที่ขยาย 1 timeframe |

### Task 1: Expose both WebSocket kline states

**Files:**
- Modify: src/tiewtrade/market_data/candle_source.py
- Modify: src/tiewtrade/integrations/binance/kline_parser.py
- Modify: src/tiewtrade/integrations/binance/public_market_data.py
- Test: tests/unit/integrations/binance/test_kline_parser.py
- Test: tests/unit/integrations/binance/test_public_market_data.py

**Interfaces:**
- Produces MarketDataKlineUpdate(candle: Candle, is_closed: bool).
- Replaces LiveCandleSource.stream_completed with stream_klines(config: MarketDataConfig) -> AsyncIterator[MarketDataKlineUpdate].
- Historical load_recent and load_range stay unchanged and return completed Candle values.

- [ ] **Step 1: Write failing parser and adapter tests**

~~~python
def test_open_websocket_kline_maps_to_open_update() -> None:
    update = parse_websocket_kline(open_kline_payload(), config())

    assert update.is_closed is False
    assert update.candle.open_time == datetime(2026, 1, 1, tzinfo=UTC)
    assert not hasattr(update.candle, "volume")


def test_stream_klines_yields_open_then_closed_from_one_websocket() -> None:
    source, session = source_with(
        websocket_payloads=[websocket_kline(closed=False), websocket_kline(closed=True)]
    )

    updates = asyncio.run(collect_updates(source))

    assert [update.is_closed for update in updates] == [False, True]
    assert session.websocket_urls == [
        "wss://data-stream.binance.vision/ws/btcusdt@kline_5m"
    ]
~~~

- [ ] **Step 2: Verify RED**

Run: pytest tests/unit/integrations/binance/test_kline_parser.py tests/unit/integrations/binance/test_public_market_data.py -k 'open_websocket or stream_klines' -v

Expected: FAIL because open parser result and stream_klines do not exist.

- [ ] **Step 3: Implement the immutable contract**

~~~python
# candle_source.py
@dataclass(frozen=True, slots=True)
class MarketDataKlineUpdate:
    candle: Candle
    is_closed: bool


class LiveCandleSource(Protocol):
    def stream_klines(
        self, config: MarketDataConfig
    ) -> AsyncIterator[MarketDataKlineUpdate]: ...
~~~

~~~python
# kline_parser.py, after existing k.x validation and _candle_from_values call
return MarketDataKlineUpdate(candle=candle, is_closed=is_closed)
~~~

~~~python
# public_market_data.py
async def _stream_klines(
    self, config: MarketDataConfig
) -> AsyncIterator[MarketDataKlineUpdate]:
    # Retain existing ws_connect URL, close/error mapping, and payload validation.
    async for message in websocket:
        if message.type is aiohttp.WSMsgType.TEXT:
            yield parse_websocket_kline(json.loads(message.data), config)

def stream_klines(
    self, config: MarketDataConfig
) -> AsyncIterator[MarketDataKlineUpdate]:
    return self._stream_klines(config)
~~~

- [ ] **Step 4: Verify GREEN and commit**

Run: pytest tests/unit/integrations/binance/test_kline_parser.py tests/unit/integrations/binance/test_public_market_data.py -v

Expected: PASS; REST behavior and one selected WebSocket URL are unchanged.

~~~bash
git add src/tiewtrade/market_data/candle_source.py src/tiewtrade/integrations/binance/kline_parser.py src/tiewtrade/integrations/binance/public_market_data.py tests/unit/integrations/binance/test_kline_parser.py tests/unit/integrations/binance/test_public_market_data.py
git commit -m "feat: expose open market-data kline updates"
~~~

### Task 2: Preserve the completed-candle safety boundary in runtime

**Files:**
- Modify: src/tiewtrade/market_data/runtime.py
- Test: tests/unit/market_data/test_runtime.py
- Modify test sources in: tests/acceptance/test_public_market_data_runtime.py, tests/acceptance/test_paper_futures_session.py, tests/acceptance/test_desktop_session_setup.py, tests/unit/application/test_paper_runtime.py, tests/unit/application/test_public_market_data_runtime_composition.py, tests/acceptance/test_desktop_chart.py

**Interfaces:**
- Adds on_open_candle: Callable[[Candle], None] | None = None to MarketDataRuntime.
- MarketDataCandleSink remains unchanged and receives closed candles only.
- Every fake implements stream_klines; completed fixture output is MarketDataKlineUpdate(candle=candle, is_closed=True).

- [ ] **Step 1: Write failing runtime tests**

~~~python
def test_open_kline_notifies_presentation_without_completed_pipeline() -> None:
    observed: list[Candle] = []
    runtime = runtime_for(
        source=FakeSource(live_updates=(MarketDataKlineUpdate(candle(15), False),)),
        on_open_candle=observed.append,
    )

    run_until_first_live_update(runtime)

    assert observed == [candle(15)]
    assert sink.live_candles == []
    assert runtime.snapshot.last_accepted_open_time == warm_up_candle.open_time


def test_open_kline_observer_failure_does_not_fail_runtime() -> None:
    runtime = runtime_for(
        source=FakeSource(live_updates=(MarketDataKlineUpdate(candle(15), False),)),
        on_open_candle=lambda candle: (_ for _ in ()).throw(RuntimeError("chart down")),
    )

    run_until_first_live_update(runtime)

    assert runtime.snapshot.state is MarketDataRuntimeState.LIVE
~~~

- [ ] **Step 2: Verify RED**

Run: pytest tests/unit/market_data/test_runtime.py -k open_kline -v

Expected: FAIL because live consumption still calls stream_completed.

- [ ] **Step 3: Route the explicit fact**

~~~python
async def _consume_live(self) -> None:
    stream = self._source.stream_klines(self._config)
    while not self._stop_requested:
        update = await self._next_before_stale(stream)
        if not update.is_closed:
            self._publish_open_candle(update.candle)
            continue
        if not await self._accept_live_candle(update.candle):
            return

def _publish_open_candle(self, candle: Candle) -> None:
    callback = self._on_open_candle
    if callback is None:
        return
    try:
        callback(candle)
    except Exception:
        pass
~~~

Change _next_before_stale, _next_reconnect_candle, and _recover_stream to AsyncIterator[MarketDataKlineUpdate]. In reconnect, loop over open updates (publish them), then use the first closed update only for backfill. Do not call _accept_live_candle, update freshness, or invoke a sink for open updates.

- [ ] **Step 4: Verify GREEN and commit**

Run: pytest tests/unit/market_data/test_runtime.py tests/acceptance/test_public_market_data_runtime.py tests/acceptance/test_paper_futures_session.py tests/acceptance/test_desktop_session_setup.py tests/unit/application/test_paper_runtime.py tests/unit/application/test_public_market_data_runtime_composition.py tests/acceptance/test_desktop_chart.py -v

Expected: PASS; open candles never reach strategy/execution/persistence and reconnect/backfill continue from closed candles.

~~~bash
git add src/tiewtrade/market_data/runtime.py tests/unit/market_data/test_runtime.py tests/acceptance/test_public_market_data_runtime.py tests/acceptance/test_paper_futures_session.py tests/acceptance/test_desktop_session_setup.py tests/unit/application/test_paper_runtime.py tests/unit/application/test_public_market_data_runtime_composition.py tests/acceptance/test_desktop_chart.py
git commit -m "feat: route open candles to presentation only"
~~~

### Task 3: Relay forming candle facts to the Qt chart owner

**Files:**
- Modify: src/tiewtrade/application/paper_runtime.py
- Modify: src/tiewtrade/ui/bot_lifecycle_workflow.py
- Modify: src/tiewtrade/desktop_main.py
- Modify: src/tiewtrade/ui/main_window.py
- Test: tests/unit/application/test_paper_runtime.py
- Test: tests/unit/ui/test_bot_lifecycle_workflow.py
- Test: tests/acceptance/test_desktop_chart.py

**Interfaces:**
- PaperRuntimeController(..., in_progress_candle_callback: Callable[[Candle], None] | None = None).
- RuntimeSnapshotPublisher.in_progress_candle(candle: ChartCandle) -> None.
- RuntimeSnapshotRelay.in_progress_candle_ready = Signal(object, int).
- MainWindow._runtime_in_progress_candle(candle: object, generation: int) -> None.

- [ ] **Step 1: Write failing relay tests**

~~~python
def test_runtime_publisher_emits_in_progress_candle_with_generation() -> None:
    relay = RuntimeSnapshotRelay()
    received: list[tuple[ChartCandle, int]] = []
    relay.in_progress_candle_ready.connect(
        lambda candle, generation: received.append((candle, generation))
    )

    relay.new_generation().in_progress_candle(chart_candle())

    assert received == [(chart_candle(), 1)]
~~~

- [ ] **Step 2: Verify RED**

Run: pytest tests/unit/ui/test_bot_lifecycle_workflow.py -k in_progress -v

Expected: FAIL because the signal and publisher method do not exist.

- [ ] **Step 3: Add the best-effort, generation-bound bridge**

~~~python
class RuntimeSnapshotPublisher:
    def in_progress_candle(self, candle: ChartCandle) -> None:
        self._relay.in_progress_candle_ready.emit(candle, self._generation)

class RuntimeSnapshotRelay(QObject):
    in_progress_candle_ready = Signal(object, int)

def _publish_in_progress_candle(self, candle: Candle) -> None:
    callback = self._in_progress_candle_callback
    if callback is None:
        return
    try:
        callback(candle)
    except Exception:
        pass
~~~

Pass on_open_candle=self._publish_in_progress_candle to MarketDataRuntime; pass in_progress_candle_callback=publish.in_progress_candle when desktop creates the controller. Connect the new relay with Qt.ConnectionType.QueuedConnection, validate relay generation exactly like completed candles, then call ChartWorkflow.in_progress_candle. Do not update any widget from the runtime thread.

- [ ] **Step 4: Verify GREEN and commit**

Run: pytest tests/unit/application/test_paper_runtime.py tests/unit/ui/test_bot_lifecycle_workflow.py tests/acceptance/test_desktop_chart.py -v

Expected: PASS; generation filtering and observer isolation work for both candle states.

~~~bash
git add src/tiewtrade/application/paper_runtime.py src/tiewtrade/ui/bot_lifecycle_workflow.py src/tiewtrade/desktop_main.py src/tiewtrade/ui/main_window.py tests/unit/application/test_paper_runtime.py tests/unit/ui/test_bot_lifecycle_workflow.py tests/acceptance/test_desktop_chart.py
git commit -m "feat: relay in-progress candles to chart workflow"
~~~

### Task 4: Keep forming state separate and follow only the latest range

**Files:**
- Modify: src/tiewtrade/application/chart_data.py
- Modify: src/tiewtrade/ui/chart_workflow.py
- Modify: src/tiewtrade/ui/main_window.py
- Test: tests/unit/application/test_chart_data.py
- Test: tests/unit/ui/test_chart_workflow.py
- Test: tests/acceptance/test_desktop_chart.py

**Interfaces:**
- ChartSnapshot.in_progress_candle: Candle | None = None.
- ChartSnapshot.display_candles -> tuple[Candle, ...].
- ChartWorkflow.in_progress_candle(candle: object) -> None.
- ChartWorkflow.runtime_snapshot(snapshot: object) -> None.

- [ ] **Step 1: Write failing read-model and workflow tests**

~~~python
def test_snapshot_separates_completed_and_forming_candle() -> None:
    snapshot = ready_chart_snapshot(
        candles=(candle("5m", minute=15),),
        in_progress_candle=candle("5m", minute=20),
    )

    assert snapshot.candles == (candle("5m", minute=15),)
    assert snapshot.display_candles == (
        candle("5m", minute=15), candle("5m", minute=20)
    )


def test_previous_range_freezes_until_next_returns_to_latest() -> None:
    workflow, snapshots, refresh_calls = configured_latest_workflow()
    workflow.runtime_snapshot(running_bot_control_snapshot())

    workflow.load_range(chart_range(0, 20))
    workflow.in_progress_candle(candle("5m", 20))
    workflow.completed_candle(candle("5m", 20))

    assert snapshots[-1].chart_range == chart_range(0, 20)
    assert snapshots[-1].in_progress_candle is None
    assert refresh_calls == []


def test_stop_clears_forming_but_keeps_completed_history() -> None:
    workflow, snapshots, _ = configured_latest_workflow()
    workflow.runtime_snapshot(running_bot_control_snapshot())
    workflow.in_progress_candle(candle("5m", 20))

    workflow.runtime_snapshot(stopped_bot_control_snapshot())

    assert snapshots[-1].in_progress_candle is None
    assert snapshots[-1].candles == snapshots[-2].candles
~~~

- [ ] **Step 2: Verify RED**

Run: pytest tests/unit/application/test_chart_data.py -k 'forming or in_progress' -v && pytest tests/unit/ui/test_chart_workflow.py -k 'previous_range or stop_clears or open_candle' -v

Expected: FAIL because snapshots and workflow have no forming/follow-latest state.

- [ ] **Step 3: Implement validated transient state**

~~~python
@dataclass(frozen=True, slots=True)
class ChartSnapshot:
    in_progress_candle: Candle | None = None

    @property
    def display_candles(self) -> tuple[Candle, ...]:
        return (
            self.candles
            if self.in_progress_candle is None
            else (*self.candles, self.in_progress_candle)
        )
~~~

Validate forming state in ChartSnapshot.__post_init__: READY/EMPTY only; instance of Candle; symbol/timeframe match; open_time == chart_range.end; and open_time <= observed_at_utc < close_time. Existing completed candle validation must remain unchanged.

In ChartWorkflow, introduce _following_latest, _runtime_running, and _pending_in_progress_candle. start/configure sets following true, running false, and clears pending state. load_range sets following true only when chart_range.end == latest_completed_boundary(timeframe, clock()), then clears forming state before loading. runtime_snapshot sets running only for BotRuntimeState.RUNNING; all other lifecycle states clear forming/pending state. in_progress_candle accepts a current-session fact only when running/following latest, buffers newest valid fact while loading, and otherwise reconstructs/emits a snapshot with observed_at_utc=max(clock(), chart_range.end).

Before completed refresh, clear matching forming state. If _following_latest is false, return after clearing: this prevents ChartHistory.refresh_completed from pulling the user from Previous history. Keep existing refresh behavior only when following latest. Clear state on loading/unavailable/retry/close/session replacement. Connect BotLifecycleWorkflow.snapshot_changed to ChartWorkflow.runtime_snapshot in MainWindow.

- [ ] **Step 4: Verify GREEN and commit**

Run: pytest tests/unit/application/test_chart_data.py tests/unit/ui/test_chart_workflow.py tests/acceptance/test_desktop_chart.py -v

Expected: PASS; completed data stays completed-only, history remains frozen, and lifecycle stop clears only the transient fact.

~~~bash
git add src/tiewtrade/application/chart_data.py src/tiewtrade/ui/chart_workflow.py src/tiewtrade/ui/main_window.py tests/unit/application/test_chart_data.py tests/unit/ui/test_chart_workflow.py tests/acceptance/test_desktop_chart.py
git commit -m "feat: show forming candle while following latest chart"
~~~

### Task 5: Render display candles with normal styling

**Files:**
- Modify: src/tiewtrade/ui/candlestick_chart.py
- Test: tests/unit/ui/test_candlestick_chart.py
- Test: tests/acceptance/test_desktop_chart.py

**Interfaces:**
- Consumes ChartSnapshot.display_candles; chart header keeps snapshot.chart_range.
- _build_painter_model(snapshot, surface) remains the renderer test seam.

- [ ] **Step 1: Write failing renderer tests**

~~~python
def test_painter_draws_forming_candle_in_normal_bullish_style() -> None:
    model = _build_painter_model(
        ready_snapshot(
            candles=(candle(15, close="101"),),
            in_progress_candle=candle(20, close="103"),
        ),
        QRectF(0, 0, 600, 300),
    )

    assert len(model.candles) == 2
    assert model.candles[-1].color == QColor("#0ECB81")
    assert model.candles[-1].body.center().x() < 600
~~~

- [ ] **Step 2: Verify RED**

Run: pytest tests/unit/ui/test_candlestick_chart.py -k forming -v

Expected: FAIL because the painter only reads snapshot.candles.

- [ ] **Step 3: Render the optional next interval**

~~~python
display_candles = snapshot.display_candles
visual_range = snapshot.chart_range
if snapshot.in_progress_candle is not None:
    visual_range = ChartRange(
        start=snapshot.chart_range.start,
        end=snapshot.chart_range.end + timeframe_to_interval(snapshot.timeframe),
    )
prices = [
    price for candle in display_candles for price in (candle.high, candle.low)
] + [marker.price for marker in snapshot.markers]
for candle in display_candles:
    x = _marker_x(candle.open_time, visual_range, surface)
~~~

Use display_candles in the empty-state check and body width calculation. Retain existing red/green color, wick/body drawing, grid, markers, and range header. Markers continue using completed chart_range; do not add labels, opacity, volume UI, polling, timers, or network code.

- [ ] **Step 4: Verify GREEN and commit**

Run: pytest tests/unit/ui/test_candlestick_chart.py tests/acceptance/test_desktop_chart.py -v

Expected: PASS; forming high/low affects scale and the candle is visible without a special visual treatment.

~~~bash
git add src/tiewtrade/ui/candlestick_chart.py tests/unit/ui/test_candlestick_chart.py tests/acceptance/test_desktop_chart.py
git commit -m "feat: render forming candle on chart"
~~~

### Task 6: Verify the application flow and safety invariant

**Files:**
- Modify if needed: tests/acceptance/test_desktop_chart.py

**Interfaces:**
- Consumes all Task 1–5 contracts; no production API is added.

- [ ] **Step 1: Add a deterministic acceptance test**

~~~python
def test_running_paper_session_shows_open_candle_then_commits_closed_candle(qtbot) -> None:
    # Use the existing fake session and fake source, never a network source.
    emit_open_then_closed_same_interval()

    assert chart_snapshots[-2].in_progress_candle == open_candle
    assert chart_snapshots[-2].candles == completed_before_open
    assert chart_snapshots[-1].in_progress_candle is None
    assert processed_strategy_candles == [closed_candle]
~~~

- [ ] **Step 2: Run focused acceptance and full verification**

Run: pytest tests/acceptance/test_desktop_chart.py -k 'open_candle_then_commits' -v

Expected: PASS.

Run: pytest

Expected: PASS.

Run: ruff check src tests && ruff format --check src tests && mypy src && git diff --check

Expected: no lint, format, type, or whitespace errors.

- [ ] **Step 3: Audit route ownership and commit test changes**

Run: rg -n "stream_completed|stream_klines|on_open_candle|in_progress_candle|process_live" src tests

Expected: only closed MarketDataKlineUpdate values can reach process_live, strategy, persistence, and execution; open values reach the presentation callback only.

~~~bash
git add tests/acceptance/test_desktop_chart.py
git commit -m "test: verify real-time chart candle flow"
~~~

Do not create an empty commit when no test file changed.

## Plan Self-Review

- Coverage: Tasks 1–3 provide one-connection open data flow with observer isolation; Task 4 protects completed snapshot semantics, follow-latest navigation, buffering, and lifecycle clearing; Task 5 provides ordinary rendering; Task 6 verifies the full safety boundary.
- Exclusions: no independent WebSocket, REST polling, live execution, volume storage/UI, timer refresh, or LIVE badge.
- Type consistency: MarketDataKlineUpdate is defined in Task 1, consumed by Task 2, bridged as a Candle in Task 3, stored as ChartSnapshot.in_progress_candle in Task 4, and rendered through display_candles in Task 5.

