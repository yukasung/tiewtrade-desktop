import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

import tiewtrade
from tiewtrade.integrations.process_lock import installation_lock
from tiewtrade.integrations.process_lock.installation_lock import (
    InstallationLock,
    lock_path_for,
)

# The holder runs the real lock in a real process, so it has to import the
# package the way this test did rather than trusting an inherited PYTHONPATH.
_SOURCE_ROOT = str(Path(tiewtrade.__file__).resolve().parents[1])

_HOLDER_READY_TIMEOUT_SECONDS = 10.0
_LOCK_RELEASE_TIMEOUT_SECONDS = 10.0

# SIGKILL and SIGTERM are POSIX signals. Windows kills a process by another
# route entirely, so the tests that need one are skipped there rather than
# rewritten — the guarantee they cover is the kernel's on both platforms, and
# there is a Windows machine in the manual test plan to see it on.
_POSIX_SIGNALS_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="SIGKILL and SIGTERM do not exist on Windows",
)


def test_lock_path_sits_beside_the_database(tmp_path: Path) -> None:
    database = tmp_path / "tiewtrade.sqlite3"

    assert lock_path_for(database).parent == database.parent


def test_lock_path_is_not_the_database_itself(tmp_path: Path) -> None:
    """A lock that opened the database file would truncate or hold it open."""
    database = tmp_path / "tiewtrade.sqlite3"

    assert lock_path_for(database) != database


def test_two_databases_do_not_share_a_lock(tmp_path: Path) -> None:
    first = tmp_path / "one" / "tiewtrade.sqlite3"
    second = tmp_path / "two" / "tiewtrade.sqlite3"

    assert lock_path_for(first) != lock_path_for(second)


def test_acquires_when_nothing_holds_it(tmp_path: Path) -> None:
    lock = InstallationLock(tmp_path / "tiewtrade.sqlite3")

    assert lock.acquire() is True

    lock.release()


def test_makes_its_own_directory(tmp_path: Path) -> None:
    """The database directory is made by the first worker that needs it, and
    the lock is taken before any worker runs."""
    lock = InstallationLock(tmp_path / "made" / "here" / "tiewtrade.sqlite3")

    assert lock.acquire() is True

    lock.release()


def test_a_second_lock_on_the_same_database_is_refused(tmp_path: Path) -> None:
    database = tmp_path / "tiewtrade.sqlite3"
    held = InstallationLock(database)
    held.acquire()

    try:
        assert InstallationLock(database).acquire() is False
    finally:
        held.release()


def test_a_lock_on_another_database_is_granted(tmp_path: Path) -> None:
    held = InstallationLock(tmp_path / "one" / "tiewtrade.sqlite3")
    held.acquire()
    other = InstallationLock(tmp_path / "two" / "tiewtrade.sqlite3")

    try:
        assert other.acquire() is True
        other.release()
    finally:
        held.release()


def test_releasing_hands_the_lock_on(tmp_path: Path) -> None:
    database = tmp_path / "tiewtrade.sqlite3"
    first = InstallationLock(database)
    first.acquire()
    first.release()
    second = InstallationLock(database)

    assert second.acquire() is True

    second.release()


def test_releasing_what_was_never_held_does_nothing(tmp_path: Path) -> None:
    InstallationLock(tmp_path / "tiewtrade.sqlite3").release()


def test_acquiring_twice_keeps_the_one_lock(tmp_path: Path) -> None:
    """Held is held: a second ask must not report the lock lost."""
    database = tmp_path / "tiewtrade.sqlite3"
    lock = InstallationLock(database)
    lock.acquire()

    try:
        assert lock.acquire() is True
    finally:
        lock.release()


def test_context_manager_releases_on_the_way_out(tmp_path: Path) -> None:
    database = tmp_path / "tiewtrade.sqlite3"

    with InstallationLock(database) as held:
        assert held is True

    assert InstallationLock(database).acquire() is True


def test_context_manager_releases_when_the_body_raises(tmp_path: Path) -> None:
    database = tmp_path / "tiewtrade.sqlite3"

    with pytest.raises(RuntimeError):
        with InstallationLock(database):
            raise RuntimeError("the window failed to open")

    assert InstallationLock(database).acquire() is True


@_POSIX_SIGNALS_ONLY
def test_another_process_holding_it_refuses_this_one(tmp_path: Path) -> None:
    database = tmp_path / "tiewtrade.sqlite3"
    holder = _start_holder(database)

    try:
        assert InstallationLock(database).acquire() is False
    finally:
        _end_holder(holder, signal.SIGTERM)


@_POSIX_SIGNALS_ONLY
def test_a_killed_process_leaves_the_lock_free(tmp_path: Path) -> None:
    """The whole point: a Bot that was never stopped cannot hold the lock.

    SIGKILL is the one death no shutdown code can answer — no signal handler,
    no atexit, no Qt closeEvent. The kernel drops the flock anyway, which is
    what makes a stale lifecycle marker provably stale.
    """
    database = tmp_path / "tiewtrade.sqlite3"
    holder = _start_holder(database)
    _end_holder(holder, signal.SIGKILL)

    assert _lock_is_free_within(database, _LOCK_RELEASE_TIMEOUT_SECONDS) is True


def _start_holder(database: Path) -> subprocess.Popen[str]:
    """A separate process that takes the lock and holds it until it is killed."""
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import sys, time\n"
                "from tiewtrade.integrations.process_lock.installation_lock "
                "import InstallationLock\n"
                "lock = InstallationLock(sys.argv[1])\n"
                "print(lock.acquire(), flush=True)\n"
                "time.sleep(300)\n"
            ),
            str(database),
        ],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": _SOURCE_ROOT},
    )
    assert holder.stdout is not None
    deadline = time.monotonic() + _HOLDER_READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        line = holder.stdout.readline()
        if line.strip() == "True":
            return holder
        if line == "":
            break
    _end_holder(holder, signal.SIGKILL)
    raise AssertionError("the holder process never took the lock")


def _end_holder(holder: subprocess.Popen[str], how: signal.Signals) -> None:
    if holder.poll() is None:
        holder.send_signal(how)
    holder.wait(timeout=_LOCK_RELEASE_TIMEOUT_SECONDS)
    if holder.stdout is not None:
        holder.stdout.close()


def _lock_is_free_within(database: Path, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        lock = InstallationLock(database)
        if lock.acquire():
            lock.release()
            return True
        time.sleep(0.05)
    return False


def test_a_lock_that_cannot_be_asked_about_is_not_a_free_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refusing and failing are different answers, and only one is safe.

    "Nobody holds it" licenses recovering a stale lifecycle marker. A lock
    that could not be asked about has said nothing of the kind, so it must
    raise rather than report the installation free.

    The platform's own call is behind a seam here rather than named outright,
    because which call it is differs by platform and this rule does not.
    """

    def refuse(*_args: object, **_kwargs: object) -> bool:
        raise PermissionError("the lock is unavailable on this filesystem")

    monkeypatch.setattr(installation_lock, "_take_the_lock", refuse)

    with pytest.raises(PermissionError):
        InstallationLock(tmp_path / "tiewtrade.sqlite3").acquire()


def test_a_lock_that_cannot_be_asked_about_leaves_no_open_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed ask must leave nothing behind, so a later ask is a fresh one."""
    database = tmp_path / "tiewtrade.sqlite3"
    lock = InstallationLock(database)

    def refuse(*_args: object, **_kwargs: object) -> bool:
        raise PermissionError("the lock is unavailable on this filesystem")

    monkeypatch.setattr(installation_lock, "_take_the_lock", refuse)
    with pytest.raises(PermissionError):
        lock.acquire()
    monkeypatch.undo()

    assert lock.acquire() is True

    lock.release()


def test_the_platform_call_is_never_imported_at_the_top_of_the_module() -> None:
    """`fcntl` does not exist on Windows, and an import of it at module level
    would fail before the app could say anything — the whole desktop is behind
    this import. It has to stay inside the branch that only POSIX takes.
    """
    source = Path(installation_lock.__file__).read_text(encoding="utf-8")

    assert "\nimport fcntl" not in source
    assert "\n    import fcntl" in source
