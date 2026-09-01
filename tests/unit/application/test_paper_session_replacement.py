"""The order a Session is cleared away in, and what each refusal is called."""

from collections.abc import Callable

import pytest

from tests.support.paper_session_setup import configured_spot_session
from tiewtrade.application.latest_market_price import MarketPriceUnavailableError
from tiewtrade.application.paper_session_replacement import (
    BotWillNotStopError,
    ReplacePaperSession,
    replacement_failure_message,
)
from tiewtrade.application.paper_session_setup import (
    ActivePaperSessionGoneError,
    ActivePaperSessionStillRunningError,
    ConfiguredPaperSession,
)


def _replacement(
    events: list[str],
    *,
    position_open: bool = True,
    fails_at: str | None = None,
    error: Exception | None = None,
) -> ReplacePaperSession:
    def step(name: str) -> Callable[[ConfiguredPaperSession], None]:
        def run(_session: ConfiguredPaperSession) -> None:
            events.append(name)
            if name == fails_at:
                raise error or RuntimeError(name)

        return run

    return ReplacePaperSession(
        stop_bot=step("stop"),
        has_open_position=lambda _session: position_open,
        close_position=step("close"),
        retire_session=step("retire"),
    )


def test_the_bot_stops_before_the_position_closes_and_the_session_retires() -> None:
    """The order is the rule.

    A Session whose Runtime is still running is one whose last write has not
    landed, and a retired Session's open Basket belongs to nothing and can
    never be sold.
    """
    events: list[str] = []

    _replacement(events).execute(configured_spot_session())

    assert events == ["stop", "close", "retire"]


def test_a_session_holding_nothing_is_retired_without_a_close() -> None:
    events: list[str] = []

    _replacement(events, position_open=False).execute(configured_spot_session())

    assert events == ["stop", "retire"]


def test_a_bot_that_will_not_stop_leaves_the_position_and_the_session_alone() -> None:
    """Nothing past the failed step runs, because the Bot still owns the Basket."""
    events: list[str] = []

    with pytest.raises(BotWillNotStopError):
        _replacement(
            events, fails_at="stop", error=BotWillNotStopError("still stopping")
        ).execute(configured_spot_session())

    assert events == ["stop"]


def test_a_close_that_has_no_price_leaves_the_session_where_it_is() -> None:
    """A Session retired over an open Basket is a Basket nothing can sell."""
    events: list[str] = []

    with pytest.raises(MarketPriceUnavailableError):
        _replacement(
            events,
            fails_at="close",
            error=MarketPriceUnavailableError("no price"),
        ).execute(configured_spot_session())

    assert events == ["stop", "close"]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            MarketPriceUnavailableError("no price"),
            "Could not reach live market data to price the close. "
            "Check your internet connection, then try again.",
        ),
        (
            BotWillNotStopError("still stopping"),
            "Paper Bot is still stopping. Wait for it to stop, then try again.",
        ),
        (
            ActivePaperSessionStillRunningError("cannot be retired"),
            "The existing Paper Session is still running somewhere. "
            "Stop it, or restart the application.",
        ),
        (
            ActivePaperSessionGoneError("no longer there"),
            "The existing Paper Session is no longer in the database.",
        ),
        (
            RuntimeError("something else entirely"),
            "Could not stop, close, or retire the existing Paper Session.",
        ),
    ],
)
def test_every_refusal_names_something_the_trader_can_do_next(
    error: Exception, expected: str
) -> None:
    """A Session that could not be replaced is still there and still startable."""
    assert replacement_failure_message(error) == expected
