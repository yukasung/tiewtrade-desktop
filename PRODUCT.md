# TiewTrade V2 Product Definition

## 1. สถานะเอกสาร

- สถานะ: อนุมัติสำหรับเริ่มวางแผน Internal Alpha
- กลุ่มผู้ใช้: เจ้าของผลิตภัณฑ์และผู้ทดสอบภายในกลุ่มเล็ก
- แพลตฟอร์มแรก: macOS
- ภาษาของ UI: อังกฤษ
- ภาษาของเอกสารและ Linear: ไทย

เอกสารนี้กำหนดขอบเขตและพฤติกรรมของผลิตภัณฑ์ หากเอกสารด้านสถาปัตยกรรมหรือแผนงานขัดกับข้อกำหนดในเอกสารนี้ ต้องแก้เอกสารเหล่านั้นให้สอดคล้องกับ `PRODUCT.md` ก่อนเริ่มพัฒนา

## 2. Product Vision

TiewTrade V2 คือ Desktop Binance Trading Bot สำหรับใช้งานแบบ Internal Alpha โดยให้ความสำคัญกับความถูกต้อง ความสามารถในการตรวจสอบย้อนหลัง การกู้คืนหลังโปรแกรมหยุดทำงาน และการป้องกันคำสั่งซื้อขายซ้ำหรือผิดสถานะ มากกว่าการรองรับฟีเจอร์จำนวนมาก

ระบบเริ่มจาก Paper Trading ที่ทำงานครบตลอดเส้นทาง ก่อนเปิดใช้งาน Live Spot และ Live USDⓈ-M Futures ตามลำดับ แต่ละระดับต้องผ่าน acceptance criteria ของระดับก่อนหน้า

## 3. ขอบเขต Internal Alpha

### 3.1 สิ่งที่รองรับ

- macOS เป็นแพลตฟอร์มแรก โดยสถาปัตยกรรมต้องไม่ปิดทางการรองรับ Windows และ Linux ในอนาคต
- Paper Trading รองรับ `BTCUSDT`, `ETHUSDT` และ `BNBUSDT`; Live ยังคงจำกัด
  `BTCUSDT` จนกว่าจะผ่าน Live safety gate
- completed candle รองรับช่วงเวลา `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `4h`
- ผู้ใช้เลือก Timeframe ก่อนเริ่ม Session และค่า Timeframe ถูกตรึงตลอด Session
- ใช้ UTC สำหรับเวลา candle และ Entry Pair
- RSI Step Grid พร้อม Versioned Preset เพียงกลยุทธ์เดียว
- Paper Spot และ Paper Futures
- Live Spot หลัง Paper acceptance ผ่าน
- Live USDⓈ-M Futures หลัง Live Spot acceptance ผ่าน
- หนึ่ง Binance Account ต่อ installation
- หนึ่ง installation มี Active Bot Session ได้สูงสุดหนึ่ง Session
- Local single-user application

### 3.2 ลำดับการส่งมอบ

1. Paper Trading Complete
2. Live Spot
3. Live USDⓈ-M Futures

แต่ละ milestone ใช้ vertical slice ที่ทำงานครบตั้งแต่ข้อมูลตลาด กลยุทธ์ การจัดการ Basket การส่งคำสั่ง การบันทึกข้อมูล UI และ Recovery ไม่สร้างโมดูลทั่วไปหรือ abstraction ล่วงหน้าหากยังไม่มีผู้ใช้จริงอย่างน้อยสองราย

## 4. RSI Step Grid Versioned Preset

### 4.1 ค่าเริ่มต้น

| Parameter | ค่า |
| --- | ---: |
| RSI period | 14 |
| RSI reset threshold | ต่ำกว่า 30 |
| RSI entry threshold | สูงกว่า 50 |
| ATR period | 14 |
| Take Profit ATR multiplier | 3 |

Preset ทุกชุดต้องมี version แบบ immutable เมื่อ Session เริ่มทำงาน Session ต้องอ้างถึง version ที่แน่นอน การแก้ค่าในภายหลังต้องสร้าง version ใหม่และไม่มีผลย้อนหลังต่อ Session เดิม

Timeframe ไม่ใช่ Strategy Preset parameter ผู้ใช้เลือกจาก `3m`, `5m`, `15m`,
`30m`, `1h`, `4h` ก่อนเริ่ม Session และระบบบันทึกเป็น immutable Session
configuration โดย Indicator และ Strategy ต้องใช้ completed candle ของ Timeframe ที่
Session เลือกเท่านั้น

`max_entries` ไม่ใช่ Strategy Preset parameter แต่เป็น immutable Session policy โดยมีค่าเริ่มต้น 10 และผู้ใช้เลือกเลขคู่ 2–20 ก่อนเริ่ม Session

### 4.2 Entry Signal

กลยุทธ์เป็น Long-only และประเมินสัญญาณจาก completed candle เท่านั้น

1. เมื่อ RSI ต่ำกว่า 30 ให้บันทึก reset signal และราคาปิดของ reset signal candle
2. รอ completed candle ที่ผ่านเงื่อนไขทั้งหมดต่อไปนี้:
   - RSI สูงกว่า 50
   - `close > open`
   - `close > ราคาปิดของ reset signal candle`
   - Basket ยังไม่ครบ `max_entries` ของ Session
   - Session และ lifecycle อนุญาตให้เพิ่ม Entry
3. เมื่อ Entry ถูก Fill ให้ใช้ reset signal นั้นเสร็จสิ้น และต้องรอ reset signal ใหม่ก่อนสร้าง Entry ถัดไป

ระบบต้องไม่ประเมิน candle ที่ยังไม่ปิด และต้องไม่ประมวลผล completed candle เดิมซ้ำ

## 5. Basket Take Profit

Basket Take Profit คำนวณตาม Position Side ด้วยสูตร:

```text
Long  = weighted_average_entry_price + (ATR(14) * 3)
Short = weighted_average_entry_price - (ATR(14) * 3)
```

กติกา:

- ใช้ ATR จาก completed candle ล่าสุดของ Timeframe ที่ Session เลือก ณ เวลาที่ Entry ถูก Fill
- คำนวณราคา Take Profit ใหม่หลังทุก Entry Fill
- Long Take Profit ปัดลง และ Short Take Profit ปัดขึ้นตาม tick size ของ Symbol และ Market Type ที่ตรึงใน Session
- RSI Step Grid Preset v1 ยังสร้างเฉพาะ Long Entry Intent
- Take Profit ครอบคลุมขนาด Position ที่ Bot เป็นเจ้าของทั้งหมดใน Basket
- เมื่อ Take Profit ถูก Fill ให้ปิด Entries ทั้งหมดใน Basket พร้อมกัน
- Paper Spot ตรวจ Take Profit และ Stop Loss จากราคาที่วิ่งอยู่ระหว่าง candle และ Fill
  ทันทีที่ราคาถึงระดับ โดยไม่รอ candle ปิด ส่วน Entry ยังคงประเมินจาก completed candle
  เท่านั้นตามข้อ 4.2
- Paper Futures ยังตรวจทั้ง Take Profit และ Liquidation ที่ candle ปิด เพราะการ Fill
  Take Profit ทันทีขณะที่ Liquidation ยังรอ candle ปิด จะทำให้ผลการเทรดดูดีเกินจริง
- เมื่อ Basket ปิดสมบูรณ์ สามารถเริ่ม Basket ใหม่ในเดือน UTC เดียวกันได้ทันที

## 6. Entry Pair และ Cooldown

Entries ถูกจัดเป็นคู่ตามลำดับ โดย Pair ลำดับที่ `n` ประกอบด้วย Entries
`(2n - 1)` และ `2n` จนครบ `max_entries` ของ Session

Lifecycle:

1. ระหว่างที่ Pair มีเพียง Entry แรก ระบบยังเปิด Entry ที่สองได้เมื่อมีสัญญาณ
2. เมื่อ Pair มีครบสอง Entries Basket เข้าสู่ Cooldown ทันที
3. ระหว่าง Cooldown ห้ามเพิ่ม Entry แต่ Basket Take Profit ยังคงทำงาน
4. Cooldown ไม่หมดอายุเองตามเวลา ผู้ใช้ต้องกด "Cancel Cooldown" เพื่อยกเลิกจึงจะเริ่ม Pair ถัดไปได้
5. วน lifecycle นี้จน Basket ปิดหรือครบ `max_entries` ของ Session
6. การปิด Basket จะ reset Entry Pair และ Cooldown state สำหรับ Basket ใหม่

การกด Cancel Cooldown ยกเลิกได้เฉพาะ Cooldown รอบที่กำลังบล็อกอยู่ครั้งเดียว เมื่อ Pair
ถัดไปครบสอง Entries Basket จะเข้า Cooldown อีกครั้งตามปกติ ใช้ได้ทั้ง Paper Spot และ
Paper Futures โดย Paper Spot เก็บผลการยกเลิกไว้ในฐานข้อมูลจึงอยู่รอดข้าม restart ส่วน
Paper Futures ยกเลิกได้เฉพาะขณะ Bot กำลังรัน เพราะ Futures ไม่รับ Basket ที่เปิดค้างกลับมา

## 7. Capital Allocation

ผู้ใช้กำหนด Available Capital และ `max_entries` สำหรับแต่ละ Session ระบบแบ่งขนาด Entry เท่ากันจาก Trading Capital ของ Mode นั้น `max_entries` ต้องเป็นเลขคู่ตั้งแต่ 2–20 และถูกตรึงตลอด Session

### 7.1 Spot

- Spot ใช้ Available Capital ทั้งจำนวนเป็น Trading Capital ไม่มี Reserve และไม่มี
  สัดส่วนให้ผู้ใช้กำหนด
- Quote notional ต่อ Entry: Available Capital หาร `max_entries`

### 7.2 Futures

- Trading Capital: 50% ของ Available Capital
- Collateral Buffer: 50% ของ Available Capital
- Initial margin budget ต่อ Entry: Trading Capital หาร `max_entries`
- Target notional ต่อ Entry: Initial margin budget ต่อ Entry คูณด้วย leverage ของ Session
- Internal Alpha ใช้ One-way Mode และ Cross Margin เท่านั้น
- Leverage เป็นจำนวนเต็ม 1x–5x และตรึงตลอด Session
- Collateral Buffer ไม่ถูกใช้สร้าง Entry ใหม่ แต่ถือเป็นเงินที่ผู้ใช้ยอมเสี่ยงทั้งหมดเพื่อเลื่อน liquidation
- Paper Futures Policy v1 ใช้ Maintenance Margin Rate 0.5% แบบ deterministic ไม่อ้างว่าเท่ากับ Binance Mark Price หรือ Maintenance Margin Tier จริง
- Paper Futures ใช้ completed-Candle price crossing เป็น Liquidation verdict แบบ
  deterministic: LONG ใช้ `candle.low <= liquidation_price` และ SHORT ใช้
  `candle.high >= liquidation_price` หากราคาแตะระดับดังกล่าวระหว่าง Candle แล้วฟื้นกลับ
  ก่อน Candle ปิด ให้ถือว่าเกิด Liquidation แล้ว
- Liquidation ปิด Basket ด้วย `close_reason = LIQUIDATION`, เปลี่ยน Session เป็น terminal `LIQUIDATED` และห้ามสร้าง Entry ใหม่

ระบบต้องบังคับ immutable Session policy, capital allocation, leverage cap และ `max_entries` ใน Paper และ Live ด้วยกติกาชุดเดียวกัน

## 8. Paper Execution Model

Paper Trading ใช้ conservative candle fill เพื่อให้ replay ได้และป้องกัน look-ahead bias

- สัญญาณจาก completed candle `N` สร้าง Entry Intent
- Entry Fill ที่ราคาเปิดของ candle `N+1`
- Take Profit เริ่มทำงานหลัง Entry Fill และ Fill เมื่อช่วงราคาของ candle ถัดจาก candle ที่ Entry Fill แตะระดับ Limit เพื่อหลีกเลี่ยงการสมมติลำดับราคา intrabar
- Paper Futures execution contract รองรับ `LONG` และ `SHORT` แต่ RSI Step Grid Preset v1 ยังสร้างเฉพาะ `LONG`
- Paper execution ไม่คิดต้นทุนการเทรดใด ๆ ทั้ง fee และ slippage: Fill ได้ราคาเปิดของ candle
  หรือราคาเป้าโดยตรง ปัดตาม tick size ของ Symbol เท่านั้น กำไรที่รายงานจึงเป็นผลของราคา
  ล้วน ๆ ให้เห็นว่ากลยุทธ์เองทำงานหรือไม่
- เมื่อใช้ข้อมูลตลาดและ Preset version เดิม Paper replay ต้องให้ผลลัพธ์เดิม

### 8.1 Candle ที่ดึงย้อนหลังหลังขาดการเชื่อมต่อ

เมื่อการเชื่อมต่อขาดแล้วกลับมา ระบบดึง candle ที่หายไปย้อนหลังมาจนถึงปัจจุบัน candle เหล่านี้
เป็นข้อมูลจริงและต้องนับทุกแท่ง แต่ราคาเปิดของมันเป็นราคาที่ตลาดผ่านไปแล้ว กฎต่อไปนี้ใช้กับ
**ทั้ง Paper และ Live** ทุก Market Type:

- **ห้ามเปิด Entry ใหม่จาก candle ที่ดึงย้อนหลัง** — Entry Intent ที่ค้างอยู่ต้องถูกคืนให้
  Strategy (ไม่ใช่ค้างรอ) เพื่อไม่ให้ไป Fill ที่ candle สดแท่งแรกด้วยสัญญาณที่อ่านจาก candle
  เก่าหลายแท่งก่อนหน้า Strategy ส่งสัญญาณใหม่ได้เมื่อตลาดสดยังบอกแบบเดิม
- **ยังต้องปิด Position ตามปกติ** — Take Profit, Stop Loss และ Liquidation ยังทำงานเต็มรูปแบบ
  เพราะเหตุการณ์เหล่านั้นเกิดขึ้นจริงในตลาดไม่ว่าระบบจะเห็นทันหรือไม่ การเมินจะรายงาน Position
  ที่ผู้ใช้ไม่ได้ถืออยู่จริง และทำให้ผลการเทรดดูดีเกินจริง
- **ยังต้องคำนวณ Indicator ตามปกติ** — RSI และ ATR ต้องเห็น candle ครบทุกแท่ง มิฉะนั้นค่าจะผิด

`market_data` มีหน้าที่บอกว่า candle นั้นมาจากการดึงย้อนหลังหรือมาสด ส่วน Session เป็นผู้บังคับ
กฎข้างต้น

Paper, Live Spot และ Live Futures ใช้ strategy, capital และ lifecycle policies ร่วมกัน แต่ใช้ execution adapter แยกกัน

### 8.1 Shared Policies และ Execution Boundary

Paper และ Live ต้องใช้ business rules ชุดเดียวกันสำหรับ Session, Strategy, capital allocation, Basket, Entry Pair, risk policy และ PnL calculation เพื่อให้ผลการตัดสินใจสอดคล้องกันทุก Mode

Execution ต้องแยกเป็น adapter ตาม Mode และ Market Type:

- Paper ใช้ adapter สำหรับจำลอง Fill และ execution costs โดยไม่ส่งคำสั่งไป Binance
- Live Spot ใช้ adapter สำหรับ Spot และต้องผ่าน Preflight ก่อน execution
- Live Futures ใช้ adapter สำหรับ Futures และต้องจัดการ margin, leverage, funding และ reconciliation ตาม semantics ของ Futures

การตั้งค่า `TradeMode.LIVE` เพียงอย่างเดียวไม่เพียงพอที่จะเริ่มการเทรดจริง และห้ามทำให้ Paper adapter เรียก Live adapter หรือ Live endpoint ได้

## 9. Live Execution Safety

- ไม่มี Binance Testnet
- ห้ามเปลี่ยนจาก Paper เป็น Live อัตโนมัติ
- Live ทุก Session ต้องผ่าน Preflight และการยืนยันจากผู้ใช้ก่อนเริ่ม
- Preflight ต้องตรวจ:
  - API credentials และ permissions
  - Binance Account ที่ installation ใช้งาน
  - BTCUSDT symbol rules
  - Balance
  - Open orders และ positions
  - Margin mode
  - Leverage
- ทุกคำสั่งต้องมี idempotency key ที่คงที่และตรวจสอบย้อนหลังได้
- Live Spot และ Live Futures ต้องใช้ execution adapter แยกกัน
- ระบบไม่ใช้ Master Account API
- API key และ secret เก็บใน OS Keyring เท่านั้น ห้ามบันทึกใน SQLite, log หรือ source control

## 10. Risk Policy

Internal Alpha ยังไม่มี:

- Stop Loss
- Maximum Drawdown hard limit
- Max Daily Loss
- Consecutive-loss stop

ระบบแสดง PnL และ Drawdown พร้อมแจ้งเตือนเพื่อการเฝ้าระวัง แต่ไม่บังคับปิด Position จากค่าเหล่านี้ ระบบยังต้องบังคับ capital allocation, maximum entries และ Futures leverage cap เสมอ

## 11. Connection Safety

เมื่อ market data ไม่สดหรือ WebSocket ขาดการเชื่อมต่อ:

- ห้ามสร้าง Entry ใหม่
- คง Take Profit ที่ส่งไว้บน Binance
- แสดงสถานะข้อมูลไม่สดและแจ้งเตือนผู้ใช้
- พยายามเชื่อมต่อใหม่ด้วยนโยบาย retry ที่จำกัดและตรวจสอบได้
- เมื่อเชื่อมต่อสำเร็จต้อง backfill completed candles ที่ขาด
- ต้อง deduplicate candles ก่อนส่งให้ Strategy
- Resume การสร้าง Entry ได้เมื่อข้อมูลต่อเนื่องและ state ผ่านการตรวจสอบแล้วเท่านั้น

## 12. Shutdown และ Recovery

### 12.1 Stop Session

Stop Session หมายถึง:

- หยุดสร้าง Entry ใหม่
- บันทึก state ปัจจุบัน
- คง Basket Take Profit ที่มีอยู่
- ไม่บังคับปิด Basket

เมื่อ Stop สำเร็จ ระบบเปิด Session Setup พร้อม configuration ล่าสุดเสมอ แม้มี Open
Basket อยู่ และไม่ retire หรือปิด Position โดยอัตโนมัติ

`Start Paper Bot` เป็น action เดียวสำหรับสร้างหรือแทนที่ Session: ระบบต้อง validate
configuration ก่อนทำ side effect; หาก Session เดิมมี Position ค้าง ให้ขอการยืนยันเพียงครั้ง
เดียวเพื่อบันทึก market close พร้อม `manual-close` audit Fill โดยใช้ราคา
public market data ที่ fresh ไม่เกิน 30 วินาที จากนั้นจึง retire Session เดิม สร้าง immutable
Session ใหม่ และเริ่ม Bot อัตโนมัติ หากราคาไม่สด, stop runtime, close, retire หรือ start
ล้มเหลว ระบบต้องไม่สร้าง Session ใหม่ และคงฟอร์มไว้ให้ผู้ใช้แก้ไขหรือกด Start ซ้ำได้

### 12.2 Startup Recovery

เมื่อเปิดโปรแกรมใหม่:

1. โหลด Session, Basket, Entry, Order และ Fill state จากฐานข้อมูล
2. ดึง exchange state สำหรับ Live Session
3. Reconcile local state กับ exchange state
4. Resume ได้เมื่อ state ตรงกัน
5. หาก state ไม่ตรงกัน ให้บล็อก Entry ใหม่ แสดงรายละเอียด และรอให้ผู้ใช้แก้ไข

ระหว่าง Recovery ระบบห้ามยกเลิกคำสั่ง เปิด Basket ใหม่ หรือแก้ state โดยอัตโนมัติ

## 13. User Interface

UI เป็นภาษาอังกฤษและใช้ Full Dark Theme ที่อ่านง่ายและน่าเชื่อถือ โดยใช้ TiewTrade
Blue เป็นสี primary action งาน network, engine และ persistence ต้องไม่บล็อก UI thread

การกด `Start Paper Bot` หมายถึงตรวจสอบค่า และเมื่อมี Session เดิมต้องจัดการ Position/retire
ตามกฎ Shutdown ก่อนสร้างและบันทึก immutable Paper Session configuration แล้วเริ่ม Market
Data, Strategy และ Paper Execution ต่อเนื่องใน flow เดียว
ระหว่างเริ่มและขณะ Runtime ทำงาน ค่า configuration ของ Session แก้ไขไม่ได้

หน้าจอหลัก:

- Session Setup และ Paper/Live Mode
- Operations Dashboard
- Chart ของ Symbol ที่เลือกสำหรับ Paper Session
- Orders
- Positions และสถานะของ Basket (Active / Max Entries reached / Cooldown)
- Notifications
- Settings และ Live Preflight
- Recovery/Reconciliation

UI ต้องแยก Paper และ Live อย่างชัดเจน แสดง Market Type, Mode, Preset Version, data freshness และ Bot Session state ตลอดเวลาที่เกี่ยวข้องกับการตัดสินใจซื้อขาย

### คำที่หน้าจอใช้ ต่างจากคำของโดเมน

โดเมนเรียกการเทรดหนึ่งรอบว่า **Basket** ซึ่งเป็นคำที่ทีมใช้คุยกัน ไม่ใช่คำที่เทรดเดอร์ใช้
หน้าจอจึงเรียกสิ่งเดียวกันว่า **Position** — คำที่ผู้ใช้เป้าหมายรู้จักอยู่แล้ว

| โดเมนและโค้ด | หน้าจอ |
|---|---|
| Basket | Position |
| Basket History | Position History |
| Basket Lifecycle | Status (ในตาราง Position) |
| Net PnL / Realized PnL | Profit / Loss |

กฎใน AGENTS.md ที่ให้ใช้คำศัพท์ตาม CONTEXT.md ยังบังคับกับ **โค้ด** เหมือนเดิม — ชื่อคลาส
ตัวแปร ตารางในฐานข้อมูล และเอกสารยังเป็น Basket ทั้งหมด ที่เปลี่ยนคือ **ข้อความที่ผู้ใช้เห็น**
เท่านั้น การแปลคำเกิดขึ้นที่ชั้น UI จุดเดียว

Chart เป็น read-only view ที่ผู้ใช้เลือก Symbol และ Timeframe ที่รองรับได้อย่างอิสระ
จาก immutable Bot Session การเลือก Chart ต้องไม่เปลี่ยน Session, Strategy, Position,
Order หรือ Bot lifecycle และจะแสดง Fill marker เฉพาะเมื่อ Symbol และ Timeframe ของ
Chart ตรงกับ Session เท่านั้น

## 14. Data และ Auditability

SQLite เก็บข้อมูลที่ไม่ใช่ secret ได้แก่:

- Versioned Preset
- Session configuration และ state
- Basket และ Entry Pair state
- Entry Intent
- Order และ Fill
- PnL
- Notification
- Recovery/Reconciliation record
- Operational audit event

ข้อมูลทุก record ที่เกี่ยวกับการซื้อขายต้องผูกกับ Session อย่างชัดเจน โดย `session_id` เป็น ownership boundary โดยตรง Durable audit trail ต้องอธิบายได้ว่า Bot ตัดสินใจอะไร เมื่อใด ใช้ candle/preset ใด และผลการส่งคำสั่งเป็นอย่างไร

## 15. Acceptance Criteria

### 15.1 Paper Trading Complete

- completed candle ของ Timeframe ที่ Session เลือกต่อเนื่อง ไม่ขาดและไม่ซ้ำหลัง backfill
- RSI Step Grid ทำงานตรงตาม Versioned Preset
- Basket Take Profit, Entry Pair และ Cooldown ผ่าน automated tests
- Paper Fill ไม่มี look-ahead bias และ replay ได้ผลเดิม
- Paper Futures ใช้ Trading Capital และ Collateral Buffer อย่างละ 50%, One-way Mode, Cross Margin, leverage จำนวนเต็ม 1x–5x ที่ตรึงตลอด Session และ Liquidation แบบ deterministic
- Restart แล้วกู้ Session และ Basket ได้โดยไม่สร้าง Entry หรือ Order ซ้ำ
- ระบบบังคับหนึ่ง Active Bot Session สำหรับ installation
- UI แสดง state, PnL, Drawdown, data freshness และเหตุการณ์สำคัญ
- business rules, persistence, recovery และ execution boundaries มี automated tests

### 15.2 Live Spot Gate

- Paper Trading Complete ผ่านทั้งหมด
- Preflight, idempotency, reconciliation และ safe shutdown ผ่านการทดสอบด้วย fake adapter
- ผู้ใช้อนุมัติเริ่มทดสอบ Live Spot โดยชัดแจ้งในอนาคต

### 15.3 Live Futures Gate

- Live Spot Gate และ acceptance ผ่าน
- Cross Margin, leverage cap, Collateral Buffer และ funding ผ่านการตรวจสอบ
- `liquidationPrice`, `markPrice` และ maintenance-margin facts จาก Binance เป็น
  authoritative source สำหรับ Live Futures ส่วนสูตร Paper Futures ใช้ตัดสิน Live ไม่ได้
- ผู้ใช้อนุมัติเริ่มทดสอบ Live Futures โดยชัดแจ้งในอนาคต

## 16. สิ่งที่ไม่อยู่ใน Internal Alpha

- Symbol Paper นอกเหนือจาก `BTCUSDT`, `ETHUSDT` และ `BNBUSDT`
- หลายกลยุทธ์หรือ Strategy Plugin System
- Generic base strategy/registry ที่ยังไม่มี consumer ตัวที่สอง
- Master Account API
- Binance Testnet
- Manual order terminal
- Advanced chart tools
- Backtest UI
- Automatic Stop Loss และ automatic loss limits
- Login และระบบสมาชิก
- License flow
- Multi-account และ Binance sub-account
- Cloud Sync
- Web application
- Mobile application

## 17. หลักการเปลี่ยนขอบเขต

การเปลี่ยน business rule, risk policy, live gate หรือ acceptance criteria ต้องแก้ `PRODUCT.md` และสร้าง Preset version ใหม่เมื่อการเปลี่ยนแปลงมีผลต่อ Strategy Session ห้ามเปลี่ยนพฤติกรรมของ Session เดิมโดยเงียบ ๆ
