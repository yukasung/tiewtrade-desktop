"""Clearing the Session on screen away, so the setup form's values can replace it.

One installation holds at most one Active Bot Session, so a new one cannot be
made beside the old one — the old one has to go first, and going is three
things in one order. The Bot stops, because a Session whose Runtime is still
running is one whose last write has not landed. Whatever it was holding is
closed, because a retired Session's open Basket belongs to nothing and can
never be sold. Only then is the Session retired.

The order is the whole of the rule, and it is a business rule rather than a
screen's: it decided what happens to a trader's money, and it was being
composed in the window that asked for it, where the only thing that could
test it was a window. Stated here it is testable on its own, and the screen
is left with what it is actually for — putting the question, and saying what
came back.

Each step says why it stopped in a type of its own, so the screen chooses its
words by asking what went wrong rather than by reading the sentence a lower
layer happened to write.
"""

from collections.abc import Callable

from tiewtrade.application.latest_market_price import MarketPriceUnavailableError
from tiewtrade.application.paper_session_setup import (
    ActivePaperSessionGoneError,
    ActivePaperSessionStillRunningError,
    ConfiguredPaperSession,
)


class PaperSessionReplacementError(RuntimeError):
    """The Session that is there could not be cleared away for a new one."""


class BotWillNotStopError(PaperSessionReplacementError):
    """The Bot was asked to stop and had not stopped by the time it answered."""


class ReplacePaperSession:
    """Stop the Bot, close what it holds, and retire the Session — in that order."""

    def __init__(
        self,
        *,
        stop_bot: Callable[[ConfiguredPaperSession], None],
        has_open_position: Callable[[ConfiguredPaperSession], bool],
        close_position: Callable[[ConfiguredPaperSession], None],
        retire_session: Callable[[ConfiguredPaperSession], None],
    ) -> None:
        self._stop_bot = stop_bot
        self._has_open_position = has_open_position
        self._close_position = close_position
        self._retire_session = retire_session

    def execute(self, session: ConfiguredPaperSession) -> None:
        """Clear this Session away, or raise saying which step would not go.

        Nothing here is undone on the way out. A Bot that stopped stays
        stopped and a Position that closed stays closed, because both are
        states the trader asked for in as many words — what fails is the
        replacement, and the answer to that is to ask for it again.
        """
        self._stop_bot(session)
        if self._has_open_position(session):
            self._close_position(session)
        self._retire_session(session)


def replacement_failure_message(error: BaseException) -> str:
    """What to tell the trader about a replacement that would not go.

    Every reason names something the trader can do next, because every one
    of them is recoverable: the Session that could not be replaced is still
    there, still theirs, and still startable.
    """
    if isinstance(error, MarketPriceUnavailableError):
        return (
            "Could not reach live market data to price the close. "
            "Check your internet connection, then try again."
        )
    if isinstance(error, BotWillNotStopError):
        return "Paper Bot is still stopping. Wait for it to stop, then try again."
    if isinstance(error, ActivePaperSessionStillRunningError):
        return (
            "The existing Paper Session is still running somewhere. "
            "Stop it, or restart the application."
        )
    if isinstance(error, ActivePaperSessionGoneError):
        return "The existing Paper Session is no longer in the database."
    return "Could not stop, close, or retire the existing Paper Session."
