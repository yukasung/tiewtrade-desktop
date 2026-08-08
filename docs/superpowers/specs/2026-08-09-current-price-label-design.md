# Current Price Label Design

**Issue:** DEV-151
**Status:** Approved

## Goal

แสดง current price line แบบ dotted และป้ายที่แกนราคาขวาเมื่อ Chart มี
in-progress candle. ป้ายแสดงราคา current close และเวลาคงเหลือก่อน candle ปิด
โดยใช้สีเดียวกับ candle ปัจจุบัน.

## Design

เพิ่ม immutable `CurrentPriceGlyph` ใน `candlestick_chart.py` ซึ่งเก็บ dotted
horizontal line, label rectangle, price text, remaining-time text และ `QColor`.
`_build_painter_model` สร้าง glyph เฉพาะเมื่อ `ChartSnapshot.in_progress_candle`
มีอยู่: ราคาใช้ `close`; สีใช้เขียวเมื่อ `close >= open` มิฉะนั้นแดง; ตำแหน่ง Y
ใช้ price scale/padded plot surface เดิม. เส้นสิ้นสุดที่ plot edge และป้ายอยู่ใน
price axis จึงไม่ทับ candle.

เวลาคงเหลือคือ `max(candle.close_time - snapshot.observed_at_utc, 0)` format
`MM:SS`; ChartWorkflow ส่ง snapshot ใหม่ทุก forming-candle fact อยู่แล้ว จึง
render ป้ายใหม่แบบ real-time โดยไม่เพิ่ม timer, persistence, strategy หรือ execution.

ไม่มี in-progress candle, chart loading/empty/unavailable หรือราคาอยู่นอกพื้นที่
plot จะไม่แสดง glyph. Crosshair คง precedence ในการแสดงป้าย hover ของตนเองเท่านั้น
และไม่เปลี่ยน current-price line.

## Tests

- forming bullish/bearish candle สร้าง glyph สี ราคา และเวลาที่ถูกต้อง
- glyph line อยู่ใน plot, label อยู่ใน price axis และไม่ล้ำ chart surface
- completed/no-forming snapshot ไม่มี glyph
- painter วาด dotted line และ colored label
- focused chart tests, full pytest, Ruff, format, Mypy และ diff check ผ่าน

## Non-goals

ไม่มี timer เพิ่มเอง, ไม่มี market-data transport change, ไม่มี Candle.volume,
ไม่มี durable storage และไม่มีผลต่อ Paper/Live order หรือ Bot lifecycle.
