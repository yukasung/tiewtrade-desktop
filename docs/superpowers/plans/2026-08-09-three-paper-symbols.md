# Three Paper Symbols Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ให้ผู้ใช้เลือก `BTCUSDT`, `ETHUSDT` หรือ `BNBUSDT` เพื่อสร้าง immutable Paper Session ได้อย่างถูกต้องตลอด UI, market data และ Paper runtime

**Architecture:** เพิ่ม allowlist และ Paper symbol rules ที่ application-owned เพื่อเป็น source เดียวของ validation และ runtime rules. UI อ่าน allowlist เพื่อแสดง dropdown แต่ไม่รู้รายละเอียด tick/step/min-notional; desktop composition ขอ rules จาก provider ตาม Session ที่เลือก.

**Tech Stack:** Python 3.12, PySide6, SQLite, pytest, Ruff, mypy

## Global Constraints

- Paper Trading เท่านั้น; ห้ามเพิ่ม Live order, credentials หรือ dynamic Binance exchange-info lookup
- อนุญาตเฉพาะ `BTCUSDT`, `ETHUSDT`, `BNBUSDT`; ห้าม free-text symbol
- Session symbol และ market configuration เป็น immutable
- รักษา single-active-session และ Start replacement flow เดิม
- UI ไม่ import SQLite, adapter หรือ execution module

---

### Task 1: Define Paper symbol allowlist and rules

**Files:**
- Create: `src/tiewtrade/application/paper_symbols.py`
- Create: `tests/unit/application/test_paper_symbols.py`
- Modify: `PRODUCT.md`
- Modify: `docs/superpowers/specs/2026-08-09-three-paper-symbols-design.md`

**Interfaces:**
- Produces: `PAPER_SYMBOLS: tuple[str, ...]`
- Produces: `paper_symbol_rules(symbol: str) -> SymbolRules`
- Produces: `validate_paper_symbol(symbol: str) -> str`

- [ ] **Step 1: Write failing allowlist/rules tests**

```python
@pytest.mark.parametrize("symbol", ["BTCUSDT", "ETHUSDT", "BNBUSDT"])
def test_paper_symbol_rules_support_the_allowlist(symbol: str) -> None:
    assert paper_symbol_rules(symbol).symbol == symbol

def test_paper_symbol_rules_reject_unknown_symbol() -> None:
    with pytest.raises(ValueError, match="Paper symbol is not supported"):
        paper_symbol_rules("DOGEUSDT")
```

- [ ] **Step 2: Verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/application/test_paper_symbols.py`

Expected: FAIL because module/function does not exist.

- [ ] **Step 3: Implement the focused rule module**

```python
PAPER_SYMBOLS = ("BTCUSDT", "ETHUSDT", "BNBUSDT")

def paper_symbol_rules(symbol: str) -> SymbolRules:
    try:
        return _RULES[symbol]
    except KeyError as error:
        raise ValueError("Paper symbol is not supported") from error
```

Use explicit Binance-compatible Paper constants for each symbol; do not add a registry or adapter abstraction.

- [ ] **Step 4: Update product/spec wording**

Replace BTC-only scope and take-profit wording with the three-symbol allowlist and symbol-specific Paper rules.

- [ ] **Step 5: Verify GREEN and commit**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/application/test_paper_symbols.py`

Run: `PYTHONPATH=src .venv/bin/python -m ruff check src/tiewtrade/application/paper_symbols.py tests/unit/application/test_paper_symbols.py`

Commit: `git add PRODUCT.md docs/superpowers/specs/2026-08-09-three-paper-symbols-design.md src/tiewtrade/application/paper_symbols.py tests/unit/application/test_paper_symbols.py && git commit -m "feat: define paper symbol allowlist"`

### Task 2: Validate and select the symbol in Session Setup

**Files:**
- Modify: `src/tiewtrade/application/paper_session_setup.py`
- Modify: `src/tiewtrade/ui/session_setup.py`
- Modify: `tests/unit/application/test_paper_session_setup.py`
- Modify: `tests/unit/ui/test_session_setup.py`

**Interfaces:**
- Consumes: `PAPER_SYMBOLS`, `validate_paper_symbol(symbol)` from Task 1
- Produces: `PaperSessionSetupValues.symbol` constrained to allowlist

- [ ] **Step 1: Write failing validation/UI tests**

```python
def test_create_paper_session_accepts_ethusdt() -> None:
    outcome = create.execute(replace(valid_values, symbol="ETHUSDT"))
    assert outcome.session.market_data.symbol == "ETHUSDT"

def test_setup_symbol_combo_has_only_paper_symbols(qtbot: QtBot) -> None:
    widget = SessionSetupWidget()
    qtbot.addWidget(widget)
    assert [widget.symbol.itemData(index) for index in range(widget.symbol.count())] == [
        "BTCUSDT", "ETHUSDT", "BNBUSDT"
    ]
```

- [ ] **Step 2: Verify RED**

Run: `QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/application/test_paper_session_setup.py tests/unit/ui/test_session_setup.py -k 'ethusdt or symbol_combo'`

Expected: FAIL because BTC-only validation/read-only symbol field remains.

- [ ] **Step 3: Implement minimal validation and dropdown**

Replace the read-only `QLineEdit` with `QComboBox` populated from `PAPER_SYMBOLS`; update `values()`, `set_values_from_session()`, accessibility, loading controls and validation field reveal. Call `validate_paper_symbol()` from `validate_paper_session()` and remove the BTC-only equality check.

- [ ] **Step 4: Verify GREEN and commit**

Run the focused tests from Step 2, then commit with `feat: allow paper symbol selection`.

### Task 3: Use symbol-specific rules in desktop composition and Paper execution

**Files:**
- Modify: `src/tiewtrade/desktop_main.py`
- Modify: `tests/unit/test_desktop_main.py`
- Modify: `tests/acceptance/test_desktop_session_setup.py`

**Interfaces:**
- Consumes: `paper_symbol_rules(session.market_data.symbol)` from Task 1
- Produces: Paper runtime and manual-close symbol rules for the selected Session symbol

- [ ] **Step 1: Write failing composition tests**

```python
@pytest.mark.parametrize("symbol", ["ETHUSDT", "BNBUSDT"])
def test_desktop_runtime_uses_selected_paper_symbol_rules(symbol: str) -> None:
    rules = desktop_symbol_rules(configured_session(symbol))
    assert rules.symbol == symbol
```

- [ ] **Step 2: Verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/test_desktop_main.py -k selected_paper_symbol_rules`

Expected: FAIL because `_symbol_rules_for()` is BTC-specific.

- [ ] **Step 3: Implement minimal composition change**

Replace the local BTC-only `_symbol_rules_for()` implementation with the Task 1 provider. Ensure `PaperRuntimeController` and `ClosePaperPosition` receive the Session-specific rules without changing transport selection or Live behavior.

- [ ] **Step 4: Add acceptance coverage for all three symbols**

Parameterize the existing Paper Session setup acceptance path over the allowlist and assert durable Session/Chart header symbol matches the selected value.

- [ ] **Step 5: Verify GREEN and commit**

Run focused unit/acceptance tests and commit with `feat: use selected paper symbol rules`.

### Task 4: Full verification and review

**Files:**
- Modify only files required by previous tasks

- [ ] **Step 1: Run full verification**

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest -q
PYTHONPATH=src .venv/bin/python -m ruff check .
PYTHONPATH=src .venv/bin/python -m ruff format --check .
PYTHONPATH=src .venv/bin/python -m mypy
git diff --check
```

Expected: all commands succeed.

- [ ] **Step 2: Review product consistency**

Search `PRODUCT.md`, `README.md` and specs for BTC-only claims; retain BTC as the default but remove claims that exclude ETH/BNB from Paper scope.

- [ ] **Step 3: Request code review and commit final corrections**

Request review against the feature range; fix Critical/Important findings, repeat the verification commands, and commit any corrections.
