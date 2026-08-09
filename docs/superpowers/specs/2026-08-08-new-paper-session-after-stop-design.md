# Single Start/Stop Paper Bot Flow

## Goal

ให้ผู้ใช้เปลี่ยน Paper configuration เช่น timeframe จาก `5m` เป็น `3m` ได้ด้วย flow
เดียว: กด Stop เพื่อหยุด runtime แล้วแก้ฟอร์มและกด `Start Paper Bot` เพื่อ replace
Session เดิมอย่างปลอดภัย

## Product and Domain Constraints

- `SessionConfig` และ timeframe immutable; การเปลี่ยนค่าต้องสร้าง Session ใหม่
- หนึ่ง installation มี Active Bot Session ได้สูงสุดหนึ่งรายการ
- ใช้ Paper Trading เท่านั้น ไม่มี Live order, credentials หรือ behavior ใหม่
- Stop เป็น graceful stop: หยุด Entry ใหม่ แต่ไม่ปิด Basket เอง
- Trade History เป็น owner ของข้อมูลย้อนหลังและต้องอ่าน Session เก่าได้

## UX

UI มี action หลักเพียง `Start Paper Bot` ใน Session Setup และ `Stop Bot` ระหว่าง runtime
ทำงาน ไม่มี `Recover` หรือ `New Session`.

- Stop สำเร็จหรือ runtime `Blocked`: แสดงฟอร์มเดิมพร้อมค่าล่าสุดและแก้ไขได้ทันที แม้มี
  Position ค้าง
- Start จากฟอร์มที่มี Session เดิม: validate ก่อน side effect ทุกครั้ง
- หากมี Position ค้าง: แสดง confirmation เพียงครั้งเดียว; Cancel ไม่เปลี่ยน Session
- Confirm หรือกรณีไม่มี Position: หยุด worker เดิม, manual-close Paper Position ด้วยราคา
  สด, retire Session เดิม, สร้าง immutable Session ใหม่ แล้วเริ่ม Bot อัตโนมัติ
- หากขั้นตอนใดล้มเหลว: ห้ามสร้าง Session ใหม่และคงฟอร์มเพื่อให้ผู้ใช้แก้ไขหรือกด Start ซ้ำ

## Architecture

`CreatePaperSession` แยก validation ที่ไม่มี side effect ออกมาให้ orchestration เรียกก่อน
confirmation. MainWindow เป็น UI orchestration owner ของ replacement flow และรัน stop,
close และ retire ผ่าน background worker.

`ClosePaperPosition` ใช้ public market price ที่อยู่ใน `LatestMarketPrice`; ต้อง fresh ตาม
bound ก่อนสร้าง `manual_close` fill พร้อม fee/slippage และปิด Basket ใน transaction เดียว.
หลัง close สำเร็จเท่านั้นจึง retire Active Session ได้.

`PaperRuntimeController` ยังคงมี recovery ภายในที่จำเป็นต่อ shutdown safety แต่ UI lifecycle
ไม่ expose recovery/new-session action. หาก worker ที่ Blocked ยังหยุดไม่ได้ replacement flow
ต้องหยุดทันทีโดยไม่ close, retire หรือสร้าง runtime ตัวที่สอง.

## Data Flow

```text
Stop / Blocked
  -> editable Session Setup
  -> Start Paper Bot validation
  -> optional close-position confirmation
  -> stop owned runtime
  -> fresh-price manual close (if exposure)
  -> retire active Session
  -> CreatePaperSession(new immutable configuration)
  -> Start Paper Runtime
```

## Failure Handling

- validation failure เกิดก่อน confirmation, close หรือ retire
- stale price, close failure, retire failure หรือ start failure ไม่สร้าง Session ใหม่
- stop timeout หรือ Blocked worker ที่ยัง active ไม่อนุญาต close/retire/create
- persistence transaction rollback ทำให้ Active Session เดิมยัง load ได้
- close window ระหว่าง background task ให้ durable storage เป็น source of truth และ discard
  stale Qt callback

## Verification

1. UI tests ยืนยันว่าไม่มี Recover/New Session และ Stop กลับสู่ฟอร์มแม้มี Position
2. workflow tests ครอบคลุม validation ก่อน confirmation, cancel, close/retire/create/start
   ordering และ retry after failure
3. paper manual-close tests ครอบคลุม fresh price, fee/slippage และ audit fill
4. `pytest`, Ruff, format, mypy และ `git diff --check` ต้องผ่าน

## Out of Scope

- เปลี่ยน configuration ภายใน Session เดิม
- ลบ Session, Basket, Fill หรือ Trade History
- สร้างหลาย Active Sessions พร้อมกัน
- Live Trading หรือ Live execution behavior
