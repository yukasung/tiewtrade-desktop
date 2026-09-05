# Position History Closed Date Range Design

## Goal

Position History ต้องแสดง Basket ตาม "วันที่ปิด" (วันที่ realize ผล) ไม่ใช่ "วันที่เปิด"
เพื่อให้ Basket ที่ Take Profit วันนี้ปรากฏใน filter `1 Day` ของวันนี้ แม้จะเปิดค้างมาหลายวัน

## Problem

`TradeHistoryFilter` เดิมมี `opened_from_utc` / `opened_before_utc` และ `_basket_where`
กรองด้วย `basket_results.opened_at_utc` ผลคือ Basket ที่เปิด 2026-09-01 แล้ว TP
2026-09-03 หลุดจากหน้าต่าง `1 Day` ของ 2026-09-03 (`2026-09-02T17:00Z` ถึง
`2026-09-03T17:00Z`) ทั้งที่ผลกำไรเกิดขึ้นในวันนั้นจริง

ชื่อฟิลด์เดิมยังไม่ตรงกับการใช้งานอยู่แล้ว: `_fill_where` ใช้ช่วงเวลาเดียวกันนี้กรอง
`trade_fills.filled_at_utc` ไม่ใช่ `opened_at_utc` มาตั้งแต่เริ่ม

## Decision

ช่วงเวลาใน `TradeHistoryFilter` เป็น "หน้าต่างที่อ่านประวัติ" แบบกลาง ๆ ไม่ผูกกับคอลัมน์ใด
คอลัมน์เดียว และผู้อ่านแต่ละฝั่งลงวันที่แถวของตัวเองด้วยเวลาที่เทรดเดอร์จะไปหามันจริง

- `opened_from_utc` / `opened_before_utc` → `from_utc` / `before_utc`
  (half-open: รวม `from_utc`, ไม่รวม `before_utc` เหมือนเดิม)
- Basket ลงวันที่ด้วย `closed_at_utc` — วันที่รอบนั้นจ่ายผล
- Fill ยังลงวันที่ด้วย `filled_at_utc` — พฤติกรรมไม่เปลี่ยน เปลี่ยนแค่ชื่อฟิลด์

## Consequences

- Basket ที่ยังเปิดอยู่มี `closed_at_utc` เป็น NULL จึงหลุดจาก query ที่ระบุช่วงเวลา
  โดยผลของการเปรียบเทียบเอง ไม่ต้องมี clause แยก ซึ่งตรงกับเจตนาเดิมของตาราง:
  แถวหนึ่งอ่านจากผลที่ realize แล้ว (Position ที่ยังเปิดอยู่ดูที่แท็บ Position)
- Query ที่ไม่ระบุช่วงเวลายังคืนทุกแถวรวม Basket ที่เปิดอยู่ เหมือนเดิม
- การเรียงลำดับยังเป็น `opened_at_utc DESC, basket_id DESC` — deterministic เท่าเดิม
  และ `basket_results_history_idx` ยังเป็น index ที่รับ order นี้
- `_closed_realized_pnl` สรุปยอดจากหน้าต่างเดียวกัน ตัวเลข Total จึงเป็นผลที่ realize
  ในช่วงที่เลือกจริง

## Schema

- เพิ่ม index `basket_results_closed_idx` บน `basket_results (closed_at_utc DESC, basket_id DESC)`
- Schema version 18 → 19 เพราะ `migrate()` return ทันทีเมื่อเวอร์ชันตรงกับ
  `_SCHEMA_VERSION` — DB ที่อยู่เวอร์ชันปัจจุบันแล้วจะไม่ได้ index ใหม่ถ้าไม่ขึ้นเวอร์ชัน
  (spec เขียนไว้ตอน main อยู่ v16 ระหว่างนั้นงาน Cooldown Policy ใช้ v17 กับ v18 ไป)
- Migration เพิ่ม index เท่านั้น ไม่ลบหรือแก้ข้อมูล จึงไม่ต้องสำรองไฟล์ก่อน
  (`_keep_a_copy` ตอนนี้ทำงานกับทุก migration แล้ว ไม่ใช่แค่ v1–v14)
- Migration ตรวจว่ามีตาราง `basket_results` และคอลัมน์ `closed_at_utc` ก่อนสร้าง index
  เพื่อให้ legacy schema รุ่นเก่ามาก ๆ ยังผ่าน migrate ได้

## UI

ตาราง Position History แยกคอลัมน์ `Date` เดิมเป็น `Opened` และ `Closed`

ถ้ากรองด้วยวันที่ปิดแต่ยังแสดงเฉพาะวันที่เปิด แถวที่ตรง filter "วันนี้" จะขึ้นวันที่ของ
หลายวันก่อน ซึ่งอ่านเป็นความสับสนอีกแบบ — สองคอลัมน์ทำให้เห็นทั้งวันที่ filter ใช้
และระยะเวลาที่รอบนั้นกินไป Basket ที่ยังเปิดแสดง `—` ในช่อง Closed

## Non-goals

- ไม่เปลี่ยนการเรียงลำดับเป็น `closed_at_utc` (ต้องตัดสินใจเรื่อง NULL ordering ของ
  Basket ที่ยังเปิดก่อน)
- ไม่เพิ่ม filter แยกระหว่างวันเปิดกับวันปิด — filter bar ยังเป็นช่วงเดียว
- ไม่แตะการกรองของ Trade History (Fill) นอกจากชื่อฟิลด์
- ไม่แตะ pagination, persistence coordinator, Strategy หรือ Execution

## Verification

- SQLite query test: Basket ที่เปิดก่อนหน้าต่างแต่ปิดในหน้าต่างถูกนับ, ที่ปิดหลังหน้าต่าง
  ไม่ถูกนับ, ที่ยังเปิดไม่ถูกนับ และ `realized_pnl` ตรงกับแถวที่นับ
- Migration test: DB ที่ตั้ง `user_version = 16` แล้ว migrate ได้ index ใหม่ และ
  `EXPLAIN QUERY PLAN` ของ range query ใช้ index นั้นจริง
- Presenter test: Basket ที่ยังเปิดแสดง `—` ในคอลัมน์ Closed
- Acceptance test: แถว Position History บนเดสก์ท็อปมีทั้ง Opened และ Closed
- รัน full pytest, Ruff check, format check, mypy และ `git diff --check`

## บันทึกเพิ่มเติม (2026-09-05)

spec นี้เขียนไว้บน branch `fix/position-history-closed-date-range` ตั้งแต่ 2026-09-03 แล้ว
แต่ยังไม่ merge งานวันที่ 2026-09-05 จึงถูกทำซ้ำขึ้นมาใหม่โดยไม่รู้ว่ามีอยู่ เพราะตอนอ่าน
`docs/superpowers/specs/` อ่านเฉพาะที่อยู่ใน working tree ไม่ได้ดู branch ที่ยังไม่ merge

การทำซ้ำรอบนั้นตัดสินใจต่างจาก spec สองข้อ และตกไปหนึ่งข้อ:

- ใช้ `COALESCE(closed_at_utc, opened_at_utc)` เพื่อ**เก็บ** Basket ที่ยังเปิดไว้ในผลลัพธ์
  ซึ่งขัดกับ Consequences ข้อแรกของ spec
- เปลี่ยนการเรียงลำดับเป็นวันปิด ซึ่ง spec ระบุเป็น non-goal ไว้ชัดเจน
- **ไม่ได้เพิ่ม index** และ `COALESCE(...)` เป็น predicate ที่ใช้ index ไม่ได้ด้วย

ผู้ใช้เลือกให้ยึด spec นี้ โค้ดบน main จึงถูกแก้กลับมาให้ตรงทั้งสามข้อ บทเรียนคือ
**ก่อนออกแบบของใหม่ ให้ดู branch ที่ยังไม่ merge ด้วย ไม่ใช่แค่ไฟล์ใน working tree**
