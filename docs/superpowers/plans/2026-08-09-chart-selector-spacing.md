# Chart Selector Spacing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** เพิ่มระยะห่างและ padding ให้ Chart Symbol/Timeframe dropdown อ่านง่ายโดยไม่เปลี่ยน Chart view behavior

**Architecture:** ปรับ geometry ที่ `CandlestickChartWidget` และ style selector เฉพาะ object name ใน dark theme เดิมเท่านั้น ไม่มีการเปลี่ยน signal, workflow หรือ Session configuration

**Tech Stack:** Python 3, PySide6, pytest-qt, Ruff, mypy

## Global Constraints

- UI ใช้ภาษาอังกฤษ; code และ identifiers ใช้ภาษาอังกฤษ
- Chart view เป็น read-only presentation state และต้องไม่เปลี่ยน immutable Bot Session
- ห้ามเปลี่ยน Symbol/Timeframe ที่รองรับหรือ Chart workflow

---

### Task 1: Space the Chart header controls

**Files:**
- Modify: `src/tiewtrade/ui/candlestick_chart.py:45-92`
- Test: `tests/unit/ui/test_candlestick_chart.py`

**Interfaces:**
- Consumes: `CandlestickChartWidget.symbol`, `CandlestickChartWidget.timeframe`, and private `_facts` labels
- Produces: Header geometry with 12px Symbol-to-Timeframe spacing and 16px Timeframe-to-facts spacing

- [ ] Write a failing pytest-qt geometry test after showing a 1200px-wide chart. Assert a 12px gap between `symbol` and `timeframe`, then a 16px gap between `timeframe` and `_facts`.
- [ ] Run `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/unit/ui/test_candlestick_chart.py -k selector_header` and confirm it fails because the current header uses zero spacing.
- [ ] Insert `addSpacing(12)` between Symbol and Timeframe, and `addSpacing(16)` between Timeframe and facts in the header layout.
- [ ] Re-run the focused test and confirm it passes.

### Task 2: Give Chart dropdown text and popup items safe padding

**Files:**
- Modify: `src/tiewtrade/ui/theme.py:62-84`
- Test: `tests/unit/ui/test_theme.py`

**Interfaces:**
- Consumes: `#chartSymbol` and `#chartTimeframe` object names
- Produces: Scoped stylesheet rules with 14px left and 40px right closed-control padding, and 12px horizontal/6px vertical popup-item padding

- [ ] Write a failing unit test that asserts `DARK_THEME` contains `QComboBox#chartSymbol, QComboBox#chartTimeframe`, `padding: 0 40px 0 14px;`, and popup item padding `padding: 6px 12px;`.
- [ ] Run `PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/ui/test_theme.py -k chart_selector_theme` and confirm it fails because no Chart-specific selector exists.
- [ ] Add only the scoped QSS rules for the two Chart comboboxes and their popup items; preserve the existing common input rule.
- [ ] Re-run the focused theme test and confirm it passes.

### Task 3: Verify the scoped UI adjustment

**Files:**
- Modify: `docs/superpowers/plans/2026-08-09-chart-selector-spacing.md` to mark tasks completed

- [ ] Run `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/unit/ui/test_candlestick_chart.py tests/unit/ui/test_chart_workflow.py tests/unit/ui/test_main_window.py`.
- [ ] Run `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q && .venv/bin/python -m ruff check src tests && .venv/bin/python -m ruff format --check src tests && PYTHONPATH=src .venv/bin/python -m mypy src && git diff --check`.
- [ ] Commit only the spacing implementation, its tests, and this plan; do not stage unrelated dirty worktree changes.
