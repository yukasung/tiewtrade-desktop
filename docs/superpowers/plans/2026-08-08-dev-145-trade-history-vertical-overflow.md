# DEV-145 Trade History Vertical Overflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ทำให้ `TradeHistoryPage` เลื่อนแนวตั้งภายใน tab เมื่อพื้นที่ไม่พอ โดย controls ไม่ซ้อนทับและไม่เปลี่ยนขนาด Chart

**Architecture:** `TradeHistoryPage` ยังคงเป็น public `QWidget` และเพิ่ม `QScrollArea` เป็น vertical overflow boundary ภายในตัวเอง เนื้อหาเดิมทั้งหมดอยู่ใน private content widget ที่รักษา minimum layout height ขณะที่ workflow, signals และ `TradingWorkspace` ไม่เปลี่ยน

**Tech Stack:** Python 3.12, PySide6, pytest, pytest-qt, Ruff, mypy

## Global Constraints

- ใช้ภาษาอังกฤษสำหรับชื่อไฟล์, module, class, function, variable และ code comments
- คงสัดส่วนของ Chart และ layout ownership ของ `TradingWorkspace` เดิม
- vertical scrollbar ใช้ `ScrollBarAsNeeded`; page-level horizontal scrollbar ใช้ `ScrollBarAlwaysOff`
- ไม่เปลี่ยน Trade History query, filters, pagination, Basket/Fill data หรือ Paper Trading policy
- table แต่ละตัวคง horizontal overflow contract เดิม
- ใช้ Test-Driven Development: failing test → minimal implementation → refactor
- ไม่เพิ่ม generic interface, base class, registry หรือ responsive framework
- ห้ามใช้ Live order, Live credentials, Binance Private API หรือเงินจริงในการทดสอบ

---

## File Structure

- Modify: `tests/unit/ui/test_trade_history_page.py` — เพิ่ม regression contract สำหรับ constrained และ sufficient vertical viewport
- Modify: `src/tiewtrade/ui/trade_history_page.py` — ให้ page เป็นเจ้าของ scroll area และ content widget โดยไม่เปลี่ยน public behavior เดิม

ไม่สร้างไฟล์ production ใหม่ เพราะ behavior นี้เป็นส่วนหนึ่งของ layout responsibility ที่ `TradeHistoryPage` เป็นเจ้าของอยู่แล้ว

### Task 1: เพิ่ม Vertical Overflow Boundary ให้ TradeHistoryPage

**Files:**
- Modify: `tests/unit/ui/test_trade_history_page.py:1-101`
- Modify: `src/tiewtrade/ui/trade_history_page.py:4-20,341-381`

**Interfaces:**
- Consumes: `TradeHistoryPage()` constructor, controls เดิม เช่น `symbol`, `from_date`, `apply_button`, `basket_table`, `fill_table`
- Produces: `TradeHistoryPage.scroll_area: QScrollArea` สำหรับ page-level vertical scrolling; signals, methods และ widget attributes เดิมทั้งหมดคง signature เดิม

- [ ] **Step 1: เพิ่ม test helper สำหรับตรวจ geometry ใน parent coordinate system**

แก้ imports ใน `tests/unit/ui/test_trade_history_page.py`:

```python
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QAbstractItemView, QWidget
```

เพิ่ม helper หลัง `history_page`:

```python
def widget_rect_in_parent(widget: QWidget, parent: QWidget) -> QRect:
    return QRect(widget.mapTo(parent, QPoint(0, 0)), widget.size())
```

- [ ] **Step 2: เขียน failing regression test สำหรับ constrained viewport**

เพิ่ม test หลัง `test_page_exposes_filters_and_exact_table_columns`:

```python
def test_page_scrolls_vertically_without_squashing_filter_controls(
    qtbot: QtBot,
) -> None:
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    page.setFixedSize(1200, 240)
    page.show()

    vertical = page.scroll_area.verticalScrollBar()
    horizontal = page.scroll_area.horizontalScrollBar()
    qtbot.waitUntil(lambda: vertical.maximum() > 0)

    filter_card = page.from_date.parentWidget()
    assert filter_card is not None
    controls = (
        page.symbol,
        page.timeframe,
        page.market,
        page.mode,
        page.status,
        page.from_date_enabled,
        page.from_date,
        page.to_date_enabled,
        page.to_date,
        page.reset_button,
        page.apply_button,
    )
    control_rects = [widget_rect_in_parent(control, filter_card) for control in controls]

    assert all(rect.width() > 0 and rect.height() > 0 for rect in control_rects)
    assert all(
        not left.intersects(right)
        for index, left in enumerate(control_rects)
        for right in control_rects[index + 1 :]
    )
    assert vertical.maximum() > 0
    assert horizontal.maximum() == 0

    page.scroll_area.ensureWidgetVisible(page.fill_table)
    qtbot.waitUntil(lambda: vertical.value() > 0)
```

- [ ] **Step 3: เขียน failing regression test สำหรับ sufficient viewport และ policy**

เพิ่ม test ต่อจาก constrained viewport test:

```python
def test_page_avoids_unnecessary_scrollbars_when_content_fits(qtbot: QtBot) -> None:
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    page.setFixedSize(1600, 1000)
    page.show()

    qtbot.waitUntil(lambda: page.scroll_area.viewport().height() > 0)

    assert (
        page.scroll_area.verticalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    )
    assert (
        page.scroll_area.horizontalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    assert page.scroll_area.verticalScrollBar().maximum() == 0
    assert page.scroll_area.horizontalScrollBar().maximum() == 0
```

- [ ] **Step 4: รัน focused tests เพื่อยืนยัน RED state**

Run:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q \
  tests/unit/ui/test_trade_history_page.py::test_page_scrolls_vertically_without_squashing_filter_controls \
  tests/unit/ui/test_trade_history_page.py::test_page_avoids_unnecessary_scrollbars_when_content_fits
```

Expected: FAIL เพราะ `TradeHistoryPage` ยังไม่มี attribute `scroll_area`

- [ ] **Step 5: เพิ่ม imports ที่จำเป็นสำหรับ scroll container**

แก้ widget imports ใน `src/tiewtrade/ui/trade_history_page.py` ให้เพิ่ม:

```python
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
```

- [ ] **Step 6: สร้าง scroll area และย้าย layout เดิมไปยัง content widget**

แทนที่ `_build_layout` ใน `src/tiewtrade/ui/trade_history_page.py` ด้วย implementation นี้ โดยคง body ที่สร้าง heading, filter, Basket History, pagination และ Trade Fills ตามลำดับเดิม:

```python
def _build_layout(self) -> None:
    outer = QVBoxLayout(self)
    outer.setContentsMargins(0, 0, 0, 0)

    self.scroll_area = QScrollArea()
    self.scroll_area.setObjectName("tradeHistoryScroll")
    self.scroll_area.viewport().setObjectName("tradeHistoryViewport")
    self.scroll_area.setWidgetResizable(True)
    self.scroll_area.setVerticalScrollBarPolicy(
        Qt.ScrollBarPolicy.ScrollBarAsNeeded
    )
    self.scroll_area.setHorizontalScrollBarPolicy(
        Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)

    content = QWidget()
    content.setObjectName("tradeHistoryContent")
    content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
    root = QVBoxLayout(content)
    root.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(14)

    heading = QLabel("Trade History")
    heading.setObjectName("pageTitle")
    description = QLabel(
        "Review recorded Basket results and execution-level Trade Fills."
    )
    description.setObjectName("supportingText")
    root.addWidget(heading)
    root.addWidget(description)
    root.addWidget(self._build_filter_card())

    basket_heading = QHBoxLayout()
    basket_heading.setSpacing(12)
    basket_title = QLabel("Basket History")
    basket_title.setObjectName("sectionTitle")
    basket_heading.addWidget(basket_title)
    basket_heading.addWidget(self.total_items)
    basket_heading.addStretch()
    basket_heading.addWidget(self.total_net_pnl_label)
    basket_heading.addWidget(self.total_net_pnl)
    root.addLayout(basket_heading)
    root.addWidget(self.basket_table, 1)
    root.addLayout(self._state_row(self.basket_state, self.retry_baskets_button))

    pagination = QHBoxLayout()
    pagination.addStretch()
    pagination.addWidget(self.previous_button)
    pagination.addWidget(self.page_label)
    pagination.addWidget(self.next_button)
    pagination.addStretch()
    root.addLayout(pagination)

    fill_title = QLabel("Trade Fills")
    fill_title.setObjectName("sectionTitle")
    root.addWidget(fill_title)
    root.addWidget(self.fill_table, 1)
    root.addLayout(self._state_row(self.fill_state, self.retry_fills_button))

    self.scroll_area.setWidget(content)
    outer.addWidget(self.scroll_area)
```

- [ ] **Step 7: รัน focused regression tests เพื่อยืนยัน GREEN state**

Run:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q \
  tests/unit/ui/test_trade_history_page.py::test_page_scrolls_vertically_without_squashing_filter_controls \
  tests/unit/ui/test_trade_history_page.py::test_page_avoids_unnecessary_scrollbars_when_content_fits
```

Expected: `2 passed`

- [ ] **Step 8: รัน Trade History unit suite และแก้เฉพาะ regression จาก layout change**

Run:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/unit/ui/test_trade_history_page.py
```

Expected: ทุก test ในไฟล์ผ่าน ไม่มี signal, filter, table หรือ state behavior เปลี่ยน

- [ ] **Step 9: รัน responsive/accessibility acceptance suite**

Run:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/acceptance/test_workspace_responsive_accessibility.py
```

Expected: ทุก test ผ่าน โดย horizontal scrolling ของ tables และ keyboard accessibility เดิมไม่ถดถอย

- [ ] **Step 10: Commit behavior change**

```bash
git add tests/unit/ui/test_trade_history_page.py src/tiewtrade/ui/trade_history_page.py
git commit -m "fix: scroll trade history in short viewports"
```

### Task 2: Repository Verification และ Issue Closure

**Files:**
- Verify: `src/`, `tests/`, `pyproject.toml`
- Update external state: Linear Issue `DEV-145` หลัง verification ผ่านเท่านั้น

**Interfaces:**
- Consumes: vertical overflow behavior จาก Task 1
- Produces: หลักฐานว่า unit, acceptance, lint, format, type และ whitespace gates ผ่าน; working tree สะอาด; `DEV-145` พร้อมเปลี่ยนเป็น `Done`

- [ ] **Step 1: รัน repository test suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: ทุก test ผ่าน ไม่มี failure หรือ error

- [ ] **Step 2: รัน Ruff lint และ format checks**

Run:

```bash
.venv/bin/python -m ruff check src tests
.venv/bin/python -m ruff format --check src tests
```

Expected: ทั้งสองคำสั่ง exit `0`; lint รายงาน `All checks passed!`

- [ ] **Step 3: รัน mypy**

Run:

```bash
.venv/bin/python -m mypy
```

Expected: exit `0` และไม่มี type errors

- [ ] **Step 4: ตรวจ committed range และ working tree**

Run:

```bash
git diff --check main...HEAD
git status --short --branch
```

Expected: `git diff --check` ไม่มี output และ status ไม่มี modified/untracked files

- [ ] **Step 5: ตรวจ diff สุดท้ายเทียบ design**

Run:

```bash
git diff --stat main...HEAD
git diff main...HEAD -- src/tiewtrade/ui/trade_history_page.py tests/unit/ui/test_trade_history_page.py
```

Expected: diff จำกัดอยู่ที่ internal page scrolling และ regression tests ไม่มีการเปลี่ยน Chart, workflow, application หรือ domain code

- [ ] **Step 6: เปลี่ยน Linear Issue เป็น Done และรายงานผล**

หลัง Steps 1–5 ผ่านทั้งหมด ให้เปลี่ยน `DEV-145` จาก `In Progress` เป็น `Done` แล้วสรุป:

- vertical overflow behavior ที่เพิ่ม
- focused และ repository verification results
- commit hashes บน `dev-145-trade-history-vertical-scroll`
- ความเสี่ยงคงเหลือ หากไม่มีให้ระบุว่าไม่พบจาก verification
- ยังไม่ push หรือ merge จนกว่าผู้ใช้จะยืนยันแยกต่างหาก
