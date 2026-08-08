# Candlestick Chart Axis and Crosshair Design

## Goal

เพิ่ม price axis ด้านขวา, time axis ด้านล่าง และ crosshair แบบ TradingView ให้
Candlestick Chart เพื่อให้ผู้ใช้ระบุราคาและเวลา UTC จากตำแหน่งเมาส์ได้ โดยไม่เปลี่ยน
Chart data, market-data runtime หรือ trading behavior.

## UX

- เมื่อ Chart อยู่ใน state `READY` หรือ `EMPTY` และมี candle แสดงอยู่ ให้แสดง price tick
  labels ด้านขวา 5 ระดับ และ time tick labels ด้านล่าง 5–6 จุดตาม chart range.
- Price tick ใช้ช่วง low/high เดียวกับที่วาด candle และแสดงทศนิยมตามค่าราคา.
- Time tick แสดง UTC ในรูป `HH:mm`; หาก range ข้ามวัน ให้ tick แรกแสดงวันที่ด้วย.
- เมาส์ที่อยู่ใน plot area แสดงเส้นประแนวนอนและแนวตั้งพร้อม price label ด้านขวาและ time
  label ด้านล่าง. Mapping เป็น continuous ระหว่าง chart range และ price range, ไม่ snap
  candle.
- เมาส์ออกจาก plot area หรือ Chart ไม่มี candle ให้ซ่อน crosshair และ labels.
- Candles, fill markers, range navigation และ retry behavior เดิมไม่เปลี่ยน.

## Rendering Model

`CandlestickChartWidget` ยังคงเป็น read-only QPainter surface. เพิ่ม painter model แบบ
immutable สำหรับ plot bounds, axis ticks และ optional crosshair position. Widget เป็น owner
ของ ephemeral mouse position เท่านั้น; helper functions เป็น owner ของ price/time mapping,
tick values และ label text.

พื้นที่วาดแบ่งเป็น:

```text
header
plot area                 price axis
time axis
```

Plot area ใช้คำนวณ candle geometry, marker geometry และ crosshair. Axis space ไม่รับ mouse
mapping. Labels ถูก clamp ให้อยู่ใน widget bounds และวาดเฉพาะเมื่อ snapshot มี candles.

## Data and Safety

- ใช้ `ChartSnapshot` และ `ChartRange` ที่มีอยู่แล้ว; ไม่เพิ่ม persistence หรือ network call.
- เวลาใช้ UTC ที่อยู่ใน `ChartRange`; ไม่มี local timezone conversion.
- ราคาเป็น display-only `Decimal` mapping; ไม่ส่งต่อไป Strategy, execution, risk หรือ
  market-data lifecycle.
- Crosshair state เป็น in-memory UI fact และ reset เมื่อ snapshot เปลี่ยน.

## Verification

1. painter model tests ครอบ plot/axis geometry, price ticks, time ticks และ tick label format.
2. widget tests ครอบ mouse move ใน plot, price/time labels, mouse leave และ snapshot reset.
3. existing candle and marker tests ยืนยันว่า geometry ยังอยู่ใน plot bounds.
4. `pytest`, Ruff, format, mypy และ `git diff --check` ผ่าน.

## Out of Scope

- OHLC tooltip, snap-to-candle, zoom/pan, order entry, volume panel และ technical indicators.
- เปลี่ยน candle data, chart history API, runtime relay หรือ trading policy.
