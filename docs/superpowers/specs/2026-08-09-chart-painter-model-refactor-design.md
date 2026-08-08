# Chart Painter Model Refactor Design

## Goal

แยก pure chart painter model และ geometry ออกจาก PySide widget โดยไม่เปลี่ยน
public behavior, visual geometry หรือ chart-data lifecycle.

## Target modules

`tiewtrade.ui.chart_painter_model` เป็นเจ้าของ immutable glyphs:
`CandleGlyph`, `MarkerGlyph`, `AxisTickGlyph`, `CrosshairGlyph`,
`CurrentPriceGlyph`, `ChartPainterModel`; และ pure functions สำหรับสร้าง model,
price/time mapping, ticks, current price, crosshair และ text formatting.

`tiewtrade.ui.candlestick_chart` เหลือ `CandlestickChartWidget`, Qt event handlers,
range controls และ QPainter rendering. Widget import public painter-model APIs แต่
ไม่มี duplicated geometry calculation.

## Preserved behavior

- completed/forming candle, marker, axis, compact time tick, crosshair และ
  current-price line/label ต้อง render เท่าเดิม
- plot bounds, label spacing, colors, padding และ accessible controls ไม่เปลี่ยน
- ไม่มีการเปลี่ยน `ChartWorkflow`, real-time feed, market-data, persistence,
  strategy, Bot lifecycle หรือ PySide signals

## Tests

- ย้าย pure model assertions ไป `tests/unit/ui/test_chart_painter_model.py`
- widget test คงเฉพาะ interactions/painting integration ใน
  `tests/unit/ui/test_candlestick_chart.py`
- before/after snapshots จาก fixtures เดิมต้องเท่ากันสำหรับ glyph values
- full pytest, Ruff, format, Mypy และ diff check ผ่าน
