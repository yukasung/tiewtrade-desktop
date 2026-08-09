# Remove Paper Open Orders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ลบ Open Orders projection, UI และ Paper runtime code path ออกทั้งหมด โดยคง Position/Basket, Trade History และ durable fill audit

**Architecture:** `TradingWorkspaceSnapshot` จะมีเพียง header และ Position/Basket presentation state; `PaperRuntime` จะไม่แปลง pending Entry Intent เป็น Open Order presentation. `TradingWorkspace` จะ render สอง tab และ runtime relay จะเปรียบเทียบเฉพาะ state ที่ยังมีผู้ใช้จริง

**Tech Stack:** Python 3, PySide6, pytest, pytest-qt, Ruff, mypy

## Global Constraints

- ใช้ Paper Trading และ fake adapters ใน tests; ห้ามเพิ่ม Live order
- ห้ามลบ `Order`, `TradeFill`, execution persistence หรือ audit history
- UI ใช้ภาษาอังกฤษ; code และ identifiers ใช้ภาษาอังกฤษ
- ไม่มี schema migration เพราะ Open Orders เป็น in-memory presentation projection

---

### Task 1: Remove the Open Orders application projection

**Files:**
- Modify: `src/tiewtrade/application/trading_workspace.py`
- Modify: `tests/unit/application/test_trading_workspace.py`
- Delete: `src/tiewtrade/ui/open_orders_table.py`
- Delete: `tests/unit/ui/test_open_orders_table.py`

**Interfaces:**
- Removes: `OpenOrderSnapshot`, `OpenOrdersTabSnapshot`, `empty_open_orders_tab`, `ready_open_orders_tab`, `loading_open_orders_tab`, `failed_open_orders_tab`, `stale_open_orders_tab`
- Changes: `TradingWorkspaceSnapshot` has no `open_orders` field or `orders` property
- Preserves: `PositionBasketTabSnapshot`, `BasketSnapshot`, `configured_workspace_snapshot`, `paper_runtime_workspace_snapshot`

- [x] **Step 1: Write failing tests**

Update workspace snapshot tests to construct snapshots without an Open Orders field and assert only the Position/Basket projection is retained.

- [x] **Step 2: Run RED test**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/application/test_trading_workspace.py`

Expected: FAIL because the application projection still requires Open Orders.

- [x] **Step 3: Remove the model**

Delete the Open Order dataclasses, factories, validators and field/property from `trading_workspace.py`; update snapshot constructors to retain Position/Basket only. Delete the isolated Open Orders table and its tests.

- [x] **Step 4: Run GREEN tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/application/test_trading_workspace.py`

Expected: PASS.

### Task 2: Remove Paper runtime and relay dependencies

**Files:**
- Modify: `src/tiewtrade/application/paper_runtime.py`
- Modify: `src/tiewtrade/application/bot_control.py`
- Modify: `tests/unit/application/test_paper_runtime.py`
- Modify: `tests/unit/application/test_bot_control.py`

**Interfaces:**
- Removes: `_pending_orders(intent)` presentation helper
- Changes: Paper runtime emits workspace snapshots without pending order projection
- Preserves: Entry intent execution, immediate Paper fills, Position/Basket state and runtime lifecycle

- [x] **Step 1: Write failing tests**

Replace assertions for `workspace.open_orders` with assertions that a pending intent does not introduce a separate workspace projection while Position/Basket and lifecycle state remain correct.

- [x] **Step 2: Run RED tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/application/test_paper_runtime.py tests/unit/application/test_bot_control.py`

Expected: FAIL because runtime and relay still create or compare Open Orders data.

- [x] **Step 3: Remove the runtime projection**

Delete `_pending_orders`, remove the `orders=` argument when making workspace snapshots, and remove `open_orders` from the runtime relay change comparison.

- [x] **Step 4: Run GREEN tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/application/test_paper_runtime.py tests/unit/application/test_bot_control.py`

Expected: PASS.

### Task 3: Remove the Workspace tab and update workflows

**Files:**
- Modify: `src/tiewtrade/ui/trading_workspace.py`
- Modify: `src/tiewtrade/ui/theme.py`
- Modify: `tests/unit/ui/test_trading_workspace.py`
- Modify: `tests/unit/ui/test_theme.py`
- Modify: `tests/acceptance/test_desktop_session_setup.py`
- Modify: `tests/acceptance/test_notification_safety_feedback.py`
- Modify: `tests/acceptance/test_workspace_responsive_accessibility.py`

**Interfaces:**
- Removes: `TradingWorkspace.open_orders`, `Open Orders` tab and its state updates
- Produces: tab index 0 `Position / Basket`; tab index 1 `Trade History`
- Preserves: `trade_history_activated` signal when index 1 is selected

- [x] **Step 1: Write failing UI/acceptance assertions**

Change tab expectations to exactly `("Position / Basket", "Trade History")`, remove Open Orders table assertions, and move Trade History selection assertions to index 1.

- [x] **Step 2: Run RED tests**

Run: `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/unit/ui/test_trading_workspace.py tests/unit/ui/test_theme.py tests/acceptance/test_desktop_session_setup.py tests/acceptance/test_notification_safety_feedback.py tests/acceptance/test_workspace_responsive_accessibility.py`

Expected: FAIL because the Workspace still creates the Open Orders widget and tab.

- [x] **Step 3: Remove UI wiring**

Remove the Open Orders import/widget, tab construction and `show_workspace_snapshot` update. Remove obsolete Open Orders theme selector while retaining generic state-label style used by the remaining tabs. Update `_tab_changed` to emit history activation for index 1.

- [x] **Step 4: Run GREEN tests**

Run the command from Step 2.

Expected: PASS.

### Task 4: Update documentation and prove no Paper Open Orders remain

**Files:**
- Modify: `PRODUCT.md`
- Modify: `docs/superpowers/specs/2026-08-08-new-paper-session-after-stop-design.md`
- Modify: `docs/superpowers/plans/2026-08-09-remove-paper-open-orders.md`

- [x] **Step 1: Update product language**

Remove Paper-specific references to Open Orders while retaining Position/Basket wording and fill audit terminology.

- [x] **Step 2: Confirm source removal**

Run: `rg -n -i "open.?orders?|OpenOrder|open_order|openOrders" src/tiewtrade tests`

Expected: no output.

- [x] **Step 3: Run final verification**

Run: `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q && .venv/bin/python -m ruff check src tests && .venv/bin/python -m ruff format --check src tests && PYTHONPATH=src .venv/bin/python -m mypy src && git diff --check`

Expected: all commands exit 0.

- [ ] **Step 4: Commit only scoped files**

Run: `git add PRODUCT.md docs/superpowers src/tiewtrade tests && git commit -m "refactor: remove paper open orders"`

Do not stage the user's `README.md` change.
