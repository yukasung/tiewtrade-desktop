# Make Run Command Design

## เป้าหมาย

ทำให้การเปิด Desktop application จาก root ของโครงการสั้นและจำง่ายด้วยคำสั่ง `make run` โดยคง runtime command เดิมไว้ทั้งหมด

## ขอบเขต

- เพิ่ม `Makefile` ที่ root ของโครงการ
- เพิ่ม target `run` เพียง target เดียว
- `run` ต้องเรียก `PYTHONPATH=src .venv/bin/python -m tiewtrade.desktop_main`
- เพิ่มวิธีใช้งานใน `README.md`

## การออกแบบ

`Makefile` เป็น convenience wrapper ไม่ใช่ package entry point และไม่เปลี่ยน dependency, virtual environment หรือ import behavior ของโปรเจกต์ ผู้ใช้ต้องรัน `make run` จาก root หลังทำขั้นตอนติดตั้งตาม README แล้ว

README เพิ่มหัวข้อ `Run Desktop App` หลังส่วนติดตั้ง พร้อมคำสั่ง `make run` และระบุว่าใช้ `.venv` ที่สร้างไว้ในโครงการ ไม่มีการเพิ่ม target สำหรับ test, lint หรือ workflow อื่นในรอบนี้

## การทดสอบ

- ใช้ `make -n run` ตรวจว่าขยายเป็น runtime command ที่ถูกต้องโดยไม่เปิด GUI
- ตรวจ README ว่ามีคำสั่ง `make run`
- รัน unit/integration tests, Ruff, format, mypy และ `git diff --check`

## ข้อยกเว้น

งานนี้ไม่เปลี่ยน Python packaging, desktop runtime, UI, market data หรือ trading behavior
