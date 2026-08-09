# Three Paper Symbols Design

## Goal

เพิ่มการเลือก Paper Trading symbol ใน Session Setup สำหรับ `BTCUSDT`, `ETHUSDT` และ
`BNBUSDT` โดยยังคง Session configuration immutable และรักษา Paper Trading safety เดิม

## Scope

- Session Setup ใช้ dropdown `Symbol` พร้อมค่าเริ่มต้น `BTCUSDT`
- dropdown แสดงเฉพาะ `BTCUSDT`, `ETHUSDT` และ `BNBUSDT`
- symbol ที่เลือกต้องไหลผ่าน market data, chart, Paper runtime, execution, persistence
  และ Trade History ของ Session นั้น
- แต่ละ symbol มี tick size, quantity step และ min notional ที่ explicit ใน Paper symbol
  rules เพื่อใช้ราคาและปริมาณอย่างถูกต้อง
- การเปลี่ยน symbol หลัง Stop ใช้ Start replacement flow เดียวกับการเปลี่ยน timeframe

## Constraints

- Paper Trading เท่านั้น ไม่มี Live order หรือ credentials
- ไม่เปิดให้พิมพ์ symbol อิสระ และไม่ดึงรายการ market จาก Binance ใน runtime
- ไม่แก้ Session ที่กำลังทำงาน; การเปลี่ยน symbol ต้องสร้าง immutable Session ใหม่
- หาก Session เดิมมี Position ระบบใช้ confirmation/manual-close/retire flow เดิมก่อน Start
  Session ใหม่

## Design

สร้าง allowlist ที่ application-owned สำหรับ Paper symbols พร้อม rules ของแต่ละคู่
แทนการกระจาย condition ตาม UI หรือ execution adapter. Session validation ตรวจ symbol
จาก allowlist และ symbol rules provider เป็น owner ของ tick size/step/min-notional สำหรับ
Paper runtime กับ manual close.

UI อ่าน allowlist เดียวกันเพื่อแสดง dropdown. Symbol field ยังล็อกระหว่าง start/runtime
และกลับมาแก้ไขได้หลัง Stop หรือ Blocked ตาม single Start/Stop flow.

## Verification

1. UI แสดงและเลือกได้เฉพาะ 3 symbol
2. validation ปฏิเสธ symbol นอก allowlist
3. Spot/Futures Paper Session สร้าง market configuration ตาม symbol ที่เลือก
4. runtime, chart และ manual-close ใช้ rules ของ symbol นั้น
5. regression ครอบคลุม replacement flow เมื่อเปลี่ยน symbol และรัน pytest/Ruff/format/mypy

## Out of Scope

- arbitrary symbol input
- dynamic Binance exchange-info lookup
- Live Trading
