# สูตรการทำงานของ RSI Step Grid Preset v1

เอกสารอ้างอิงสูตรทั้งหมดของ Strategy Preset `rsi-step-grid-v1` รวบไว้ที่เดียว
เปิดดูแล้วคำนวณตามมือได้โดยไม่ต้องอ่านโค้ด ทุกสูตรมีบรรทัดในโค้ดจริงกำกับไว้

**ขอบเขต** — อธิบายเส้นทาง Paper Spot เป็นหลัก กติกา Basket / Entry Group /
Take Profit ที่อยู่ใน `trading` เป็นของกลาง Paper Futures ใช้ชุดเดียวกัน
ต่างกันแค่ adapter ที่ฟิลและเรื่อง margin/leverage ซึ่งไม่อยู่ในเอกสารนี้

**สัญลักษณ์ที่ใช้ตลอดเอกสาร**

| สัญลักษณ์ | ความหมาย |
| --- | --- |
| `n` | period ของอินดิเคเตอร์ |
| `i` | ดัชนีของ Entry หนึ่งตัว |
| `g` | Entry Group หนึ่งกลุ่ม |
| `floor_tick(x)` | ปัดลงให้ลงตัวกับ `tick_size` |
| `ceil_tick(x)` | ปัดขึ้นให้ลงตัวกับ `tick_size` |
| `floor_step(x)` | ปัดลงให้ลงตัวกับ `step_size` |

---

## 0. ค่าคงที่ของ Preset

จาก [`preset.py`](../../src/tiewtrade/strategies/rsi_step_grid/preset.py)

| พารามิเตอร์ | ค่า |
| --- | --- |
| `version` | `rsi-step-grid-v1` |
| `rsi_period` | 14 |
| `rsi_reset_threshold` | 30 |
| `rsi_entry_threshold` | 50 |
| `atr_period` | 14 |
| `take_profit_atr_multiplier` | 3 |

ค่าเหล่านี้ **canonical ต่อ version** — `__post_init__` โยน `ValueError` ทันที
ถ้าค่าใดค่าหนึ่งไม่ตรง ปรับจูนไม่ได้ เพื่อให้ replay ชุดแท่งเทียนเดิมได้ผลเดิมเสมอ

`max_entries` **ไม่ได้** อยู่ใน Preset — เป็น session config ต้องเป็นเลขคู่ 2–20
([`entry_policy.py`](../../src/tiewtrade/trading/entry_policy.py))

---

## 1. สูตรอินดิเคเตอร์

จาก [`indicators.py`](../../src/tiewtrade/strategies/indicators.py)

### 1.1 Wilder smoothing

พื้นฐานของทั้ง RSI และ ATR ([`indicators.py:30`](../../src/tiewtrade/strategies/indicators.py#L30))

```
avg_new = (avg_prev × (n − 1) + value) / n
```

### 1.2 True Range

([`indicators.py:60`](../../src/tiewtrade/strategies/indicators.py#L60))

```
TR = max( high − low,
          |high − prev_close|,
          |low  − prev_close| )
```

แท่งแรกของชุดไม่มี `prev_close` → `TR = high − low`

### 1.3 ATR

```
seed  : ATR = ค่าเฉลี่ยเลขคณิตของ TR 14 ตัวแรก
ต่อไป : ATR = wilder_smooth(ATR_prev, TR, 14)
```

### 1.4 RSI

```
gain = max(close − prev_close, 0)
loss = max(prev_close − close, 0)

seed  : avgGain, avgLoss = ค่าเฉลี่ยเลขคณิตของ 14 ตัวแรก
ต่อไป : avgGain = wilder_smooth(avgGain_prev, gain, 14)
        avgLoss = wilder_smooth(avgLoss_prev, loss, 14)

RS  = avgGain / avgLoss
RSI = 100 − 100 / (1 + RS)
```

เคสขอบ ([`indicators.py:40`](../../src/tiewtrade/strategies/indicators.py#L40))

| เงื่อนไข | RSI |
| --- | --- |
| `avgGain = 0` และ `avgLoss = 0` | 50 |
| `avgLoss = 0` | 100 |
| `avgGain = 0` | 0 |

### 1.5 Warm-up

([`preset_rules.py:34`](../../src/tiewtrade/strategies/preset_rules.py#L34))

```
warm_up = max(rsi_period + 1, atr_period, WILDER_WARM_UP_CANDLES)
        = max(15, 14, 250)
        = 250 แท่ง
```

ก่อนครบ 250 แท่ง `update()` คืน `None` และกลยุทธ์ไม่ถูกถามเลย

Wilder smoothing ไม่เคยลืมค่า seed สนิท ถ้าเริ่มอ่านที่ 14 แท่ง ช่วงตลาดเงียบจะสอน
ค่าเฉลี่ยว่า "ไม่มีอะไรเคลื่อนไหว" แล้วแท่งธรรมดาแท่งถัดไปจะอ่านออกมาเป็นค่าสุดขั้ว
250 คือพื้นที่ทำให้ seed จางพอจนค่าที่อ่านได้เป็นของตลาดจริง ทั้ง Strategy และกราฟ
เริ่มนับจากจำนวนเดียวกัน แท่งเดียวกันจึงอ่านได้ค่าเดียวกันทั้งสองฝั่ง

---

## 2. เงื่อนไขเข้าซื้อ

จาก [`strategy.py`](../../src/tiewtrade/strategies/rsi_step_grid/strategy.py)

กลยุทธ์มีสถานะเดียวที่จำไว้คือ `reset_close` และทำงานเป็นสองจังหวะ

### 2.1 ARM — ติดอาวุธ

```
ถ้า RSI < 30:
      reset_close := candle.close
      return None            ← จบทันที ไม่ซื้อ ไม่พิจารณาต่อ
```

เช็คก่อนทุกอย่างเสมอ แท่งที่ RSI ต่ำกว่า 30 จึงไม่มีทางเป็นแท่งที่เข้าซื้อ

### 2.2 FIRE — ลั่นไก

ต้องเป็นจริงพร้อมกันทั้งหมด ([`strategy.py:35-43`](../../src/tiewtrade/strategies/rsi_step_grid/strategy.py#L35-L43))

```
reset_close ≠ None          ← เคยดำดิ่งต่ำกว่า 30 มาก่อน
AND RSI   > 50              ← เด้งกลับแรงพอ
AND close > open            ← แท่งเขียว
AND close > reset_close     ← ปิดเหนือจุดที่เคยลงลึก
AND can_enter               ← ประตูเปิด (ดู 2.3)

→ ออก EntryIntent  (side = LONG เสมอ)
```

ทั้งหมดเป็น strict inequality — `RSI = 50` พอดี หรือ `close = reset_close` พอดี
ไม่ผ่าน

### 2.3 `can_enter` ประกอบด้วยอะไร

| เงื่อนไข | ที่มา |
| --- | --- |
| ไม่มี `pending_intent` ค้างอยู่ | [`strategy.py:26`](../../src/tiewtrade/strategies/rsi_step_grid/strategy.py#L26) |
| Entry Pair gate เปิด (ไม่ติด Cooldown) | [`entry_pair.py`](../../src/tiewtrade/trading/entry_pair.py) `entry_gate()` |
| `basket.entry_count < max_entries` | `PaperCandleTransition.evaluate_strategy` |
| ไม่ใช่แท่งที่เพิ่งขายไม้ออกไป | `exited_on_this_candle` |

ข้อสุดท้ายกันไม่ให้สัญญาณที่อ่านบนแท่งที่เพิ่งขาย ไปฟิลที่ open ของแท่งถัดไปทันที
ซึ่งเท่ากับเข้าใหม่บนหลังการออกของตัวเอง

### 2.4 ที่มาของคำว่า "Step"

([`strategy.py:56-60`](../../src/tiewtrade/strategies/rsi_step_grid/strategy.py#L56-L60))

```
on_entry_filled  →  pending_intent := None
                    reset_close    := None      ← ล้างทิ้ง
```

ล้าง `reset_close` แปลว่าไม้ถัดไปต้องกลับไปเริ่มที่ ARM ใหม่หมด **RSI ต้องดำดิ่ง
ต่ำกว่า 30 อีกรอบ** ไม้แต่ละไม้จึงห่างกันเป็นขั้นบันได ไม่ซื้อรัวติดกัน

`on_entry_rejected` ล้างเฉพาะ `pending_intent` — `reset_close` ยังอยู่ สัญญาณเดิม
จึงกลับมาลั่นไกใหม่ได้บนแท่งสด

---

## 3. สูตรการฟิล

จาก [`paper_spot.py:56-77`](../../src/tiewtrade/execution/paper_spot.py#L56-L77)
และ [`symbol_rules.py`](../../src/tiewtrade/trading/symbol_rules.py)

```
fill_price = ceil_tick(candle_ถัดไป.open)              ← ปัดขึ้น เข้าข้างตลาด
quantity   = floor_step(entry_notional / fill_price)   ← ปัดลง เข้าข้างตลาด
เงื่อนไข   : fill_price × quantity ≥ min_notional      ← ไม่ผ่าน → reject ทั้งไม้
atr        = intent.atr                                ← ATR ของแท่งสัญญาณ
filled_at  = candle.open_time
```

การปัดทั้งสองทางเข้าข้างตลาดเสมอ Paper Fill จึงไม่มีทางออกมาสวยกว่าความจริง

### 3.1 ลำดับเหตุการณ์ใน 1 แท่ง — ที่มาของ `intent.atr`

([`paper_spot_session.py:229-257`](../../src/tiewtrade/application/paper_spot_session.py#L229-L257))

| ลำดับ | ทำอะไร |
| --- | --- |
| 1 | ฟิล Intent ที่ค้างจากแท่งก่อน ที่ราคา `open` ของแท่งนี้ |
| 2 | เช็คว่าแท่งนี้พา Group ไหนถึง TP หรือไม่ |
| 3 | `indicators.update(candle)` ← **ATR/RSI ของแท่งนี้เพิ่งเกิดตรงนี้** |
| 4 | ถามกลยุทธ์ว่ามีสัญญาณใหม่ไหม |

ตอนขั้นที่ 1 อินดิเคเตอร์ยังไม่ได้กินแท่งปัจจุบัน ATR ของแท่งที่กำลังฟิลจึงยัง
**ไม่มีตัวตน** — ค่าที่ใช้ได้มีแค่ค่าที่ `EntryIntent` พกมาจากแท่งสัญญาณ

สรุปเป็นประโยคเดียว:

> **ราคา** มาจากแท่งที่ฟิล (`open` ของแท่งถัดไป) — **ATR** มาจากแท่งสัญญาณ (แท่งก่อนหน้า)

### 3.2 แท่ง Backfill

แท่งที่ refetch มาหลังหลุดการเชื่อมต่อ **เปิด Entry ไม่ได้** — Intent ที่ค้างอยู่จะถูก
ส่งคืนกลยุทธ์ (`reject_pending_intent`) แต่ยังป้อนอินดิเคเตอร์และยังปิด Basket ได้
รายละเอียดใน `PRODUCT.md` 8.1

---

## 4. สูตรการจัดกลุ่ม (Entry Group)

จาก [`basket.py:399`](../../src/tiewtrade/trading/basket.py#L399)

**Entry Group** คือกลุ่มของไม้ที่มี Take Profit ร่วมกันและขายออกพร้อมกัน
Basket หนึ่งใบถือได้หลาย Group พร้อมกัน

```
latest = max(open_group_ids)                    ← กองที่เปิดล่าสุด กองเดียว

ถ้า averages_down(fill_price, group_avg(latest)):
      เข้ากอง latest
มิฉะนั้น:
      เปิดกองใหม่  group_id := group_sequence + 1
```

`averages_down` ([`basket.py:386`](../../src/tiewtrade/trading/basket.py#L386))

```
Long  : fill_price <  group_avg      ← จ่ายถูกกว่า
Short : fill_price >  group_avg      ← ขายแพงกว่า
```

ราคาเท่ากันพอดีนับเป็น **ไม่ถัว** → เปิดกองใหม่

### 4.1 สองข้อที่ต้องเน้น

**เทียบกับกองล่าสุดกองเดียว** ไม้ที่ถูกกว่าค่าเฉลี่ยของกองเก่าแต่แพงกว่ากองล่าสุด
ยังนับเป็นถัวขึ้น กองเก่ารอราคาของมันมานานแล้ว ระหว่างนั้นตลาดเคลื่อนไปไกล
การดึงเป้าของกองเก่ากลับลงมาจะทำให้ Basket ต้องรอราคาที่มันเคยผ่านไปแล้ว

**ตัดสินครั้งเดียวตอนฟิล ล็อกถาวร** `group_id` เก็บอยู่ใน `BasketEntry`
([`basket.py:59`](../../src/tiewtrade/trading/basket.py#L59)) เป็นข้อเท็จจริงของไม้นั้น
ไม่ใช่ค่าที่อ่านใหม่จาก Basket ทีหลัง ราคาตลาดจะวิ่งไปไหนก็ไม่ย้ายกอง

### 4.2 หมายเลขไม่ถูกใช้ซ้ำ

ทั้ง `entry_number` และ `group_id` เดินหน้าอย่างเดียว ไม้ที่ขายออกไปไม่คืนหมายเลข
เพราะไม้ใหม่ที่ได้หมายเลขกองเดิมจะอ่านออกมาเหมือนเคยขายพร้อมกันมาก่อน

---

## 5. สูตร Take Profit

จาก [`basket.py`](../../src/tiewtrade/trading/basket.py) —
`_group_average_entry_price` ([:376](../../src/tiewtrade/trading/basket.py#L376)),
`_measure_take_profit` ([:520](../../src/tiewtrade/trading/basket.py#L520)),
`_held_or_closer` ([:547](../../src/tiewtrade/trading/basket.py#L547))

### 5.1 ต้นทุนเฉลี่ยของกอง

ค่าเฉลี่ย **ถ่วงน้ำหนักด้วยจำนวน** ไม่ใช่ค่าเฉลี่ยเลขคณิตของราคา

```
             Σ (price_i × qty_i)
group_avg = ─────────────────────      เฉพาะไม้ที่ยังเปิดอยู่ในกอง g
                Σ (qty_i)
```

### 5.2 สูตรหลัก

```
TP_raw   = group_avg + ATR_latest × 3          (Long)
TP_new   = floor_tick(TP_raw)                  (Long — ปัดลง)

TP_raw   = group_avg − ATR_latest × 3          (Short)
TP_new   = ceil_tick(TP_raw)                   (Short — ปัดขึ้น)

TP_final = min(TP_new, TP_held)                (Long)
TP_final = max(TP_new, TP_held)                (Short)
```

`TP_raw ≤ 0` → โยน `ValueError`

**`ATR_latest`** คือ ATR ที่ติดมากับไม้ที่ใหม่ที่สุดในกอง เรียงด้วย
`(filled_at, entry_number)` — ไม่ใช่ ATR ปัจจุบันของตลาด และไม่ใช่ ATR ของไม้แรก
ATR ของแต่ละไม้จึงถูกเก็บไว้ใน `BasketEntry` ตลอดอายุ เพราะตอนไม้ออกจากกอง
ต้องหยิบ ATR ของไม้ที่เหลือมาคำนวณใหม่ ถ้าไม่เก็บไว้ก็ย้อนไปหาไม่ได้

**การปัด** เข้าข้างตลาดเสมอ Long ปัดลง Short ปัดขึ้น และใช้ `tick_size` ที่ปักไว้
จาก Fill แรกของ Basket — Basket ไม่ไปถาม exchange rule ใหม่ทุกครั้งที่ปัด

### 5.3 กฎ "เลื่อนเข้าใกล้ได้อย่างเดียว"

`TP_held` คือเป้าที่กองถืออยู่ก่อนหน้า กองที่เพิ่งเปิดไม่มี → ใช้ `TP_new` ตรง ๆ

ถ้า ATR พุ่งขึ้นจนสูตรให้เป้าไกลกว่าเดิม จะยึดเป้าเดิมไว้ ไม่งั้นราคาที่ตลาดเกือบ
แตะแล้วจะถูกผลักออกไปนอกระยะ กองก็จะถือ Position ที่ทำกำไรได้แล้วต่อไปเรื่อย ๆ

กฎนี้ทำให้เป้าของกองเป็น **ประวัติ ไม่ใช่ค่าที่อ่านใหม่ได้** — เล่นไม้ที่ยังเปิดอยู่ซ้ำ
ไม่ได้ค่าเดิมกลับมา Basket ที่ดึงจากฐานข้อมูลจึงต้องถูกบอกค่าตรง ๆ ผ่าน
`restore_group_take_profit_prices`

### 5.4 TP ถูกคำนวณใหม่เมื่อไหร่

| จังหวะ | ฟังก์ชัน |
| --- | --- |
| ไม้ใหม่เข้ากอง | `add_entry` — วัดก่อนเพิ่มไม้จริง เป้าที่วัดไม่ได้ทิ้ง Basket ไว้เท่าเดิม |
| ไม้ออกจากกอง | `_refresh_group_take_profit` ([:563](../../src/tiewtrade/trading/basket.py#L563)) |
| ไม้สุดท้ายของกองออก | กองถูกลบทิ้ง ไม่มี TP อีกต่อไป |

### 5.5 `basket.take_profit_price` ใช้ทำอะไร

คืน **เป้าที่ใกล้ที่สุดในบรรดาทุกกอง** — ไว้แสดงผลอย่างเดียว ไม่ใช้ตัดสินการขาย
Basket ไม่มีเป้าของตัวเอง มีแต่เป้าของแต่ละกอง

---

## 6. สูตรการขายและ PnL

### 6.1 ทริกเกอร์และลำดับ

จาก [`basket.py:338`](../../src/tiewtrade/trading/basket.py#L338)

```
ถึงเป้า : Long  →  high ≥ TP_g
          Short →  low  ≤ TP_g

ลำดับ  : เรียงกองที่ถึงเป้า จาก TP ใกล้ไปไกล
```

กองที่ถึงเป้าจะขาย **ทุกไม้ในกอง ที่ราคาเดียวกัน** กองอื่นที่ยังไม่ถึงอยู่เฉย ๆ
แท่งที่กว้างพอจะแตะสองกองต้องขายกองที่ใกล้ก่อน เพราะ limit order ที่ราคาใกล้กว่า
ย่อมฟิลก่อนที่ตลาดจะวิ่งไปถึงอีกราคา อ่านกลับด้านจะเป็นการบันทึกราคาดีในลำดับ
ที่ตลาดไม่ได้ให้จริง

### 6.2 ราคาขาย

จาก [`paper_take_profit.py`](../../src/tiewtrade/execution/paper_take_profit.py)

```
Long  : exit_price = floor_tick(TP_g)
Short : exit_price = ceil_tick(TP_g)
```

ขายที่ราคาเป้า ไม่ใช่ที่ `high` ของแท่ง ส่วนที่ตลาดวิ่งเลยเป้าไปไม่ใช่ของเรา

การปิดเองด้วยมือใช้ราคาตลาด ณ ตอนนั้น ปัดแบบเดียวกัน
([`paper_spot.py:110`](../../src/tiewtrade/execution/paper_spot.py#L110))

### 6.3 PnL

จาก [`basket.py:589`](../../src/tiewtrade/trading/basket.py#L589)

คิด **รายไม้ จากราคาที่ไม้นั้นซื้อมาเอง** ไม่ได้คิดจากค่าเฉลี่ยของกอง

```
Long  : PnL_i = (exit_price − price_i) × qty_i
Short : PnL_i = (price_i − exit_price) × qty_i
```

ค่าเฉลี่ยของกองใช้ตอน **วางเป้า** เท่านั้น ไม่ได้ใช้ตอนคิดกำไร

### 6.4 `SettledEntries` — ยอดสะสมของไม้ที่ขายไปแล้ว

([`basket.py:133`](../../src/tiewtrade/trading/basket.py#L133))

Basket ใบเดียวถูกทยอยขายได้หลายรอบ แต่ในประวัติเป็นรายการเดียว ยอดของไม้ที่
ขายไปแล้วจึงต้องอยู่ต่อ — สะสม `count`, `quantity`, `notional`, `realized_pnl`
Basket ที่ดึงจากฐานข้อมูลถูกส่งค่านี้เข้ามา ไม่ได้เริ่มจากศูนย์

### 6.5 `realized_pnl` กับ `exit_realized_pnl` ต่างกันอย่างไร

([`basket.py:161`](../../src/tiewtrade/trading/basket.py#L161))

| ฟิลด์ | ความหมาย | ใช้ทำอะไร |
| --- | --- | --- |
| `exit_realized_pnl` | เฉพาะการขายครั้งนี้ | ค่าของ Fill ที่ออก |
| `realized_pnl` | ทั้งรอบ รวมไม้ที่ขายไปก่อนหน้า | ตัวเลขสรุป |

แยกไว้กันนับซ้ำ — ไม้ที่ขายไปก่อนหน้ามี Fill ของตัวเองที่บันทึกกำไรไว้แล้ว
ถ้าเอา `realized_pnl` ทั้งก้อนไปออก Fill อีกทีจะกลายเป็นบันทึกกำไรสองรอบ

สองเส้นทางที่ Basket ปิด

```
ขายครบทีละไม้ →  settled_result()
                 exit_realized_pnl = 0        ← ไม่ขายอะไรเพิ่ม เป็นใบสรุปเฉย ๆ
                 realized_pnl      = ยอดสะสมจาก SettledEntries

ปิดรวบทั้งใบ  →  close()
                 exit_realized_pnl = Σ PnL_i ของไม้ที่ยังเปิดอยู่ กวาดที่ราคาเดียว
                 realized_pnl      = exit_realized_pnl + ยอดสะสม
```

---

## 7. ตัวอย่างเดินตัวเลขตั้งแต่ต้นจนจบ

สมมติ `tick_size = 0.01`, `step_size = 0.00001`, `min_notional = 5`,
`entry_notional = 250 USD` ต่อไม้, `max_entries = 4`, Long

### 7.1 ไม้ที่ 1

```
แท่ง A   RSI 26  ปิด 97,500          →  ARM  reset_close := 97,500
...
แท่ง K   RSI 58  เปิด 99,000 ปิด 99,800   →  FIRE  (58 > 50, เขียว, 99,800 > 97,500)
                                            ATR ณ แท่ง K = 500
แท่ง L   เปิด 100,000                →  ฟิล
```

```
fill_price = ceil_tick(100,000)              = 100,000
quantity   = floor_step(250 / 100,000)       = 0.00250
notional   = 100,000 × 0.00250               = 250.0000  ≥ 5  ผ่าน

กอง 1 (เพิ่งเปิด ไม่มี TP_held)
group_avg  = 100,000.00
TP         = floor_tick(100,000 + 500 × 3)   = 101,500.00
```

`reset_close` ถูกล้าง → ต้องรอ RSI ลงต่ำกว่า 30 อีกรอบ

### 7.2 ไม้ที่ 2 — ถัวลง

ราคาไม่ขึ้น ลงไปจน RSI ต่ำกว่า 30 อีกครั้ง ARM ใหม่ แล้ว FIRE บนแท่งที่ ATR = 520
แท่งถัดไปเปิดที่ 98,100

```
fill_price = ceil_tick(98,100)               = 98,100
quantity   = floor_step(250 / 98,100)        = 0.00254        ← ปัดลง
notional   = 98,100 × 0.00254                = 249.1740  ≥ 5  ผ่าน
```

**จัดกลุ่ม** — 98,100 < 100,000 (ค่าเฉลี่ยกอง 1) → **ถัวลง เข้ากอง 1**

```
              (100,000 × 0.00250) + (98,100 × 0.00254)
group_avg  = ─────────────────────────────────────────  = 99,042.46
                        0.00250 + 0.00254

TP_new     = floor_tick(99,042.46 + 520 × 3)  = 100,602.46
TP_held    = 101,500.00
TP_final   = min(100,602.46, 101,500.00)      = 100,602.46    ← ใกล้ขึ้น ใช้ค่าใหม่
```

```
กอง 1   ไม้ 1 + ไม้ 2   เป้าเดิม 101,500.00  →  เป้าใหม่ 100,602.46
```

เป้าเลื่อนลงมา 897.54 จุด เพราะต้นทุนเฉลี่ยถูกลง

### 7.3 ขายออก

แท่งถัดมา `high = 100,700` → `100,700 ≥ 100,602.46` → กอง 1 ถึงเป้า

```
exit_price = floor_tick(100,602.46) = 100,602.46

ไม้ 1  PnL = (100,602.46 − 100,000) × 0.00250 = 1.5061500
ไม้ 2  PnL = (100,602.46 −  98,100) × 0.00254 = 6.3562484
                                         รวม  = 7.8623984 USD
```

ขายราคาเดียวกัน แต่ไม้ 2 ได้กำไรมากกว่าไม้ 1 ราว 4.2 เท่า เพราะซื้อได้ถูกกว่า
และมันคือไม้ที่ดึงเป้าของทั้งกองลงมาให้ขายได้เร็วขึ้น

ไม้สุดท้ายออก → Basket ว่าง → `settled_result()` ออกใบสรุป
`realized_pnl = 7.8623984`, `exit_realized_pnl = 0`

### 7.4 เคสเทียบ — ไม้ที่ 2 ถัวขึ้น

ถ้าไม้ 2 ฟิลที่ 101,000 แทน (ATR ณ แท่งสัญญาณ = 540)

```
quantity   = floor_step(250 / 101,000)        = 0.00247
101,000 ≥ 100,000 (ค่าเฉลี่ยกอง 1)  →  เปิดกองใหม่

กอง 1   ไม้ 1   avg 100,000.00   TP 101,500.00   ← ไม่ถูกแตะเลย
กอง 2   ไม้ 2   avg 101,000.00   TP floor_tick(101,000 + 540 × 3) = 102,620.00
```

สองไม้ขายคนละราคา คนละเวลา ไม้ 1 ไม่ถูกไม้ใหม่ดันต้นทุนขึ้น

### 7.5 เคสเทียบ — ATR พุ่งตอนถัวลง

กลับไปสถานการณ์ 7.2 แต่ ATR ณ แท่งสัญญาณของไม้ 2 = 900

```
TP_new   = floor_tick(99,042.46 + 900 × 3)  = 101,742.46
TP_held  = 101,500.00
TP_final = min(101,742.46, 101,500.00)      = 101,500.00    ← ยึดเป้าเดิม
```

สูตรให้เป้าไกลกว่าเดิม `_held_or_closer` จึงไม่ยอมขยับ

---

## 8. สิ่งที่กลยุทธ์นี้ *ไม่* ทำ

| ไม่มี | รายละเอียด |
| --- | --- |
| Short | Preset v1 ออก `PositionSide.LONG` เท่านั้น แม้ Paper Futures execution จะรองรับ Short ก็ตาม |
| Stop loss | ไม่มีการตัดขาดทุนอัตโนมัติ ออกได้ทางเดียวคือถึง TP หรือผู้ใช้ปิดเอง |
| Cooldown หมดอายุเอง | ครบ Entry Pair (ทุก 2 Fill) ประตูปิด ปลดได้ด้วย "Cancel Cooldown" เท่านั้น ไม่ลาพักตามเวลา |
| เข้าซื้อบนแท่ง backfill | แท่งที่ refetch มาปิด Basket ได้ แต่เปิด Entry ไม่ได้ |
| ปรับพารามิเตอร์ | ค่าใน Preset canonical ต่อ version แก้แล้ว `ValueError` |

ดูรายละเอียดเชิงนโยบายใน `PRODUCT.md` และคำศัพท์ใน `CONTEXT.md`
