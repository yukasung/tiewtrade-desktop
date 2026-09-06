"""A Bot that was killed while holding a Position, and the day after.

The unit tests around the lifecycle prove each marker transition on its own.
What is proved here is the story a trader actually lives through: the machine
went down while a Basket was open, they open the application again, and what
they had is still there — recovered to a stop that is honest about what
happened, taken back on the next Start, and never traded on by the recovery
itself.

Nothing here reaches a network. The market feed is a fixed run of candles and
the recovery paths are given a feed that fails the test if it is asked for
anything at all, because a Bot working out what it missed while it was gone is
exactly the thing this product does not do.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from tests.support.paper_runtime_candles import warm_up_candles
from tests.support.paper_runtime_fakes import (
    RUNTIME_NOW,
    ImmediateScheduler,
    PaperRuntimeCandleSource,
)
from tests.support.paper_session_setup import configured_spot_session
from tiewtrade.application.paper_runtime import PaperRuntimeController
from tiewtrade.application.paper_session_setup import ConfiguredPaperSession
from tiewtrade.application.trade_history import TradeHistoryFilter
from tiewtrade.application.trading_workspace import BotRuntimeState
from tiewtrade.integrations.sqlite.active_paper_sessions import (
    SQLiteActivePaperSessions,
)
from tiewtrade.integrations.sqlite.database import SQLiteDatabase
from tiewtrade.integrations.sqlite.paper_runtime_lifecycle import (
    PaperRuntimeLifecycleState,
    SQLitePaperRuntimeLifecycle,
)
from tiewtrade.integrations.sqlite.trade_history import SQLiteTradeHistory
from tiewtrade.trading.session_config import MarketType
from tiewtrade.trading.symbol_rules import SymbolRules
from tiewtrade.trading.trade_history import BasketResult, BasketStatus


def test_a_bot_killed_holding_a_position_comes_back_to_the_same_position(
    tmp_path: Path,
) -> None:
    """Open an Entry, lose the process, open the application again.

    The Position is durable and the Bot is not, so what the trader comes back
    to is read out of the database rather than remembered: the same Basket,
    the same Entry, and the same Take Profit it was heading for.
    """
    session, database = _session_with_one_open_entry(tmp_path)
    lifecycle = SQLitePaperRuntimeLifecycle(database)
    opened = _open_basket(database, session)
    assert opened is not None
    _leave_the_marker_a_killed_bot_leaves(lifecycle, session)

    # A second process, which is what a restart is.
    recovered = _controller(database, source=_a_feed_nothing_may_ask_for()).recover(
        session
    )

    assert recovered.workspace.header is not None
    assert recovered.workspace.header.runtime_state is BotRuntimeState.STOPPED
    marker = lifecycle.read(session.config.session_id)
    assert marker is not None
    assert marker.state is PaperRuntimeLifecycleState.STOPPED

    still_open = _open_basket(database, session)
    assert still_open is not None
    assert still_open.basket_id == opened.basket_id
    assert still_open.entry_count == opened.entry_count
    assert still_open.status is BasketStatus.OPEN

    # And the next Start picks the Basket up rather than opening a second one.
    resumed = _controller(database, source=PaperRuntimeCandleSource(live=()))
    result = resumed.start(session)
    try:
        assert result.workspace.header is not None
        assert result.workspace.header.runtime_state is BotRuntimeState.RUNNING
        basket = resumed.current_workspace.basket
        assert basket is not None
        assert basket.entry_count == opened.entry_count
        assert basket.take_profit_price is not None
    finally:
        resumed.stop(session)

    assert _open_basket(database, session) is not None


def test_recovery_does_no_trading_and_asks_the_market_nothing(
    tmp_path: Path,
) -> None:
    """A Bot that was gone does not work out what it missed while it was gone.

    Recovery reads a marker and writes a marker. It refetches no candles, so
    it builds no indicator state and revives no Entry Intent — a Bot that came
    back and opened an Entry off a signal from before it died would be trading
    on a market that has moved on without it.
    """
    session, database = _session_with_one_open_entry(tmp_path)
    before = _open_basket(database, session)
    assert before is not None
    _leave_the_marker_a_killed_bot_leaves(
        SQLitePaperRuntimeLifecycle(database), session
    )
    feed = _a_feed_nothing_may_ask_for()

    controller = _controller(database, source=feed)
    controller.inspect_startup(session, sole_process=True)
    controller.recover(session)

    after = _open_basket(database, session)
    assert after is not None
    assert after.entry_count == before.entry_count
    fills = SQLiteTradeHistory(database).list_trade_fills(
        TradeHistoryFilter(market_type=MarketType.SPOT)
    )
    assert len(fills) == before.entry_count


def test_a_bot_that_cannot_claim_the_installation_stays_blocked(
    tmp_path: Path,
) -> None:
    """A marker a live Bot is keeping reads the same as one a dead Bot left.

    Only a caller that has claimed the installation can tell them apart, so
    one that has not says nothing and stays Blocked: two Bots on one Session
    is what this guards against, and a stuck Session is the cheaper of the
    two failures.
    """
    session, database = _session_with_one_open_entry(tmp_path)
    lifecycle = SQLitePaperRuntimeLifecycle(database)
    _leave_the_marker_a_killed_bot_leaves(lifecycle, session)

    result = _controller(
        database, source=_a_feed_nothing_may_ask_for()
    ).inspect_startup(session)

    assert result.blocked_reason == "Paper Bot recovery required"
    marker = lifecycle.read(session.config.session_id)
    assert marker is not None
    assert marker.state is PaperRuntimeLifecycleState.RUNNING


def _session_with_one_open_entry(
    tmp_path: Path,
) -> tuple[ConfiguredPaperSession, SQLiteDatabase]:
    """Run a real Bot until it holds one Entry, then stop it cleanly.

    The Position this leaves behind is written by the Runtime itself rather
    than assembled by the test, so what recovery is asked to preserve is what
    trading actually produces.
    """
    session = configured_spot_session()
    database = SQLiteDatabase(tmp_path / "recovery.sqlite3")
    database.migrate()
    assert SQLiteActivePaperSessions(database).create(session).created
    controller = _controller(database, source=PaperRuntimeCandleSource())
    result = controller.start(session)
    assert result.workspace.header is not None
    assert result.workspace.header.runtime_state is BotRuntimeState.RUNNING
    _wait_until(lambda: _open_basket(database, session) is not None)
    controller.stop(session)
    return session, database


def _leave_the_marker_a_killed_bot_leaves(
    lifecycle: SQLitePaperRuntimeLifecycle,
    session: ConfiguredPaperSession,
) -> None:
    """What a process that never got to write "stopped" leaves in its place."""
    lifecycle.mark_running(session.config.session_id, RUNTIME_NOW)


def _controller(
    database: SQLiteDatabase,
    *,
    source: PaperRuntimeCandleSource,
) -> PaperRuntimeController:
    return PaperRuntimeController(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=SymbolRules(
            symbol="BTCUSDT",
            tick_size=Decimal("0.1"),
            step_size=Decimal("0.001"),
            min_notional=Decimal("5"),
        ),
        source_factory=lambda _endpoints: source,
        scheduler=ImmediateScheduler(),
        snapshot_callback=lambda _snapshot: None,
        clock=lambda: RUNTIME_NOW,
    )


class _FeedNothingMayAskFor(PaperRuntimeCandleSource):
    async def load_recent(self, *args: object, **kwargs: object) -> tuple[()]:
        pytest.fail("recovery must not contact market data")


def _a_feed_nothing_may_ask_for() -> _FeedNothingMayAskFor:
    return _FeedNothingMayAskFor(warm_up=warm_up_candles(), live=())


def _open_basket(
    database: SQLiteDatabase, session: ConfiguredPaperSession
) -> BasketResult | None:
    return SQLiteTradeHistory(database).find_open_basket(session.config.session_id)


def _wait_until(condition: object) -> None:
    from threading import Event

    waited = Event()
    for _ in range(500):
        if condition():  # type: ignore[operator]
            return
        waited.wait(0.02)
    raise AssertionError("the Bot never opened the Entry the test waited for")
