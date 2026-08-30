# TiewTrade

TiewTrade เป็นบอตซื้อขายระยะ Internal Alpha ที่พัฒนาแบบ Paper-first ปัจจุบัน
โครงการนี้รองรับการ replay Paper Spot แบบกำหนดผลแน่นอนสำหรับ Candle ที่ปิดสมบูรณ์ของ
BTCUSDT ช่วงเวลา 5 นาที

CLI สำหรับ replay ไม่เชื่อมต่อ Binance และไม่ส่งคำสั่ง Live

## คำสั่งที่ใช้บ่อย

| คำสั่ง | ทำอะไร |
| --- | --- |
| `make run-dev` | รันแอปกับฐานข้อมูลสำหรับพัฒนา — ใช้ตัวนี้ระหว่างเขียนโค้ด |
| `make run` | รันแอปกับฐานข้อมูลชุดเดียวกับที่ผู้ใช้จริงเปิด |
| `make verify` | ตรวจทุกอย่างก่อน commit |
| `make build` | สร้าง `dist/TiewTrade.app` |

## การติดตั้ง

สร้างสภาพแวดล้อมเสมือน แล้วติดตั้งโครงการพร้อมส่วนพึ่งพาสำหรับการพัฒนา:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev]"
```

## Run Desktop App

จาก root ของโครงการ หลังติดตั้งตามขั้นตอนข้างต้นแล้ว ให้รัน:

```bash
make run-dev
```

ซึ่งเขียนลง `.local/tiewtrade.sqlite3` ในโครงการ ลบทิ้งเพื่อเริ่มใหม่ได้เสมอ:

```bash
rm -rf .local
```

ส่วน `make run` เปิดฐานข้อมูลชุดเดียวกับที่แอปที่ติดตั้งแล้วเปิด คือ
`~/Library/Application Support/TiewTrade/tiewtrade.sqlite3` — ใช้เมื่อต้องการเห็นสิ่งที่
ผู้ใช้จริงเห็น ไม่ใช่ระหว่างทดลอง เพราะ Session ที่เปิดทิ้งไว้จะกลายเป็นส่วนหนึ่งของ
ประวัติการเทรดจริง

```bash
make run
```

## ทดสอบบน branch ปัจจุบันก่อน merge

```bash
git switch feat/binance-style-trade-history
git branch --show-current
make run-dev
```

## การตรวจซ้ำด้วย fixture

เรียกใช้ deterministic Paper Spot replay จาก root ของโครงการ:

```bash
PYTHONPATH=src .venv/bin/python -m tiewtrade.paper_replay_main \
  tests/fixtures/btcusdt_5m_tracer.csv \
  --capital-per-entry 250 \
  --max-entries 4
```

ผลลัพธ์ JSON ที่คงที่คือ:

```json
{"accepted_candles":40,"closed_baskets":1,"current_entries":0,"realized_pnl":"23.679"}
```

## การตรวจสอบ

```bash
make verify
```

รวมทุกอย่างที่ต้องผ่านก่อน commit ไว้ในคำสั่งเดียว — pytest, ruff check, ruff format,
mypy, `git diff --check` และ replay fixture ข้างต้น หรือจะเรียกแยกทีละอย่างก็ได้:

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests
.venv/bin/python -m ruff format --check src tests
.venv/bin/python -m mypy
git diff --check
```

## Packaging

สร้าง macOS app bundle จาก root ของโครงการ:

```bash
make build
```

`make build` เรียก `make verify` ให้ก่อนเสมอ และหยุดถ้าไม่ผ่าน — bundle ที่สร้างจากโค้ด
ที่ยังไม่ผ่านการตรวจคือ bundle ที่ไม่มีใครเชื่อถือได้ ผลลัพธ์อยู่ที่ `dist/TiewTrade.app`
ส่วน `make clean-build` ลบ `build/` และ `dist/` ทิ้ง

ฐานข้อมูลไม่ได้ถูกรวมเข้า bundle และไม่ควรถูกรวม แอปสร้าง Schema ของตัวเองเมื่อผู้ใช้
เปิดครั้งแรก ที่ `~/Library/Application Support/TiewTrade/` — ซึ่งเป็นเหตุผลที่ประวัติ
การเทรดอยู่รอดข้ามการอัปเดตที่เขียนทับตัว `.app`

ติดตั้งหรือส่งต่อ:

```bash
open dist/TiewTrade.app
cp -R dist/TiewTrade.app /Applications/
ditto -c -k --keepParent dist/TiewTrade.app ~/Desktop/TiewTrade.zip
```

### Signing และ Notarization

`make build` เซ็นแบบ ad-hoc ซึ่งเปิดได้เฉพาะบนเครื่องที่สร้างมัน เครื่องอื่นที่รับไฟล์ไป
ต้องคลิกขวาที่ `TiewTrade.app` แล้วเลือก Open หนึ่งครั้ง หรือ:

```bash
xattr -dr com.apple.quarantine /Applications/TiewTrade.app
```

การเซ็นให้ดับเบิลคลิกเปิดได้ทุกเครื่องต้องใช้ Certificate ชนิด Developer ID Application
ซึ่งมีเฉพาะสมาชิก Apple Developer Program แบบเสียค่าใช้จ่าย บัญชี Apple ID ธรรมดา
ออกได้แค่ Apple Development ซึ่งใช้แจกจ่ายไม่ได้ เมื่อมี Certificate แล้ว:

```bash
make check-signature
make sign-build TIEWTRADE_SIGN_IDENTITY="Developer ID Application: NAME (TEAMID)"

xcrun notarytool store-credentials tiewtrade \
  --apple-id APPLE_ID --team-id TEAMID --password APP_SPECIFIC_PASSWORD
make notarize TIEWTRADE_NOTARY_PROFILE=tiewtrade
```

`sign-build` และ `notarize` ยังไม่เคยถูกรันจริง เพราะยังไม่มี Certificate ในโครงการนี้

