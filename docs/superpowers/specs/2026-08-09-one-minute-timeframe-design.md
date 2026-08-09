# One-Minute Timeframe Design

## เป้าหมาย

เพิ่ม `1m` เป็น timeframe ที่รองรับของ TiewTrade เพื่อให้ผู้ใช้เลือกได้จาก Session
Setup, Session validation, public market-data runtime, Chart และ Trade History ใช้
ค่าเดียวกันอย่างสอดคล้อง

## ขอบเขต

- เพิ่ม `1m` ที่ shared `SUPPORTED_V1_TIMEFRAME_CHOICES` ก่อน `3m`
- Paper Spot และ Paper Futures ใช้ `1m` ได้ทันทีเมื่อสร้าง Session ใหม่
- Live จะรับรอง `1m` ตาม shared domain rule เมื่อ Live safety gate และ runtime
  ถูกเปิดใช้งานในอนาคต
- ไม่เพิ่ม Live runtime, credentials, Live preflight หรือการส่ง Live order
- Session ที่เริ่มแล้วคง timeframe แบบ immutable; การเปลี่ยนเป็น `1m` ต้อง Start
  Session ใหม่ตาม replacement flow เดิม

## การออกแบบ

`market_data.config` เป็นเจ้าของรายการ timeframe เดียวอยู่แล้ว จึงเพิ่ม `1m` ใน
allowlist นี้เท่านั้น ทุก consumer ที่ต้องแสดงหรือ validate timeframe ใช้ constant
เดียวกัน ได้แก่ `MarketDataConfig`, Session Setup และ Trade History filter

Binance public REST/WebSocket adapters รับ interval จาก immutable
`MarketDataConfig` โดยตรง จึงจะ subscribe/backfill `1m` โดยไม่เพิ่ม adapter หรือ
branch ใหม่ Candle alignment, gap detection, chart paging และ real-time forming
candle คำนวณ interval ผ่าน `timeframe_to_interval` ซึ่งรองรับ `1m` อยู่แล้ว

## ข้อผิดพลาดและความปลอดภัย

- ค่า timeframe นอก allowlist ยังคงถูกปฏิเสธก่อนสร้าง Session
- ไม่มี fallback จาก `1m` ไป timeframe อื่น
- ไม่มีการเปลี่ยนแปลง Live execution boundary และไม่มี Live order จากงานนี้

## การทดสอบ

- `MarketDataConfig` ยอมรับ `1m` และคำนวณ interval หนึ่งนาที
- Paper Session validation สร้าง immutable Session ด้วย `1m`
- Session Setup และ Trade History แสดง `1m` เป็นตัวเลือกแรก
- public market-data URL/subscription ใช้ `1m` จาก Session จริง
- รัน full pytest, Ruff, format, mypy และ `git diff --check`
