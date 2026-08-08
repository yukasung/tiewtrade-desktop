# Current Price Label Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render a TradingView-style current price line and right-axis label for the forming candle.

**Architecture:** Extend the chart painter model with one optional immutable `CurrentPriceGlyph`. The existing forming-candle snapshot supplies the price, color, and remaining time; the UI remains presentation-only.

**Tech Stack:** Python, PySide6 QPainter, pytest, Ruff, Mypy.

## Global Constraints

- Read-only ephemeral chart presentation; no market transport, persistence, strategy, execution, or lifecycle changes.
- Current price uses `in_progress_candle.close`; green when close is at least open and red otherwise.
- No glyph when there is no forming candle.

---

### Task 1: Model the current-price glyph

**Files:**
- Modify: `src/tiewtrade/ui/candlestick_chart.py`
- Test: `tests/unit/ui/test_candlestick_chart.py`

**Interfaces:**
- Produces: `CurrentPriceGlyph(line, label_rect, price_text, remaining_text, color)` and optional `ChartPainterModel.current_price`.

- [ ] **Step 1: Write failing painter-model tests**

```python
def test_forming_candle_exposes_colored_current_price_label() -> None:
    snapshot = ready_snapshot(
        in_progress_candle=candle(20, open_price="100", close_price="103")
    )
    model = chart_module._build_painter_model(snapshot, QRectF(0, 0, 600, 300))

    assert model.current_price is not None
    assert model.current_price.price_text == "103.00"
    assert model.current_price.remaining_text == "00:00"
    assert model.current_price.color == QColor("#0ECB81")
    assert model.plot_surface.contains(model.current_price.line.p1())
    assert model.plot_surface.contains(model.current_price.line.p2())
    assert model.current_price.label_rect.left() >= model.plot_surface.right()
```

- [ ] **Step 2: Verify RED**

Run: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest tests/unit/ui/test_candlestick_chart.py -k current_price -q`

Expected: FAIL because `ChartPainterModel.current_price` does not exist.

- [ ] **Step 3: Implement the glyph**

```python
@dataclass(frozen=True, slots=True)
class CurrentPriceGlyph:
    line: QLineF
    label_rect: QRectF
    price_text: str
    remaining_text: str
    color: QColor
```

Build it only from `snapshot.in_progress_candle`; map close with `_price_y`, make the line `plot_surface.left()` to `plot_surface.right()`, and use the existing price-axis bounds for a two-line label. Format the remaining duration with a helper that clamps negative values to zero.

- [ ] **Step 4: Verify GREEN**

Run: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest tests/unit/ui/test_candlestick_chart.py -q`

Expected: PASS.

### Task 2: Draw and verify the TradingView-style presentation

**Files:**
- Modify: `src/tiewtrade/ui/candlestick_chart.py`
- Test: `tests/unit/ui/test_candlestick_chart.py`

- [ ] **Step 1: Write failing rendering/color tests**

```python
def test_bearish_forming_candle_uses_red_current_price_label() -> None:
    model = chart_module._build_painter_model(
        ready_snapshot(in_progress_candle=candle(20, open_price="103", close_price="100")),
        QRectF(0, 0, 600, 300),
    )

    assert model.current_price is not None
    assert model.current_price.color == QColor("#F6465D")
```

- [ ] **Step 2: Verify RED**

Run: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest tests/unit/ui/test_candlestick_chart.py -k bearish_current_price -q`

Expected: FAIL before red color rendering is implemented.

- [ ] **Step 3: Draw line and two-line label**

Set a dotted `QPen` with glyph color, draw its line before candles, fill the right-axis label with glyph color, then draw `price_text + "\\n" + remaining_text` in white. Draw the crosshair last so hover labels retain priority.

- [ ] **Step 4: Verify full quality gates and commit**

Run:
```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy src
git diff --check
```

Expected: all commands pass.

```bash
git add src/tiewtrade/ui/candlestick_chart.py tests/unit/ui/test_candlestick_chart.py
git commit -m "feat: show realtime current price label"
```

## Plan self-review

The two tasks cover conditional glyph creation, bullish/bearish color, right-axis placement, dotted line, remaining-time format, no-forming absence, painter ordering, and all required checks.
