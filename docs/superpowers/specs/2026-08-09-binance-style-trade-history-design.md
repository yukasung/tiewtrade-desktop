# Binance-Style Trade History Design

## Supersession

สำหรับ Trade History UI และ query read model, เอกสารนี้แทนที่ข้อกำหนดเรื่อง
Basket-selected fills และ pagination ใน
`2026-07-23-paper-trade-history-design.md`,
`2026-07-29-dev-96-trade-history-ui-design.md` และ
`2026-07-26-trade-history-query-pagination.md`. เอกสารเดิมยังเป็นหลักฐานของ
implementation ก่อนหน้า แต่ไม่ใช่พฤติกรรมเป้าหมายหลังงานนี้.

## Goal

ปรับหน้า `Trade History` ให้ดูและใช้งานใกล้ Binance โดยแบ่งข้อมูลตามระดับ
การตัดสินใจเป็นสองแท็บ และตัด pagination ออกจาก Internal Alpha workflow:

- `Basket History` สำหรับผลลัพธ์ของรอบกลยุทธ์
- `Trade History` สำหรับ Buy/Sell Fill รายการจริงจากทุก Basket

## Scope

- หน้าจอมี tab bar ภายใน: `Basket History` และ `Trade History`
- ใช้ filter bar แนวนอนร่วมกัน: Symbol, Timeframe, Market Type, Trade Mode,
  ช่วงวันที่, `Apply Filters` และ `Reset`
- `Status` filter แสดงเฉพาะ `Basket History` เพราะเป็น Basket fact เท่านั้น
- แต่ละแท็บโหลดเฉพาะข้อมูลของตัวเอง มี Loading, Empty, Error และ Retry state
  แยกกัน
- ไม่มี Previous/Next control และไม่แสดง page count

## Data Flow

`Basket History` คง query และ summary Net PnL ของ Basket ตาม filter เดิม แต่เปลี่ยน
contract ให้คืนทุกรายการที่ตรง filter เรียงล่าสุดก่อน โดยไม่รับ `PageRequest`.

`Trade History` เพิ่ม query durable read model สำหรับ Fill จากทุก Basket ที่ตรง filter
โดยตรง ไม่สร้างผลลัพธ์จากการวน query Basket ใน UI. Query join only กับ facts ที่
จำเป็นสำหรับ filter และ presentation ได้แก่ Symbol, Timeframe, Market Type และ Trade
Mode; เรียง `filled_at_utc DESC, fill_id DESC` เพื่อให้ผลลัพธ์ deterministic.

Trade History table แสดง:

1. Filled At
2. Symbol
3. Side
4. Price
5. Quantity
6. Fee
7. Total
8. Realized PnL
9. Source

สี Side และ PnL เป็น supplementary visual cue; signed numeric values ยังต้องอ่านได้
โดยไม่พึ่งสี.

## UI Structure

```text
Trade History
  [Basket History] [Trade History]
  Symbol | Timeframe | Market | Mode | Date range | Apply Filters | Reset
  [Status]                         (Basket History only)
  -------------------------------------------------------------
  selected table
```

แท็บ active ใช้ accent และ underline ตาม navigation style เดียวกับ workspace. Tables
เต็มความกว้าง, header เรียบ, แถวคั่นบาง และ empty state อยู่กลาง table viewport.
ไม่มี Basket selection หรือ detail table ที่ผูกกับแถว Basket อีกต่อไป.

## Failure Handling

- การสลับ tab ไม่ล้าง durable result ของอีก tab
- Query fail แสดง state และ Retry เฉพาะ tab ที่ร้องขอ
- Filter validation fail ไม่ส่ง query และคง durable results เดิม
- UI ไม่ import SQLite หรือประกอบ Fill list ด้วยตัวเอง; workflow/application query
  เป็น owner ของ background work และ immutable read models

## Non-goals

- ไม่เปลี่ยน SQLite schema หรือ durable Paper execution records
- ไม่เปลี่ยน Session lifecycle, strategy, Basket lifecycle หรือ execution costs
- ไม่สร้าง Live order หรือเชื่อม credential ใหม่
- ไม่รองรับ virtualized/infinite scrolling ในรอบนี้; Internal Alpha โหลดรายการทั้งหมด
  ที่ตรง filter ในครั้งเดียวตามคำสั่งผู้ใช้

## Verification

- Application/integration tests ตรวจ cross-Basket Fill query, deterministic sort และ
  shared filters โดยไม่มี `PageRequest`
- UI tests ตรวจ tab switching, field visibility, no pagination controls, exact table
  columns และ independent state/retry handling
- Acceptance tests ตรวจ Basket และ Trade History แสดง durable Paper facts โดยไม่
  เปลี่ยน execution side effect
- รัน focused tests, full pytest, Ruff, format check, mypy และ `git diff --check`
