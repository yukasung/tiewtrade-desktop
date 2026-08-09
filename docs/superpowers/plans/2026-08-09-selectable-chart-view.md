# Selectable Chart View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users select a read-only chart Symbol and Timeframe without changing the immutable Bot Session or trading behavior.

**Architecture:** `ChartViewSelection` validates the shared Paper symbol/timeframe lists and describes only presentation identity. `ChartWorkflow` owns the selected view and invalidates generation-bound chart requests when it changes. The desktop root replaces only the chart-only public feed; Bot lifecycle and execution receive no call.

**Tech Stack:** Python 3.12+, PySide6, public Binance REST/WebSocket market data, pytest/pytest-qt, Ruff, mypy.

## Global Constraints

- Presentation selection is never persisted in SQLite or Bot Session configuration.
- No lifecycle, strategy, execution, credentials, private endpoint, or Live order path may run from a selection change.
- Symbols: `BTCUSDT`, `ETHUSDT`, `BNBUSDT`; timeframes: `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `4h`.
- Chart market type follows the immutable Session market type.
- Markers display only when the selected view identity equals the Session identity.

---

### Task 1: Create validated view identity

**Files:**

- Create: `src/tiewtrade/application/chart_view_selection.py`
- Create: `tests/unit/application/test_chart_view_selection.py`
- Modify: `PRODUCT.md:246-267`

**Interfaces:**

- Produces: `ChartViewSelection(symbol: str, timeframe: str)` and `market_data_config() -> MarketDataConfig`.
- Consumes: `validate_paper_symbol()` and `MarketDataConfig`.

- [ ] **Step 1: Write the failing test**

```python
def test_chart_view_selection_builds_market_data_config() -> None:
    selection = ChartViewSelection(symbol="ETHUSDT", timeframe="1m")

    assert selection.market_data_config() == MarketDataConfig(
        symbol="ETHUSDT", timeframe="1m"
    )
```

Add parameterized tests that `DOGEUSDT` and `7m` raise `ValueError`.

- [ ] **Step 2: Verify red**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/application/test_chart_view_selection.py`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the minimal value object**

```python
@dataclass(frozen=True, slots=True)
class ChartViewSelection:
    symbol: str
    timeframe: str

    def __post_init__(self) -> None:
        validate_paper_symbol(self.symbol)
        MarketDataConfig(symbol=self.symbol, timeframe=self.timeframe)

    def market_data_config(self) -> MarketDataConfig:
        return MarketDataConfig(symbol=self.symbol, timeframe=self.timeframe)
```

Update `PRODUCT.md` to state that Chart selection is read-only and independent from immutable Bot configuration.

- [ ] **Step 4: Verify green**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/application/test_chart_view_selection.py`

Expected: PASS.

### Task 2: Bind Chart workflow and history to selection

**Files:**

- Modify: `src/tiewtrade/application/chart_data.py:90-230`
- Modify: `src/tiewtrade/application/chart_history.py:1-130`
- Modify: `src/tiewtrade/ui/chart_workflow.py:20-480`
- Modify: `tests/unit/application/test_chart_data.py`
- Modify: `tests/unit/application/test_chart_history.py`
- Modify: `tests/unit/ui/test_chart_workflow.py`

**Interfaces:**

- Produces: `ChartWorkflow.select_view(selection: ChartViewSelection) -> None`.
- Rule: `ChartSnapshot.symbol/timeframe` are selection identity; markers are empty unless selection matches `session.market_data`.

- [ ] **Step 1: Write failing workflow tests**

```python
def test_selecting_a_chart_view_loads_its_default_range(qtbot: QtBot) -> None:
    workflow, loads = chart_workflow_for(configured_spot_session())

    workflow.select_view(ChartViewSelection(symbol="ETHUSDT", timeframe="1m"))

    qtbot.waitUntil(lambda: loads == [("ETHUSDT", "1m")])


def test_chart_snapshot_hides_markers_for_a_different_view() -> None:
    snapshot = chart_snapshot_for_selected_view("ETHUSDT", "1m")

    assert snapshot.markers == ()
```

Add a latest-request-wins test proving delayed `BTCUSDT/5m` data cannot publish after an `ETHUSDT/1m` selection.

- [ ] **Step 2: Verify red**

Run: `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/unit/application/test_chart_data.py tests/unit/application/test_chart_history.py tests/unit/ui/test_chart_workflow.py`

Expected: FAIL because `select_view` and selected-view loading do not exist.

- [ ] **Step 3: Implement selection-bound loading**

Store `_selection` in `ChartWorkflow`, initialize it from the Session identity in `configure()`, and add `select_view()`. The method increments the generation, clears pending candles, and loads a fresh default range. `ChartHistory` loads public candles with `selection.market_data_config()`; it lists fills only if that config equals `session.market_data`. Candle identity checks use selection, not Session.

- [ ] **Step 4: Verify green**

Run: `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/unit/application/test_chart_data.py tests/unit/application/test_chart_history.py tests/unit/ui/test_chart_workflow.py`

Expected: PASS.

### Task 3: Add header controls and replace only chart feed

**Files:**

- Modify: `src/tiewtrade/ui/candlestick_chart.py:1-180`
- Modify: `src/tiewtrade/ui/main_window.py:80-410`
- Modify: `src/tiewtrade/application/chart_realtime_feed.py:35-150`
- Modify: `src/tiewtrade/desktop_main.py:383-475`
- Modify: `tests/unit/ui/test_candlestick_chart.py`
- Modify: `tests/unit/ui/test_main_window.py`
- Modify: `tests/unit/application/test_chart_realtime_feed.py`
- Modify: `tests/acceptance/test_desktop_chart.py`

**Interfaces:**

- Produces: `CandlestickChartWidget.view_requested = Signal(object)`.
- Consumes: `ChartWorkflow.select_view()` and a chart-feed start operation that accepts Session plus `ChartViewSelection`.

- [ ] **Step 1: Write failing UI/feed tests**

```python
def test_chart_header_emits_selected_view(qtbot: QtBot) -> None:
    chart = CandlestickChartWidget()
    qtbot.addWidget(chart)
    selected = []
    chart.view_requested.connect(selected.append)

    chart.symbol.setCurrentIndex(chart.symbol.findData("BNBUSDT"))
    chart.timeframe.setCurrentIndex(chart.timeframe.findData("1m"))

    assert selected[-1] == ChartViewSelection(symbol="BNBUSDT", timeframe="1m")
```

Add tests proving selection replacement stops/restarts chart feed, old-generation candle callbacks are discarded, and no Bot lifecycle call occurs.

- [ ] **Step 2: Verify red**

Run: `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/unit/ui/test_candlestick_chart.py tests/unit/ui/test_main_window.py tests/unit/application/test_chart_realtime_feed.py tests/acceptance/test_desktop_chart.py`

Expected: FAIL because chart header controls and selection feed routing do not exist.

- [ ] **Step 3: Implement controls and routing**

Create `QComboBox` controls from `PAPER_SYMBOLS` and `SUPPORTED_V1_TIMEFRAME_CHOICES`; disable them while the selected chart loads. `MainWindow` routes `view_requested` to `ChartWorkflow.select_view()` and injected chart-feed replacement. `ChartOnlyFeedController` builds runtime config from `selection.market_data_config()` but gets Binance endpoint market type from the Session. Replace relay generation before replacing feed; never call lifecycle methods.

- [ ] **Step 4: Verify green**

Run: `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/unit/ui/test_candlestick_chart.py tests/unit/ui/test_main_window.py tests/unit/application/test_chart_realtime_feed.py tests/acceptance/test_desktop_chart.py`

Expected: PASS.

### Task 4: Verify complete safety behavior

**Files:**

- Verify: `src/tiewtrade/application/chart_view_selection.py`
- Verify: `src/tiewtrade/application/chart_data.py`
- Verify: `src/tiewtrade/ui/chart_workflow.py`
- Verify: `src/tiewtrade/ui/candlestick_chart.py`

- [ ] **Step 1: Run focused coverage**

Run: `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/unit/application/test_chart_view_selection.py tests/unit/application/test_chart_data.py tests/unit/application/test_chart_history.py tests/unit/application/test_chart_realtime_feed.py tests/unit/ui/test_chart_workflow.py tests/unit/ui/test_candlestick_chart.py tests/unit/ui/test_main_window.py tests/acceptance/test_desktop_chart.py`

Expected: PASS.

- [ ] **Step 2: Run full quality gate**

Run: `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q`, then Ruff check/format, Mypy, and `git diff --check`.

Expected: all checks pass.
