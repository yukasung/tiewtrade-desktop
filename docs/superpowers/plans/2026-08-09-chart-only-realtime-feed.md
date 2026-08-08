# Chart-only Real-time Feed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the current Session's forming and completed candles immediately at Desktop startup without starting Paper Bot trading.

**Architecture:** Add an application-owned `ChartOnlyFeedController` around the existing public `MarketDataRuntime`, with a sink that publishes immutable `Candle` facts only. A dedicated generation-bound Qt `ChartFactRelay` forwards those facts to `ChartWorkflow`; it replaces the Bot Runtime relay as the chart UI's source. Desktop composition starts/stops the feed with Session/UI lifecycle, independently of Bot Start/Stop.

**Tech Stack:** Python 3.12, asyncio, threads, PySide6 signals, existing `MarketDataRuntime`, pytest, Ruff, Mypy.

## Global Constraints

- Use Paper Trading and fake public adapters only; do not use credentials, private endpoints, or Live orders.
- `ChartOnlyFeedController` must not import Strategy, execution, SQLite lifecycle, `PaperRuntimeController`, or UI modules.
- Forming candle facts are in-memory/read-only and must not reach persistence, execution, replay, or lifecycle decisions.
- Preserve the existing `ConfiguredPaperSession` symbol/timeframe validation and generation safety.
- All user-visible UI copy remains English; no database migration or Bot lifecycle state change belongs in this work.

---

## File structure

| File | Responsibility |
| --- | --- |
| `src/tiewtrade/application/chart_realtime_feed.py` | Read-only public market-data runtime thread, fact sink, start/replace/stop lifecycle. |
| `src/tiewtrade/ui/chart_workflow.py` | Generation-bound Qt chart-fact relay and current-session fact handling without Bot-state gating. |
| `src/tiewtrade/ui/main_window.py` | Starts chart history/feed on Session ready, routes chart facts, stops feed before close. |
| `src/tiewtrade/ui/desktop.py` | Passes chart-feed lifecycle dependencies to `MainWindow`. |
| `src/tiewtrade/desktop_main.py` | Composes public chart feed and controlled desktop shutdown. |
| `tests/unit/application/test_chart_realtime_feed.py` | Feed lifecycle, facts, replacement, shutdown, and dependency-isolation tests. |
| `tests/unit/ui/test_chart_workflow.py` | Configured/stopped chart updates and stale-generation tests. |
| `tests/unit/ui/test_main_window.py` | Session startup, Bot independence, replacement, and shutdown wiring tests. |
| `tests/unit/test_desktop_main.py` | Desktop composition tests. |

## Task 1: Build the application-owned read-only chart feed

**Files:**
- Create: `src/tiewtrade/application/chart_realtime_feed.py`
- Create: `tests/unit/application/test_chart_realtime_feed.py`

**Interfaces:**
- Consumes: `ConfiguredPaperSession`, `MarketDataRuntime`, `MarketDataCandleSource`, `RuntimeScheduler`, and `Candle`.
- Produces: `ChartOnlyFeedController.start(session, publish_completed, publish_in_progress) -> None`, `stop() -> None`, `close() -> None`; accepted completed/forming candles are sent through the per-generation `Callable[[Candle], None]` callbacks.

- [ ] **Step 1: Write the failing lifecycle tests**

```python
def test_start_publishes_forming_and_completed_candles_without_bot_runtime() -> None:
    completed: list[Candle] = []
    forming: list[Candle] = []
    controller = ChartOnlyFeedController(
        source_factory=lambda endpoints: FakeCandleSource(
            open_candle=open_candle,
            completed_candle=completed_candle,
        ),
        scheduler=FakeRuntimeScheduler(now=observed_at_utc),
    )

    controller.start(configured_spot_session(), completed.append, forming.append)

    assert forming == [open_candle]
    assert completed == [completed_candle]


def test_replacing_session_stops_old_feed_and_drops_old_callbacks() -> None:
    controller.start(first_session, received.append, received.append)
    old_source.publish_open(old_candle)
    controller.start(second_session, received.append, received.append)
    old_source.publish_open(old_candle)

    assert old_source.closed is True
    assert received == [old_candle, second_candle]
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/application/test_chart_realtime_feed.py -q`

Expected: FAIL because `tiewtrade.application.chart_realtime_feed` does not exist.

- [ ] **Step 3: Write the minimal controller and read-only sink**

```python
class _ChartOnlySink:
    async def warm_up(
        self, candles: tuple[Candle, ...], *, received_at: datetime
    ) -> None:
        del candles, received_at

    async def process_completed(
        self, candle: Candle, *, received_at: datetime
    ) -> None:
        del received_at
        self._publish_completed(candle)


class ChartOnlyFeedController:
    def start(
        self,
        session: ConfiguredPaperSession,
        publish_completed: Callable[[Candle], None],
        publish_in_progress: Callable[[Candle], None],
    ) -> None:
        self.stop()
        self._start_generation(session, publish_completed, publish_in_progress)

    def stop(self) -> None:
        runtime, loop, thread = self._claim_active_runtime()
        if runtime is not None and loop is not None:
            asyncio.run_coroutine_threadsafe(runtime.stop(), loop).result(timeout=30)
        if thread is not None:
            thread.join(timeout=30)
```

Use `MarketDataRuntime(config=session.market_data, warm_up_count=1, source=source,
sink=sink, scheduler=self._scheduler, on_open_candle=publisher.in_progress_candle)`.
Its `on_open_candle` callback calls only `publish_in_progress`; create the
source with `BinancePublicEndpoints.for_market_type(session.config.market_type)`.
The controller must not instantiate `PaperRuntimeController` or a persistence adapter.

- [ ] **Step 4: Run the focused test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/application/test_chart_realtime_feed.py -q`

Expected: PASS.

- [ ] **Step 5: Add import-boundary regression and commit**

```python
def test_chart_realtime_feed_has_no_trading_execution_or_persistence_dependency() -> None:
    source = Path("src/tiewtrade/application/chart_realtime_feed.py").read_text()
    assert "paper_runtime" not in source
    assert "execution" not in source
    assert "integrations.sqlite" not in source
```

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/application/test_chart_realtime_feed.py -q`

Expected: PASS.

```bash
git add src/tiewtrade/application/chart_realtime_feed.py tests/unit/application/test_chart_realtime_feed.py
git commit -m "feat: add chart-only realtime feed"
```

## Task 2: Make chart facts independent of Bot state

**Files:**
- Modify: `src/tiewtrade/ui/chart_workflow.py:26-360`
- Modify: `tests/unit/ui/test_chart_workflow.py`

**Interfaces:**
- Consumes: completed/in-progress `ChartCandle` values through `ChartFactRelay`.
- Produces: `ChartFactRelay.new_generation() -> ChartFactPublisher`, `invalidate() -> None`, and `is_current(generation: int) -> bool`. `ChartWorkflow.in_progress_candle()` works for configured and stopped Sessions.

- [ ] **Step 1: Write failing relay and lifecycle-independent workflow tests**

```python
def test_configured_session_displays_valid_forming_candle() -> None:
    workflow = ready_workflow()
    workflow.in_progress_candle(candle("5m", 20))

    assert snapshots[-1].in_progress_candle == candle("5m", 20)


def test_chart_fact_relay_rejects_invalidated_generation() -> None:
    relay = ChartFactRelay()
    first = relay.new_generation()
    relay.invalidate()
    first.in_progress_candle(candle("5m", 20))

    assert received == []
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest tests/unit/ui/test_chart_workflow.py -k 'configured or relay' -q`

Expected: FAIL because the workflow currently requires `_runtime_running` and no `ChartFactRelay` exists.

- [ ] **Step 3: Implement the chart-only Qt relay and remove only the Bot-state gate**

```python
class ChartFactPublisher:
    def completed_candle(self, candle: ChartCandle) -> None:
        self._relay.completed_candle_ready.emit(candle, self._generation)

    def in_progress_candle(self, candle: ChartCandle) -> None:
        self._relay.in_progress_candle_ready.emit(candle, self._generation)


class ChartFactRelay(QObject):
    completed_candle_ready = Signal(object, int)
    in_progress_candle_ready = Signal(object, int)
```

Remove the `_runtime_running` check and the Bot snapshot dependency from
`ChartWorkflow`. Retain current Session symbol/timeframe checks, following-latest
behavior, observed-interval validation, pending queues, and `close()` invalidation.

- [ ] **Step 4: Run focused chart tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest tests/unit/ui/test_chart_workflow.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the chart-fact boundary**

```bash
git add src/tiewtrade/ui/chart_workflow.py tests/unit/ui/test_chart_workflow.py
git commit -m "feat: accept chart facts without bot runtime"
```

## Task 3: Wire feed startup, replacement, and shutdown through the UI

**Files:**
- Modify: `src/tiewtrade/ui/main_window.py:25-245`
- Modify: `src/tiewtrade/ui/desktop.py:12-65`
- Modify: `tests/unit/ui/test_main_window.py`

**Interfaces:**
- Consumes: `start_chart_feed: Callable[[ConfiguredPaperSession], None]`, `stop_chart_feed: Callable[[], None]`, and `ChartFactRelay`.
- Produces: a current Session causes exactly one history load and chart-feed start; Bot lifecycle signal changes do not call chart-feed callbacks.

- [ ] **Step 1: Write failing MainWindow behavior tests**

```python
def test_session_ready_starts_chart_feed_without_starting_bot(qtbot: QtBot) -> None:
    started: list[ConfiguredPaperSession] = []
    window = make_window(start_chart_feed=started.append)

    emit_session_ready(window, session)

    assert started == [session]
    assert bot_start_calls == []


def test_stop_bot_does_not_stop_chart_feed(qtbot: QtBot) -> None:
    window.workspace.stop_bot_requested.emit()
    assert stopped_chart_feeds == []
```

- [ ] **Step 2: Run focused UI tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest tests/unit/ui/test_main_window.py -k 'chart_feed or stop_bot' -q`

Expected: FAIL because `MainWindow` has no chart-feed lifecycle dependencies.

- [ ] **Step 3: Wire only Session and window lifecycle to chart feed**

```python
def _show_session(self, session: ConfiguredPaperSession) -> None:
    self._lifecycle_workflow.configure(session)
    self._chart_workflow.start(session)
    self._start_chart_feed(session)


def _close_workflows(self) -> None:
    self._stop_chart_feed()
    self._chart_fact_relay.invalidate()
    self._chart_workflow.close()
    assert stopped_chart_feeds == []
```

Connect `ChartFactRelay` with queued Qt connections to handlers that call
`ChartWorkflow` only when `relay.is_current(generation)` is true. Remove the
existing `RuntimeSnapshotRelay` connections that deliver facts to the chart; keep
that relay for Bot lifecycle/workspace updates.

- [ ] **Step 4: Run focused MainWindow tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest tests/unit/ui/test_main_window.py -q`

Expected: PASS.

- [ ] **Step 5: Commit UI wiring**

```bash
git add src/tiewtrade/ui/main_window.py src/tiewtrade/ui/desktop.py tests/unit/ui/test_main_window.py
git commit -m "feat: start chart feed with current session"
```

## Task 4: Compose public dependencies and controlled shutdown

**Files:**
- Modify: `src/tiewtrade/desktop_main.py:35-390`
- Modify: `tests/unit/test_desktop_main.py`

**Interfaces:**
- Consumes: `ChartOnlyFeedController`, `ChartFactRelay`, `BinancePublicMarketData`, and `ChartHistory`.
- Produces: `run_desktop_ui` receives `start_chart_feed`, `stop_chart_feed`, and
  `chart_fact_relay` keyword dependencies.

- [ ] **Step 1: Write failing desktop-composition test**

```python
def test_desktop_composes_chart_feed_from_public_market_data(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        desktop_main, "run_desktop_ui", lambda **kwargs: captured.update(kwargs) or 0
    )

    assert desktop_main.run_desktop(tmp_path / "tiewtrade.sqlite3") == 0
    assert callable(captured["start_chart_feed"])
    assert callable(captured["stop_chart_feed"])
    assert isinstance(captured["chart_fact_relay"], ChartFactRelay)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_desktop_main.py -k 'chart_feed' -q`

Expected: FAIL because composition does not provide chart-feed dependencies.

- [ ] **Step 3: Compose one feed and a combined shutdown callback**

```python
chart_fact_relay = ChartFactRelay()
chart_feed = ChartOnlyFeedController(source_factory=BinancePublicMarketData)

def start_chart_feed(session: ConfiguredPaperSession) -> None:
    publisher = chart_fact_relay.new_generation()
    chart_feed.start(session, publisher.completed_candle, publisher.in_progress_candle)

def shutdown_desktop_runtimes() -> None:
    chart_feed.close()
    runtime_actions.shutdown()
```

Allocate one relay publisher inside `start_chart_feed(session)`, not at composition
time, so completed and forming callbacks use the same generation. Pass the start/stop
wrappers and relay to `run_desktop_ui`. The feed uses
`BinancePublicEndpoints.for_market_type`; it must not reuse Bot Runtime callbacks.

- [ ] **Step 4: Run focused desktop tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_desktop_main.py -k 'chart_feed or shutdown' -q`

Expected: PASS.

- [ ] **Step 5: Commit desktop composition**

```bash
git add src/tiewtrade/desktop_main.py tests/unit/test_desktop_main.py
git commit -m "feat: compose chart-only realtime feed"
```

## Task 5: Run acceptance regression and quality gates

**Files:**
- Modify if necessary: only the tests and owners listed in Tasks 1–4.

- [ ] **Step 1: Add a full lifecycle acceptance regression**

```python
def test_chart_updates_before_bot_start_and_after_bot_stop_then_ignores_replaced_session(
    qtbot: QtBot,
) -> None:
    window, feed = ready_window_with_fake_chart_feed(qtbot)
    feed.publish_open(first_session, candle("5m", 20))
    stop_bot(window)
    feed.publish_completed(first_session, candle("5m", 20))
    replace_session(window, second_session)
    feed.publish_open(first_session, candle("5m", 25))

    assert window.workspace.chart.snapshot.session_id == second_session.config.session_id
```

- [ ] **Step 2: Run acceptance and focused regression suites**

Run:
```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/application/test_chart_realtime_feed.py \
  tests/unit/ui/test_chart_workflow.py \
  tests/unit/ui/test_main_window.py \
  tests/unit/test_desktop_main.py -q
```

Expected: PASS.

- [ ] **Step 3: Run repository quality gates**

Run:
```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy src
git diff --check
```

Expected: all commands pass.

- [ ] **Step 4: Commit final regression coverage**

```bash
git add src tests
git commit -m "test: cover chart-only feed lifecycle"
```

## Plan self-review

- Spec coverage: Tasks 1–4 cover startup, feed ownership, no execution route, fact relay, Bot independence, replacement, shutdown, and scoped failures. Task 5 covers the full acceptance path and quality gates.
- Placeholder scan: each code-bearing task names exact files, interfaces, commands, and expected outcome.
- Type consistency: `ChartOnlyFeedController` publishes `Candle`, `ChartFactRelay` carries the existing `ChartCandle` fact, and `ChartWorkflow` remains the sole `ChartSnapshot` owner.
