"""The one process allowed to run this installation's Bot.

One installation uses exactly one Binance account and has at most one Active
Bot Session, and until now nothing enforced it: two windows opened on the same
database would both trade the same Session.

The lock is a file lock rather than a marker of our own because of the one
death no shutdown code can answer. A process that is killed, panics, or loses
power runs no signal handler, no `atexit`, and no Qt `closeEvent` — but the
kernel drops its file locks regardless. That is what makes this a fact worth
reading: holding the lock proves no other process is running this
installation, so a lifecycle marker left saying "running" is provably left
over rather than live.

Which call takes the lock is the platform's business — `flock` on macOS and
Linux, `msvcrt.locking` on Windows — and both are the kernel's own locks, so
the guarantee above holds everywhere the app runs.
"""

from __future__ import annotations

import errno
import sys
from pathlib import Path
from types import TracebackType
from typing import IO

LOCK_FILE_SUFFIX = ".lock"

# Compared inline rather than through a name, because a type checker reads
# `sys.platform` as a fact about the build it is checking and only then knows
# that the branch it does not take names modules its platform does not have.
if sys.platform == "win32":
    import msvcrt

    # Windows locks a byte range rather than the file, and the range is taken
    # from wherever the handle happens to be. One byte at the start is enough:
    # every process asks for the same range, and the file holds nothing to
    # read.
    _LOCKED_BYTES = 1
    # A range somebody else holds comes back as EACCES on Windows, and as
    # EDEADLOCK when the lock could not be granted within its retry count.
    # Both mean "taken"; anything else means the lock could not be asked
    # about, which must not read as "nobody is there".
    _ALREADY_HELD = frozenset({errno.EACCES, errno.EDEADLOCK})

    def _take_the_lock(handle: IO[str]) -> bool:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, _LOCKED_BYTES)
        except OSError as error:
            if error.errno in _ALREADY_HELD:
                return False
            raise
        return True

    def _drop_the_lock(handle: IO[str]) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, _LOCKED_BYTES)

else:
    import fcntl

    def _take_the_lock(handle: IO[str]) -> bool:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def _drop_the_lock(handle: IO[str]) -> None:
        fcntl.flock(handle, fcntl.LOCK_UN)


def lock_path_for(database_path: Path | str) -> Path:
    """The lock beside the database, never the database itself.

    Locking the database file would mean opening it, and the one process that
    may open it is SQLite.
    """
    database = Path(database_path)
    return database.with_name(database.name + LOCK_FILE_SUFFIX)


class InstallationLock:
    """Whether this process is the only one using an installation's database."""

    def __init__(self, database_path: Path | str) -> None:
        self._path = lock_path_for(database_path)
        self._handle: IO[str] | None = None

    @property
    def path(self) -> Path:
        return self._path

    def acquire(self) -> bool:
        """Take the lock, or report that another process is holding it.

        Held is held: asking again while this process already has it answers
        yes without reopening, so a second ask can never report the lock lost.
        """
        if self._handle is not None:
            return True
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._path.open("a")
        try:
            taken = _take_the_lock(handle)
        except BaseException:
            # A backend raises only when the lock could not be asked about at
            # all, which must never read as "nobody is there".
            handle.close()
            raise
        if not taken:
            # The one refusal that is an answer rather than a fault: somebody
            # else holds it.
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        """Give the lock up. Releasing what was never held is not an error.

        The lock file itself is left behind: unlinking it would let a process
        that is still waiting on this one lock a file no one else can see.
        """
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            _drop_the_lock(handle)
        finally:
            handle.close()

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()
