from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID

import pytest

from tiewtrade.application.paper_futures_session import (
    PaperFuturesFailureReason,
    PaperFuturesSession,
    PaperFuturesSessionError,
    PaperFuturesSessionIdentity,
    PaperFuturesSessionState,
)
from tiewtrade.market_data.candle import Candle
from tiewtrade.market_data.config import MarketDataConfig
from tiewtrade.strategies.entry_intent import EntryIntent
from tiewtrade.strategies.rsi_step_grid.preset import RsiStepGridPreset
from tiewtrade.strategies.rsi_step_grid.strategy import RsiStepGridStrategy
from tiewtrade.trading.entry_pair import EntryGate, EntryGateState
from tiewtrade.trading.entry_policy import EntryPolicy
from tiewtrade.trading.futures_policy import FuturesTradingPolicy
from tiewtrade.trading.session_config import MarketType, SessionConfig, TradeMode
from tiewtrade.trading.symbol_rules import SymbolRules


def test_session_requires_paper_futures_configuration_and_matching_preset() -> None:
    session = futures_session()

    with pytest.raises(ValueError, match="Paper Futures"):
        make_session(replace(session, trade_mode=TradeMode.LIVE))

    spot_session = SessionConfig(
        session_id=session.session_id,
        preset_version=session.preset_version,
        market_type=MarketType.SPOT,
        trade_mode=TradeMode.PAPER,
        capital_per_entry=session.capital_per_entry,
        entry_policy=session.entry_policy,
    )
    with pytest.raises(ValueError, match="Paper Futures"):
        make_session(spot_session)

    with pytest.raises(ValueError, match="preset version"):
        make_session(replace(session, preset_version="wrong-version"))


def test_session_requires_futures_policy() -> None:
    session = futures_session()
    object.__setattr__(session, "futures_policy", None)

    with pytest.raises(ValueError, match="futures_policy"):
        make_session(session)


def test_session_exposes_immutable_persistence_identity() -> None:
    session = make_session()

    assert session.identity == PaperFuturesSessionIdentity(
        session_id=UUID("00000000-0000-0000-0000-000000000108"),
        symbol="BTCUSDT",
        timeframe="5m",
        preset_version="rsi-step-grid-v1",
        leverage=5,
    )


def test_incomplete_candle_is_not_processed() -> None:
    session = make_session()
    incomplete = candle(0, open="100", close="101")

    snapshot = session.process_completed_candle(
        incomplete,
        received_at=incomplete.close_time - timedelta(seconds=1),
    )

    assert snapshot.accepted is False
    assert session._latest_mark_price is None


def test_unexpected_execution_error_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
    pending = arm_entry_intent(session)
    entry_candle = next_candle(
        pending.signal_candle,
        open="120",
        high="121",
        low="119",
        close="120",
    )

    def raise_execution_error(*args: object, **kwargs: object) -> None:
        raise ValueError("broken execution invariant")

    monkeypatch.setattr(session._executor, "fill_entry", raise_execution_error)

    with pytest.raises(PaperFuturesSessionError, match="execution failed"):
        session.process_completed_candle(
            entry_candle,
            received_at=entry_candle.close_time,
        )

    assert session.snapshot.state is PaperFuturesSessionState.FAILED_CLOSED
    assert session.snapshot.failure_reason is PaperFuturesFailureReason.EXECUTION_ERROR
    assert session.snapshot.pending_intent is None
    # Said as a plain fact, because what reads it next feeds Spot and Futures
    # alike and cannot ask either one about its own state enum.
    assert session.snapshot.failed_closed is True
    assert cast(RsiStepGridStrategy, session._strategy)._pending_intent is None

    before = session.snapshot
    following_candle = next_candle(
        entry_candle,
        open="120",
        high="121",
        low="119",
        close="120",
    )
    after = session.process_completed_candle(
        following_candle,
        received_at=following_candle.close_time,
    )
    assert not after.accepted
    assert after.state is before.state
    assert after.basket_id == before.basket_id
    assert after.basket_entry_count == before.basket_entry_count
    assert after.account_equity == before.account_equity


def test_an_entry_that_failed_leaves_the_session_holding_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Fill that raised must not leave half a Basket behind it."""
    session = make_session()
    pending = arm_entry_intent(session)
    entry_candle = next_candle(
        pending.signal_candle,
        open="120",
        high="121",
        low="119",
        close="120",
    )

    def raise_execution_error(*args: object, **kwargs: object) -> None:
        raise ValueError("broken execution invariant")

    monkeypatch.setattr(session._executor, "fill_entry", raise_execution_error)

    with pytest.raises(PaperFuturesSessionError, match="execution failed"):
        session.process_completed_candle(
            entry_candle,
            received_at=entry_candle.close_time,
        )

    assert session.snapshot.state is PaperFuturesSessionState.FAILED_CLOSED
    assert session.snapshot.failure_reason is PaperFuturesFailureReason.EXECUTION_ERROR
    assert session.snapshot.pending_intent is None
    assert session.snapshot.basket_id is None
    assert session.snapshot.basket_entry_count == 0
    assert session.snapshot.position_side is None
    assert session.snapshot.take_profit_price is None
    assert session.snapshot.account_equity == futures_session().session_capital
    assert session._basket is None
    assert session._lifecycle.entry_count == 0
    assert cast(RsiStepGridStrategy, session._strategy)._pending_intent is None


def make_session_in_cooldown() -> PaperFuturesSession:
    """A Basket holding a completed Entry Pair, which is a Basket in Cooldown.

    Four Entries of quota rather than two, so the Pair closes the gate with a
    Cooldown rather than with the Session running out of Entries.
    """
    session = make_session(
        replace(futures_session(), entry_policy=EntryPolicy(max_entries=4))
    )
    pending = arm_entry_intent(session)
    first_candle = next_candle(
        pending.signal_candle, open="120", high="121", low="100", close="120"
    )
    session.process_completed_candle(first_candle, received_at=first_candle.close_time)
    second_pending = arm_next_entry_intent(session, first_candle)
    second_candle = next_candle(
        second_pending.signal_candle, open="120", high="121", low="100", close="120"
    )
    snapshot = session.process_completed_candle(
        second_candle, received_at=second_candle.close_time
    )
    assert snapshot.basket_entry_count == 2
    assert snapshot.entry_gate == EntryGate(EntryGateState.COOLDOWN)
    return session


def test_waive_cooldown_opens_the_futures_gate() -> None:
    session = make_session_in_cooldown()

    snapshot = session.waive_cooldown(datetime(2026, 1, 25, tzinfo=UTC))

    assert snapshot.accepted is True
    assert snapshot.entry_gate == EntryGate(EntryGateState.OPEN)


def test_waive_cooldown_rejects_a_futures_session_not_in_cooldown() -> None:
    session, _entry_candle = make_session_with_open_long_basket()

    snapshot = session.waive_cooldown(datetime(2026, 1, 25, tzinfo=UTC))

    assert snapshot.accepted is False


def test_waive_cooldown_rejects_a_futures_session_with_no_basket() -> None:
    session = make_session()

    snapshot = session.waive_cooldown(datetime(2026, 1, 25, tzinfo=UTC))

    assert snapshot.accepted is False


def make_session(
    session: SessionConfig | None = None,
    *,
    min_notional: Decimal = Decimal("5"),
) -> PaperFuturesSession:
    return PaperFuturesSession(
        session or futures_session(),
        MarketDataConfig(symbol="BTCUSDT", timeframe="5m"),
        SymbolRules(
            symbol="BTCUSDT",
            tick_size=Decimal("0.1"),
            step_size=Decimal("0.1"),
            min_notional=min_notional,
        ),
        RsiStepGridPreset.v1(),
    )


def futures_session() -> SessionConfig:
    return SessionConfig(
        session_id=UUID("00000000-0000-0000-0000-000000000108"),
        preset_version="rsi-step-grid-v1",
        market_type=MarketType.FUTURES,
        trade_mode=TradeMode.PAPER,
        capital_per_entry=Decimal("250"),
        entry_policy=EntryPolicy(max_entries=2),
        futures_policy=FuturesTradingPolicy.v1(leverage=5),
    )


def make_session_with_open_long_basket() -> tuple[PaperFuturesSession, Candle]:
    session = make_session()
    pending = arm_entry_intent(session)
    entry_candle = next_candle(
        pending.signal_candle,
        open="120",
        high="121",
        low="100",
        close="120",
    )
    snapshot = session.process_completed_candle(
        entry_candle,
        received_at=entry_candle.close_time,
    )
    assert snapshot.entry_fill is not None
    return session, entry_candle


def arm_entry_intent(session: PaperFuturesSession) -> EntryIntent:
    for current in signal_candles():
        snapshot = session.process_completed_candle(
            current,
            received_at=current.close_time,
        )
        if snapshot.pending_intent is not None:
            return snapshot.pending_intent
    raise AssertionError("expected a pending Entry Intent")


def arm_next_entry_intent(
    session: PaperFuturesSession, previous: Candle
) -> EntryIntent:
    current = previous
    close = previous.close
    for _ in range(30):
        next_close = close - Decimal("1")
        current = next_candle(
            current,
            open=str(close),
            high=str(close + Decimal("1")),
            low=str(next_close - Decimal("1")),
            close=str(next_close),
        )
        session.process_completed_candle(current, received_at=current.close_time)
        close = next_close

    for _ in range(25):
        next_close = close + Decimal("1")
        current = next_candle(
            current,
            open=str(close),
            high=str(next_close + Decimal("1")),
            low=str(close - Decimal("1")),
            close=str(next_close),
        )
        snapshot = session.process_completed_candle(
            current,
            received_at=current.close_time,
        )
        if snapshot.pending_intent is not None:
            return snapshot.pending_intent
        close = next_close

    raise AssertionError("expected another pending Entry Intent")


def signal_candles() -> list[Candle]:
    candles: list[Candle] = []
    close = Decimal("100")
    for minute in range(0, 75, 5):
        candles.append(candle(minute, open=str(close + 1), close=str(close)))
        close -= Decimal("1")
    for minute in range(75, 200, 5):
        current = candle(minute, open=str(close), close=str(close + 1))
        candles.append(current)
        close += Decimal("1")
    return candles


def next_candle(
    previous: Candle,
    *,
    open: str,
    high: str,
    low: str,
    close: str,
) -> Candle:
    minute = int(
        (
            previous.open_time + timedelta(minutes=5) - datetime(2026, 1, 1, tzinfo=UTC)
        ).total_seconds()
        / 60
    )
    return candle(minute, open=open, high=high, low=low, close=close)


def candle(
    minute: int,
    *,
    open: str,
    close: str,
    high: str | None = None,
    low: str | None = None,
) -> Candle:
    open_price = Decimal(open)
    close_price = Decimal(close)
    return Candle(
        symbol="BTCUSDT",
        timeframe="5m",
        open_time=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=minute),
        open=open_price,
        high=(
            max(open_price, close_price) + Decimal("1")
            if high is None
            else Decimal(high)
        ),
        low=(
            min(open_price, close_price) - Decimal("1") if low is None else Decimal(low)
        ),
        close=close_price,
    )
