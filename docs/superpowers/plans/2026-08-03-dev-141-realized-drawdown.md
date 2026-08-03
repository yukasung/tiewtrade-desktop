# DEV-141 Realized Drawdown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** แสดง realized Drawdown ที่ deterministic และ accessible ใน Trading Workspace เพื่อปิด Paper Trading Complete Gate.

**Architecture:** โมดูล `trading` คำนวณ cumulative realized equity จาก closed Basket results; `application` แปลงผลเป็น immutable Workspace fact; Paper Runtime อ่าน durable history ของ Session แล้ว publish snapshot ที่มี fact นี้เสมอ. PySide6 UI render ค่า supplied เท่านั้นและไม่คำนวณ PnL หรือ Drawdown.

**Tech Stack:** Python 3.13, `Decimal`, frozen dataclasses, SQLite, PySide6, pytest/pytest-qt.

## Global Constraints

- Drawdown คือ monetary realized-equity drawdown หน่วย USDT `Decimal`; ห้ามใช้ `float` หรือ percentage
- ไม่มี closed Basket ต้องเป็น unavailable (`None`) ไม่ใช่ `Decimal("0")`
- History ต้อง deterministic และ session-scoped: `closed_at_utc ASC, basket_id ASC`
- UI ห้าม import SQLite, execution, strategy, private endpoint หรือคำนวณ Drawdown
- Drawdown เป็น monitoring fact เท่านั้น: ห้ามเปลี่ยน entry, exit, capital allocation หรือ forced close
- ห้ามเพิ่ม Live credentials, Testnet, Binance Private API หรือ Live order
- Qt tests ใช้ `QT_QPA_PLATFORM=offscreen PYTHONPATH=src`

---

## File structure

- Create: `src/tiewtrade/trading/realized_drawdown.py` — pure exact-Decimal projection
- Modify: `src/tiewtrade/application/trading_workspace.py` — immutable `DrawdownSnapshot`
- Modify: `src/tiewtrade/integrations/sqlite/trade_history.py` — focused ordered session query
- Modify: `src/tiewtrade/application/paper_runtime.py` — durable snapshot composition
- Modify: `src/tiewtrade/ui/trading_workspace.py` — header presentation
- Modify tests in `tests/unit/trading`, `tests/unit/application`, `tests/unit/integrations/sqlite`, `tests/unit/ui`, and `tests/acceptance`
- Modify: `PROJECT_PLAN.md` only after verification

### Task 1: Exact realized-equity calculation

**Files:**

- Create: `src/tiewtrade/trading/realized_drawdown.py`
- Test: `tests/unit/trading/test_realized_drawdown.py`

**Interfaces:**

- Produces `RealizedDrawdown(realized_equity: Decimal, peak_realized_equity: Decimal, amount: Decimal)`
- Produces `realized_drawdown(baskets: tuple[BasketResult, ...]) -> RealizedDrawdown | None`

- [ ] **Step 1: Write the failing tests**

```python
def test_realized_drawdown_uses_closed_basket_equity_curve() -> None:
    snapshot = realized_drawdown((
        basket_result(net_realized_pnl=Decimal("10")),
        basket_result(net_realized_pnl=Decimal("-4")),
        basket_result(net_realized_pnl=Decimal("2")),
    ))

    assert snapshot == RealizedDrawdown(
        realized_equity=Decimal("8"),
        peak_realized_equity=Decimal("10"),
        amount=Decimal("2"),
    )


def test_realized_drawdown_is_unavailable_without_closed_history() -> None:
    assert realized_drawdown(()) is None
```

Also cover an all-loss curve, non-finite PnL, and an open Basket.

- [ ] **Step 2: Verify red**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/trading/test_realized_drawdown.py -q`

Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement minimal projection**

```python
@dataclass(frozen=True, slots=True)
class RealizedDrawdown:
    realized_equity: Decimal
    peak_realized_equity: Decimal
    amount: Decimal


def realized_drawdown(
    baskets: tuple[BasketResult, ...],
) -> RealizedDrawdown | None:
    if not baskets:
        return None
    equity = Decimal("0")
    peak = Decimal("0")
    for basket in baskets:
        if basket.status is not BasketStatus.CLOSED:
            raise ValueError("Drawdown requires closed Baskets")
        equity += basket.net_realized_pnl
        peak = max(peak, equity)
    return RealizedDrawdown(equity, peak, peak - equity)
```

Validate finite `Decimal` inputs and non-negative output.

- [ ] **Step 4: Verify green**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/trading/test_realized_drawdown.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tiewtrade/trading/realized_drawdown.py tests/unit/trading/test_realized_drawdown.py
git commit -m "feat: calculate realized drawdown"
```

### Task 2: Durable history to immutable Workspace fact

**Files:**

- Modify: `src/tiewtrade/application/trading_workspace.py`
- Modify: `src/tiewtrade/integrations/sqlite/trade_history.py`
- Test: `tests/unit/application/test_trading_workspace.py`
- Test: `tests/unit/integrations/sqlite/test_trade_history_query.py`

**Interfaces:**

- Produces `DrawdownSnapshot | None` on `TradingWorkspaceSnapshot.drawdown`
- Produces `workspace_with_drawdown(workspace, drawdown)`
- Produces `SQLiteTradeHistory.list_closed_baskets_for_session(session_id) -> tuple[BasketResult, ...]`

- [ ] **Step 1: Write failing tests**

```python
def test_workspace_preserves_drawdown_through_loading_error_and_stale() -> None:
    ready = replace(
        configured_workspace_snapshot(configured_spot_session(), observed_at_utc=_utc()),
        drawdown=DrawdownSnapshot(
            realized_equity=Decimal("8"),
            peak_realized_equity=Decimal("10"),
            amount=Decimal("2"),
        ),
    )

    assert loading_workspace_snapshot(ready).drawdown == ready.drawdown
    assert failed_workspace_snapshot(ready, "Workspace data is unavailable").drawdown == ready.drawdown
    assert stale_workspace_snapshot(ready).drawdown == ready.drawdown
```

Persist Basket records across two Sessions and assert the new query returns only the requested Session, closed-only, in `closed_at_utc ASC, basket_id ASC` order.

- [ ] **Step 2: Verify red**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/application/test_trading_workspace.py tests/unit/integrations/sqlite/test_trade_history_query.py -q`

Expected: FAIL because the new fact and query do not exist.

- [ ] **Step 3: Implement the focused boundaries**

Add frozen `DrawdownSnapshot` to `application/trading_workspace.py` with finite-Decimal validation. Add `drawdown: DrawdownSnapshot | None = None` to `TradingWorkspaceSnapshot`. Implement `workspace_with_drawdown` using `replace` so existing loading/error/stale helpers preserve the same immutable fact.

Add one parameterized SQLite query:

```sql
SELECT * FROM basket_results
WHERE session_id = ? AND status = ?
ORDER BY closed_at_utc ASC, basket_id ASC
```

Map using existing `_basket_from_row`; do not add a new table or schema migration.

- [ ] **Step 4: Verify green**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/application/test_trading_workspace.py tests/unit/integrations/sqlite/test_trade_history_query.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tiewtrade/application/trading_workspace.py src/tiewtrade/integrations/sqlite/trade_history.py tests/unit/application/test_trading_workspace.py tests/unit/integrations/sqlite/test_trade_history_query.py
git commit -m "feat: project drawdown into workspace"
```

### Task 3: Runtime publication and accessible Desktop header

**Files:**

- Modify: `src/tiewtrade/application/paper_runtime.py`
- Modify: `src/tiewtrade/ui/trading_workspace.py`
- Test: `tests/unit/application/test_paper_runtime.py`
- Test: `tests/unit/ui/test_trading_workspace.py`
- Test: `tests/acceptance/test_workspace_responsive_accessibility.py`

**Interfaces:**

- Consumes: `list_closed_baskets_for_session`, `realized_drawdown`, and `workspace_with_drawdown`
- Produces: `TradingWorkspace.header_drawdown`

- [ ] **Step 1: Write failing tests**

```python
def test_runtime_snapshot_includes_durable_realized_drawdown(...) -> None:
    result = controller.inspect_startup(configured_session)

    assert result.workspace.drawdown is not None
    assert result.workspace.drawdown.amount == Decimal("2")


def test_header_renders_exact_drawdown_and_unavailable_accessibly(qtbot: QtBot) -> None:
    workspace.show_workspace_snapshot(snapshot_with_drawdown)
    assert workspace.header_drawdown.text() == "Drawdown: 2 USDT"
    assert workspace.header_drawdown.accessibleName() == "Drawdown: 2 USDT"

    workspace.show_workspace_snapshot(configured_snapshot_without_history)
    assert workspace.header_drawdown.text() == "Drawdown: Not available"
```

Extend responsive acceptance to assert the text is visible at 1200px and 1199px.

- [ ] **Step 2: Verify red**

Run: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest tests/unit/application/test_paper_runtime.py tests/unit/ui/test_trading_workspace.py tests/acceptance/test_workspace_responsive_accessibility.py -q`

Expected: FAIL because Runtime does not project history and the label does not exist.

- [ ] **Step 3: Implement composition and rendering**

Add the focused controller method:

```python
def _workspace_with_drawdown(
    self, workspace: TradingWorkspaceSnapshot, session: ConfiguredPaperSession
) -> TradingWorkspaceSnapshot:
    baskets = self._trade_history.list_closed_baskets_for_session(
        session.config.session_id
    )
    return workspace_with_drawdown(workspace, realized_drawdown(baskets))
```

Apply it to configured, startup/recovery, runtime-ready, candle, stop, and blocked publications. Query failure follows existing fail-closed behavior; never fabricate zero.

Create `header_drawdown = QLabel("Drawdown: Not available")`, place it in the persistent header, and format supplied `amount` with `format(amount, "f")`. Set exact matching text and accessible name; UI must not import the calculator.

- [ ] **Step 4: Verify green**

Run: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest tests/unit/application/test_paper_runtime.py tests/unit/ui/test_trading_workspace.py tests/acceptance/test_workspace_responsive_accessibility.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tiewtrade/application/paper_runtime.py src/tiewtrade/ui/trading_workspace.py tests/unit/application/test_paper_runtime.py tests/unit/ui/test_trading_workspace.py tests/acceptance/test_workspace_responsive_accessibility.py
git commit -m "feat: show paper workspace drawdown"
```

### Task 4: Complete Gate verification

**Files:**

- Modify: `PROJECT_PLAN.md`
- Test: repository quality gates

- [ ] **Step 1: Add final acceptance evidence**

Assert a closed Basket sequence produces `Drawdown: 2 USDT`; configured Session history without a closed Basket produces `Drawdown: Not available`.

- [ ] **Step 2: Run Paper acceptance**

Run: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest tests/acceptance/test_paper_spot_replay.py tests/acceptance/test_paper_futures_session.py tests/acceptance/test_desktop_session_setup.py tests/acceptance/test_workspace_responsive_accessibility.py -q`

Expected: PASS.

- [ ] **Step 3: Record verified status**

Update Milestone 3 in `PROJECT_PLAN.md`: Drawdown is an exact-Decimal realized, closed-Basket monitoring fact; it is unavailable before closed history and never forces a close.

- [ ] **Step 4: Run all quality gates**

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest -q
PYTHONPATH=src .venv/bin/python -m ruff check src tests
PYTHONPATH=src .venv/bin/python -m ruff format --check src tests
PYTHONPATH=src .venv/bin/python -m mypy
npm --prefix docs-site test
npm --prefix docs-site run check:content
git diff --check
```

Expected: every command passes.

- [ ] **Step 5: Commit verified delivery**

```bash
git add PROJECT_PLAN.md tests/acceptance/test_workspace_responsive_accessibility.py
git commit -m "test: verify realized drawdown gate"
```

## Plan self-review

- Spec coverage: Tasks 1–3 cover exact semantics, unavailable state, immutable propagation, deterministic durable history, UI/accessibility, and stale publication protection. Task 4 covers complete verification and documentation.
- Placeholder scan: no incomplete implementation markers remain.
- Type consistency: `RealizedDrawdown` is the pure projection type; `DrawdownSnapshot` is the Workspace-bound type; all runtime publication uses `workspace_with_drawdown`.

