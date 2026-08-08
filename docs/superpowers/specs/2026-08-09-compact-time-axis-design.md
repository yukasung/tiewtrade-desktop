# Compact Time Axis Design

## Goal

ปรับ label แกนเวลา Chart ให้แสดง `HH:mm` โดยไม่แสดง `UTC` และเพิ่มจำนวน tick
ตามพื้นที่ plot เพื่อให้อ่านช่วงเวลาได้ถี่ขึ้นแบบ TradingView.

## Design

`_time_text` จะ format label เป็น `HH:mm` เสมอ. `_time_ticks` ใช้ label width
`56.0` pixels แทน `72.0` และคำนวณ tick count จากความกว้าง plot โดยรักษา spacing
ขั้นต่ำเท่ากับความกว้าง label จึงไม่เกิด overlap. Crosshair ไม่เปลี่ยน: ยังคงใช้
วันและเวลาเต็ม UTC เพื่อให้ time lookup ชัดเจน.

ไม่มีผลต่อ Candle data, real-time feed, Bot, persistence หรือ timezone policy;
UTC ยังคงเป็น time source ภายในและใน header range.

## Tests

- ทุก time-axis tick เป็น `HH:mm` ไม่มี `UTC`
- plot กว้าง 600px สร้าง tick มากกว่า implementation เดิม 6 ticks โดย label rect
  ไม่ซ้อนกัน
- existing crosshair time label ยังคงมี `UTC`
- focused chart test, full pytest, Ruff, format, Mypy และ diff check ผ่าน
