# Remove Paper Open Orders Design

## Goal

ตัดแนวคิด Open Orders ออกจาก Paper workspace ทั้ง UI และ application presentation state เพราะ Paper Bot v1 fill Entry Intent ทันทีและไม่มี limit order ที่ค้างรอจับคู่

## Scope

- Workspace เหลือสอง tab: `Position / Basket` และ `Trade History`
- ลบ `OpenOrdersTable` และ tests ของมัน
- ลบ `OpenOrderSnapshot`, `OpenOrdersTabSnapshot`, state helper และ `open_orders` field จาก `TradingWorkspaceSnapshot`
- ลบ `_pending_orders` และการส่ง pending Entry Intent ไปยัง workspace snapshot
- ตัด comparison และ relay condition ที่อ้างถึง `open_orders`
- ปรับ UI, application และ acceptance tests ให้ตรวจ Position/Basket กับ Trade History เท่านั้น
- ปรับ PRODUCT.md และ spec ที่เกี่ยวข้องให้ Paper Session replacement ไม่กล่าวถึง Open Order

## Non-goals

- ไม่ลบ `Order`, `TradeFill`, fill audit, execution persistence หรือ Basket lifecycle
- ไม่เพิ่ม limit order, cancel order หรือ Live order
- ไม่เปลี่ยน signal generation, risk policy, Paper fill pricing หรือ manual-close flow

## Migration

ไม่มี schema migration: Open Orders ไม่ได้ persist เป็น durable record ใน Paper runtime และเป็น presentation projection เท่านั้น

## Verification

- Unit tests ยืนยัน `TradingWorkspaceSnapshot` ไม่มี Open Orders projection และ Paper runtime ยังสร้าง Position/Basket snapshot ได้
- UI test ยืนยันเหลือสอง tab และ tab index / Trade History activation ถูกต้อง
- รัน application, runtime, UI และ acceptance tests ที่เกี่ยวข้อง ตามด้วย full pytest, Ruff, format check, mypy และ `git diff --check`
