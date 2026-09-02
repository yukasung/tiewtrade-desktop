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
| `make build` | สร้าง `dist/TiewTrade.app` (macOS) |
| `packaging/build_windows.ps1` | สร้าง `dist\TiewTrade\TiewTrade.exe` (รันบน Windows) |
| `./scripts/build-linux.sh` | สร้าง `dist-linux/TiewTrade/` (build ผ่าน Docker บน macOS ได้) |

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

PyInstaller ข้ามระบบปฏิบัติการไม่ได้ — macOS bundle ต้อง build บน macOS, Windows
executable ต้อง build บนเครื่อง Windows และ Linux executable ต้อง build บน Linux
ทั้งสามอ่าน `packaging/tiewtrade.spec` ไฟล์เดียวกัน

### macOS

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

### Signing และ Notarization (macOS)

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


### Windows

ต้องทำบนเครื่อง Windows ที่มี Python 3.12 ติดตั้งไว้ ตั้งสภาพแวดล้อมครั้งเดียวจาก root
ของโครงการ:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

จากนั้นสั่ง build:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
```

สคริปต์รันชุดตรวจเดียวกับ `make verify` ให้ก่อนและหยุดถ้าไม่ผ่าน ด้วยเหตุผลเดียวกับ
`make build` — ข้ามได้ด้วย `-SkipVerify` เมื่อเพิ่งตรวจไปแล้วใน checkout เดียวกัน

ผลลัพธ์เป็นโฟลเดอร์ `dist\TiewTrade\` ที่มี `dist\TiewTrade\TiewTrade.exe` อยู่ข้างใน
พร้อมไฟล์ที่แอปต้องใช้ตอนรัน ส่งต่อทั้งโฟลเดอร์ (zip ได้) — `.exe` ตัวเดียวรันไม่ได้
เครื่องปลายทางไม่ต้องติดตั้ง Python หรือ Qt

ฐานข้อมูลไม่ได้ถูกรวมไปด้วยเช่นเดียวกับบน macOS แอปสร้างของตัวเองเมื่อเปิดครั้งแรกที่
`%LOCALAPPDATA%\TiewTrade\` ซึ่งเป็นที่ที่ Windows เก็บสถานะของแอป — ประวัติการเทรด
จึงอยู่รอดข้ามการอัปเดตที่เขียนทับโฟลเดอร์ `TiewTrade`

โครงการนี้ยังไม่มี Code Signing Certificate ของ Windows ดังนั้น SmartScreen จะเตือน
เมื่อเปิดครั้งแรก ผู้ใช้ต้องเลือก More info แล้ว Run anyway หนึ่งครั้ง

### Linux

build จาก macOS ได้ โดยใช้ Docker เป็น Linux build environment (ต้องเปิด Docker
Desktop ไว้):

```bash
./scripts/build-linux.sh
```

สคริปต์รันชุดตรวจเดียวกับ `make verify` ในคอนเทนเนอร์ให้ก่อน ข้ามได้ด้วย `--skip-verify`
ผลลัพธ์เป็นโฟลเดอร์ `dist-linux/TiewTrade/` ที่มี `dist-linux/TiewTrade/TiewTrade` อยู่
ข้างใน ส่งต่อทั้งโฟลเดอร์เช่นเดียวกับ Windows

Image ใช้ Ubuntu 22.04 และ build เป็น `linux/amd64` เป็นค่าเริ่มต้น — Apple Silicon
build เป็น arm64 ถ้าไม่ระบุ ซึ่งรันบน Ubuntu x86_64 ไม่ได้

ฐานข้อมูลไม่ได้ถูกรวมไปด้วย แอปสร้างของตัวเองเมื่อเปิดครั้งแรกที่
`${XDG_DATA_HOME:-~/.local/share}/TiewTrade/`

รายละเอียดทั้งหมด — วิธี copy ไป Ubuntu, Library ที่ต้องติดตั้ง, และวิธีแก้ปัญหา Qt
platform plugin — อยู่ใน [docs/linux-build.md](docs/linux-build.md)
