"""What this installation calls itself on screen, kept beside the database.

One machine looks exactly like every other machine running this, and the
title is how a trader tells them apart — written once and read at the top of
every screen afterwards. What it says is theirs to decide: whose Bot it is,
which machine it runs on, what it is being used for. Nothing here reads it
but the toolbar, so nothing here has an opinion about it.

It is a preference rather than a credential and rather than a fact about a
Session: it says nothing the Strategy, the risk policy or the ledger may
read, and it outlives every Session there has been. So it is kept in a file
of its own in the directory the database already lives in — not where this
machine keeps the things that would be dangerous to leak, and not in SQLite,
where "Clear All Database" would take a trader's own title away from them
along with their history.

A file that will not answer is read as no title at all. A window named for
the application alone is the one thing that can be shown honestly without one.
"""

import logging
from pathlib import Path

_logger = logging.getLogger(__name__)

BOT_TITLE_FILE_NAME = "bot_title.txt"

MAX_BOT_TITLE_LENGTH = 60
"""As much of a remark as a toolbar can hold beside the name of the app."""

_UNREADABLE_EVENT = "bot.title.unreadable"


class BotTitleError(RuntimeError):
    """The title could not be kept where the trader asked it to be kept."""


def read_bot_title(directory: Path) -> str:
    """Read the title, or read a file that will not answer as no title."""
    try:
        return _one_line((directory / BOT_TITLE_FILE_NAME).read_text(encoding="utf-8"))
    except FileNotFoundError:
        # Never written, or written empty and taken away again.
        return ""
    except (OSError, ValueError) as error:
        _logger.warning("%s reason=%s", _UNREADABLE_EVENT, type(error).__name__)
        return ""


def save_bot_title(directory: Path, title: str) -> None:
    """Keep the title, and take the file away when it is emptied.

    An emptied field is a trader saying this installation needs no title of
    its own, which is the state it starts in — so it is stored the way that
    state is stored, by there being no file.
    """
    path = directory / BOT_TITLE_FILE_NAME
    trimmed = _one_line(title)
    try:
        if not trimmed:
            path.unlink(missing_ok=True)
            return
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{trimmed}\n", encoding="utf-8")
    except OSError as error:
        _logger.warning("bot.title.unwritable reason=%s", type(error).__name__)
        raise BotTitleError("the bot title could not be saved") from error


def _one_line(title: str) -> str:
    """One line of it, however it was typed or however the file was edited."""
    return " ".join(title.split())[:MAX_BOT_TITLE_LENGTH]
