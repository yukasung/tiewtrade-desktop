"""The one process allowed to run this installation's Bot.

One installation uses exactly one Binance account and has at most one Active
Bot Session, and until now nothing enforced it: two windows opened on the same
database would both trade the same Session.

The lock is `flock` rather than a marker of our own because of the one death
no shutdown code can answer. A process that is killed, panics, or loses power
runs no signal handler, no `atexit`, and no Qt `closeEvent` — but the kernel
drops its file locks regardless. That is what makes this a fact worth reading:
holding the lock proves no other process is running this installation, so a
lifecycle marker left saying "running" is provably left over rather than live.
"""

from __future__ import annotations

import fcntl
from pathlib import Path
from types import TracebackType
from typing import IO

LOCK_FILE_SUFFIX = ".lock"


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
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # The one refusal that is an answer rather than a fault: somebody
            # else holds it. Every other error means the lock could not be
            # asked about at all, which must not read as "nobody is there".
            handle.close()
            return False
        except BaseException:
            handle.close()
            raise
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
            fcntl.flock(handle, fcntl.LOCK_UN)
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
