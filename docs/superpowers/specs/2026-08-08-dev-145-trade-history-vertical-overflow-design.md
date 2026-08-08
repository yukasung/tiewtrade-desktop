# DEV-145 Trade History Vertical Overflow Design

## เป้าหมาย

แก้ปัญหา filter input, date input และปุ่มใน `TradeHistoryPage` ซ้อนทับหรือถูกตัด
เมื่อ Unified Trading Workspace มีพื้นที่แนวตั้งไม่พอ โดยคงสัดส่วนของ Chart เดิมและ
ให้ผู้ใช้เลื่อนดูเนื้อหาทั้งหน้า Trade History ภายใน tab ได้

งานนี้แก้เฉพาะ presentation behavior ไม่เปลี่ยน Trade History query, Basket/Fill
data, pagination semantics, Paper Trading policy หรือ dependency boundary ที่กำหนดใน
`PRODUCT.md`, `CONTEXT.md` และ `ARCHITECTURE.md`

## ปัญหาและสาเหตุ

`TradingWorkspace` แบ่งพื้นที่แนวตั้งระหว่าง Chart กับ tabs ด้วย stretch ratio คงที่
เมื่อ window เตี้ย `TradeHistoryPage` จึงอาจได้รับความสูงต่ำกว่า minimum size ของ
เนื้อหา แต่ page ไม่มี vertical overflow boundary ทำให้ Qt บีบ geometry ของ
`filterCard` และ child widgets จนความสูงเป็นศูนย์หรือเกิดการวาดซ้อนทับ

การตรวจ geometry ที่ความสูงจำกัดพบว่า Trade History viewport เหลือประมาณ `95 px`
ขณะที่ page ต้องการขั้นต่ำประมาณ `505 px` และ `filterCard` ถูกบีบจากขั้นต่ำประมาณ
`142 px` เหลือ `0 px` จึงยืนยันว่าต้นเหตุอยู่ที่ page-level vertical overflow ไม่ใช่
stylesheet หรือความสูงของ input รายตัว

## ขอบเขต

งานนี้ครอบคลุม:

- vertical scrolling ภายใน `TradeHistoryPage` เมื่อเนื้อหาสูงกว่า tab viewport
- การรักษา natural/minimum height ของ heading, filters, buttons, tables และ
  pagination เพื่อไม่ให้ widgets ซ้อนทับ
- การคง keyboard navigation และ accessible names ของ controls เดิม
- regression tests สำหรับ constrained vertical viewport
- behavior เดิมเมื่อ viewport มีพื้นที่เพียงพอ

งานนี้ไม่ครอบคลุม:

- การเปลี่ยนสัดส่วนหรือซ่อน Chart
- การ redesign filters, tables หรือ visual theme
- การเปลี่ยน horizontal scrolling contract ของแต่ละ table
- การเปลี่ยน Trade History workflow, query, filtering หรือ pagination
- การเปลี่ยน domain/application layer หรือ persistence

## แนวทางที่เลือก

ให้ `TradeHistoryPage` เป็นเจ้าของ vertical overflow ของตัวเองโดยเพิ่ม
`QScrollArea` ภายใน page และย้าย layout เนื้อหาเดิมไปอยู่ใน content widget ของ scroll
area วิธีนี้รักษา public widget interface และ signal/display methods เดิม ผู้เรียกใช้
เช่น `TradingWorkspace` และ workflow wiring จึงไม่ต้องรับรู้รายละเอียด responsive
ของ page

กำหนด scroll area ดังนี้:

- `setWidgetResizable(True)` เพื่อให้ content ขยายตามความกว้างของ viewport
- vertical scrollbar policy เป็น `ScrollBarAsNeeded`
- horizontal scrollbar policy เป็น `ScrollBarAlwaysOff`
- frame ของ scroll area ถูกปิด เพื่อไม่เพิ่ม visual border ซ้อนกับ page/card เดิม
- content widget ใช้ layout, margins และ spacing เดิมของ `TradeHistoryPage`
- content widget รักษา vertical size hint ตามเนื้อหา และไม่ถูกบีบต่ำกว่า minimum
  layout size

เมื่อพื้นที่พอ scroll area จะไม่แสดง vertical scrollbar และหน้าตายังคงเดิม เมื่อ
พื้นที่ไม่พอ ผู้ใช้เลื่อนทั้ง filters, Basket History, pagination และ Trade Fills
ภายใน tab โดย Chart และ workspace layout ไม่เปลี่ยนขนาดจาก contract เดิม

## แนวทางที่ไม่เลือก

### ครอบ page ที่ TradingWorkspace

วิธีนี้แก้ปัญหาได้แต่ทำให้ `TradingWorkspace` ต้องรู้ minimum-size behavior ภายใน
Trade History และทำให้ presentation ownership รั่วออกจาก module ที่สร้าง widgets
จึงไม่เลือก

### เลื่อนเฉพาะ Filter Card

วิธีนี้แก้อาการ input ซ้อนทับในภาพ แต่ Basket History, pagination และ Trade Fills
ยังถูกบีบเมื่อ tab เตี้ย จึงไม่แก้ page-level overflow ครบถ้วน

### บังคับ minimum window height หรือย่อ Chart

การบังคับ window height ทำให้ใช้งานบนพื้นที่จอจำกัดยาก ส่วนการย่อ Chart ขัดกับ
ตัวเลือกที่ยืนยันให้คงสัดส่วน Chart จึงไม่เลือกทั้งสองแบบ

## Component และ Ownership

`TradeHistoryPage` ยังคงเป็น public `QWidget` และเป็นเจ้าของ:

- outer layout ที่มี scroll area เป็น child หลัก
- private content widget ที่ถือ heading, filters, tables และ state widgets เดิม
- vertical overflow และ scrollbar policies
- Qt signals และ display methods เดิมทั้งหมด

`TradingWorkspace` ยังคงเป็นเจ้าของการวาง Chart กับ tabs และไม่ต้องเพิ่มเงื่อนไข
เฉพาะ Trade History ส่วน `TradeHistoryWorkflow` และ application query seam ไม่เปลี่ยน

## Interaction และ Accessibility

- mouse wheel และ trackpad เลื่อน page ตาม behavior มาตรฐานของ Qt
- keyboard focus traversal เข้าถึง filter controls, buttons, tables และ pagination
  ตามลำดับเดิม
- เมื่อ focused widget อยู่นอก viewport `QScrollArea` ต้องเลื่อนให้ widget มองเห็นได้
  ตาม Qt focus behavior
- scrollbar เป็นกลไก presentation เท่านั้น ไม่มีผลต่อ filter values, selection,
  query generation หรือ loading/error state
- table ยังคงจัดการ horizontal overflow ของตัวเองตาม contract ใน `DEV-139`

## Test Strategy

ใช้ TDD โดยเพิ่ม failing regression test ก่อนแก้ production code:

1. สร้าง `TradeHistoryPage` ใน viewport ที่ความสูงต่ำกว่า content minimum
2. ยืนยันว่ามี vertical scroll range และไม่มี page-level horizontal scroll range
3. ยืนยันว่า filter/date/button widgets มี geometry สูงกว่า zero และไม่ intersect กัน
4. เลื่อนไปยัง control ด้านล่างและยืนยันว่าเข้าถึงได้ผ่าน scroll viewport
5. สร้าง page ใน viewport ที่สูงพอและยืนยันว่าไม่มี vertical scrollbar ที่ไม่จำเป็น
6. รัน unit tests ของ Trade History และ responsive/accessibility acceptance tests เดิม

การทดสอบ geometry จะยืนยัน behavioral invariant แทนการผูกกับ pixel size ของ theme
โดยไม่จำเป็น เพื่อลด brittle tests ข้าม Qt platform plugin

## ความเสี่ยงและการควบคุม

- nested scrolling ระหว่าง page และ tables อาจทำให้ wheel interaction ไม่ชัดเจน:
  ใช้ page scroll เฉพาะแนวตั้ง และคง horizontal scrolling ไว้ที่ tables
- `setWidgetResizable(True)` อาจทำให้ content ถูกบีบตามความสูง:
  กำหนด vertical size policy/minimum size จาก layout เพื่อรักษา natural height
- scrollbar อาจปรากฏทั้งที่พื้นที่พอจาก size hint ที่สูงเกินจริง:
  ทดสอบทั้ง constrained และ sufficient viewport และหลีกเลี่ยง fixed pixel height
- refactor layout อาจกระทบ object identity ที่ tests/workflow ใช้:
  ย้าย widgets เดิมเข้า content layout โดยไม่เปลี่ยน attributes, signals หรือ methods

## Verification

ก่อนปิด `DEV-145` ต้องรันและอ่านผลของ:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/unit/ui/test_trade_history_page.py
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/acceptance/test_workspace_responsive_accessibility.py
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests
.venv/bin/python -m ruff format --check src tests
.venv/bin/python -m mypy
git diff --check
```

## เงื่อนไขความสำเร็จ

งานเสร็จเมื่อ controls ใน Trade History ไม่ซ้อนทับหรือถูกบีบเป็น zero-height ใน
constrained viewport, ผู้ใช้เลื่อนแนวตั้งเข้าถึงเนื้อหาทั้งหน้าได้, ไม่มี page-level
horizontal scrollbar, sufficient viewport ไม่แสดง scrollbar ที่ไม่จำเป็น และ
verification gates ที่เกี่ยวข้องผ่านโดยไม่เปลี่ยน Chart หรือ Trade History behavior
เดิม
