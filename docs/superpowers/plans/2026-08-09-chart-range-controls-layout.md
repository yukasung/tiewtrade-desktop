# Chart Range Controls Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make chart range navigation controls compact, arrow-based, consistently spaced, and separate from the chart plot and price labels.

**Architecture:** `CandlestickChartWidget` owns both its header layout and painter surface. It will retain the header layout, group the range label and fixed-width buttons in a nested layout, and derive the plot surface from the header's measured geometry. Chart-data and range-navigation behavior do not change.

**Tech Stack:** Python 3.13, PySide6, pytest-qt, Ruff, mypy.

## Global Constraints

- UI copy remains English; code identifiers and comments remain English.
- Preserve `range_requested`, `retry_requested`, `accessibleName`, keyboard activation, and completed-candle chart behavior.
- Use existing Qt and theme components; do not modify market-data, trading, or persistence modules.
- Use TDD: observe the focused test fail before production code changes.
- Do not create a worktree.

---

### Task 1: Lay out range controls without overlapping the plot

**Files:**
- Modify: `tests/unit/ui/test_candlestick_chart.py: test_previous_and_next_range_controls_are_keyboard_accessible`
- Modify: `src/tiewtrade/ui/candlestick_chart.py: CandlestickChartWidget.__init__, crosshair, paintEvent, _chart_surface`

**Interfaces:**
- Consumes: `ChartSnapshot`, `ChartRange`, `build_painter_model(snapshot, surface: QRectF)`, and `range_requested`.
- Produces: arrow navigation buttons with 44px widths and a plot surface beginning 12px below the computed header bounds.

- [ ] **Step 1: Write the failing regression test**

Extend the existing keyboard-accessibility test with:

```python
chart.resize(1200, 500)
chart.show()
chart.show_snapshot(ready_snapshot())
assert chart.previous_range_button.text() == "←"
assert chart.next_range_button.text() == "→"
assert chart.previous_range_button.width() == 44
assert chart.next_range_button.width() == 44
assert chart.previous_range_button.accessibleName() == "Previous chart range"
assert chart.next_range_button.accessibleName() == "Next chart range"
surface = chart._chart_surface()
assert surface.top() >= chart.previous_range_button.geometry().bottom() + 12
assert surface.top() >= chart.next_range_button.geometry().bottom() + 12
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/ui/test_candlestick_chart.py::test_previous_and_next_range_controls_are_keyboard_accessible`.

Expected: FAIL because the buttons still contain words, lack fixed widths, and `_chart_surface` is not an instance method.

- [ ] **Step 3: Write minimal implementation**

Retain the header as `self._header`; make its direct spacing zero. Create `controls = QHBoxLayout()` with 8px spacing. Add `self._range`, then `controls.addSpacing(12)`, then `QPushButton("←")` and `QPushButton("→")`, each with `setFixedWidth(44)`. Add `controls` to the header after its stretch. Keep existing object names, accessible names, and click connections.

Replace the module-level helper with `CandlestickChartWidget._chart_surface(self) -> QRectF`. Its top is `self._header.geometry().bottom() + 12`, its left/right margins remain 12px, and its bottom margin remains 12px. Update `crosshair` and `paintEvent` to call `self._chart_surface()`.

- [ ] **Step 4: Run the focused test and verify it passes**

Run `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/ui/test_candlestick_chart.py::test_previous_and_next_range_controls_are_keyboard_accessible`.

Expected: PASS; the test proves button text, fixed widths, accessibility, and non-overlap with the plot surface.

- [ ] **Step 5: Run chart regression tests**

Run `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/ui/test_candlestick_chart.py`.

Expected: all chart tests PASS, including range navigation, current-price label, axes, crosshair, and painter-model tests.

- [ ] **Step 6: Commit the implementation**

Run `git add src/tiewtrade/ui/candlestick_chart.py tests/unit/ui/test_candlestick_chart.py && git commit -m "fix: space chart range controls"`.

### Task 2: Verify the complete desktop UI contract

**Files:**
- Verify: `src/tiewtrade/ui/candlestick_chart.py`
- Verify: `tests/unit/ui/test_candlestick_chart.py`

**Interfaces:**
- Consumes: the completed Task 1 layout and painter-surface contract.
- Produces: verified repository state with no test, lint, format, typing, or whitespace regressions.

- [ ] **Step 1: Run the full test suite**

Run `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest -q`.

Expected: PASS with no Qt exceptions.

- [ ] **Step 2: Run repository quality gates**

Run `.venv/bin/ruff check src tests`, `.venv/bin/ruff format --check src tests`, `.venv/bin/mypy src`, and `git diff --check`.

Expected: no lint violations, all files formatted, no mypy issues, and no whitespace errors.

- [ ] **Step 3: Record the verification result in the handoff**

Report focused and full test totals, quality-gate outcomes, the implementation commit, and that the branch has not been pushed or merged.
