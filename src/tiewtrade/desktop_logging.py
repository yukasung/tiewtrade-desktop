"""Where the desktop app's events go, so a stopped Bot can be explained.

The Runtime already says why it stopped — a reconnect attempt, a backfill
that came back short, the reason it failed closed — through the logging
module. Nothing is listening: a bundled desktop app has no terminal, so
`logging.lastResort` writes to a stderr no one reads. One file beside the
database is the whole answer.
"""

from __future__ import annotations

import logging
from io import TextIOWrapper
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FILE_NAME = "tiewtrade.log"
PACKAGE_LOGGER = "tiewtrade"
HANDLER_NAME = "tiewtrade.desktop.file"
_MAX_BYTES = 1_000_000
_BACKUP_COUNT = 3
# Attributes every LogRecord carries. What is left is what an event stated
# through `extra`, which is the part worth reading.
_RECORD_ATTRIBUTES = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | {"message", "asctime", "taskName"}


class _DirectoryCreatingFileHandler(RotatingFileHandler):
    """Makes its own directory, at the moment it first has something to write.

    The desktop composes before the window opens and must not touch the disk
    while doing it — the database directory is made by the first worker that
    needs it, and a log file that made its own directory eagerly would be the
    one thing that did.
    """

    def _open(self) -> TextIOWrapper:
        Path(self.baseFilename).parent.mkdir(parents=True, exist_ok=True)
        return super()._open()


class _EventFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        fields = {
            name: value
            for name, value in record.__dict__.items()
            if name not in _RECORD_ATTRIBUTES
        }
        if not fields:
            return line
        stated = " ".join(f"{name}={value}" for name, value in sorted(fields.items()))
        return f"{line} {stated}"


def configure_desktop_logging(directory: Path) -> Path:
    """Send this package's events to a rotating file in `directory`.

    Only this package's logger is touched: the root logger belongs to
    whoever embeds us, and a desktop app has no business claiming it.
    """
    log_path = directory / LOG_FILE_NAME
    logger = logging.getLogger(PACKAGE_LOGGER)
    logger.setLevel(logging.INFO)
    for existing in list(logger.handlers):
        if existing.name != HANDLER_NAME:
            continue
        # Asked for the same file again, the file it already has is the
        # answer. Asked for a different one, the old file is not.
        if Path(getattr(existing, "baseFilename", "")) == log_path.resolve():
            return log_path
        logger.removeHandler(existing)
        existing.close()

    handler = _DirectoryCreatingFileHandler(
        log_path,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
        delay=True,
    )
    handler.name = HANDLER_NAME
    handler.setFormatter(
        _EventFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logger.addHandler(handler)
    return log_path
