# Cooldown Policy ต่อ Session

วันที่: 2026-09-05

## ปัญหา

ผู้ใช้สั่งว่า **"settings ให้สามารถเลือก cooldown ได้ เช่น ปิด cooldown หรือ cooldown กี่วัน"**

คำสั่งนี้ขัดกับเอกสารที่เป็น source of truth ทั้งชุด `CONTEXT.md`, `PRODUCT.md`,
`CLAUDE.md` และ `ARCHITECTURE.md` ระบุตรงกันว่า Cooldown **ไม่หมดอายุเองตามเวลาโดยเจตนา**
และไม่ใช่ค่าที่ผู้ใช้เปลี่ยนได้ ยิ่งกว่านั้น คอมเมนต์ migration ใน
`integrations/sqlite/database.py` บันทึกไว้ว่า schema v13 เคยผูก waiver กับเดือนปฏิทิน
เพราะ Cooldown สมัยนั้นหมดอายุเองเมื่อผ่านไปสองเดือน แล้ว **ถูกถอดออกโดยตั้งใจ**

จึงแจ้งข้อขัดแย้งกลับไปพร้อมประวัตินี้ ผู้ใช้ยืนยันว่าต้องการ **เปลี่ยน product policy จริงจัง**
และให้แก้เอกสารให้สอดคล้องเป็นส่วนหนึ่งของงาน

## การตัดสินใจ

### 1. ตรึงที่ Session ไม่ใช่หน้า Settings

ผู้ใช้พูดคำว่า "settings" แต่หน้า Settings ของแอปเป็น preference ระดับเครื่อง (LINE, และ
อนาคตคือ Binance API key) ส่วน Cooldown เป็นกฎที่ตัดสินว่า Bot จะเปิด Entry หรือไม่

ค่าจึงอยู่บน Session Setup form และตรึงตลอด Session เหมือน `max_entries` เหตุผล:
`SessionConfig` เป็น immutable boundary ตาม `ARCHITECTURE.md` ถ้าเปลี่ยนกลางคันได้ Session
จะไม่ immutable อีกต่อไป และ replay ต้องเก็บประวัติการเปลี่ยนค่าเพื่อให้ได้ผลเดิม ซึ่งเป็น
ราคาที่ไม่คุ้มกับสิ่งที่ได้

`CooldownPolicy` ถูกวางไว้ใน `EntryPolicy` ไม่ใช่ field ใหม่บน `SessionConfig` เพราะ
Cooldown คือพฤติกรรมของ Entry gate และ `EntryPairLifecycle` รับ `EntryPolicy` อยู่แล้ว
ทั้ง live และ replay จึงไม่ต้องเดินสายใหม่เลยแม้แต่จุดเดียว

### 2. วัดด้วยเวลาของ candle ไม่ใช่นาฬิกาเครื่อง

`EntryPairLifecycle.entry_gate` เดิมเป็น pure function ที่ไม่แตะเวลาเลย การเพิ่มโหมดที่นับวัน
แปลว่าต้องมีนาฬิกาเข้ามาสักที่

เลือกให้ `entry_gate(as_of)` **รับเวลาเข้ามาเป็น argument** แทนที่จะให้ lifecycle ถือ clock
เอง เพราะ CLAUDE.md ห้าม business rules ขึ้นกับ infrastructure และเพราะทางเลือกนี้ทำให้ทุกค่า
เวลาบนเส้นทางเทรดตรวจสอบได้ว่ามาจากไหน ผู้ป้อนเวลาคือ Session ซึ่งใช้เวลาปิดของ candle
ล่าสุดที่รับมา — จึงยืนยันได้ว่า `paper_replay_main` กับ fixture เดิมยังได้
`{"realized_pnl":"23.679"}` เท่าเดิมทุกครั้ง

ราคาที่จ่ายคือ signature กว้างขึ้นราว 11 จุด ส่วนใหญ่เป็นเทสต์ จึงแยกเป็น commit
"ขั้นกลไกล้วน" ที่รันทั้ง suite ผ่านโดยไม่แก้ assertion เชิงพฤติกรรมแม้แต่ข้อเดียว —
นั่นคือหลักฐานว่าโหมดเดิมไม่ถูกแตะ

ฝั่ง UI ห้ามคำนวณ gate เอง (ห้ามเรียก `now()`) ให้ format วันที่ที่ domain แสตมป์มาบน
snapshot เท่านั้น ไม่งั้นหน้าจอกับ Bot อาจตอบไม่ตรงกัน

### 3. ไม่ต้อง migrate เวลาที่ Pair ครบ แต่ต้อง migrate ตัว policy

Cooldown ยืนอยู่ได้ก็ต่อเมื่อ `_holds_whole_pair` เป็นจริง คือทั้งสอง Entries ของ Pair ล่าสุด
ยังเปิดอยู่ และ `read_open_basket_state` สร้าง `OpenBasketEntry` พร้อม `filled_at` เสมอ
→ **Fill ที่ทำให้ Pair ครบอยู่ในกลุ่ม Entry ที่ replay อ่านกลับเสมอ และเวลาถูก persist ไว้แล้ว**
`replayed` จึง derive เวลานั้นออกมาได้ ไม่ต้องมีคอลัมน์ใหม่ และไม่ต้องแตะตาราง
`paper_cooldown_waivers` เลย

สิ่งที่ต้องเก็บคือ Cooldown Policy เอง เพราะเป็นค่าของ Session → `cooldown_mode` และ
`cooldown_days` บน `bot_sessions` (schema v17) ทุก Session ที่เขียนไว้ก่อนที่ตัวเลือกนี้จะมี
กลับมาเป็น `until_cancelled` ซึ่งคือกฎที่มันกำลังรันอยู่จริง

ระหว่างทางพบกับดัก: `_migrate_capital_per_entry_v12` copy แถวโดยอ้าง column list ปัจจุบัน
จึงพังทันทีที่ตารางมีคอลัมน์ใหม่ แก้โดยให้ migration เขียนเฉพาะคอลัมน์ยุคของตัวเอง
(`_V12_BOT_SESSIONS_COLUMN_NAMES`) แล้วปล่อยให้ DEFAULT ของตารางเติมส่วนที่เพิ่มมาทีหลัง
คอลัมน์ถัดไปที่เพิ่มจึงไม่ต้องไปไล่จำในทุก migration ก่อนหน้า

### 4. DURATION ไม่ใช่การกลับไปทำแบบ v13

ข้อนี้เขียนไว้ให้ชัดเพราะคนอ่านครั้งหน้าจะสงสัย v13 ผูก waiver กับ**เดือนปฏิทิน** ซึ่ง
"ไม่ได้บอกอะไรเลย" — Basket ที่เข้า Cooldown วันสิ้นเดือนได้ Cooldown สั้นกว่าอีก Basket
อย่างไร้เหตุผล ส่วน `DURATION` นับจาก**ช่วงเวลาจริง**ตั้งแต่ instant ที่ Pair ครบใน market time
และ waiver ยังชี้ Pair เหมือนเดิม ไม่มีแถว waiver ใดต้องถูกทิ้ง

### 5. แจ้งเตือนเมื่อ Cooldown หมดอายุเอง

`CooldownEndedNotice` เป็นของใหม่ เพราะไม่มีใครกดอะไรเลย จึงไม่มีอย่างอื่นบอกผู้ใช้ว่า Bot
กลับมาเปิด Entry ได้แล้ว (ผู้ใช้เลือกข้อนี้เอง เทียบกับทางเลือก "เงียบไว้")

ไม่ reuse `CooldownCancelledNotice` เพราะผู้ใช้ไม่ได้ยกเลิกอะไร และต้องแยกจากกรณีที่
Cooldown จบเพราะผู้ใช้ปิด Entry ของ Pair นั้น ตัวแยกคือ **วันที่ Cooldown จะปลด**: Pair ที่ถูก
ขายไปหนึ่ง Entry ไม่มีวันปลดให้ระบุ (`cooldown_ends_at()` คืน `None`) ส่วน Cooldown ที่ครบวัน
ยังถือครบทั้ง Pair จึงยังระบุวันได้

## สิ่งที่ไม่ทำ

- ไม่เพิ่ม CLI flag ให้ `paper_replay_main` — ค่า default คือสิ่งที่ทำให้ fixture output เท่าเดิม
- ไม่เพิ่ม `if mode is OFF` ใน UI เลย ปุ่ม Cancel Cooldown ผูกกับ gate อยู่แล้ว และภายใต้
  `OFF` gate ไม่เคยเป็น `COOLDOWN` ปุ่มจึงหายไปเองโดยไม่ต้องมีเงื่อนไขใหม่
- ไม่ลบ `EntryPairLifecycle.entry_gate_for` ที่ไม่มี production caller — เป็นคนละเรื่องกับงานนี้
