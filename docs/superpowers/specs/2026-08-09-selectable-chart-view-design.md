# Selectable Chart View Design

## เป้าหมาย

ให้ผู้ใช้เลือก Symbol และ Timeframe เพื่อดู Chart ได้อิสระจาก Bot Session โดยใช้
public Binance market data แบบ read-only และไม่เปลี่ยนเงื่อนไขการเทรดของ Bot

## ขอบเขต

- Chart header มี dropdown `Symbol` และ `Timeframe`
- Symbol ที่เลือกได้คือ `BTCUSDT`, `ETHUSDT`, `BNBUSDT`
- Timeframe ที่เลือกได้คือ `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `4h`
- selection เป็น presentation state ในหน่วยความจำ ไม่ถูกบันทึกใน Session หรือ SQLite
- Bot Session, Strategy, Paper execution, Position และ Start/Stop ไม่เปลี่ยนเมื่อเลือก
  Chart อื่น
- Chart โหลด historical candles และ update forming candle จาก public feed ต่อเนื่อง
  แม้ Bot หยุดอยู่
- ไม่เพิ่ม Live credentials, private endpoint หรือ Live order

## การออกแบบ

เพิ่ม immutable `ChartViewSelection` ที่เก็บ `symbol` และ `timeframe` ที่ผ่าน
allowlist เดียวกับ Paper Session `ChartWorkflow` เป็นเจ้าของ selection ปัจจุบันและ
generation ของ request/feed เมื่อ selection เปลี่ยน workflow ต้อง invalidate
callback เก่า โหลด default range ของ selection ใหม่ แล้วให้ composition root
replace chart-only public feed

`ChartSnapshot` ยังคงมี session identity เพื่อรักษา ownership ของ chart range และ
current-price presentation แต่ source candle identity มาจาก `ChartViewSelection`
แทน Session เมื่อดู Chart ที่ต่างจาก Session `TradeFill` markers จะเป็น tuple ว่าง
โดยเจตนา เพื่อไม่แสดง Fill ของ Bot บนกราฟคนละ Symbol หรือ Timeframe

`ChartOnlyFeedController` รับ Chart view market-data configuration ที่สร้างจาก
selection แทนการรับ Session ทั้งก้อน การเปลี่ยน selection หยุด feed เดิม, invalidates
relay generation และเริ่ม feed ใหม่ก่อนส่ง facts กลับ Qt thread callback ที่มี generation
เก่าหรือ identity ไม่ตรงต้องถูกทิ้ง

## UI

วาง `Symbol` และ `Timeframe` combo ก่อน chart facts ใน header ของ
`CandlestickChartWidget` ค่าเริ่มต้นเท่ากับ Session เพื่อให้ผู้ใช้เห็นกราฟ trading context
ทันที เมื่อเปลี่ยน selection ปุ่ม combo ถูก disable ระหว่างโหลด และเปิดใช้งานเมื่อ chart
เข้าสู่ `READY`, `EMPTY` หรือ `UNAVAILABLE`

current-price line/label เป็นของ candle ล่าสุดใน Chart view ที่เลือก ไม่ใช่ราคา Session
เสมอ ดังนั้นต้อง update ตาม selection และไม่แสดงค่าจาก feed เดิมระหว่างสลับ view

## ความผิดพลาดและความปลอดภัย

- การเลือกที่ไม่อยู่ allowlist ถูกปฏิเสธใน application boundary
- history/feed failure แสดง `Chart unavailable` เฉพาะพื้นที่ Chart; Bot state ไม่เปลี่ยน
- selection change ไม่เรียก lifecycle, execution, persistence หรือ trade-history write
- marker ถูกซ่อนทั้งชุดเมื่อ selection ไม่ตรงกับ immutable Session market-data identity

## การทดสอบ

- Chart view default ตรงกับ Session และ combo แสดง allowlists ครบ
- เปลี่ยน Symbol/Timeframe ส่ง request public-market-data config ใหม่และ reset range
- forming/closed candles จาก feed generation เก่าไม่เปลี่ยน chart ปัจจุบัน
- markers/current price ใช้ selection ใหม่; markers มีเฉพาะเมื่อ identity ตรงกับ Session
- history/feed failure ไม่กระทบ Bot Control หรือ Start/Stop
- รัน focused unit/UI/desktop tests, full pytest, Ruff, format, mypy และ diff check
