# Chart Painter Model Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move pure chart glyph/model/geometry code out of the Qt widget without changing chart behavior.

**Architecture:** `chart_painter_model.py` owns painter dataclasses and pure calculations. `candlestick_chart.py` imports those values and retains QWidget event handling and QPainter calls.

**Tech Stack:** Python, PySide6, pytest, Ruff, Mypy.

## Global Constraints

- Do not change chart rendering geometry, UI copy, transport, workflow, feed, persistence, strategy, execution, or Bot lifecycle.
- Keep forming candle, markers, current-price label, compact axis, crosshair, and range controls behavior identical.

---

### Task 1: Extract pure painter model

**Files:**
- Create: `src/tiewtrade/ui/chart_painter_model.py`
- Modify: `src/tiewtrade/ui/candlestick_chart.py`
- Create: `tests/unit/ui/test_chart_painter_model.py`
- Modify: `tests/unit/ui/test_candlestick_chart.py`

**Interfaces:**
- Produces: `ChartPainterModel`, glyph dataclasses, `build_painter_model(snapshot: ChartSnapshot, surface: QRectF) -> ChartPainterModel`, and `crosshair_at(position: QPointF, model: ChartPainterModel) -> CrosshairGlyph | None`.
- Consumes: immutable `ChartSnapshot`, Qt value types, Decimal, and no QWidget or QPainter.

- [ ] **Step 1: Write a failing module-boundary test**

```python
def test_painter_model_is_pure_and_widget_imports_its_public_api() -> None:
    model_source = Path("src/tiewtrade/ui/chart_painter_model.py").read_text()
    widget_source = Path("src/tiewtrade/ui/candlestick_chart.py").read_text()

    assert "QWidget" not in model_source
    assert "QPainter" not in model_source
    assert "from tiewtrade.ui.chart_painter_model import" in widget_source
```

- [ ] **Step 2: Verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/ui/test_chart_painter_model.py -q`

Expected: FAIL because the module does not exist.

- [ ] **Step 3: Move glyphs and pure functions unchanged**

Move `CandleGlyph`, `MarkerGlyph`, `AxisTickGlyph`, `CrosshairGlyph`,
`CurrentPriceGlyph`, `ChartPainterModel`, `_build_painter_model`,
`_current_price_glyph`, axis/tick/text/mapping helpers, and `_crosshair_at`
into the new module. Rename only the exported entry points to
`build_painter_model` and `crosshair_at`; retain helper math verbatim.

- [ ] **Step 4: Replace widget-local model calls**

```python
from tiewtrade.ui.chart_painter_model import (
    ChartPainterModel,
    CrosshairGlyph,
    build_painter_model,
    crosshair_at,
)

model = build_painter_model(snapshot, surface)
return crosshair_at(position, model)
```

- [ ] **Step 5: Verify model and widget tests**

Run:
```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/ui/test_chart_painter_model.py \
  tests/unit/ui/test_candlestick_chart.py -q
```

Expected: PASS.

### Task 2: Preserve glyph output and run full regression

**Files:**
- Modify: `tests/unit/ui/test_chart_painter_model.py`
- Modify: `tests/unit/ui/test_candlestick_chart.py`

- [ ] **Step 1: Add before/after behavior checks**

```python
def test_forming_snapshot_preserves_all_painter_features() -> None:
    model = build_painter_model(forming_snapshot(), QRectF(0, 0, 600, 300))

    assert model.candles
    assert model.markers
    assert model.price_ticks
    assert model.time_ticks
    assert model.current_price is not None
    assert crosshair_at(QPointF(200, 120), model) is not None
```

- [ ] **Step 2: Run full quality gates**

Run:
```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy src
git diff --check
```

Expected: all commands pass.

- [ ] **Step 3: Commit**

```bash
git add src/tiewtrade/ui/chart_painter_model.py src/tiewtrade/ui/candlestick_chart.py tests/unit/ui
git commit -m "refactor: extract chart painter model"
```

## Plan self-review

Tasks cover the requested file boundary, widget integration, all existing visual features, pure-model tests, and repository quality gates without adding behavior.
