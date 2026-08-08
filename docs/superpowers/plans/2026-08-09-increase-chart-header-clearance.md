# Increase Chart Header Clearance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve 44px chart range buttons while creating a 24px clear band between the header controls and all chart prices, grid lines, and candles.

**Architecture:** `CandlestickChartWidget._chart_surface()` is the single geometry boundary used by drawing and crosshair logic. Increase only its header clearance constant from 12px to 24px; the chart painter model automatically relocates all plot content using the supplied surface.

**Tech Stack:** Python 3.13, PySide6, pytest-qt, Ruff, mypy.

## Global Constraints

- Keep the navigation controls at 44px width and preserve 12px/8px horizontal spacing.
- Preserve navigation signals, accessibility names, keyboard behavior, and all market-data behavior.
- Use TDD and do not create a worktree.

---

### Task 1: Reserve a 24px band below chart controls

**Files:**
- Modify: `tests/unit/ui/test_candlestick_chart.py: test_previous_and_next_range_controls_are_keyboard_accessible`
- Modify: `src/tiewtrade/ui/candlestick_chart.py: CandlestickChartWidget._chart_surface`

**Interfaces:**
- Consumes: `CandlestickChartWidget._header.geometry()` and the existing range-button geometries.
- Produces: `_chart_surface() -> QRectF` whose top is at least 24px below both range buttons.

- [ ] **Step 1: Write the failing regression test**

Change the two existing surface assertions to:

```python
assert surface.top() >= chart.previous_range_button.geometry().bottom() + 24
assert surface.top() >= chart.next_range_button.geometry().bottom() + 24
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/ui/test_candlestick_chart.py::test_previous_and_next_range_controls_are_keyboard_accessible`.

Expected: FAIL because `_chart_surface()` still begins 12px below the header.

- [ ] **Step 3: Write minimal implementation**

Change the top calculation in `_chart_surface()` to:

```python
top = self._header.geometry().bottom() + 24
```

Keep the existing 12px left, right, and bottom margins; do not change button widths or the header's horizontal layouts.

- [ ] **Step 4: Run focused chart tests**

Run `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/ui/test_candlestick_chart.py`.

Expected: PASS, confirming controls, axes, crosshair, and current-price label share the moved plot surface.

- [ ] **Step 5: Commit the implementation**

Run `git add src/tiewtrade/ui/candlestick_chart.py tests/unit/ui/test_candlestick_chart.py && git commit -m "fix: increase chart header clearance"`.

### Task 2: Verify repository quality

**Files:**
- Verify: `src/tiewtrade/ui/candlestick_chart.py`
- Verify: `tests/unit/ui/test_candlestick_chart.py`

**Interfaces:**
- Consumes: the Task 1 geometry contract.
- Produces: verified full repository state.

- [ ] **Step 1: Run full verification**

Run `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest -q`, `.venv/bin/ruff check src tests`, `.venv/bin/ruff format --check src tests`, `.venv/bin/mypy src`, and `git diff --check`.

Expected: all tests and quality gates PASS.

- [ ] **Step 2: Record handoff details**

Report full test total, quality-gate results, implementation commit, and that the branch has not been pushed or merged.
