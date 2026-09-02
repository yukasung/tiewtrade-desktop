# Linux Package ผ่าน Docker Build Container

วันที่: 2026-09-03

ต่อจาก [`packaging/tiewtrade.spec`](../../../packaging/tiewtrade.spec) ที่รองรับ macOS และ
Windows อยู่แล้ว คู่มือการใช้งานอยู่ที่ [`docs/linux-build.md`](../../linux-build.md)
เอกสารนี้บันทึกเหตุผลของการตัดสินใจและข้อจำกัดที่พบจริงระหว่างทำ

## ปัญหา

ต้องได้ Linux package สำหรับ Ubuntu Desktop โดยยังพัฒนาบน macOS ตามเดิม PyInstaller
ข้ามระบบปฏิบัติการไม่ได้ — Linux executable ต้อง build บน Linux ที่ลิงก์กับ Qt ของ Linux
จึงต้องมี Linux ให้ build สักที่หนึ่ง โดยไม่แตะ Architecture หรือ Business Logic ของบอต

## สิ่งที่ตรวจพบก่อนลงมือ

| หัวข้อ | ผลตรวจ |
| --- | --- |
| Python | 3.12 (`requires-python >= 3.12`, ruff/mypy target `py312`) |
| Qt binding | PySide6 `>=6.10,<7` |
| Entry point | `packaging/entry_point.py` → `run_desktop()` ใน `src/tiewtrade/desktop_main.py` (ไม่ใช่ `main.py`) |
| Dependency config | `pyproject.toml` (hatchling) — ไม่มี `requirements.txt`/`poetry.lock` |
| Assets | ไม่มี icon/font/JSON/resource ใด ๆ ที่ต้อง bundle |
| `.env` | ไม่มีในโครงการ credential อยู่ใน OS keyring |
| SQLite | ไม่ถูก bundle อยู่แล้ว แอปสร้างเองตอนรันครั้งแรก |
| PyInstaller spec | มีอยู่แล้ว รองรับ macOS + Windows จาก spec เดียว |

จุดเดียวที่เป็นปัญหาข้ามระบบปฏิบัติการจริงคือ Path ของข้อมูล Runtime — ดูหัวข้อถัดไป

## การตัดสินใจ

### 1. Container ทำหน้าที่ build อย่างเดียว

`Dockerfile` ที่ root อธิบาย Ubuntu 22.04 ที่มี Python 3.12 (deadsnakes), Qt system
library และ Dependencies ของโครงการ Source Code ยังอยู่บน macOS คอนเทนเนอร์ได้สำเนา
ไปแล้วส่งกลับมาเป็น `dist-linux/TiewTrade/` เท่านั้น จากนั้นถูกทิ้ง

เลือก **22.04 ไม่ใช่ 24.04** เพราะ binary ผูกกับ glibc ที่ build มา และรันได้บนรุ่นนั้น
ขึ้นไป — build บน 22.04 (glibc 2.35) จึงเปิดได้ทั้งบน 22.04 และ 24.04 ส่วนการ build บน
24.04 จะรันบน 22.04 ไม่ได้

Layer ที่ติดตั้ง Dependencies อ่านรายการจาก `pyproject.toml` โดยตรง ไม่มีสำเนารายการ
Dependency ชุดที่สองในโครงการที่จะ Drift ได้ และ Layer นั้นถูกสร้างใหม่ก็ต่อเมื่อ
`pyproject.toml` เปลี่ยน การแก้โค้ดจึงไม่ต้องติดตั้ง PySide6 ใหม่

### 2. spec เดียว สามระบบปฏิบัติการ

ไม่สร้าง spec ใหม่ ขยาย `packaging/tiewtrade.spec` เดิมแทน สองจุด:

- `BUNDLE` เป็นเรื่องของ macOS อยู่แล้ว (`if IS_MACOS:`) บน Linux โฟลเดอร์ที่ `COLLECT`
  สร้างคือตัวโปรแกรมทั้งหมด เหมือน Windows — ไม่ต้องแก้อะไร
- **Hidden import ของ keyring** เปลี่ยนจากรายการเดียวที่ใส่ทุก Platform เป็นเลือกตาม
  `sys.platform`: macOS → `keyring.backends.macOS`, Windows → `.Windows`, Linux →
  `.SecretService` และ `.libsecret` keyring หา Backend ผ่าน Entry Point ซึ่ง PyInstaller
  ตามไม่ได้ ถ้าไม่ระบุ Backend จะหายไปตอนรัน แอปอ่านค่าที่เก็บไว้เป็น "ยังไม่ตั้งค่า" โดย
  ไม่ Raise การแจ้งเตือน LINE จะดูเหมือนถูกปิดไว้ตลอด

  ที่แยกตาม Platform เพราะ Backend ของอีกสองระบบไม่ได้ถูกติดตั้งบนเครื่องที่กำลัง build
  การสั่ง PyInstaller ให้หา Module ที่ไม่มีอยู่คือ Warning ทุกครั้งที่ build โดยไม่ได้อะไร
  (ผลข้างเคียง: macOS build ตอนนี้ไม่มี Warning เรื่องนี้แล้ว)

  ไม่มีการเพิ่ม hidden import แบบเดา — ทุกตัวมาจาก Backend ที่ keyring มีจริงบน Linux

### 3. Runtime Data — บั๊กจริงที่โผล่จากการทำ Linux

`_application_directory()` เดิมเขียนว่า "ถ้าไม่ใช่ Windows ให้ใช้
`~/Library/Application Support`" ซึ่งถูกเฉพาะบน macOS บน Linux แอปจะสร้างโฟลเดอร์แบบ
macOS ไว้ในบ้านผู้ใช้ — เป็นที่ที่ไม่มีเครื่องมือสำรองข้อมูลตัวไหนของ Linux มอง

แก้เป็นตอบแยกสามระบบ Linux ใช้ `XDG_DATA_HOME` ตามสเปก XDG (default `~/.local/share`,
และค่าที่ไม่ใช่ Absolute Path ต้องถูกมองข้ามตามสเปก) ทำแบบ TDD: เขียน Test ที่ล้มก่อน
แล้วจึงแก้

การแก้นี้ทำให้ Test เดิมตัวหนึ่งล้มบน Linux —
`test_desktop_composition_defers_database_directory_creation_to_load_worker` ผูกกับ Path
ของ macOS ไว้ตรง ๆ (เดิมมัน "ผ่าน" บน Linux เพราะโค้ดตอบผิดแบบเดียวกัน) แก้ให้ถาม
Platform ผ่าน `default_database_path()` แทน สิ่งที่ Test นั้นตรวจคือ "ไม่มีโฟลเดอร์อื่น
ถูกสร้างนอกจากโฟลเดอร์เดียวนั้น" ซึ่งไม่เปลี่ยน

**ไม่มีการแก้ Trading Strategy, Order Execution, Risk Management หรือ Business Logic ใด ๆ**
สิ่งเดียวที่แตะใน `src/` คือ Path ของข้อมูล Runtime ข้างต้น

### 4. คอนเทนเนอร์รันชุดตรวจก่อน build

`packaging/build_linux.sh` เป็นคู่ขนานของ `packaging/build_windows.ps1` และ Target
`build` ใน Makefile — pytest, ruff, mypy, deterministic replay แล้วค่อย PyInstaller
ด้วยเหตุผลเดิม: build ที่สร้างจากโค้ดที่ไม่ผ่านคือ build ที่ไม่มีใครเชื่อถือได้
`scripts/build-linux.sh` เป็นตัวขับจากฝั่ง macOS (build image + run) เพื่อให้เหลือคำสั่งเดียว

### 5. บังคับ Architecture ไว้ที่ `linux/amd64`

Mac ที่เป็น Apple Silicon จะ build Image เป็น arm64 ถ้าไม่บอก และ arm64 executable บน
Ubuntu x86_64 จะขึ้น `cannot execute binary file` ซึ่งดูเหมือนไฟล์เสียทั้งที่เป็นเรื่อง
Architecture ล้วน ๆ สคริปต์จึงระบุ `--platform` เสมอ และตอนจบ build เรียก `readelf`
พิมพ์ Class/Machine ออกมาให้เห็น

## สิ่งที่ตรวจสอบจริงแล้ว

1. `docker build` ผ่าน (linux/amd64)
2. PyInstaller ผ่าน ได้ `dist-linux/TiewTrade/` (executable + `_internal/`, ~182 MB)
3. `readelf` ยืนยัน **ELF64 / x86-64**
4. นำ package ไปรันใน `ubuntu:22.04` ที่สะอาด โดยติดตั้งเฉพาะ Library ที่เอกสารบอกให้
   ติดตั้ง: `libqxcb.so` resolve ครบทุกตัว, แอปรันได้ 40 วินาทีจนถูก Timeout ตัด (ไม่ Crash)
   และสร้าง `~/.local/share/TiewTrade/{tiewtrade.sqlite3,.lock,.log}` จริง — ยืนยันว่า
   การแก้ Path ทำงานในไฟล์ที่ build ออกมา ไม่ใช่แค่ใน Test
5. ฝั่ง macOS: 1698 tests, ruff check/format, mypy, `git diff --check` และ replay
   (`realized_pnl: 23.679`) ผ่านครบ
6. `dist/TiewTrade.app` ยัง build ได้จาก spec ที่แก้แล้ว โดยไม่มี Warning เรื่อง hidden import

รอบแรกของข้อ 4 **ล้ม** — เอกสารบอกให้ติดตั้งแค่สามตัว แต่ Ubuntu ที่สะอาดจริงขาด
`libGL`, `libglib`, `libX11`, `libEGL`, `libfontconfig`, `libfreetype`, `libdbus` ด้วย
จึงขยายรายการในเอกสารเป็นชุดเต็มแล้วทดสอบซ้ำจนผ่าน

## ข้อจำกัดที่รู้อยู่

### Qt Widget Test ไม่ถูกรันในคอนเทนเนอร์บน Apple Silicon

Event Loop ของ Qt **Segfault** ใต้ Emulation x86_64 ของ Apple Silicon จุดที่ Crash อยู่ใน
`tests/unit/ui` (`table_empty_state.eventFilter` ระหว่าง `qtbot` exec) ตรวจแล้วว่า:

- เกิดซ้ำได้ทั้งบน Ubuntu 22.04 และ 24.04 → **ไม่ใช่เรื่องรุ่นของ Base Image**
- Test ชุดเดียวกันผ่านบน macOS (`make verify`) และผ่านบน Linux x86_64 จริงใน CI
  → **ไม่ใช่เรื่องโค้ด** ตัวแปรร่วมคือ Emulator

`packaging/build_linux.sh` จึงใส่ `--ignore=tests/unit/ui` พร้อมเหตุผลกำกับ และเปิดคืนได้
ด้วย `TIEWTRADE_INCLUDE_UI_TESTS=1` เมื่อ build บนเครื่อง Linux x86_64 จริงที่ไม่มี
Emulation สคริปต์พิมพ์บอกทุกครั้งที่ข้าม

**นี่คือการลดด่านตรวจของ Linux build ลงจาก `make verify`** ส่วนนั้นยังพึ่ง macOS และ CI
เป็นด่านหลัก ถ้ารับไม่ได้ ทางออกคือ build บน Linux x86_64 จริง (เช่น CI runner) แทน

### Ubuntu arm64 build ไม่ได้

PySide6 ตั้งแต่ 6.10 ไม่ปล่อย Wheel สำหรับ Linux aarch64 เลย รุ่นสุดท้ายที่มีคือ 6.8.0.2
การสั่ง `TIEWTRADE_LINUX_PLATFORM=linux/arm64` จะหยุดที่ขั้นติดตั้ง Dependencies ด้วย
`No matching distribution found for PySide6` การรองรับ arm64 ต้องลดเพดาน PySide6 ก่อน
ซึ่งเป็นการตัดสินใจเรื่อง Dependency ของโครงการ ไม่ใช่เรื่อง Packaging — จึงไม่ทำในงานนี้
แต่คงตัวแปรไว้ เพราะเป็นข้อจำกัดของเวอร์ชัน ไม่ใช่ของสคริปต์

### อื่น ๆ

- build บน Apple Silicon ช้ามาก (~5–10 นาที) เพราะรันผ่าน Emulation
- **ยังไม่ได้เปิดบน Ubuntu Desktop ที่มีจอจริง** ที่ยืนยันแล้วคือรันด้วย Platform
  `offscreen` ในคอนเทนเนอร์ — เส้นทาง `xcb` จริงตรวจได้แค่ระดับ "Library resolve ครบ"
- ไม่มี `.deb`, AppImage หรือ `.desktop` launcher ผลลัพธ์เป็นโฟลเดอร์ที่รันด้วยมือ
- Executable ไม่ได้เซ็น (Linux ไม่บังคับเหมือน macOS/Windows แต่ก็ไม่มีอะไรยืนยันที่มา)
- ใช้ `--onedir` ตามที่กำหนด ไม่ใช้ `--onefile`
- Ubuntu ต้องมี Secret Service (gnome-keyring/KWallet) ไม่งั้น Token ของ LINE อ่านเป็น
  "ยังไม่ตั้งค่า" — เป็นพฤติกรรมเดิมของแอปเมื่อ keyring ไม่ตอบ ไม่ใช่ของใหม่

## ไฟล์ที่เกี่ยวข้อง

สร้าง: `Dockerfile`, `.dockerignore`, `packaging/build_linux.sh`, `scripts/build-linux.sh`,
`docs/linux-build.md`, `tests/unit/test_linux_build.py`

แก้: `packaging/tiewtrade.spec`, `src/tiewtrade/desktop_main.py`, `Makefile`, `README.md`,
`.gitignore`, `tests/unit/test_desktop_main.py`

`tests/unit/test_linux_build.py` ยึดสามไฟล์นี้ไว้ด้วยกันแบบเดียวกับที่
`tests/unit/test_windows_build.py` ทำกับฝั่ง Windows — spec, สคริปต์ในคอนเทนเนอร์,
สคริปต์ฝั่ง macOS และเอกสาร ต้องยังพูดตรงกันเมื่อมีใครแก้ตัวใดตัวหนึ่ง
