# One-Minute Timeframe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `1m` available when creating immutable Paper Sessions, through the shared timeframe rule that future Live runtime will consume.

**Architecture:** `tiewtrade.market_data.config` owns the V1 timeframe allowlist. `MarketDataConfig`, Session Setup, Trade History and public Binance adapters consume it, so the feature is one list expansion plus contracts proving no consumer carries a local list.

**Tech Stack:** Python 3.12+, PySide6, pytest/pytest-qt, Ruff, mypy.

## Global Constraints

- Add `1m` only to the shared V1 timeframe allowlist, before `3m`.
- Paper uses `1m` immediately; do not create Live runtime, credentials, preflight, or orders.
- A Session timeframe remains immutable; changing to `1m` uses the existing replacement flow.
- Use Paper adapters and fake/public-market-data contracts only in tests.
- Keep UI copy in English and documentation in Thai.

---

### Task 1: Expand the shared V1 timeframe rule

**Files:**

- Modify: `src/tiewtrade/market_data/config.py:6-34`
- Modify: `tests/unit/market_data/test_market_data_config.py:1-40`
- Modify: `PRODUCT.md:21-29`

**Interfaces:**

- Produces: `SUPPORTED_V1_TIMEFRAME_CHOICES == ("1m", "3m", "5m", "15m", "30m", "1h", "4h")`.
- Consumed by: `MarketDataConfig`, `SessionSetupWidget`, `TradeHistoryPage`, public Binance adapters.

- [ ] **Step 1: Write the failing test**

```python
def test_market_data_config_accepts_one_minute_timeframe() -> None:
    config = MarketDataConfig(symbol="BTCUSDT", timeframe="1m")

    assert config.interval == timedelta(minutes=1)
```

Remove `"1m"` from the parameterized unsupported-timeframe test.

- [ ] **Step 2: Verify the existing shared consumer paths**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/market_data/test_market_data_config.py`

Expected: FAIL because `1m` is outside `SUPPORTED_V1_TIMEFRAMES`.

- [ ] **Step 3: Implement the smallest domain change**

```python
SUPPORTED_V1_TIMEFRAME_CHOICES = (
    "1m",
    "3m",
    "5m",
    "15m",
    "30m",
    "1h",
    "4h",
)
```

Update the product’s completed-candle list to start with `1m`.

- [ ] **Step 4: Verify green**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/market_data/test_market_data_config.py`

Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add PRODUCT.md src/tiewtrade/market_data/config.py tests/unit/market_data/test_market_data_config.py
git commit -m "feat: support one-minute market data timeframe"
```

### Task 2: Cover UI and public-market-data consumers

**Files:**

- Modify: `tests/unit/ui/test_session_setup.py:90-105`
- Modify: `tests/unit/ui/test_trade_history_page.py:55-95`
- Modify: `tests/unit/integrations/binance/test_public_market_data.py:125-170`

**Interfaces:**

- Consumes: `SUPPORTED_V1_TIMEFRAME_CHOICES` from Task 1.
- Produces: UI and Binance public-market-data contracts for `1m`.

- [ ] **Step 1: Write the failing consumer tests**

```python
assert [widget.timeframe.itemData(index) for index in range(widget.timeframe.count())] == [
    "1m", "3m", "5m", "15m", "30m", "1h", "4h"
]

assert [page.timeframe.itemText(index) for index in range(page.timeframe.count())] == [
    "All", "1m", "3m", "5m", "15m", "30m", "1h", "4h"
]
```

Add a public-market-data test with `MarketDataConfig(symbol="BTCUSDT", timeframe="1m")`; assert REST parameters and websocket stream include `1m`.

- [ ] **Step 2: Verify red**

Run: `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/unit/ui/test_session_setup.py tests/unit/ui/test_trade_history_page.py tests/unit/integrations/binance/test_public_market_data.py`

Expected: PASS. The assertions should pass without changing UI or adapter production code, proving each consumer reads the shared list or immutable `config.timeframe`.

- [ ] **Step 3: Retain the shared-constant implementation**

Do not add any UI or adapter timeframe lists. `SessionSetupWidget` and `TradeHistoryPage` already iterate `SUPPORTED_V1_TIMEFRAME_CHOICES`; Binance adapters already receive `config.timeframe`.

- [ ] **Step 4: Verify green**

Run: `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/unit/ui/test_session_setup.py tests/unit/ui/test_trade_history_page.py tests/unit/integrations/binance/test_public_market_data.py`

Expected: PASS.

- [ ] **Step 5: Commit Task 2**

```bash
git add tests/unit/ui/test_session_setup.py tests/unit/ui/test_trade_history_page.py tests/unit/integrations/binance/test_public_market_data.py
git commit -m "test: cover one-minute timeframe consumers"
```

### Task 3: Verify the completed feature

**Files:**

- Verify: `PRODUCT.md`, `src/tiewtrade/market_data/config.py`
- Verify: `src/tiewtrade/ui/session_setup.py`, `src/tiewtrade/ui/trade_history_page.py`

**Interfaces:**

- Consumes: Tasks 1–2.
- Produces: verified `1m` support for Paper and the shared domain contract for future Live runtime.

- [ ] **Step 1: Run focused coverage**

Run: `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/unit/market_data/test_market_data_config.py tests/unit/application/test_paper_session_setup.py tests/unit/ui/test_session_setup.py tests/unit/ui/test_trade_history_page.py tests/unit/integrations/binance/test_public_market_data.py`

Expected: PASS.

- [ ] **Step 2: Run the quality gate**

```bash
PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests
.venv/bin/python -m ruff format --check src tests
PYTHONPATH=src .venv/bin/python -m mypy src
git diff --check
```

Expected: all checks pass.
