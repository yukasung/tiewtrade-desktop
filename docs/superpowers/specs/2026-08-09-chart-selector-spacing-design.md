# Chart Selector Spacing Design

## Goal

ทำให้ตัวเลือก Symbol และ Timeframe ใน header ของ Chart อ่านง่ายและไม่ชนกัน โดยไม่เปลี่ยนพฤติกรรม Chart view หรือ Bot Session

## Layout

- Header ของ Chart เว้นระยะ 12px ระหว่าง Symbol dropdown และ Timeframe dropdown
- เว้นระยะ 16px ระหว่าง Timeframe dropdown และข้อความ facts ของ Chart
- การจัดวาง controls ช่วงเวลาและปุ่มนำทางด้านขวาคงเดิม

## Dropdown Styling

- ใช้ style เฉพาะ `#chartSymbol` และ `#chartTimeframe`
- ข้อความใน closed dropdown มี padding ซ้าย 14px และ padding ขวา 40px เพื่อแยกจากลูกศร
- รายการใน popup มี padding แนวนอน 12px และแนวตั้ง 6px
- สี, border, focus state และ disabled state สืบทอด theme เดิม

## Constraints

- ไม่เปลี่ยน Symbol/Timeframe ที่รองรับ, ค่าเริ่มต้น, signal หรือ Chart view workflow
- ไม่เปลี่ยน immutable Bot Session, Position, Order หรือ Bot lifecycle

## Verification

- Unit test ยืนยัน geometry ระหว่าง Symbol, Timeframe และ facts ตาม spacing ที่กำหนด
- Unit test ยืนยัน object name ของ dropdown คงเดิมเพื่อให้ theme เฉพาะเป้าหมายได้
- รัน Chart UI tests, full pytest, Ruff, format check, mypy และ `git diff --check`
