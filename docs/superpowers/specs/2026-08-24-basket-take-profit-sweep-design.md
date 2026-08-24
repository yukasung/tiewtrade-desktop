# Basket Take Profit ที่วัดจากราคาเฉลี่ย และการปิดรวบไม้ที่ติดดอย

วันที่: 2026-08-24

## ปัญหา

Basket ที่ถัวเฉลี่ยลงมาแล้วมี Entry ที่ราคาแพงที่สุดค้างอยู่ ไม่มีทางถูกปิดเลยภายใต้
take profit รายไม้ ผู้ใช้ต้องการให้ "ปิดรวบ" คือเมื่อราคาถึงเป้าที่วัดจากราคาเฉลี่ยรวม
ให้ปิด Entry ที่ยังขาดทุนออกไปพร้อมกัน

ระหว่างสำรวจพบว่านี่คือกติกาที่ `PRODUCT.md` §5 ระบุไว้อยู่แล้ว
(`weighted_average_entry_price ± ATR*3`, "ปิด Entries ทั้งหมดใน Basket พร้อมกัน")
แต่ commit `16b408c` ("make the Entry the unit a Position is held and sold in")
เปลี่ยน implementation ไปเป็นเป้ารายไม้โดยไม่ได้แก้ `PRODUCT.md` งานนี้จึงเป็นการดึง
โค้ดกลับมาตรงกับเอกสาร ไม่ใช่การเพิ่มกติกาใหม่

## การตัดสินใจ

### 1. ทำไม take profit รายไม้อยู่ร่วมกับการปิดรวบไม่ได้

สำหรับ LONG ที่ถัวลงมา ระดับ `avg + ATR*k` อยู่ที่หรือสูงกว่าเป้าของ Entry ที่ถูกที่สุดเสมอ
ถ้าปล่อยให้เป้ารายไม้ทำงานคู่กัน Entry ที่กำไรจะถูกขายออกก่อน → ราคาเฉลี่ยของที่เหลือสูงขึ้น
→ ระดับปิดรวบสูงขึ้นตาม → Entry ที่ติดดอยไม่มีวันถูกกวาดออก เป้ารายไม้แบบอัตโนมัติจึงต้อง
ถูก **แทนที่** ไม่ใช่เพิ่มเข้าไปคู่กัน

สิ่งที่ยังอยู่ครบคือการปิด Entry รายตัว **ด้วยคำสั่งของผู้ใช้** ตาม `PRODUCT.md` ข้อ 6.3 —
`Basket.close_entry`, `settled_result` และ `SettledEntries` ไม่ถูกแตะ

### 2. ทำไม ATR อยู่ที่ `BasketEntry` ไม่ใช่ scalar ของ Basket

`application/open_basket.py` สร้าง Basket กลับมาหลัง restart ด้วยการเล่น `add_entry`
ของเฉพาะ Entry ที่ยังเปิดอยู่ ถ้า Basket จำ ATR เป็นค่าเดียวของ "Fill ล่าสุดตลอดกาล"
ค่านั้นจะหายไปเมื่อ Entry ที่ Fill ล่าสุดถูกปิดรายตัวไปแล้ว ทำให้ runtime กับ replay
ให้เป้าต่างกัน การเก็บ ATR ไว้ที่ Entry ทำให้ทั้งสองทางตรงกันโดยโครงสร้าง

**ATR ที่ใช้คือของ Entry ที่ยังเปิดอยู่และ Fill ล่าสุด** ปกติเท่ากับกติกาเดิมใน
`PRODUCT.md:92` และถ้าผู้ใช้ปิด Entry ล่าสุดทิ้งก็ตกไปใช้ของ Entry ที่เปิดล่าสุดถัดลงมา —
มีคำตอบนิยามชัด replay ได้ และไม่ต้องเพิ่ม column ใหม่ (`trade_fills.entry_atr` มีอยู่แล้ว
และยิ่งสำคัญขึ้นเพราะตอนนี้เป็นที่มาของเป้าทั้งใบ)

### 3. คำนวณเป้าใหม่ตอนปิด Entry รายตัวด้วย

`Basket._remeasure_targets()` ถูกเรียกทั้งท้าย `add_entry` และท้าย `close_entry`
จุดหลังเป็นการแก้บั๊กที่มีอยู่ก่อนแล้ว: `close_entry` เดิมไม่แตะ `_stop_loss_price` เลย
ทำให้หลังผู้ใช้ปิด Entry รายตัว stop loss ยังอิงราคาเฉลี่ยของชุด Entry ที่ไม่มีอยู่แล้ว
เป้า take profit ระดับ Basket จะเจอบั๊กเดียวกันถ้าไม่คำนวณใหม่ที่นี่

`tick_size` ถูกเก็บไว้จาก `add_entry` ครั้งแรกเพื่อให้ `close_entry` ปัดเศษใหม่ได้
(ค่านี้ตรึงตลอด session) `Basket` ยังไม่รู้จัก `SymbolRules` ตาม dependency rule ใน
`ARCHITECTURE.md`

### 4. `ClosedBasket.exit_realized_pnl` — ทำไมต้องมี

`record_close` เดิมเขียน `closed.realized_pnl` (ยอดรวมทั้ง Basket) ลงที่ **ตัว Fill** ด้วย
ทั้งที่ Entry ที่ผู้ใช้ขายไปก่อนหน้ามี SELL Fill พร้อม PnL ของตัวเองอยู่แล้ว → Fills table
แสดงเงินก้อนเดียวกันสองครั้ง เดิมเกิดได้เฉพาะกรณี manual close แล้วตามด้วย stop loss
หลังการปิดรวบมันจะกลายเป็นเส้นทางปกติ

`Basket.close` มีตัวเลข "เฉพาะที่การปิดครั้งนี้ขาย" อยู่แล้วก่อนบวก `SettledEntries`
จึงยกออกมาเป็น field ให้ persistence อ่าน แทนที่จะให้ recorder ไปลบเลขเอง (ซึ่งจะเป็นการ
คำนวณ business decision ผิด boundary) `settled_result` คืน `Decimal("0")` เพราะการขาย
ถูก book โดย `close_entry` ที่ทำให้ Basket ว่างไปแล้ว

**แถว basket ยังใช้ `ClosedBasket.realized_pnl` (overwrite)** เพราะหลัง restart
`SettledEntries` ถูก seed จาก Fill ชุดเดียวกับที่ running total ของ store สร้าง
ทั้งสองจึงตรงกันโดยโครงสร้าง ส่วน `record_entry_exit` ที่บวกสะสมคงไว้ตามเดิม

### 5. UI

- `Basket.reaches_take_profit(high, low)` เป็นกติกาเทียบราคาที่เดียว ใช้ร่วมกันทั้ง
  Spot executor, Futures executor และ dialog ตอนกด Start เพื่อไม่ให้มี "second reading
  of the rule that sells"
- คอลัมน์ "Take Profit" ในตาราง Position อ่านจาก `row.basket.take_profit_price` จึงแสดง
  ค่าเดียวกันทุกแถว ซึ่งเป็นความจริงใหม่ — Entry ที่ซื้อแพงไม่มีเป้าของตัวเองให้รอ
- `leaves_entries_holding` ถูกลบทั้งสาย (`take_profit_confirmation` → `start_decision`
  → `open_position_workflow` → `main_window`) เพราะสภาพ "บางไม้ถึงเป้า บางไม้ไม่ถึง"
  ไม่มีอีกแล้ว ส่วน `sale_is_unknown` คงไว้ทั้งดุ้น — เป็นบั๊กที่เคยทำให้ไม่มีคำถามถูกถามเลย

## ผลกระทบที่ต้องบอก ไม่ใช่เปลี่ยนเงียบ ๆ

**Basket ที่เปิดค้างอยู่ข้ามการอัปเกรดนี้จะได้เป้าใหม่ทันทีที่ Runtime หยิบกลับมา** เป้าเดิม
รายไม้ไม่เคยถูกเก็บลงฐานข้อมูล (ถูกคำนวณใหม่จาก `entry_atr` ทุกครั้ง) จึงไม่มีอะไรให้กู้คืน
ตาม `PROJECT_PLAN.md:238` เรื่องนี้ต้องถูกบันทึกไว้ ไม่ใช่ปล่อยผ่าน

ค่าคาดหวังของ deterministic replay (`23.679`) **ไม่เปลี่ยน** เพราะ Basket ใน
`tests/fixtures/btcusdt_5m_tracer.csv` มี Entry เดียว ราคาเฉลี่ยจึงเท่ากับราคาที่ไม้นั้นจ่าย
สิ่งที่เปลี่ยนคือ SELL Fill มี `closes_entry_number is None` แทนที่จะเป็น `1`

## ที่ยังไม่แก้ในงานนี้

`PRODUCT.md:222` ระบุว่า Internal Alpha ยังไม่มี Stop Loss แต่
`strategies/rsi_buy_only/preset.py` มี `stop_loss_atr_multiplier` จริงและ `Basket`
บังคับใช้อยู่ — เป็นข้อขัดแย้งคนละเรื่อง แยกเป็นงานต่างหาก
