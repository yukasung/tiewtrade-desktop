# Candlestick Chart Axis and Crosshair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render fixed UTC time and price axes plus a mouse-following price/time crosshair in the Candlestick Chart.

**Architecture:** Keep `CandlestickChartWidget` as a read-only QPainter surface. Immutable painter glyphs derive plot bounds, axes, and crosshair. The widget stores only ephemeral mouse position and clears it when a snapshot changes or the mouse exits.

**Tech Stack:** Python 3.14, PySide6 QPainter, Decimal, pytest-qt, Ruff, mypy.

## Global Constraints

- Use UTC values from `ChartRange`; do not convert to local time.
- Display only: do not change Chart data, runtime relay, persistence, Strategy, risk, or execution.
- Candles and fill markers remain inside the plot area; axes are outside it.
- Do not add OHLC tooltip, snap-to-candle, zoom/pan, indicators, volume panel, or order controls.

---

### Task 1: Render fixed price and time axes

**Files:**
- Modify: `src/tiewtrade/ui/candlestick_chart.py:15-230`
- Test: `tests/unit/ui/test_candlestick_chart.py`

**Interfaces:**
- Add `AxisTickGlyph(line: QLineF, label_rect: QRectF, label: str, value: str)`.
- Extend `ChartPainterModel` with `plot_surface`, `price_ticks`, and `time_ticks`.
- `_build_painter_model(snapshot, surface)` reserves right/bottom axis space from its outer `surface` argument.

- [ ] **Step 1: Write the failing model test**

```python
def test_painter_model_reserves_axis_space_and_exposes_ticks() -> None:
    model = chart_module._build_painter_model(ready_snapshot(), QRectF(0, 0, 600, 300))

    assert model.plot_surface.right() < 600
    assert model.plot_surface.bottom() < 300
    assert len(model.price_ticks) == 5
    assert len(model.time_ticks) in {5, 6}
    assert model.time_ticks[0].label.startswith("2026-01-01")
```

- [ ] **Step 2: Run it to verify RED**

Run: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest tests/unit/ui/test_candlestick_chart.py -k exposes_ticks -q`

Expected: FAIL because the painter model has no axis fields.

- [ ] **Step 3: Write the minimal rendering model**

```python
@dataclass(frozen=True, slots=True)
class AxisTickGlyph:
    line: QLineF
    label_rect: QRectF
    label: str
    value: str

def _plot_surface(surface: QRectF) -> QRectF:
    return QRectF(surface.left(), surface.top(), surface.width() - 72, surface.height() - 26)
```

Calculate five evenly spaced Decimal ticks from the existing display low/high and six evenly spaced UTC ticks from `ChartRange`. Format first time tick `YYYY-MM-DD HH:mm UTC` and remaining ticks `HH:mm UTC`. Build existing candle/marker geometry from `plot_surface`.

- [ ] **Step 4: Draw axes and verify GREEN**

Draw grid/tick labels in the reserved axis rectangles while preserving candle and marker order.

Run: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest tests/unit/ui/test_candlestick_chart.py -q`

Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add src/tiewtrade/ui/candlestick_chart.py tests/unit/ui/test_candlestick_chart.py
git commit -m "feat: draw chart price and time axes"
```

### Task 2: Render continuous mouse crosshair

**Files:**
- Modify: `src/tiewtrade/ui/candlestick_chart.py:45-180`
- Test: `tests/unit/ui/test_candlestick_chart.py`

**Interfaces:**
- Add `CrosshairGlyph(vertical: QLineF, horizontal: QLineF, price_label: AxisTickGlyph, time_label: AxisTickGlyph)`.
- Add `_crosshair_at(position: QPointF, model: ChartPainterModel, snapshot: ChartSnapshot) -> CrosshairGlyph | None`.
- Add read-only `CandlestickChartWidget.crosshair` for tests.

- [ ] **Step 1: Write failing interaction tests**

```python
def test_mouse_over_plot_shows_crosshair_price_and_time(qtbot: QtBot) -> None:
    chart = displayed_chart(qtbot)
    qtbot.mouseMove(chart, QPoint(300, 180))

    assert chart.crosshair is not None
    assert chart.crosshair.price_label.label
    assert chart.crosshair.time_label.label.endswith("UTC")

def test_crosshair_hides_when_mouse_leaves_plot(qtbot: QtBot) -> None:
    chart = displayed_chart(qtbot)
    qtbot.mouseMove(chart, QPoint(300, 180))
    qtbot.mouseMove(chart, QPoint(chart.width() - 4, 20))

    assert chart.crosshair is None
```

- [ ] **Step 2: Run them to verify RED**

Run: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest tests/unit/ui/test_candlestick_chart.py -k crosshair -q`

Expected: FAIL because the widget has no crosshair property or mouse handling.

- [ ] **Step 3: Implement mouse tracking and mapping**

```python
def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
    self._crosshair_position = event.position()
    self.update()

def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
    self._crosshair_position = None
    self.update()
    super().leaveEvent(event)
```

Enable mouse tracking. Generate crosshair only inside `model.plot_surface`; map x continuously to `ChartRange.start/end` and y continuously to display low/high. Reset the stored position in `show_snapshot`.

- [ ] **Step 4: Draw labels and verify GREEN**

Draw dashed muted lines after grid and before candles; draw filled labels inside reserved axis rectangles and clamp labels to widget bounds.

Run: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest tests/unit/ui/test_candlestick_chart.py -q`

Expected: PASS.

- [ ] **Step 5: Commit Task 2**

```bash
git add src/tiewtrade/ui/candlestick_chart.py tests/unit/ui/test_candlestick_chart.py
git commit -m "feat: add chart crosshair labels"
```

### Task 3: Verify rendering boundaries and quality

**Files:**
- Modify: `tests/unit/ui/test_candlestick_chart.py`

**Interfaces:**
- Consumes completed painter model and mouse interaction.
- Produces regression evidence only.

- [ ] **Step 1: Add the boundary regression test**

```python
def test_candles_and_markers_remain_inside_plot_not_axis_space() -> None:
    model = chart_module._build_painter_model(ready_snapshot(), QRectF(0, 0, 600, 300))

    assert all(model.plot_surface.contains(glyph.body.center()) for glyph in model.candles)
    assert all(model.plot_surface.contains(marker.label_rect.center()) for marker in model.markers)
```

- [ ] **Step 2: Run chart tests**

Run: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest tests/unit/ui/test_candlestick_chart.py -q`

Expected: PASS.

- [ ] **Step 3: Run quality gates**

Run: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest -q; .venv/bin/python -m ruff check src tests; .venv/bin/python -m ruff format --check src tests; .venv/bin/python -m mypy`

Expected: all commands return zero.

- [ ] **Step 4: Commit Task 3**

```bash
git add tests/unit/ui/test_candlestick_chart.py
git commit -m "test: cover chart axis boundaries"
```
