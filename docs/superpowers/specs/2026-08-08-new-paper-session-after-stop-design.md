# New Paper Session After Stop Design

## Goal

ให้ผู้ใช้สร้าง Paper Session ใหม่ด้วย configuration ใหม่ เช่นเปลี่ยน timeframe จาก
`5m` เป็น `3m` ได้หลัง Session เดิมอยู่ในสถานะ `Stopped` หรือ `Blocked` โดยเก็บ
Session, Basket และ Trade Fill เดิมไว้เป็นประวัติ

## Product and Domain Constraints

- `SessionConfig` และ timeframe เป็น immutable ตลอด Session ตาม `PRODUCT.md` และ
  `CONTEXT.md`; การเปลี่ยน configuration ต้องสร้าง Session ใหม่เสมอ
- หนึ่ง installation มี Active Bot Session ได้สูงสุดหนึ่งรายการ
- Paper Trading เท่านั้น; ไม่มี Live API, credentials หรือ order behavior ใหม่
- Trading, strategy, risk, execution, persistence ของ Session เดิมต้องไม่ถูกแก้ไขหรือ
  เริ่มทำงานจาก flow นี้
- Trade History เป็น owner ของข้อมูลย้อนหลังและต้องอ่าน Session เก่าต่อได้

## UX

Bot Control แสดงปุ่ม `New Session` เมื่อ state เป็น `Stopped`. สำหรับ `Blocked` ปุ่มจะ
แสดงและ enabled เฉพาะเมื่อ runtime worker ของ Session เดิมจบแล้ว; หาก worker ยังทำงาน
หรือ Stop timeout ให้ปุ่ม disabled พร้อมข้อความให้ปิดและเปิดแอปใหม่ก่อน. ไม่มีปุ่มนี้ใน
`Configured`, `Starting`, `Running` และ `Stopping`.

เมื่อกดปุ่ม ระบบแสดง confirmation:

> Create a new Paper Session? The current session will remain available in Trade History.

- Cancel: คงหน้าและ active Session เดิมทั้งหมด; ผู้ใช้ยัง Start Session เดิมได้เมื่อ
  lifecycle อนุญาต
- Confirm: เริ่ม replace workflow และ disable action ระหว่างทำงาน
- สำเร็จ: เปิด Session Setup ที่แก้ไข field ได้และไม่มี active Session เดิมใน workspace;
  ผู้ใช้กรอก configuration แล้ว Create Session ใหม่ได้
- ล้มเหลว: คง Bot Control ของ Session เดิม, ไม่มี Setup form ที่นำไปสร้าง Session ซ้ำ,
  และแสดง sanitized error

## Architecture

เพิ่ม `blocked` เป็น durable `PaperRuntimeLifecycleState` และบันทึกเมื่อ Paper Runtime
เปลี่ยนเป็น `Blocked`; schema migration ต้องรองรับ database เดิมที่มี CHECK constraint
เฉพาะ `running`/`stopped`. เพิ่ม focused application use case สำหรับ retire current active
Paper Session แล้วปล่อยให้ Session Setup use case สร้าง Active Session ใหม่ในขั้นตอนถัดไป.
การ retire ตรวจ lifecycle marker ของ Session เดิมภายใน SQLite transaction เดียว: อนุญาต
เฉพาะ `STOPPED` หรือ `BLOCKED`. `PaperRuntimeController` เป็น owner ของ in-process
runtime worker และต้องปฏิเสธ retire หาก worker ของ Session เดิมยังทำงานอยู่. ถ้า state
เปลี่ยน, ไม่มี active session หรือ persistence ล้มเหลว ต้อง rollback ทั้งหมดและไม่ลบ active
record.

SQLite เก็บ Session facts และ Trade History เดิมแบบ immutable; การ retire เปลี่ยนเพียง
ความเป็น active ของ Session ใน registry จึงไม่ลบหรือแก้ Basket/Fill. เพิ่ม
`NewSessionWorkflow` เป็น focused UI workflow แยกจาก `BotLifecycleWorkflow`: มันรัน
retire use case off the Qt thread, invalidate runtime relay/chart state เดิม, แล้ว emit
setup-required เมื่อสำเร็จ. `BotLifecycleWorkflow` จึงยังรับเฉพาะ result ที่มี active
Session และไม่ต้องสร้าง lifecycle snapshot ปลอมหลัง retire. Session ใหม่จะผ่าน
validation และ `CreatePaperSession` เดิม จึงยังบังคับ active Session สูงสุดหนึ่งรายการ.

## Data Flow

```text
Stopped / Blocked Bot Control
  -> New Session confirmation
  -> PaperRuntimeLifecycle durable state is stopped / blocked
  -> RetireActivePaperSession(session_id)
  -> SQLite transaction verifies lifecycle state and removes active registry row
  -> UI invalidates old runtime/chart generation
  -> Session Setup
  -> CreatePaperSession(new immutable configuration)
  -> SQLite active registry contains only new session
```

ไม่มี edge จาก retire workflow ไป MarketDataRuntime, Strategy, Paper execution หรือ
Trade History write path.

## Failure Handling

- confirm ระหว่าง lifecycle state ที่ไม่ใช่ `Stopped`/`Blocked`: reject without mutation
- runtime fail-closed ต้อง persist `blocked` ก่อน UI เปิด New Session; หาก persist ไม่สำเร็จ
  ให้คง fail-closed behavior และไม่อนุญาต retire
- `Blocked` ที่ runtime worker ยังไม่หยุด เช่น Stop timeout: UI ต้องไม่ retire และต้อง
  แสดงคำแนะนำให้ปิดและเปิดแอปใหม่; หลัง restart ไม่มี worker เดิมและ durable blocked
  marker จึงใช้ New Session ได้
- SQLite failure: rollback; active Session เดิมยัง load ได้หลัง restart
- Session Setup validation failure: Session เก่าเป็น retired แล้ว แต่ไม่มี Session ใหม่
  ถูกสร้าง และ Setup แสดง field error ตาม behavior เดิม
- close window ระหว่าง retire task: stale Qt callback ถูก discard; durable transaction
  เป็น source of truth

## Verification

1. unit tests สำหรับ lifecycle policy: durable `BLOCKED` persistence, permit only
   `STOPPED`/`BLOCKED` เมื่อไม่มี owned runtime worker และ reject `RUNNING`/transition
   states หรือ Stop timeout ที่ worker ยังทำงาน
2. SQLite integration tests: retire ไม่ลบ Session/Basket/Fill, atomic rollback และ
   restart หลัง retire ไม่มี active session
3. UI tests: action visibility, cancel, success-to-setup, failure behavior และ busy state
4. desktop acceptance: Create/Start/Stop -> New Session -> configure `3m` -> Create new
   Session; Trade History ยังอ่านผลของ Session แรกได้
5. `pytest`, Ruff, format, mypy และ `git diff --check` ต้องผ่าน

## Out of Scope

- แก้ configuration ภายใน Session เดิม
- ลบ Session, Basket, Fill หรือ Trade History
- สร้างหลาย Active Sessions พร้อมกัน
- เปลี่ยน Live Trading, strategy preset, risk/execution policy หรือ database backup UI
