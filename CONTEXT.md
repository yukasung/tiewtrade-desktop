# TiewTrade Domain Context

## Trade Mode

- `PAPER` — จำลอง execution ด้วยข้อมูลที่ควบคุมได้ ไม่ส่งคำสั่งไป Binance
- `LIVE` — execution กับ exchange จริงได้เฉพาะหลัง Preflight และผู้ใช้ยืนยันอย่างชัดเจน

## Market Type

- `SPOT` — ซื้อขาย Spot และใช้ Spot capital policy
- `FUTURES` — ใช้ Futures margin, leverage และ funding

## Position Side

- `LONG` — Position ที่ได้กำไรเมื่อราคาปรับขึ้น
- `SHORT` — Position ที่ได้กำไรเมื่อราคาปรับลง
- Paper Futures execution contract รองรับ `LONG` และ `SHORT` แต่ RSI Step Grid Preset v1 ยังสร้างเฉพาะ `LONG`

## Futures Trading Policy

`FuturesTradingPolicy` เป็น immutable policy ของ Futures Session ที่กำหนด Trading
Capital 50%, Collateral Buffer 50%, leverage จำนวนเต็ม 1x–5x ที่ตรึงตลอด Session,
One-way Mode, Cross Margin และ Maintenance Margin Rate 0.5% แบบ deterministic โดยไม่
อ้างว่าเท่ากับ Binance Mark Price หรือ Maintenance Margin Tier จริง

## Shared Policy Rule

Paper และ Live ใช้ business rules ชุดเดียวกันสำหรับ Strategy, capital, Basket, Entry Pair, risk policy และ PnL แต่ใช้ execution adapter แยกกัน

ห้ามสรุปว่า Paper กับ Live ใช้ execution code เดียวกัน การใช้ร่วมกันอยู่ที่กฎการตัดสินใจ ไม่ใช่ side effect ของการส่งคำสั่ง

## Session Configuration

`SessionConfig` เป็น configuration แบบ immutable ที่ระบุ Session, Preset version, Market Type, Trade Mode, Available Capital, `EntryPolicy` และ market-specific capital policy

การตั้งค่า `LIVE` ไม่ใช่คำสั่งเริ่มการเทรด และไม่ข้าม Preflight, explicit confirmation หรือ reconciliation

## Account และ Session Ownership

หนึ่ง installation ใช้ Binance Account เดียวและมี Active Bot Session ได้สูงสุดหนึ่ง Session โดย Session เป็น ownership boundary โดยตรงสำหรับ Basket, Entry, Order, Fill, PnL และ audit records ระบบไม่สร้าง abstraction สำหรับหลายบัญชีหรือ Binance sub-account

## Configuration และ Policy Boundaries

ค่าที่ผู้ใช้หรือ session เลือกได้ต้องส่งผ่าน configuration ไม่ hardcode ใน business logic:

- `symbol` — สัญลักษณ์ตลาด เช่น `BTCUSDT`
- `timeframe` — ช่วงเวลาของ candle ที่เลือกได้จาก `3m`, `5m`, `15m`, `30m`,
  `1h`, `4h` และถูกตรึงตลอด Session
- `market_type` — `SPOT` หรือ `FUTURES`
- `trade_mode` — `PAPER` หรือ `LIVE`
- `available_capital` และ `preset_version`
- `max_entries` — เลือกจาก form ก่อนเริ่ม Session
- `cooldown_policy` — เลือกจาก form ก่อนเริ่ม Session ว่า Cooldown ของ Session นี้จบลงอย่างไร

ระบบใช้ `UTC` เป็น timezone กลางเสมอ และไม่เปิดให้เปลี่ยน timezone ใน session configuration

ค่าจาก form ต้องผ่าน validation แล้วสร้างเป็น immutable Session policy ห้ามเปลี่ยนระหว่าง Session โดย `max_entries` ต้องเป็นเลขคู่ตั้งแต่ 2–20 และ `cooldown_policy` ถูกตรึงตลอด Session เช่นเดียวกัน โดยโหมด `DURATION` ต้องระบุจำนวนวัน 1–30

RSI parameters, take-profit rule และการจับคู่ Entry Pair ยังคงอยู่ใน Strategy Preset หรือ product policy และไม่ใช่ค่าที่ผู้ใช้เปลี่ยนระหว่าง Session โดยรูปของ take-profit rule เป็น product policy กลาง ส่วน ATR multiplier ที่ป้อนให้สูตรมาจาก Preset ส่วนที่ผู้ใช้เลือกได้คือ Cooldown Policy ซึ่งบอกว่า Cooldown ที่ Entry Pair สร้างขึ้นจบลงอย่างไร ไม่ใช่การเปลี่ยนกฎว่า Pair เกิดเมื่อไหร่

Cooldown Policy เลือกตอนสร้าง Session และตรึงตลอด Session มีสามแบบ: `OFF` — Entry Pair ที่ครบไม่ปิด gate เลย, `DURATION` — ปิด gate แล้วเปิดเองเมื่อผ่านไป N วัน (1–30) นับจากเวลาที่ Pair ครบ, และ `UNTIL_CANCELLED` — ค่าเริ่มต้น ยืนอยู่จนกว่าผู้ใช้จะกด "Cancel Cooldown" โหมด `DURATION` วัดด้วยเวลาของ candle ที่ Session รับล่าสุด ไม่ใช่นาฬิกาของเครื่อง เพื่อให้ replay ชุด candle เดิมให้คำตอบเดิมทุกครั้ง

Cooldown เกิดขึ้นเมื่อ Basket ทำ Entry Pair ครบสอง Entries โดย Cooldown ระบุว่า Basket กำลังถือทั้งสอง Entries ของ Pair ล่าสุดพร้อมกัน จึงจบลงทันทีที่ Entry ใด Entry หนึ่งของ Pair นั้นถูกปิด ไม่ว่าจะด้วย Take Profit หรือการปิดเอง (การปิด Entry ของ Pair ก่อนหน้าที่จบไปแล้วไม่นับ เพราะ Pair ล่าสุดยังถือครบอยู่) ข้อเท็จจริงนี้ไม่ต้องเก็บลงฐานข้อมูล เพราะ replay อ่านกลับได้จากเลข Entry ที่ยังเปิดค้างอยู่ ด้วยเหตุผลเดียวกันนี้เวลาที่ Pair ครบก็ไม่ต้องเก็บเช่นกัน — Cooldown ยืนอยู่ได้ก็ต่อเมื่อทั้งสอง Entries ของ Pair นั้นยังเปิดอยู่ Fill ที่ทำให้ Pair ครบจึงอยู่ในกลุ่ม Entry ที่ replay อ่านกลับพร้อม `filled_at` ของมันเสมอ

ถ้ายังถือครบทั้งคู่และ Cooldown ยังไม่หมดอายุ Basket จะถูกบล็อกจนกว่าผู้ใช้กดปุ่ม "Cancel Cooldown" พร้อม confirmation dialog ภายใต้ `UNTIL_CANCELLED` นี่คือการข้าม risk policy ที่ตั้งใจไว้ ส่วนภายใต้ `DURATION` คือการเลือกไม่รอให้ครบวัน dialog จึงบอกวันที่ Cooldown จะปลดเอง ภายใต้ `OFF` ไม่มี Cooldown ให้ยกเลิก ปุ่มจึงไม่ปรากฏเพราะ gate ไม่เคยเป็น Cooldown การกดหนึ่งครั้งยกเลิกได้เฉพาะ Cooldown รอบที่กำลังบล็อกอยู่ Pair ถัดไปที่ Basket เดียวกันทำครบยังเข้า Cooldown ตามปกติ waiver ผูกกับ Basket และ persist คู่กับ Fills ของมัน จึงอยู่รอดแม้ restart หรือ replay กลับ ปุ่มนี้ใช้ได้ทั้ง Paper Spot และ Paper Futures โดย Paper Spot เก็บ waiver ลงฐานข้อมูลจึงอยู่รอดข้าม restart ส่วน Paper Futures ไม่เก็บ เพราะไม่รับ Basket ที่เปิดค้างกลับมาอยู่แล้ว waiver จึงมีอายุเท่ากับ Runtime ที่ถืออยู่

เมื่อ Cooldown แบบ `DURATION` หมดอายุเอง ระบบจะแจ้งผู้ใช้ (`CooldownEndedNotice`) เพราะไม่มีใครกดอะไรเลย จึงไม่มีอย่างอื่นบอกว่า Bot กลับมาเปิด Entry ได้แล้ว ส่วน Cooldown ที่จบเพราะมีการปิด Entry ของ Pair นั้น ไม่แจ้ง เพราะผู้ใช้เป็นคนทำเอง

กฎเฉพาะตลาดต้องถูกตรวจสอบที่ boundary ก่อนเริ่ม session:

- Spot ไม่มี capital policy ของตัวเอง: เทรด Available Capital ทั้งจำนวน แบ่งเท่ากันตาม `max_entries`
- Futures ใช้ `FuturesTradingPolicy` ที่กำหนด One-way Mode, Cross Margin, leverage และ collateral buffer
- Paper และ Live ใช้ business/risk policy เดียวกัน แต่ใช้ execution adapter แยกกัน

### Candle ที่ดึงย้อนหลัง (Backfilled Candle)

Candle ที่ระบบดึงย้อนหลังมาหลังการเชื่อมต่อขาด ห้ามใช้เปิด Entry ใหม่ เพราะราคาเปิดของมันเป็น
ราคาที่ตลาดผ่านไปแล้ว แต่ยังใช้ปิด Position ได้เต็มรูปแบบ (Take Profit และการปิดเอง)
และยังใช้คำนวณ Indicator ตามปกติ กฎนี้ใช้กับทั้ง `PAPER` และ `LIVE` ทุก Market
Type — ดู PRODUCT.md ข้อ 8.1

### Entry Group

**Entry Group** คือกลุ่มของ Entries ภายใน Basket เดียวกันที่ใช้เป้า Take Profit ร่วมกัน
Entry ใหม่เข้ากลุ่มไหนตัดสินตอน Fill ด้วยราคาที่จ่าย เทียบกับราคาเฉลี่ยของ **กลุ่มล่าสุดที่ยัง
เปิดอยู่**:

- **ถัวลง** (Long จ่ายต่ำกว่าค่าเฉลี่ย / Short ขายสูงกว่า) → เข้ากลุ่มนั้น และกลุ่มคำนวณเป้าใหม่
  จากค่าเฉลี่ยที่เปลี่ยนไป Entry เดิมในกลุ่มได้เป้าใหม่นี้ด้วย
- **ถัวขึ้น** รวมถึงราคาเท่ากันพอดี → **เปิดกลุ่มใหม่** กลุ่มเดิมจึงถือเป้าเดิมของมันต่อไปโดย
  ไม่ถูกไม้ที่แพงกว่ามาถ่วงให้ไกลออกไป

กลุ่มที่ Entry นั้นสังกัดไม่เปลี่ยนอีกเลยหลัง Fill และเลขกลุ่มไม่ถูกใช้ซ้ำ กลุ่มที่ขายหมดแล้วหายไป
โดยไม่ยกเลขให้กลุ่มถัดไป

### Basket Take Profit

Take Profit มีระดับเดียวคือระดับกลุ่ม วัดจากราคาเฉลี่ยถ่วงน้ำหนักของ Entries ที่ยังเปิดอยู่ใน
กลุ่มนั้น คู่กับ ATR ของ Fill ล่าสุดในกลุ่ม เมื่อราคาแตะเป้าของกลุ่มใด **ทุก Entry ในกลุ่มนั้นถูกขาย
ที่ราคาเป้านั้นพร้อมกัน** รวมถึงไม้ที่ยังขาดทุน ส่วนกลุ่มอื่นถือต่อไปโดยไม่ถูกแตะ ไม่มีตาข่ายระดับ
Basket ที่กวาดทุกไม้พร้อมกันอีกต่อไป candle เดียวที่แตะสองกลุ่มจะขายกลุ่มที่ราคาไปถึงก่อนก่อน

เป้าของกลุ่ม **ขยับเข้าใกล้ได้อย่างเดียว ไม่ถอยออก** (Long ปรับลงได้ Short ปรับขึ้นได้) เพื่อไม่ให้
ATR ที่โตขึ้นดันเป้าที่ราคาเดินมาถึงแล้วให้หนีออกไป และคำนวณใหม่ทุกครั้งที่องค์ประกอบของกลุ่ม
เปลี่ยน ทั้งเมื่อมี Entry Fill และเมื่อ Entry ถูกปิดรายตัว

`Basket.take_profit_price` คือเป้าที่ใกล้ที่สุดในบรรดากลุ่ม ใช้แสดงผลเท่านั้น ไม่ตัดสินการขาย
เพราะเป้าของกลุ่มที่ตลาดทิ้งไว้ข้างหลังไม่ถูกกระทบ — ดู PRODUCT.md ข้อ 5

### Basket Close Reason

Basket ปิดได้ด้วย `TAKE_PROFIT` หรือ `MANUAL` เมื่อผู้ใช้สั่งปิดเอง
Internal Alpha ไม่มี Liquidation: ไม่มีกลไกใดบังคับปิด Position ไม่ว่าราคาจะเสียหายเพียงใด
