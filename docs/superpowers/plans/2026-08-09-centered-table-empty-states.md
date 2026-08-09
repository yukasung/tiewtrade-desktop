# Centered Table Empty States Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** แสดง empty message กึ่งกลาง table body สำหรับ Position/Basket, Basket History และ Trade Fills

**Architecture:** เพิ่ม `TableEmptyStateOverlay` ที่เป็น `QLabel` child ของ `QTableWidget.viewport()` และใช้จริงกับสาม table surfaces. Overlay align ตรงกลาง viewport และเปิดเฉพาะ empty copy; loading/error/stale labels ใช้ state row เดิม

**Tech Stack:** Python 3, PySide6, pytest-qt, Ruff, mypy

## Global Constraints

- UI ใช้ภาษาอังกฤษ; code และ identifiers ใช้ภาษาอังกฤษ
- Overlay ต้องไม่ทับ horizontal header และต้องไม่รับ mouse event
- ห้ามเปลี่ยน query, pagination, basket selection, fill loading หรือ persistence

---

### Task 1: Add a reusable empty-state overlay

**Files:**
- Create: `src/tiewtrade/ui/table_empty_state.py`
- Create: `tests/unit/ui/test_table_empty_state.py`

**Interfaces:**
- Produces: `TableEmptyStateOverlay(table: QTableWidget)`
- Behavior: `show_message(message)` positions the label at the center of `table.viewport()` and lets pointer events pass through

- [x] Write a failing pytest-qt test with a shown table that asserts the label is visible, center-aligned inside the viewport, below the horizontal header, and transparent to mouse events.
- [x] Run `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/unit/ui/test_table_empty_state.py` and confirm RED.
- [x] Implement the minimal overlay widget using a child label, `Qt.WA_TransparentForMouseEvents`, table viewport geometry, and resize/event filtering.
- [x] Re-run the focused test and confirm GREEN.

### Task 2: Use the overlay for Position / Basket

**Files:**
- Modify: `src/tiewtrade/ui/position_basket_table.py`
- Modify: `tests/unit/ui/test_position_basket_table.py`

**Interfaces:**
- Consumes: `TableEmptyStateOverlay`
- Produces: centered empty `No open Position or Basket`; existing non-empty/loading/error/stale state behavior

- [x] Write a failing test that shows an empty Position/Basket snapshot and asserts the empty label center is near the table viewport center and the label is hidden after a ready Basket row.
- [x] Run the focused Position/Basket test and confirm RED.
- [x] Replace the vertical empty-label placement with the overlay only for `WorkspaceTabState.EMPTY`; preserve current state row placement for other non-ready states.
- [x] Re-run focused test and confirm GREEN.

### Task 3: Use the overlay for Trade History tables

**Files:**
- Modify: `src/tiewtrade/ui/trade_history_page.py`
- Modify: `tests/unit/ui/test_trade_history_page.py`

**Interfaces:**
- Consumes: `TableEmptyStateOverlay`
- Produces: centered `No trade history` for an empty basket result and `No fills for this Basket` for empty selected fills

- [x] Write failing UI tests for centered Basket History and Trade Fills empty labels, plus hidden overlays after durable rows.
- [x] Run `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/unit/ui/test_trade_history_page.py` and confirm RED.
- [x] Attach one overlay to each history table and make `_set_basket_state` / `_set_fill_state` route only the two exact empty copies to overlays; preserve state row and retry buttons otherwise.
- [x] Re-run focused test and confirm GREEN.

### Task 4: Verify all empty table states

**Files:**
- Modify: `docs/superpowers/plans/2026-08-09-centered-table-empty-states.md` to mark complete

- [x] Run `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/unit/ui/test_table_empty_state.py tests/unit/ui/test_position_basket_table.py tests/unit/ui/test_trade_history_page.py tests/acceptance/test_desktop_trade_history.py`.
- [x] Run `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q && .venv/bin/python -m ruff check src tests && .venv/bin/python -m ruff format --check src tests && PYTHONPATH=src .venv/bin/python -m mypy src && git diff --check`.
- [x] Commit only the overlay implementation, its tests, and this plan; do not stage `README.md`.
