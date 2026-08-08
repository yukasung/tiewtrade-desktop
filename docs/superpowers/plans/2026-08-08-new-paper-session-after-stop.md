# New Paper Session After Stop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** ให้ผู้ใช้ retire Paper Session ที่ Stopped หรือ Blocked แล้วสร้าง Session ใหม่ด้วย configuration ใหม่ โดยเก็บประวัติเดิมไว้

**Architecture:** SQLite lifecycle จะ persist state blocked และ migration จะรองรับ schema เดิม. A focused retire use case จะตรวจ active session กับ durable lifecycle state ใน transaction เดียวก่อนตั้ง ended_at_utc. UI แสดง New Session เฉพาะ state ที่อนุญาต, ขอ confirmation, invalidate runtime facts แล้ว route สู่ Setup.

**Tech Stack:** Python 3.12, SQLite, PySide6, pytest, ruff, mypy

## Global Constraints

- SessionConfig/timeframe immutable; เปลี่ยน configuration ต้องสร้าง Session ใหม่.
- หนึ่ง installation มี Active Bot Session ได้สูงสุดหนึ่งรายการ.
- Retire ต้องไม่ลบ/แก้ Session, Basket, Fill หรือ Trade History เดิม.
- New Session ใช้ได้เมื่อ durable STOPPED หรือ durable BLOCKED ที่ไม่มี owned runtime worker;
  ห้าม RUNNING/STARTING/STOPPING และ Blocked ที่ worker ยังทำงาน.
- Paper/fake adapters only; ไม่มี Live API, credentials หรือ orders ใหม่.
- SQLite error ต้อง rollback และคง active Session เดิม.
- UI English, docs/Linear Thai; code identifiers English.

---

### Task 1: Persist blocked lifecycle and migrate legacy SQLite databases

**Files:**
- Modify: src/tiewtrade/integrations/sqlite/database.py
- Modify: src/tiewtrade/integrations/sqlite/paper_runtime_lifecycle.py
- Modify: src/tiewtrade/application/paper_runtime.py
- Test: tests/unit/integrations/sqlite/test_database.py
- Test: tests/unit/integrations/sqlite/test_paper_runtime_lifecycle.py
- Test: tests/unit/application/test_paper_runtime.py

**Interfaces:**
- Add PaperRuntimeLifecycleState.BLOCKED.
- Add SQLitePaperRuntimeLifecycle.mark_blocked(session_id: UUID, observed_at_utc: datetime) -> None.
- Database schema version becomes 6 and accepts lifecycle values running, stopped, blocked.

- [ ] **Step 1: Write failing migration and lifecycle tests**

~~~python
def test_migrate_v5_rebuilds_lifecycle_table_and_preserves_stopped_record(tmp_path: Path) -> None:
    database = legacy_v5_database_with_stopped_lifecycle(tmp_path)
    database.migrate()

    assert SQLitePaperRuntimeLifecycle(database).read(SESSION_ID).state is PaperRuntimeLifecycleState.STOPPED
    SQLitePaperRuntimeLifecycle(database).mark_blocked(SESSION_ID, UTC_NOW)
    assert SQLitePaperRuntimeLifecycle(database).read(SESSION_ID).state is PaperRuntimeLifecycleState.BLOCKED


def test_failed_closed_runtime_persists_blocked_lifecycle_marker() -> None:
    controller = controller_with_failed_runtime()

    result = controller.start(session())

    assert result.workspace.header.runtime_state is BotRuntimeState.BLOCKED
    assert lifecycle.read(session().config.session_id).state is PaperRuntimeLifecycleState.BLOCKED
~~~

- [ ] **Step 2: Verify RED**

Run: PYTHONPATH=src .venv/bin/python -m pytest tests/unit/integrations/sqlite/test_database.py tests/unit/integrations/sqlite/test_paper_runtime_lifecycle.py tests/unit/application/test_paper_runtime.py -k 'blocked or lifecycle' -v

Expected: FAIL because blocked is not a valid lifecycle state/schema value.

- [ ] **Step 3: Implement the durable state and migration**

~~~python
class PaperRuntimeLifecycleState(StrEnum):
    RUNNING = "running"
    STOPPED = "stopped"
    BLOCKED = "blocked"

def mark_blocked(self, session_id: UUID, observed_at_utc: datetime) -> None:
    self._mark(session_id, PaperRuntimeLifecycleState.BLOCKED, observed_at_utc)
~~~

Set SQLiteDatabase._SCHEMA_VERSION = 6. For version 5, rebuild paper_runtime_lifecycle in its migration transaction: rename old table, create the new table with CHECK state IN ('running', 'stopped', 'blocked'), copy session_id/state/observed_at_utc, then drop the old table. Keep foreign key ownership and existing records. In PaperRuntimeController, persist mark_blocked before publishing a fail-closed result; if persistence fails retain fail-closed behavior and do not report a durable blocked marker.

- [ ] **Step 4: Verify GREEN and commit**

Run: PYTHONPATH=src .venv/bin/python -m pytest tests/unit/integrations/sqlite/test_database.py tests/unit/integrations/sqlite/test_paper_runtime_lifecycle.py tests/unit/application/test_paper_runtime.py -v

Expected: PASS.

~~~bash
git add src/tiewtrade/integrations/sqlite/database.py src/tiewtrade/integrations/sqlite/paper_runtime_lifecycle.py src/tiewtrade/application/paper_runtime.py tests/unit/integrations/sqlite/test_database.py tests/unit/integrations/sqlite/test_paper_runtime_lifecycle.py tests/unit/application/test_paper_runtime.py
git commit -m "feat: persist blocked paper lifecycle"
~~~

### Task 2: Retire an eligible active session atomically

**Files:**
- Create: src/tiewtrade/application/retire_paper_session.py
- Modify: src/tiewtrade/integrations/sqlite/active_paper_sessions.py
- Test: tests/unit/application/test_retire_paper_session.py
- Test: tests/unit/integrations/sqlite/test_active_paper_sessions.py
- Test: tests/acceptance/test_desktop_session_setup.py

**Interfaces:**
- RetireActivePaperSession(session_id: UUID, retire_active: Callable[[UUID, datetime], None], clock: Callable[[], datetime]) with execute(session: ConfiguredPaperSession) -> None.
- SQLiteActivePaperSessions.retire(session_id: UUID, ended_at_utc: datetime) -> None.
- Retire permits lifecycle state stopped/blocked only; PaperRuntimeController rejects New Session while it owns a live runtime worker.

- [ ] **Step 1: Write failing atomicity tests**

~~~python
def test_retire_stopped_active_session_preserves_trade_history_and_removes_active(tmp_path: Path) -> None:
    sessions, lifecycle, history, stopped = persisted_stopped_session(tmp_path)

    sessions.retire(stopped.config.session_id, UTC_NOW)

    assert sessions.get_active() is None
    assert history.list_fills(stopped.config.session_id) != ()


def test_retire_running_session_rolls_back_without_ending_active_session(tmp_path: Path) -> None:
    sessions, lifecycle, running = persisted_running_session(tmp_path)

    with pytest.raises(PaperSessionUnavailableError):
        sessions.retire(running.config.session_id, UTC_NOW)

    assert sessions.get_active().config.session_id == running.config.session_id
~~~

- [ ] **Step 2: Verify RED**

Run: PYTHONPATH=src .venv/bin/python -m pytest tests/unit/application/test_retire_paper_session.py tests/unit/integrations/sqlite/test_active_paper_sessions.py -k retire -v

Expected: FAIL because retire API/use case does not exist.

- [ ] **Step 3: Implement the transaction**

~~~python
def retire(self, session_id: UUID, ended_at_utc: datetime) -> None:
    connection = self._database.connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        marker = connection.execute(
            "SELECT state FROM paper_runtime_lifecycle WHERE session_id = ?",
            (str(session_id),),
        ).fetchone()
        if marker is None or marker["state"] not in {"stopped", "blocked"}:
            raise sqlite3.IntegrityError("retire requires stopped or blocked session")
        updated = connection.execute(
            "UPDATE bot_sessions SET ended_at_utc = ? WHERE session_id = ? AND ended_at_utc IS NULL",
            (ended_at_utc.isoformat(), str(session_id)),
        ).rowcount
        if updated != 1:
            raise sqlite3.IntegrityError("active session is required")
        connection.commit()
    except sqlite3.Error as error:
        connection.rollback()
        raise PaperSessionUnavailableError("Active Paper Session retire failed") from error
    finally:
        connection.close()
~~~

The application use case validates the ConfiguredPaperSession source and delegates once; it owns no SQLite import. Do not delete lifecycle, basket, or fill rows.

- [ ] **Step 4: Verify GREEN and commit**

Run: PYTHONPATH=src .venv/bin/python -m pytest tests/unit/application/test_retire_paper_session.py tests/unit/integrations/sqlite/test_active_paper_sessions.py tests/acceptance/test_desktop_session_setup.py -k 'retire or active_session' -v

Expected: PASS.

~~~bash
git add src/tiewtrade/application/retire_paper_session.py src/tiewtrade/integrations/sqlite/active_paper_sessions.py tests/unit/application/test_retire_paper_session.py tests/unit/integrations/sqlite/test_active_paper_sessions.py tests/acceptance/test_desktop_session_setup.py
git commit -m "feat: retire stopped or blocked paper session"
~~~

### Task 3: Add the New Session action and setup transition

**Files:**
- Modify: src/tiewtrade/application/bot_control.py
- Modify: src/tiewtrade/ui/bot_control.py
- Create: src/tiewtrade/ui/new_session_workflow.py
- Modify: src/tiewtrade/ui/trading_workspace.py
- Modify: src/tiewtrade/ui/main_window.py
- Modify: src/tiewtrade/desktop_main.py
- Test: tests/unit/ui/test_bot_control.py
- Test: tests/unit/ui/test_new_session_workflow.py
- Test: tests/unit/ui/test_main_window.py

**Interfaces:**
- Add BotControlAction.NEW_SESSION = "new_session".
- Add BotControlWidget.new_session_requested signal.
- NewSessionWorkflow(retire_session: Callable[[ConfiguredPaperSession], None]) emits setup_required only after a successful retire.
- MainWindow calls SessionWorkflow.start() from NewSessionWorkflow.setup_required to show Setup only when no active session remains.

- [ ] **Step 1: Write failing UI tests**

~~~python
def test_stopped_and_blocked_snapshots_show_new_session_action(qtbot) -> None:
    widget = BotControlWidget(stop_confirmation=lambda confirm, cancel: confirm())

    widget.show_snapshot(stopped_snapshot())
    assert widget.new_session_button.isVisible()
    assert widget.new_session_button.isEnabled()

    widget.show_snapshot(running_snapshot())
    assert not widget.new_session_button.isVisible()


def test_blocked_session_with_live_runtime_disables_new_session(qtbot) -> None:
    widget = BotControlWidget()
    widget.show_snapshot(blocked_snapshot(new_session_allowed=False))

    assert widget.new_session_button.isVisible()
    assert not widget.new_session_button.isEnabled()


def test_confirmed_new_session_retires_then_opens_setup(qtbot) -> None:
    window, retire_calls = new_session_window()

    window.workspace.bot_control_widget.new_session_button.click()

    assert retire_calls == [stopped_session.config.session_id]
    assert window.workspace.setup.isVisible()
~~~

- [ ] **Step 2: Verify RED**

Run: QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest tests/unit/ui/test_bot_control.py tests/unit/ui/test_new_session_workflow.py tests/unit/ui/test_main_window.py -k new_session -v

Expected: FAIL because action, signal, workflow and transition do not exist.

- [ ] **Step 3: Implement confirmation and transition**

~~~python
class BotControlAction(StrEnum):
    START = "start"
    STOP = "stop"
    RECOVER = "recover"
    NEW_SESSION = "new_session"
~~~

Allow NEW_SESSION only in BotControlSnapshot STOPPED/BLOCKED action validation. Add a New Session button and confirmation message exactly: Create a new Paper Session? The current session will remain available in Trade History. NewSessionWorkflow invalidates runtime relay first, executes retire on a background task, and on success emits setup_required. MainWindow connects it to SessionWorkflow.start(); SessionWorkflow loads no active Session then emits its existing setup_required. Cancel performs no callback. Failure retains the old BotControl snapshot and displays sanitized failure notification. Do not show the Setup page until retire succeeds.

- [ ] **Step 4: Verify GREEN and commit**

Run: QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest tests/unit/ui/test_bot_control.py tests/unit/ui/test_new_session_workflow.py tests/unit/ui/test_main_window.py -v

Expected: PASS.

~~~bash
git add src/tiewtrade/application/bot_control.py src/tiewtrade/ui/bot_control.py src/tiewtrade/ui/new_session_workflow.py src/tiewtrade/ui/trading_workspace.py src/tiewtrade/ui/main_window.py src/tiewtrade/desktop_main.py tests/unit/ui/test_bot_control.py tests/unit/ui/test_new_session_workflow.py tests/unit/ui/test_main_window.py
git commit -m "feat: open setup for a new paper session"
~~~

### Task 4: Verify end-to-end session replacement and safety

**Files:**
- Modify: tests/acceptance/test_desktop_session_setup.py
- Modify: tests/acceptance/test_notification_safety_feedback.py if notification assertion needs the new action

**Interfaces:**
- Consumes Tasks 1–3; no new production API.

- [ ] **Step 1: Write a deterministic desktop acceptance test**

~~~python
def test_stopped_session_can_create_new_three_minute_session_and_keep_history(qtbot, tmp_path: Path) -> None:
    window, database = durable_desktop_window(tmp_path)
    first = create_start_and_stop_five_minute_session(window, qtbot)
    persist_trade_fill_for(first, database)

    confirm_new_session(window)
    create_three_minute_session(window, qtbot)

    assert active_session(database).market_data.timeframe == "3m"
    assert listed_fills(database, first.config.session_id) != ()
    assert active_session(database).config.session_id != first.config.session_id
~~~

- [ ] **Step 2: Verify RED then GREEN**

Run: QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest tests/acceptance/test_desktop_session_setup.py -k 'new_three_minute_session' -v

Expected before implementation: FAIL because New Session does not exist. Expected after implementation: PASS.

- [ ] **Step 3: Run all verification and audit**

Run: QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest -q

Expected: PASS.

Run: .venv/bin/python -m ruff check src tests && .venv/bin/python -m ruff format --check src tests && PYTHONPATH=src .venv/bin/python -m mypy && git diff --check

Expected: PASS.

Run: rg -n "ended_at_utc|new_session|mark_blocked|retire\(" src tests

Expected: retire changes only active-session ownership and lifecycle state; no trade-history deletion or Live integration edge.

- [ ] **Step 4: Commit verification tests**

~~~bash
git add tests/acceptance/test_desktop_session_setup.py tests/acceptance/test_notification_safety_feedback.py
git commit -m "test: verify new paper session flow"
~~~

Do not create an empty commit if only earlier tasks supplied every required test.

## Plan Self-Review

- Task 1 covers durable BLOCKED and legacy schema migration; Task 2 covers atomic active-session retirement and retained history; Task 3 covers confirmation, visibility, failure and setup routing; Task 4 verifies the full 5m-to-3m workflow.
- No task changes immutable session configuration, deletes durable history, creates concurrent active sessions, or uses Live APIs.
- Types align: lifecycle BLOCKED enables SQLiteActivePaperSessions.retire; RetireActivePaperSession feeds NewSessionWorkflow; its setup_required event invokes SessionWorkflow.start.
