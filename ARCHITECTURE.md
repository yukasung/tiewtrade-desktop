# TiewTrade Architecture

## Feature-first Modular Monolith

TiewTrade ใช้ application เดียวที่แบ่ง Module ตาม capability ของผลิตภัณฑ์ โดย business rules ไม่ขึ้นกับ UI, database หรือ exchange SDK

## Shared Business Rules

Paper และ Live ใช้ implementation เดียวกันของ business rules สำหรับ:

- Session identity และ immutable Preset
- Strategy และ Entry Intent
- Capital allocation
- Basket และ Entry Pair/Cooldown
- Risk limits และ PnL calculation

`trading` เป็นเจ้าของ Futures side-aware PnL และ margin policy รวมถึง One-way Mode,
Cross Margin, leverage และ shared risk limits

การใช้ implementation ร่วมกันทำให้ Strategy, capital, Basket, Entry Pair และ risk limits
สอดคล้องกัน แต่ไม่ทำให้ Paper simulation แทน authority ของ Exchange ใน Live Mode

## Execution Adapters

Execution เป็น seam ที่แยก side effects ออกจาก business rules โดยมี adapter แยกตาม Mode และ Market Type:

| Mode | Market Type | Adapter responsibility |
| --- | --- | --- |
| Paper | Spot/Futures | จำลอง Fill โดยไม่ส่งคำสั่งจริงและไม่คิดต้นทุนการเทรด |
| Live | Spot | ส่งคำสั่ง Spot หลัง Preflight, idempotency และ reconciliation |
| Live | Futures | จัดการ Futures order, margin, leverage, funding และ reconciliation |

Paper และ Live ห้ามใช้ execution implementation เดียวกัน เพราะ failure modes, account facts และ side effects ต่างกัน แม้จะใช้ business rules ร่วมกัน

`execution` เป็นเจ้าของ Paper fills และ deterministic simulation โดยไม่คำนวณหรือเปลี่ยน
business policy

## Configuration Boundary

`SessionConfig` เป็น shared configuration ที่เก็บ session ID, Preset version, Market Type, Trade Mode, capital และ execution costs

หนึ่ง installation ใช้ Binance Account เดียวและมี Active Bot Session ได้สูงสุดหนึ่ง Session ทุก Basket, Entry, Order, Fill, PnL และ audit record ผูกกับ `session_id` โดยตรง

Market identity ต้องเป็นข้อมูลจาก configuration ไม่ใช่ค่าคงที่ใน strategy หรือ market-data pipeline:

| Configuration | เจ้าของ | กติกา |
| --- | --- | --- |
| `symbol` | Session/market configuration | เลือก symbol ที่ exchange รองรับ |
| `timeframe` | Session/market configuration | เลือก `3m`, `5m`, `15m`, `30m`, `1h`, `4h` และตรึงตลอด Session |
| `timezone` | System policy | ใช้ `UTC` เท่านั้น |
| `trade_mode`, `market_type` | `SessionConfig` | ใช้เลือก execution boundary |
| `preset_version` | Strategy preset | กำหนด RSI, TP, entry และ lifecycle rules |
| `max_entries` | `EntryPolicy` | ใช้ร่วมกันทุก Market Type และตรึงตลอด Session |

ค่าความเสี่ยง เช่น leverage, margin mode และ collateral buffer ต้องถูกแยกตาม market/execution policy และ validate ก่อนสร้าง runtime

Configuration นี้ไม่มี side effect และไม่เลือกหรือเรียก adapter โดยตรง การเลือก adapter ต้องเกิดที่ application composition seam และ `TradeMode.LIVE` ต้องผ่าน Preflight กับ explicit confirmation ก่อนเสมอ

## Dependency Direction

Business rules ไม่ import Binance SDK, SQLite หรือ UI รายละเอียด integration ต้องอยู่ใน adapter ที่ implement interface ของ consumer Module

Live Binance integration และ reconciliation เป็นเจ้าของการอ่าน account facts จาก Exchange
แล้วสะท้อน authoritative state เข้า application boundary โดย `trading` ห้าม import
Binance SDK หรือรู้จัก transport รายละเอียดนี้

`application` เป็นเจ้าของ completed-candle orchestration ที่จัดลำดับ `trading` policy
และ `execution` adapter ห้ามมี reverse dependency จาก `trading` หรือ `execution` กลับไป
หา `application` และห้าม Module ใดเข้าถึง Binance Private API นอกจาก concrete Live
integration ที่ส่งมอบตาม Live gate

## Desktop UI Boundary

`ui` ใช้ PySide6 เป็น thin presentation layer โดยแปลงค่าจาก form เป็น application
request และแสดง durable result เท่านั้น UI ห้าม import SQLite, Strategy หรือ Execution
adapter โดยตรง และงาน persistence ต้องทำผ่าน focused worker นอก UI thread

ข้อยกเว้นที่จำกัดเฉพาะ Chart presentation: `ui` อาจแสดง ephemeral chart read-model
ที่ application/market-data runtime ส่งผ่าน application-owned, generation-bound relay ได้
เพื่อแสดง market-data fact ที่ยังไม่ durable เช่น in-progress candle. Fact นี้ต้องเป็น
read-only, อยู่ในหน่วยความจำ, ไม่ถูกบันทึก, ไม่ใช้สร้าง durable marker และไม่มีเส้นทางไปยัง
Strategy, risk policy, execution, persistence, replay หรือ lifecycle decision. UI ยังคงห้าม
import transport, SQLite, Strategy หรือ Execution adapter โดยตรง และต้องล้าง ephemeral fact
เมื่อ session หรือ runtime generation ไม่ใช่ current

Application layer เป็นเจ้าของ `Create Paper Session` use case ซึ่งสร้าง shared immutable
Session/market configuration ก่อนเรียก concrete SQLite adapter. Desktop lifecycle
orchestration เริ่ม Paper Runtime ต่อจากการสร้างสำเร็จใน action `Start Paper Bot` เดียว
โดย UI รอผลผ่าน background worker และไม่เรียก adapter โดยตรง

Application layer เป็นเจ้าของ `ClosePaperPosition` use case สำหรับ Paper Session
เมื่อผู้ใช้ยืนยันเริ่ม Session ใหม่ขณะมี Open Basket โดยคำนวณ Paper exit Fill จาก
ราคาตลาดล่าสุดของ Session แล้วให้ SQLite Trade History บันทึก
closed Basket กับ exit Fill ใน transaction เดียว UI ต้อง retire Session หลังการบันทึก
สำเร็จเท่านั้น โดย orchestration นี้ทำงานผ่าน background worker และใช้ราคาที่ public
market-data controller ส่งเข้า application-owned `LatestMarketPrice` แยกจาก ephemeral
Chart read-model โดย service นี้เก็บแบบ thread-safe พร้อม freshness bound และเป็น contract
เดียวที่ manual close ใช้อ่าน execution price จึงไม่มีเส้นทางจาก Chart presentation fact
ไป execution, ไม่ใช้ stopped runtime snapshot และไม่มีเส้นทางนี้สำหรับ Live execution

## Persistence Boundary

`application` เป็นเจ้าของ business Session orchestration และลำดับ shared rules
ส่วน `integrations/sqlite` เป็นเจ้าของ transaction, durable mapping และ
persistence-specific fail-closed coordinator ที่ครอบ concrete application Session
โดย coordinator นี้ทำได้เฉพาะเรียก Session, บันทึกผลแบบ synchronous และปิดรับ
candle ถัดไปเมื่อยืนยัน durability ไม่ได้ ห้ามคำนวณหรือเปลี่ยน business decision

ช่วงที่มี persistence adapter เพียงแบบเดียวให้ใช้ concrete coordinator ได้โดยไม่สร้าง
generic interface ล่วงหน้า เมื่อมี consumer ของ persistence adapter อย่างน้อยสองแบบ
จึงค่อยย้าย contract ไปไว้ที่ Module ผู้ใช้งาน

## Session Notification Boundary

การแจ้งเตือนออกนอกเครื่องเป็น side effect ที่ไม่มี trading decision ใดพึ่งพา `trading` และ
`execution` จึงไม่รู้จักมันเลย `application` เป็นเจ้าของ notice ทุกชนิดใน
`application/session_notice.py` (`EntryOpenedNotice`, `BasketClosedNotice`,
`CooldownStartedNotice`, `CooldownCancelledNotice`, `MarketFeedLostNotice`,
`MarketFeedRestoredNotice`, `BotErrorNotice`) และ contract `SessionNotifier`
ส่วน `integrations/line` เป็นผู้ implement contract นี้ผ่าน LINE Messaging API push
(LINE Notify ปิดบริการเมื่อ มี.ค. 2025 จึงใช้ไม่ได้)

หนึ่งเหตุการณ์คือหนึ่งข้อความ notice แต่ละชนิดถือคนละข้อเท็จจริงและอ่านคนละแบบ เปิด Position กับ
ปิด Position จึงเป็นคนละข้อความเสมอ แม้ candle เดียวจะทำทั้งสองอย่าง

**ใครเป็นคนพูด** แบ่งตามว่าใครรู้เรื่องนั้น:

| เหตุการณ์ | ผู้เรียก | เงื่อนไข |
| --- | --- | --- |
| Entry เปิด | `PaperRuntimeSink` | snapshot มี Entry Fill |
| Position ปิด | `PaperRuntimeSink` | snapshot มี `closed_basket` (candle ปิด, candle ระหว่างแท่ง, และการขาย Entry สุดท้ายตามคำสั่งเทรดเดอร์) |
| Cooldown เริ่ม | `PaperRuntimeSink` | Entry gate เพิ่งเปลี่ยนเป็น `COOLDOWN` |
| Cooldown ถูกยกเลิก | `PaperRuntimeSink` | `waive_cooldown` สำเร็จ |
| ข้อมูลตลาดขาด / กลับมา | `PaperRuntimeController` | runtime transition |
| Bot หยุดเพราะ error | `PaperRuntimeController` | `_publish_blocked` |

snapshot บอกได้แค่ว่า "gate เป็นอะไร" ไม่เคยบอกว่า "gate เปลี่ยน" sink จึงเป็นผู้จำ gate เดิมไว้
เทียบเอง และพูดเรื่อง Cooldown ครั้งเดียวตอนที่มันปิด ไม่ใช่ทุก candle ที่มันยังปิดอยู่ ทำนองเดียวกัน
Controller จำไว้ว่า feed กำลังส่งอยู่หรือไม่ และพูดครั้งเดียวต่อการขาดหนึ่งครั้ง ไม่ใช่ครั้งเดียว
ต่อ transition และพูดเรื่อง Bot หยุดครั้งเดียวต่อการหยุดหนึ่งครั้ง แม้จะมีหลายที่ค้นพบการหยุดนั้น

`PaperRuntimeSink` เรียก notifier **หลังปล่อย entry lock** เสมอ notifier ต้องคืนค่าทันที
ห้าม raise และต้องเรียกได้จากทุก thread — candle ยกเรื่องขึ้นบน loop ของ Runtime ส่วนคำสั่ง
ของเทรดเดอร์ยกขึ้นบน thread ที่เขากดมา `LineNoticeNotifier` เป็นผู้รับผิดชอบความต่างนี้
ด้วย `call_soon_threadsafe` ส่วนการส่งจริงถูก queue ไว้ให้ worker ของ `integrations/line` ทำ
candle ถัดไปจึงไม่รอ network และการส่งล้มเหลวไม่ทำให้ Session fail-closed — ค่าที่เสียคือข้อความ
ไม่ใช่ Session

การปิด Position หรือยกเลิก Cooldown ตอน Bot หยุด (`close_one_entry_at_rest`,
`waive_cooldown_at_rest`, `ClosePaperPosition`) ไม่ส่งข้อความ เพราะไม่มี Runtime ถือ notifier
อยู่ — เทรดเดอร์ที่ทำเองตอนนั้นกำลังมองหน้าจออยู่แล้ว

Channel access token เป็น credential จึงอยู่ใน OS keyring ผ่าน
`integrations/keyring/line_notifications.py` เท่านั้น ห้ามลง SQLite, log หรือ fixture
`line_notification_composition.py` เป็น seam เดียวที่ประกอบ keyring adapter เข้ากับ
transport โดย `desktop_main` และ `ui` รับเฉพาะ callable ที่ประกอบไว้แล้ว จึงไม่ import
keyring หรือ transport เอง และหน้าจอ Settings รับรู้ได้แค่ว่า "มี token เก็บไว้หรือไม่"
ไม่เคยอ่านค่า token กลับมาแสดง log ของ `integrations/line` บอกได้แค่ชนิด notice กับ symbol
ไม่เคยบอกตัวเลขผลกำไรขาดทุน

Session อ่านค่าตั้งค่าตอน Runtime เริ่มทำงาน การแก้ค่าจึงมีผลกับการ Start ครั้งถัดไป

## Backfilled Candle

`market_data` เป็นผู้รู้ว่า candle ใดมาสดและ candle ใดถูกดึงย้อนหลังหลังการเชื่อมต่อขาด จึงส่ง
ข้อเท็จจริงนี้ลงมาทาง `MarketDataCandleSink.process_completed(..., backfilled=...)` แต่
**ไม่ตัดสินใจแทน** — กฎที่ว่า candle ย้อนหลังห้ามเปิด Entry เป็น business rule ที่ Session
(`PaperSpotSession` / `PaperFuturesSession`) เป็นผู้บังคับ

Session ที่ได้รับ candle ย้อนหลังจะคืน Entry Intent ที่ค้างอยู่ให้ Strategy แทนการ Fill แต่ยัง
ประมวลผลทุกอย่างที่เหลือตามปกติ ทั้ง indicator, Take Profit และ Stop Loss
รายละเอียดกฎอยู่ใน PRODUCT.md ข้อ 8.1

## Cooldown Waiver

`trading` เป็นเจ้าของกลไก waive Cooldown แบบ pure ใน `EntryPairLifecycle`
(`waive_cooldown`/`restore_waiver`) โดยไม่รู้จัก persistence ผู้ใช้ waive ได้ครั้งเดียวต่อ
Cooldown หนึ่งรอบ ผ่าน use case ที่ `application` เป็นเจ้าของ ซึ่งเดินตาม pattern
เดียวกับ manual Close Entry คือถามที่ Runtime ก่อนถ้ากำลังถือ Session นั้นอยู่ ไม่งั้น fallback
ไปที่ database ตรงๆ waiver ถูก persist คู่กับ Fills ของ Basket ใน `integrations/sqlite`
และถูก apply กลับเข้า `EntryPairLifecycle` ทุกครั้งที่ Basket replay ผ่าน `reopen_basket`
เพื่อให้อยู่รอด restart

การจบ Cooldown เพราะ Pair ล่าสุดถูกปิดไปหนึ่ง Entry เป็นคนละเรื่องกับ waiver และไม่ถูก
persist: `EntryPairLifecycle.release_fill` รับเลข Entry ที่ขายเข้ามาเพื่อรู้ว่าเป็น Pair ไหน
ส่วน `replayed` อ่านข้อเท็จจริงเดียวกันนี้กลับจากเลข Entry ที่ยังเปิดค้าง จึงไม่มี schema หรือ
migration เพิ่ม — waiver คือคำสั่งของผู้ใช้ที่ไม่มี Fill ใดบันทึกไว้ จึงต้องเก็บ ส่วนการที่ Pair
ไม่ครบแล้วเป็นสิ่งที่ Entries ที่เหลือบอกได้เอง

Paper Spot และ Paper Futures ต่าง waive Cooldown ได้ทั้งคู่ผ่าน coordinator เดียวกัน แต่
ต่างกันที่ persistence: Paper Spot persist waiver ลง SQLite เพราะ replay Basket ที่เปิดอยู่
กลับมาได้ ส่วน Paper Futures ไม่ persist เพราะไม่เคยรับ Basket ที่เปิดค้างกลับมาเลย waiver
ของ Futures จึงมีอายุเท่ากับ Runtime ที่ถืออยู่ และมีเฉพาะเส้นทาง runtime เท่านั้น ไม่มี
at-rest path
