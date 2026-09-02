# Linux Build

สร้าง Linux package ของ TiewTrade จากเครื่อง macOS โดยใช้ Docker เป็น Linux build
environment เท่านั้น — Source Code ยังเขียนและรันบน macOS ตามปกติ

PyInstaller ข้ามระบบปฏิบัติการไม่ได้ Linux executable จึงต้อง build บน Linux ที่ลิงก์กับ
Qt ของ Linux คอนเทนเนอร์ทำหน้าที่นั้นอย่างเดียว ใช้ `packaging/tiewtrade.spec` ไฟล์เดียว
กับ macOS และ Windows แล้วส่งผลลัพธ์กลับมาที่ `dist-linux/` จากนั้นก็ถูกทิ้ง

เหตุผลเบื้องหลังการตัดสินใจแต่ละข้อ สิ่งที่ตรวจสอบไปแล้ว และข้อจำกัดทั้งหมด อยู่ใน
[superpowers/specs/2026-09-03-linux-build-container-design.md](superpowers/specs/2026-09-03-linux-build-container-design.md)

## 1. สิ่งที่ต้องมีบน macOS

- Docker Desktop ติดตั้งและกำลังรันอยู่
- Checkout ของโครงการนี้

ไม่ต้องติดตั้ง Python, Qt หรือ PyInstaller เพิ่มสำหรับการ build นี้ ทุกอย่างอยู่ใน Image

## 2. วิธี Build

จาก root ของโครงการ:

```bash
./scripts/build-linux.sh
```

สคริปต์จะ build Image แล้วรัน PyInstaller ให้ ครั้งแรกใช้เวลาหลายนาที (ต้องดาวน์โหลด
Ubuntu, Python 3.12 และ PySide6) ครั้งต่อไปเร็วขึ้นเพราะ Docker cache ชั้นที่ติดตั้ง
Dependencies ไว้ — ชั้นนั้นจะถูกสร้างใหม่ก็ต่อเมื่อ `pyproject.toml` เปลี่ยน

ก่อน build จริง คอนเทนเนอร์รันชุดตรวจเดียวกับ `make verify` (pytest, ruff, mypy,
deterministic replay) และหยุดถ้าไม่ผ่าน ด้วยเหตุผลเดียวกับ `make build` ข้ามได้เมื่อเพิ่ง
ตรวจไปแล้วใน checkout เดียวกัน:

```bash
./scripts/build-linux.sh --skip-verify
```

ยกเว้นอย่างเดียวคือ Test ของ Qt Widget (`tests/unit/ui`) ซึ่งไม่ถูกรันในคอนเทนเนอร์ บน
Mac ที่เป็น Apple Silicon คอนเทนเนอร์นี้คือ x86_64 แบบ Emulate และ Event Loop ของ Qt
Segfault ภายใต้ Emulation นั้น (เกิดซ้ำได้ทั้งบน Ubuntu 22.04 และ 24.04) ขณะที่ Test ชุด
เดียวกันผ่านบน macOS ด้วย `make verify` และผ่านบน Linux จริงใน CI — เป็นข้อจำกัดของ
Emulator ไม่ใช่ของโค้ด ถ้า build บนเครื่อง Linux x86_64 จริงซึ่งไม่มี Emulation:

```bash
TIEWTRADE_INCLUDE_UI_TESTS=1 ./scripts/build-linux.sh
```

ถ้าต้องการสั่ง Docker เอง สองคำสั่งนี้คือสิ่งที่สคริปต์ทำ:

```bash
docker build --platform linux/amd64 -t tiewtrade-linux-build .
docker run --rm --platform linux/amd64 \
  -v "$(pwd)/dist-linux:/app/dist" \
  tiewtrade-linux-build
```

## 3. ผลลัพธ์อยู่ที่ไหน

```
dist-linux/
└── TiewTrade/
    ├── TiewTrade          <- executable
    └── _internal/         <- Python runtime, Qt libraries, dependencies
```

ต้องส่งทั้งโฟลเดอร์ `dist-linux/TiewTrade/` — ไฟล์ `TiewTrade` ตัวเดียวรันไม่ได้
เครื่องปลายทางไม่ต้องติดตั้ง Python หรือ Qt

## 4. Architecture

Mac ที่เป็น Apple Silicon จะ build Image เป็น arm64 ถ้าไม่บอกเป็นอย่างอื่น และ arm64
executable รันบน Ubuntu x86_64 ไม่ได้ — ขึ้น `cannot execute binary file` ซึ่งดูเหมือน
ไฟล์เสีย ทั้งที่เป็นเรื่อง architecture ล้วน ๆ สคริปต์จึงระบุ `--platform linux/amd64`
ไว้ตายตัว

ตรวจ architecture ของเครื่องปลายทางก่อน:

```bash
uname -m        # x86_64 = amd64 (ค่าเริ่มต้นของสคริปต์), aarch64 = arm64
```

**ปัจจุบัน build ได้เฉพาะ amd64** — PySide6 ตั้งแต่ 6.10 (รุ่นที่ `pyproject.toml` ระบุ)
ไม่ปล่อย Wheel สำหรับ Linux aarch64 เลย รุ่นสุดท้ายที่มีคือ 6.8.0.2 การสั่ง
`TIEWTRADE_LINUX_PLATFORM=linux/arm64` จึงหยุดตั้งแต่ตอนติดตั้ง Dependencies ด้วย
`No matching distribution found for PySide6` การรองรับ Ubuntu บน arm64 ต้องลดเพดาน
PySide6 ลงก่อน ซึ่งเป็นการตัดสินใจเรื่อง Dependency ของโครงการ ไม่ใช่เรื่อง Packaging

ตรวจไฟล์ที่ได้:

```bash
file dist-linux/TiewTrade/TiewTrade
```

## 5. นำไปรันบน Ubuntu

Copy ไปเครื่องปลายทาง:

```bash
tar -czf tiewtrade-linux.tar.gz -C dist-linux TiewTrade
scp tiewtrade-linux.tar.gz user@ubuntu-host:~
```

บน Ubuntu:

```bash
tar -xzf tiewtrade-linux.tar.gz
chmod +x TiewTrade/TiewTrade      # ถ้า permission หายระหว่าง copy
./TiewTrade/TiewTrade
```

ต้องรันบน Ubuntu **Desktop** ที่มี GUI session (X11 หรือ Wayland) — TiewTrade เป็นแอป
หน้าต่าง ไม่มีโหมด Headless ผ่าน SSH ธรรมดารันไม่ได้ (ยกเว้นใช้ X11 forwarding ด้วย
`ssh -X`)

### Dependencies ที่ Ubuntu ต้องมี

PySide6 มี Qt มาให้ในตัว แต่ Qt ยังต้องการ System Library ของระบบอีกชุดหนึ่ง Ubuntu
Desktop ปกติมีเกือบครบอยู่แล้ว ที่ขาดบ่อยคือ `libxcb-cursor0` (Qt 6 ต้องใช้ แต่ Ubuntu
ไม่ได้ติดตั้งมาให้เสมอ) และ `libegl1` ติดตั้งทั้งชุดนี้ครั้งเดียวจบ — รายการนี้คือชุดที่
ทดสอบแล้วว่าเพียงพอบน Ubuntu 22.04 ที่ไม่มีอะไรติดตั้งไว้เลย:

```bash
sudo apt-get update
sudo apt-get install -y \
  libgl1 libegl1 libglib2.0-0 libdbus-1-3 libfontconfig1 libfreetype6 \
  libx11-6 libx11-xcb1 libxkbcommon0 libxkbcommon-x11-0 \
  libxcb1 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
  libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-shm0 \
  libxcb-sync1 libxcb-xfixes0 libxcb-xinerama0
```

ตรวจว่ามีอะไรขาดจริงบ้างได้จาก Plugin ตัวที่ Qt ต้องโหลด:

```bash
ldd TiewTrade/_internal/PySide6/Qt/plugins/platforms/libqxcb.so | grep "not found"
```

### ข้อมูลที่แอปเขียนตอนรัน

ไม่มีฐานข้อมูลถูกรวมมากับ package แอปสร้าง Schema ของตัวเองเมื่อเปิดครั้งแรกที่:

```
${XDG_DATA_HOME:-~/.local/share}/TiewTrade/
├── tiewtrade.sqlite3
└── tiewtrade.log
```

ประวัติการเทรดจึงอยู่รอดข้ามการอัปเดตที่เขียนทับโฟลเดอร์ `TiewTrade` และไม่มีอะไรถูก
เขียนลงในโฟลเดอร์ของ executable

Channel Access Token ของ LINE ไม่ได้อยู่ในไฟล์เหล่านั้น — มันอยู่ใน Secret Service ของ
เครื่อง (gnome-keyring หรือ KWallet) บน Ubuntu Desktop มาตรฐานมีอยู่แล้ว ถ้าไม่มี แอป
จะอ่านค่าที่เก็บไว้เป็น "ยังไม่ตั้งค่า" และการแจ้งเตือน LINE จะดูเหมือนถูกปิดไว้ตลอด:

```bash
sudo apt-get install -y gnome-keyring
```

## 6. Troubleshoot

**`qt.qpa.plugin: Could not load the Qt platform plugin "xcb"`**

ขาด System Library ของ Qt ติดตั้งชุดในหัวข้อข้างบน แล้วดูรายการที่ขาดจริงด้วย:

```bash
QT_DEBUG_PLUGINS=1 ./TiewTrade/TiewTrade
```

บรรทัด `Cannot load library ...` จะบอกชื่อไฟล์ `.so` ที่หาไม่เจอ

**`cannot execute binary file: Exec format error`**

Architecture ไม่ตรง เทียบ `uname -m` บนเครื่องปลายทางกับ `file TiewTrade/TiewTrade`
แล้ว build ใหม่ตามหัวข้อ Architecture

**`version 'GLIBC_2.xx' not found`**

Ubuntu ปลายทางเก่ากว่าที่ Image ใช้ build Image นี้ใช้ Ubuntu 22.04 (glibc 2.35)
ซึ่งรันได้บน 22.04 ขึ้นไป ถ้าต้องรองรับเก่ากว่านั้นต้องเปลี่ยน `FROM` ใน `Dockerfile`
เป็นรุ่นที่เก่ากว่า

**`Permission denied`**

```bash
chmod +x TiewTrade/TiewTrade
```

**รันผ่าน SSH แล้วขึ้น `could not connect to display`**

ไม่มี GUI session ต้องเปิดบนหน้าจอของเครื่อง Ubuntu เอง หรือใช้ `ssh -X`

## 7. ข้อจำกัดที่ควรรู้

- Executable ไม่ได้เซ็น — Linux ไม่บังคับเรื่องนี้เหมือน macOS/Windows จึงไม่มีคำเตือน
  ให้กดผ่าน แต่ก็ไม่มีอะไรยืนยันที่มาของไฟล์เช่นกัน
- ไม่มี `.deb`/AppImage/`.desktop` launcher — ผลลัพธ์เป็นโฟลเดอร์ที่รันด้วยมือ
- Build แบบ `--onedir` โดยตั้งใจ (ไม่ใช่ `--onefile`) — เปิดเร็วกว่า และเวลามีปัญหา
  ยังเห็นไฟล์ที่ถูกรวมมาจริง ๆ ได้
- build บน Apple Silicon สำหรับ `linux/amd64` รันผ่าน Emulation จึงช้ากว่าปกติมาก
  โดยเฉพาะช่วงรัน Test
- Test ของ Qt Widget ไม่ถูกรันในคอนเทนเนอร์บน Apple Silicon (ดูหัวข้อ "วิธี Build")
  ยังต้องพึ่ง `make verify` บน macOS และ CI เป็นด่านหลักของส่วนนั้น
- ยังไม่มี Ubuntu arm64 — PySide6 6.10 ไม่มี Wheel สำหรับ Linux aarch64
- Package ที่ได้ยังไม่ได้ถูกรันทดสอบบน Ubuntu Desktop จริง สิ่งที่ยืนยันแล้วคือ build
  ผ่านและได้ ELF x86-64 ออกมา
