# Binance-Style Trade History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the connected, paginated Basket/Fills presentation with independent unpaginated `Basket History` and cross-Basket `Trade History` tabs.

**Architecture:** Immutable application read models own unpaginated histories. SQLite reads Basket results and joined Fill facts; the Qt workflow runs only the active tab's query in a background task and preserves the other tab's durable result. `TradeHistoryPage` presents shared filters and two tab-specific tables without selecting a Basket.

**Tech Stack:** Python 3, PySide6, SQLite, pytest, pytest-qt, Ruff, mypy.

## Global Constraints

- UI copy is English; code comments and identifiers are English.
- Use Paper/fake adapters only; do not change Live execution, credentials, SQLite schema, durable records, Session lifecycle, strategy, Basket lifecycle, fee, or slippage policy.
- Trade rows order by `filled_at_utc DESC, fill_id DESC`.
- No `PageRequest`, Previous/Next controls, or page count remains in active Trade History application/UI/workflow/composition/tests.
- Retain `list_fills(basket_id)` for chart/manual-close consumers; the Trade History UI uses `list_trade_fills(filters)`.
- Do not stage or modify user-owned `README.md`.

---

### Task 1: Publish unpaginated history read models and presenter rows

**Files:**
- Modify: `src/tiewtrade/application/trade_history.py`
- Modify: `src/tiewtrade/ui/trade_history_presenter.py`
- Modify: `tests/unit/application/test_trade_history_query.py`
- Modify: `tests/unit/ui/test_trade_history_presenter.py`

**Interfaces:**
- Produces `BasketHistoryResult(items: tuple[BasketResult, ...], net_realized_pnl: Decimal)`.
- Produces `TradeFillHistoryItem(fill: TradeFill, symbol: str, timeframe: str, market_type: MarketType, trade_mode: TradeMode)`.
- Produces `trade_fill_rows(items: tuple[TradeFillHistoryItem, ...]) -> tuple[FillRow, ...]`.

- [ ] Write failing tests for the new models and rows.

```python
def test_trade_fill_history_row_contains_joined_symbol_and_execution_facts() -> None:
    item = TradeFillHistoryItem(
        fill=trade_fill(price=Decimal("65000"), quantity=Decimal("0.02")),
        symbol="BTCUSDT", timeframe="5m",
        market_type=MarketType.SPOT, trade_mode=TradeMode.PAPER,
    )
    assert trade_fill_rows((item,))[0].values == (
        "2026-08-02 01:02:04 UTC", "BTCUSDT", "Buy", "65000", "0.02",
        "0.00 USDT", "1300 USDT", "0.00 USDT · Break-even", "Paper",
    )
```

- [ ] Run `PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/application/test_trade_history_query.py tests/unit/ui/test_trade_history_presenter.py`; expect RED because these interfaces are absent.
- [ ] Implement the frozen dataclasses. Map Total from durable `fill.notional`, never price × quantity in the UI. Leave `PageState` until its callers move in Task 5.

```python
@dataclass(frozen=True, slots=True)
class BasketHistoryResult:
    items: tuple[BasketResult, ...]
    net_realized_pnl: Decimal

@dataclass(frozen=True, slots=True)
class TradeFillHistoryItem:
    fill: TradeFill
    symbol: str
    timeframe: str
    market_type: MarketType
    trade_mode: TradeMode
```

- [ ] Re-run the focused command; expect GREEN.
- [ ] Commit Task 1 files with message `feat: add unpaginated trade history read models`.

### Task 2: Query complete Basket and cross-Basket Fill histories from SQLite

**Files:**
- Modify: `src/tiewtrade/integrations/sqlite/trade_history.py`
- Modify: `tests/unit/integrations/sqlite/test_trade_history_query.py`
- Modify: `tests/acceptance/test_trade_history_query_acceptance.py`

**Interfaces:**
- Produces `SQLiteTradeHistory.list_baskets(filters: TradeHistoryFilter) -> BasketHistoryResult`.
- Produces `SQLiteTradeHistory.list_trade_fills(filters: TradeHistoryFilter) -> tuple[TradeFillHistoryItem, ...]`.
- Preserves `list_fills(basket_id: UUID) -> tuple[TradeFill, ...]` unchanged.

- [ ] Write failing SQLite tests.

```python
def test_list_trade_fills_filters_joined_baskets_and_orders_newest_first(history) -> None:
    newest, oldest, excluded = record_three_fills(history)
    result = history.list_trade_fills(
        TradeHistoryFilter(symbol="BTCUSDT", trade_mode=TradeMode.PAPER)
    )
    assert tuple(item.fill.fill_id for item in result) == (newest.fill_id, oldest.fill_id)
    assert excluded.fill_id not in {item.fill.fill_id for item in result}

def test_list_baskets_returns_all_matching_rows_without_page_request(history) -> None:
    assert len(history.list_baskets(TradeHistoryFilter()).items) == 3
```

- [ ] Run `PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/integrations/sqlite/test_trade_history_query.py tests/acceptance/test_trade_history_query_acceptance.py`; expect RED.
- [ ] Implement joined durable Fill query and unpaginated Basket query.

```sql
SELECT trade_fills.*, basket_results.symbol, basket_results.timeframe,
       basket_results.market_type, basket_results.trade_mode
FROM trade_fills
JOIN basket_results ON basket_results.basket_id = trade_fills.basket_id
{where_sql}
ORDER BY trade_fills.filled_at_utc DESC, trade_fills.fill_id DESC
```

Use a dedicated `TradeFillHistoryItem` row mapper. Remove `LIMIT`, `OFFSET`, count query, and `PageRequest` from `list_baskets`; preserve `_basket_where` and durable Net PnL aggregation.

- [ ] Re-run the focused command; expect GREEN, including existing `list_fills` contracts.
- [ ] Commit Task 2 files with message `feat: query unpaginated basket and fill histories`.

### Task 3: Replace the workflow with independent active-tab requests

**Files:**
- Modify: `src/tiewtrade/ui/trade_history_workflow.py`
- Modify: `tests/unit/ui/test_trade_history_workflow.py`

**Interfaces:**
- Consumes `list_baskets: Callable[[TradeHistoryFilter], BasketHistoryResult]` and `list_trade_fills: Callable[[TradeHistoryFilter], tuple[TradeFillHistoryItem, ...]]`.
- Produces `HistoryTab.BASKETS`, `HistoryTab.TRADES`, `select_tab(tab)`, `retry_baskets()`, and `retry_trades()`.
- Emits loading/ready/empty/unavailable signals per tab; removes `select_basket`, `go_to_page`, and Basket-specific Fill signals.

- [ ] Write a failing independent-tab test.

```python
def test_selecting_trade_tab_queries_all_fills_without_selecting_a_basket(qtbot) -> None:
    captured: list[TradeHistoryFilter] = []
    workflow = TradeHistoryWorkflow(
        list_baskets=lambda _filters: BasketHistoryResult((), Decimal("0")),
        list_trade_fills=lambda filters: captured.append(filters) or (trade_history_item(),),
    )
    with qtbot.waitSignal(workflow.trades_ready):
        workflow.select_tab(HistoryTab.TRADES)
    assert captured == [TradeHistoryFilter()]
```

- [ ] Run `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/unit/ui/test_trade_history_workflow.py`; expect RED.
- [ ] Implement two generation-guarded channels, one per tab. Each owns task, generation, pending filter, failed filter, and loading. `apply_filters` validates once and requests only the active channel. Ignore obsolete callbacks and callbacks after `close()`; a Trade failure must not clear Basket durable results.

```python
class HistoryTab(Enum):
    BASKETS = auto()
    TRADES = auto()

def select_tab(self, tab: HistoryTab) -> None:
    if self._closed or tab is self._active_tab:
        return
    self._active_tab = tab
    self._request_active_tab()
```

- [ ] Re-run the workflow test; expect GREEN for start, tab switch, validation, retry, stale callback suppression, and close.
- [ ] Commit Task 3 files with message `refactor: separate basket and trade history workflow`.

### Task 4: Rebuild the page as Binance-style history tabs

**Files:**
- Modify: `src/tiewtrade/ui/trade_history_page.py`
- Modify: `src/tiewtrade/ui/theme.py`
- Modify: `tests/unit/ui/test_trade_history_page.py`
- Modify: `tests/unit/ui/test_theme.py`

**Interfaces:**
- Consumes `HistoryTab`, `BasketHistoryResult`, `TradeFillHistoryItem`, `basket_rows`, and `trade_fill_rows`.
- Produces `history_tab_changed(HistoryTab)`, `baskets_retry_requested()`, `trades_retry_requested()`, and tab-specific display methods.

- [ ] Write a failing layout/visibility test.

```python
def test_page_uses_two_history_tabs_and_hides_status_for_trade_history(qtbot) -> None:
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    assert page.history_tabs.tabText(0) == "Basket History"
    assert page.history_tabs.tabText(1) == "Trade History"
    page.history_tabs.setCurrentIndex(1)
    assert page.status.isHidden()
    assert page.trade_table.isVisible()
    assert page.basket_table.isHidden()
    assert page.findChildren(QPushButton, "previousPageButton") == []
```

- [ ] Run `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/unit/ui/test_trade_history_page.py tests/unit/ui/test_theme.py`; expect RED.
- [ ] Use `QTabWidget` or `QTabBar` with one table in each tab. Keep filters above, move Basket Net PnL summary inside its tab, and show Status only there. Remove pagination, Basket selection, and Fill-detail state. Attach `TableEmptyStateOverlay` to each table viewport. Define:

```python
TRADE_HEADERS = (
    "Filled At", "Symbol", "Side", "Price", "Quantity", "Fee", "Total",
    "Realized PnL", "Source",
)
```

Update theme selectors for active tab accent/underline and Side/PnL supplementary colors.

- [ ] Re-run the page/theme test; expect GREEN for headers, active tab, filter visibility, overlays, retry scope, and no pagination widgets.
- [ ] Commit Task 4 files with message `feat: show basket and trade history tabs`.

### Task 5: Rewire composition and migrate direct callers

**Files:**
- Modify: `src/tiewtrade/ui/main_window.py`
- Modify: `src/tiewtrade/ui/desktop.py`
- Modify: `src/tiewtrade/desktop_main.py`
- Modify: `tests/unit/ui/test_main_window.py`
- Modify: `tests/unit/test_desktop_main.py`
- Modify: `tests/acceptance/test_desktop_trade_history.py`
- Modify: all direct `list_baskets(filters, PageRequest(...))` callers under `src/` and `tests/`.

**Interfaces:**
- Inject `list_baskets_after_migration(filters)` and `list_trade_fills_after_migration(filters)` into `TradeHistoryWorkflow`.
- Retain basket-id `list_fills` injection for chart/manual-close consumers, but remove it from Trade History UI composition.

- [ ] Write a failing composition test.

```python
def test_trade_history_tab_loads_cross_basket_fills_from_desktop_composition(qtbot) -> None:
    window = build_window_with_two_baskets_and_fills(qtbot)
    open_trade_history(window)
    window.trade_history.history_tabs.setCurrentIndex(1)
    qtbot.waitUntil(lambda: window.trade_history.trade_table.rowCount() == 2)
    assert window.trade_history.basket_table.rowCount() == 2
```

- [ ] Run `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/unit/ui/test_main_window.py tests/unit/test_desktop_main.py tests/acceptance/test_desktop_trade_history.py`; expect RED.
- [ ] Add the composition adapter below, wire `history_tab_changed` and `trades_retry_requested`, and remove only UI page/selection/detail connections. Update every caller to `history.list_baskets(filters)` while preserving `items` and `net_realized_pnl` assertions.

```python
def list_trade_fills_after_migration(
    filters: TradeHistoryFilter,
) -> tuple[TradeFillHistoryItem, ...]:
    prepare_database()
    return history.list_trade_fills(filters)
```

- [ ] Re-run the focused composition tests plus `tests/acceptance/test_desktop_session_setup.py tests/acceptance/test_desktop_chart.py tests/acceptance/test_notification_safety_feedback.py`; expect GREEN without execution side effects.
- [ ] Commit Task 5 files with message `feat: load cross-basket trade history tabs`.

### Task 6: Verify migration and record completion

**Files:**
- Modify: `docs/superpowers/plans/2026-08-09-binance-style-trade-history.md`

- [ ] Audit obsolete paths with `rg -n "PageRequest|page_requested|go_to_page|basket_selected|select_basket|fills_retry_requested" src/tiewtrade/ui src/tiewtrade/desktop_main.py tests/unit/ui tests/acceptance`; expect no active Trade History UI/workflow/composition reference.
- [ ] Run targeted verification: `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/unit/application/test_trade_history_query.py tests/unit/integrations/sqlite/test_trade_history_query.py tests/unit/ui/test_trade_history_presenter.py tests/unit/ui/test_trade_history_workflow.py tests/unit/ui/test_trade_history_page.py tests/unit/ui/test_main_window.py tests/acceptance/test_trade_history_query_acceptance.py tests/acceptance/test_desktop_trade_history.py`.
- [ ] Run full verification: `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q && .venv/bin/python -m ruff check src tests && .venv/bin/python -m ruff format --check src tests && PYTHONPATH=src .venv/bin/python -m mypy src && git diff --check`.
- [ ] Mark this plan complete and commit it with message `docs: record trade history implementation verification`.
