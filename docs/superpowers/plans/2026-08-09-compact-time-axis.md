# Compact Time Axis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show more compact, UTC-free time ticks on the chart bottom axis.

**Architecture:** Change only `_time_text` and `_time_ticks` in the existing painter model. The UTC clock and crosshair full timestamp remain unchanged.

**Tech Stack:** Python, PySide6, pytest, Ruff, Mypy.

## Global Constraints

- UTC remains the internal time source; only bottom-axis display changes.
- No market-data, bot, persistence, or chart-geometry changes.

---

### Task 1: Render compact adaptive time ticks

**Files:**
- Modify: `src/tiewtrade/ui/candlestick_chart.py:449-545`
- Test: `tests/unit/ui/test_candlestick_chart.py:80-90`

- [ ] **Step 1: Write failing tests**

```python
def test_time_axis_uses_compact_labels_and_dense_non_overlapping_ticks() -> None:
    model = chart_module._build_painter_model(ready_snapshot(), QRectF(0, 0, 600, 300))

    assert len(model.time_ticks) > 6
    assert all("UTC" not in tick.label for tick in model.time_ticks)
    assert all(len(tick.label) == 5 for tick in model.time_ticks)
    assert all(
        left.label_rect.right() <= right.label_rect.left()
        for left, right in zip(model.time_ticks, model.time_ticks[1:])
    )
```

- [ ] **Step 2: Verify RED**

Run: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest tests/unit/ui/test_candlestick_chart.py -k time_axis -q`

Expected: FAIL because labels include `UTC` and 600px plot has at most six ticks.

- [ ] **Step 3: Implement minimal formatting and spacing**

```python
def _time_text(value: datetime) -> str:
    return f"{value:%H:%M}"

label_width = 56.0
tick_count = min(12, max(2, int(plot_surface.width() // label_width) + 1))
```

Keep existing label-rect clamping and crosshair timestamp format.

- [ ] **Step 4: Verify focused tests**

Run: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest tests/unit/ui/test_candlestick_chart.py -q`

Expected: PASS.

- [ ] **Step 5: Verify quality and commit**

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
git commit -m "feat: compact chart time axis"
```

## Plan self-review

The plan covers UTC-free bottom labels, adaptive density, non-overlap, preserved crosshair context, and all repository quality gates.
