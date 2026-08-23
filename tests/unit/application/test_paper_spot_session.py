from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast
from unittest.mock import Mock, create_autospec
from uuid import UUID, uuid5

import pytest

from tiewtrade.application.open_basket import OpenBasketEntry, OpenBasketState
from tiewtrade.application.paper_spot_session import (
    PaperSpotFailureReason,
    PaperSpotSession,
    PaperSpotSessionError,
    PaperSpotSessionIdentity,
    PaperSpotSessionSnapshot,
    PaperSpotSessionState,
)
from tiewtrade.application.session_persistence import SessionPersistenceCoordinator
from tiewtrade.integrations.sqlite.paper_spot_history import PaperSpotSQLiteHistory
from tiewtrade.integrations.sqlite.persistent_paper_spot_session import (
    create_persistent_paper_spot_session,
)
from tiewtrade.market_data.candle import Candle
from tiewtrade.market_data.config import MarketDataConfig
from tiewtrade.strategies.entry_intent import EntryIntent
from tiewtrade.strategies.fast_test.preset import FastTestPreset
from tiewtrade.strategies.indicators import WilderIndicators
from tiewtrade.strategies.rsi_buy_only.preset import RsiBuyOnlyPreset
from tiewtrade.strategies.rsi_step_grid.preset import RsiStepGridPreset
from tiewtrade.strategies.rsi_step_grid.strategy import RsiStepGridStrategy
from tiewtrade.trading.basket import BasketCloseReason, SettledEntries
from tiewtrade.trading.entry_pair import (
    EntryGate,
    EntryGateState,
    EntryPairLifecycle,
)
from tiewtrade.trading.entry_policy import EntryPolicy
from tiewtrade.trading.session_config import (
    MarketType,
    SessionConfig,
    TradeMode,
)
from tiewtrade.trading.symbol_rules import SymbolRules


def test_session_rejects_a_non_paper_spot_configuration() -> None:
    config = session_config(trade_mode=TradeMode.LIVE)

    with pytest.raises(ValueError, match="Paper Spot"):
        PaperSpotSession(
            config,
            MarketDataConfig(symbol="BTCUSDT", timeframe="5m"),
            SymbolRules(
                symbol="BTCUSDT",
                tick_size=Decimal("0.01"),
                step_size=Decimal("0.001"),
                min_notional=Decimal("5"),
            ),
            RsiStepGridPreset.v1(),
        )


def test_session_exposes_immutable_persistence_identity() -> None:
    application = paper_session()

    assert application.identity == PaperSpotSessionIdentity(
        session_id=UUID("00000000-0000-0000-0000-000000000079"),
        symbol="BTCUSDT",
        timeframe="5m",
        preset_version="rsi-step-grid-v1",
    )


def test_pending_intent_fills_at_the_next_completed_candle_open() -> None:
    session = session_config()
    application = PaperSpotSession(
        session,
        MarketDataConfig(symbol="BTCUSDT", timeframe="5m"),
        SymbolRules(
            symbol="BTCUSDT",
            tick_size=Decimal("0.01"),
            step_size=Decimal("0.001"),
            min_notional=Decimal("5"),
        ),
        RsiStepGridPreset.v1(),
    )
    pending = arm_entry_intent(application)
    fill_candle = candle(125, open_price="120", close_price="121")

    snapshot = application.process_completed_candle(
        fill_candle, received_at=fill_candle.close_time
    )

    assert pending.signal_candle.open_time < fill_candle.open_time
    assert snapshot.entry_fill is not None
    assert snapshot.entry_fill.intent_id == pending.intent_id
    assert snapshot.entry_fill.price == Decimal("120")
    assert snapshot.basket_id == uuid5(session.session_id, "basket:1")
    assert snapshot.basket_entry_count == 1
    assert snapshot.take_profit_price == Decimal("129")


def test_snapshot_has_no_basket_id_before_first_entry() -> None:
    application = paper_session()
    first_candle = candle(0, open_price="100", close_price="101")

    snapshot = application.process_completed_candle(
        first_candle,
        received_at=first_candle.close_time,
        backfilled=False,
    )

    assert snapshot.accepted is True
    assert snapshot.basket_id is None


def test_entry_gate_waits_for_a_candle_to_answer_against() -> None:
    application = paper_session()

    assert application.snapshot.entry_gate is None

    first_candle = candle(0, open_price="100", close_price="101")
    snapshot = application.process_completed_candle(
        first_candle,
        received_at=first_candle.close_time,
        backfilled=False,
    )

    assert snapshot.entry_gate == EntryGate(EntryGateState.OPEN)


def test_completed_pair_reports_its_cooldown_in_the_snapshot() -> None:
    application = paper_session()
    first_intent = arm_entry_intent(application)
    first_fill_candle = candle(
        minute_after(first_intent), open_price="120", close_price="121"
    )
    application.process_completed_candle(
        first_fill_candle, received_at=first_fill_candle.close_time
    )
    second_intent = arm_entry_intent(
        application,
        start_minute=minute_after(first_intent) + 5,
        downtrend_candles=60,
    )
    second_fill_candle = candle(
        minute_after(second_intent), open_price="100", close_price="101"
    )

    snapshot = application.process_completed_candle(
        second_fill_candle, received_at=second_fill_candle.close_time
    )

    # A completed Pair holds the gate until the trader lifts it.
    assert snapshot.basket_entry_count == 2
    assert snapshot.entry_gate == EntryGate(EntryGateState.COOLDOWN)
    assert snapshot.entry_completed_pairs == 1


def test_waive_cooldown_opens_the_gate_and_reports_the_waived_pair() -> None:
    application = paper_session()
    first_intent = arm_entry_intent(application)
    first_fill_candle = candle(
        minute_after(first_intent), open_price="120", close_price="121"
    )
    application.process_completed_candle(
        first_fill_candle, received_at=first_fill_candle.close_time
    )
    second_intent = arm_entry_intent(
        application,
        start_minute=minute_after(first_intent) + 5,
        downtrend_candles=60,
    )
    second_fill_candle = candle(
        minute_after(second_intent), open_price="100", close_price="101"
    )
    application.process_completed_candle(
        second_fill_candle, received_at=second_fill_candle.close_time
    )

    snapshot = application.waive_cooldown(datetime(2026, 1, 25, tzinfo=UTC))

    assert snapshot.accepted is True
    assert snapshot.waived_pair is not None
    assert snapshot.entry_gate == EntryGate(EntryGateState.OPEN)


def test_waive_cooldown_rejects_a_session_that_is_not_in_cooldown() -> None:
    application = paper_session()
    first_intent = arm_entry_intent(application)
    first_fill_candle = candle(
        minute_after(first_intent), open_price="120", close_price="121"
    )
    application.process_completed_candle(
        first_fill_candle, received_at=first_fill_candle.close_time
    )

    snapshot = application.waive_cooldown(datetime(2026, 1, 25, tzinfo=UTC))

    assert snapshot.accepted is False
    assert snapshot.waived_pair is None


def test_waive_cooldown_rejects_a_session_with_no_open_basket() -> None:
    application = paper_session()

    snapshot = application.waive_cooldown(datetime(2026, 1, 25, tzinfo=UTC))

    assert snapshot.accepted is False


def test_a_backfilled_candle_opens_no_entry() -> None:
    """A price the market has already left behind must not buy anything.

    Candles recovered after a disconnect are real and must be counted, but
    filling an Entry at one means paying an open that is minutes or hours
    gone — harmless to pretend on Paper, an order at a price that no longer
    exists on Live.
    """
    application = paper_session()
    pending = arm_entry_intent(application)
    fill_candle = candle(minute_after(pending), open_price="120", close_price="121")

    snapshot = application.process_completed_candle(
        fill_candle, received_at=fill_candle.close_time, backfilled=True
    )

    assert snapshot.accepted is True
    assert snapshot.entry_fill is None
    assert snapshot.basket_entry_count == 0


def test_an_intent_a_backfilled_candle_refused_can_arm_again() -> None:
    """The Intent goes back to the strategy rather than waiting in line.

    Left pending, it would fill on the first live candle off a signal read
    from a candle long past. Handed back, the strategy signals again when
    the live market still says the same thing.
    """
    application = paper_session()
    pending = arm_entry_intent(application)
    fill_candle = candle(minute_after(pending), open_price="120", close_price="121")

    snapshot = application.process_completed_candle(
        fill_candle, received_at=fill_candle.close_time, backfilled=True
    )

    # The strategy is free to signal again off this candle; what must not
    # survive is the Intent that was already waiting to be filled.
    assert snapshot.entry_fill is None
    if snapshot.pending_intent is not None:
        assert snapshot.pending_intent.intent_id != pending.intent_id


def test_a_backfilled_candle_still_feeds_the_indicators() -> None:
    """RSI and ATR have to see every candle or they answer for the wrong ones."""
    application = paper_session()
    pending = arm_entry_intent(application)
    before = application.snapshot.entry_gate
    fill_candle = candle(minute_after(pending), open_price="120", close_price="121")

    application.process_completed_candle(
        fill_candle, received_at=fill_candle.close_time, backfilled=True
    )

    # The candle was accepted into the stream, which is what dates the gate.
    assert before is not None
    assert application.snapshot.entry_gate is not None


def test_a_backfilled_candle_still_takes_profit() -> None:
    """A target the market reached while the feed was down was still reached.

    Refusing to sell there would report a Basket the trader does not have,
    and would flatter every result that followed it.
    """
    original = buy_only_paper_session()
    entry_snapshot, next_minute, entry_atr = arm_and_fill_buy_only_entry(original)
    assert entry_snapshot.entry_fill is not None
    assert entry_snapshot.basket_id is not None
    target = entry_snapshot.take_profit_price
    assert target is not None
    entry = entry_snapshot.entry_fill
    resumed = buy_only_paper_session(
        open_basket=OpenBasketState(
            basket_id=entry_snapshot.basket_id,
            entries=(
                OpenBasketEntry(
                    entry_number=1,
                    price=entry.price,
                    quantity=entry.quantity,
                    filled_at=entry.filled_at,
                    atr=entry_atr,
                ),
            ),
            closed_basket_count=0,
        )
    )
    reached = candle(
        next_minute,
        open_price=str(target - Decimal("5")),
        close_price=str(target - Decimal("1")),
        high=str(target),
        low=str(target - Decimal("5")),
    )
    resumed.warm_up_completed_candles(
        [candle(next_minute - 5, open_price="100", close_price="101")],
        received_at=reached.open_time,
    )

    sold = resumed.process_completed_candle(
        reached, received_at=reached.close_time, backfilled=True
    )

    assert sold.closed_basket is not None
    assert sold.closed_basket.close_reason is BasketCloseReason.TAKE_PROFIT


def test_incomplete_candle_is_not_processed() -> None:
    application = paper_session()
    incomplete = candle(0, open_price="100", close_price="101")

    snapshot = application.process_completed_candle(
        incomplete,
        received_at=incomplete.close_time - timedelta(seconds=1),
        backfilled=False,
    )

    assert snapshot.accepted is False
    assert application._indicators._previous_close is None


def test_warm_up_seeds_indicators_without_creating_trade_side_effects() -> None:
    application = paper_session()
    warm_up = indicator_ready_candles_with_entry_signal()
    next_candle = next_candle_after(warm_up[-1])

    application.warm_up_completed_candles(
        warm_up,
        received_at=warm_up[-1].close_time,
    )
    snapshot = application.process_completed_candle(
        next_candle,
        received_at=next_candle.close_time,
        backfilled=False,
    )

    assert snapshot.entry_fill is None
    assert snapshot.closed_basket_count == 0
    assert snapshot.basket_entry_count == 0


def test_take_profit_skips_entry_fill_candle_and_closes_on_following_candle() -> None:
    application = paper_session()
    arm_entry_intent(application)
    entry_candle = candle(125, open_price="120", close_price="121", high="140")

    entry_snapshot = application.process_completed_candle(
        entry_candle, received_at=entry_candle.close_time
    )

    assert entry_snapshot.entry_fill is not None
    assert entry_snapshot.closed_basket is None
    assert entry_snapshot.exit_fill is None
    assert entry_snapshot.closed_basket_count == 0
    assert entry_snapshot.basket_entry_count == 1

    target_candle = candle(130, open_price="125", close_price="130", high="140")
    target_snapshot = application.process_completed_candle(
        target_candle, received_at=target_candle.close_time
    )

    assert target_snapshot.closed_basket is not None
    assert target_snapshot.basket_id == target_snapshot.closed_basket.basket_id
    assert target_snapshot.closed_basket.entry_count == 1
    # A take profit sells the Entry, and the Basket closes because it was
    # the only one left.
    assert len(target_snapshot.entry_exits) == 1
    assert target_snapshot.exit_fill is None
    assert target_snapshot.entry_exits[0].fill.filled_at == target_candle.close_time
    assert target_snapshot.entry_exits[0].closed.entry_number == 1
    assert target_snapshot.closed_basket_count == 1
    assert target_snapshot.basket_entry_count == 0
    assert target_snapshot.take_profit_price is None


def test_stop_loss_closes_the_basket_when_configured() -> None:
    application = buy_only_paper_session()
    entry_snapshot, next_minute, _ = arm_and_fill_buy_only_entry(application)
    assert entry_snapshot.entry_fill is not None
    entry_price = entry_snapshot.entry_fill.price

    stop_loss_candle = candle(
        next_minute,
        open_price=str(entry_price),
        close_price=str(entry_price),
        high=str(entry_price),
        low=str(entry_price - Decimal("10")),
    )
    closed_snapshot = application.process_completed_candle(
        stop_loss_candle, received_at=stop_loss_candle.close_time
    )

    assert closed_snapshot.closed_basket is not None
    assert closed_snapshot.closed_basket.close_reason is BasketCloseReason.STOP_LOSS
    assert closed_snapshot.exit_fill is not None
    assert closed_snapshot.closed_basket_count == 1
    assert closed_snapshot.basket_entry_count == 0


def test_take_profit_fills_the_moment_the_forming_candle_reaches_it() -> None:
    """The price is at the target now; waiting for the close would report a
    fill the market has already moved past."""
    application = buy_only_paper_session()
    entry_snapshot, next_minute, _ = arm_and_fill_buy_only_entry(application)
    assert entry_snapshot.take_profit_price is not None
    target = entry_snapshot.take_profit_price
    forming = candle(
        next_minute,
        open_price=str(target - Decimal("5")),
        close_price=str(target - Decimal("1")),
        high=str(target),
        low=str(target - Decimal("5")),
    )
    observed_at = forming.open_time + timedelta(minutes=2)

    snapshot = application.process_in_progress_candle(forming, observed_at=observed_at)

    assert snapshot.accepted is True
    assert snapshot.closed_basket is not None
    assert snapshot.closed_basket.close_reason is BasketCloseReason.TAKE_PROFIT
    assert len(snapshot.entry_exits) == 1
    # Stamped when it happened, not at a close that has not come.
    assert snapshot.entry_exits[0].fill.filled_at == observed_at
    assert snapshot.basket_entry_count == 0


def test_stop_loss_fills_the_moment_the_forming_candle_reaches_it() -> None:
    application = buy_only_paper_session()
    entry_snapshot, next_minute, _ = arm_and_fill_buy_only_entry(application)
    assert entry_snapshot.entry_fill is not None
    entry_price = entry_snapshot.entry_fill.price
    forming = candle(
        next_minute,
        open_price=str(entry_price),
        close_price=str(entry_price),
        high=str(entry_price),
        low=str(entry_price - Decimal("10")),
    )

    snapshot = application.process_in_progress_candle(
        forming, observed_at=forming.open_time + timedelta(minutes=1)
    )

    assert snapshot.closed_basket is not None
    assert snapshot.closed_basket.close_reason is BasketCloseReason.STOP_LOSS


def test_a_forming_candle_short_of_both_targets_leaves_the_basket_open() -> None:
    application = buy_only_paper_session()
    entry_snapshot, next_minute, _ = arm_and_fill_buy_only_entry(application)
    assert entry_snapshot.entry_fill is not None
    entry_price = entry_snapshot.entry_fill.price
    forming = candle(
        next_minute,
        open_price=str(entry_price),
        close_price=str(entry_price),
        high=str(entry_price + Decimal("1")),
        low=str(entry_price - Decimal("1")),
    )

    snapshot = application.process_in_progress_candle(
        forming, observed_at=forming.open_time + timedelta(minutes=1)
    )

    assert snapshot.accepted is False
    assert snapshot.closed_basket is None
    assert snapshot.basket_entry_count == 1


def test_only_the_candle_forming_after_the_last_close_is_acted_on() -> None:
    """A candle that is not the one now forming is not a live price."""
    application = buy_only_paper_session()
    entry_snapshot, next_minute, _ = arm_and_fill_buy_only_entry(application)
    assert entry_snapshot.take_profit_price is not None
    target = entry_snapshot.take_profit_price
    stale = candle(
        next_minute + 5,
        open_price=str(target),
        close_price=str(target),
        high=str(target),
        low=str(target),
    )

    snapshot = application.process_in_progress_candle(
        stale, observed_at=stale.open_time
    )

    assert snapshot.accepted is False
    assert snapshot.basket_entry_count == 1


def test_no_basket_means_nothing_to_close_between_candles() -> None:
    application = buy_only_paper_session()
    first = candle(0, open_price="100", close_price="101")
    application.process_completed_candle(first, received_at=first.close_time)
    forming = candle(5, open_price="101", close_price="900", high="900", low="101")

    snapshot = application.process_in_progress_candle(
        forming, observed_at=forming.open_time
    )

    assert snapshot.accepted is False
    assert snapshot.exit_fill is None


def test_stop_loss_takes_precedence_over_take_profit_on_the_same_candle() -> None:
    application = buy_only_paper_session()
    entry_snapshot, next_minute, _ = arm_and_fill_buy_only_entry(application)
    assert entry_snapshot.entry_fill is not None
    assert entry_snapshot.take_profit_price is not None
    take_profit_price = entry_snapshot.take_profit_price
    entry_price = entry_snapshot.entry_fill.price

    both_touch_candle = candle(
        next_minute,
        open_price=str(entry_price),
        close_price=str(entry_price),
        high=str(take_profit_price + Decimal("5")),
        low=str(entry_price - Decimal("10")),
    )
    closed_snapshot = application.process_completed_candle(
        both_touch_candle, received_at=both_touch_candle.close_time
    )

    assert closed_snapshot.closed_basket is not None
    assert closed_snapshot.closed_basket.close_reason is BasketCloseReason.STOP_LOSS


def test_sell_candle_cannot_signal_the_next_entry() -> None:
    """A re-entry has to wait for the candle after the one that sold."""
    application = buy_only_paper_session()
    entry_snapshot, next_minute, _ = arm_and_fill_buy_only_entry(application)
    assert entry_snapshot.entry_fill is not None
    assert entry_snapshot.take_profit_price is not None
    target = entry_snapshot.take_profit_price

    sell_candle = candle(
        next_minute,
        open_price=str(target),
        close_price=str(target + Decimal("2")),
        high=str(target + Decimal("5")),
    )
    sell_snapshot = application.process_completed_candle(
        sell_candle, received_at=sell_candle.close_time
    )

    assert sell_snapshot.entry_exits
    assert sell_snapshot.pending_intent is None

    following_candle = candle(
        next_minute + 5,
        open_price=str(target + Decimal("2")),
        close_price=str(target + Decimal("4")),
    )
    following_snapshot = application.process_completed_candle(
        following_candle, received_at=following_candle.close_time
    )

    assert following_snapshot.pending_intent is not None
    assert following_snapshot.pending_intent.signal_candle == following_candle


def test_candle_that_sold_between_ticks_cannot_also_buy() -> None:
    """An exit inside a forming candle still owns that candle.

    The pending Entry would fill at that candle's open — before the exit that
    had already happened — putting a Buy and a Sell on the same candle and
    re-entering on the back of its own exit.
    """
    application = fast_test_paper_session()
    entry_snapshot, next_minute = arm_and_fill_fast_test_entry(application)
    assert entry_snapshot.take_profit_price is not None
    target = entry_snapshot.take_profit_price
    armed, next_minute = arm_next_fast_test_entry(
        application, entry_snapshot, from_minute=next_minute
    )
    assert armed.pending_intent is not None

    forming = candle(
        next_minute,
        open_price=str(target - Decimal("5")),
        close_price=str(target - Decimal("1")),
        high=str(target),
        low=str(target - Decimal("5")),
    )
    sold = application.process_in_progress_candle(
        forming, observed_at=forming.open_time + timedelta(minutes=2)
    )
    assert sold.closed_basket is not None

    completed = application.process_completed_candle(
        forming, received_at=forming.close_time
    )

    assert completed.entry_fill is None
    assert completed.basket_entry_count == 0
    assert completed.pending_intent is None

    following = candle(
        next_minute + 5,
        open_price=str(forming.close),
        close_price=str(forming.close + Decimal("1")),
    )
    resumed = application.process_completed_candle(
        following, received_at=following.close_time
    )

    assert resumed.entry_fill is None
    assert resumed.pending_intent is not None
    assert resumed.pending_intent.signal_candle == following


def test_fast_test_entries_land_three_candles_apart() -> None:
    """A flat market cannot space Entries, so the preset has to space them."""
    application = fast_test_paper_session()
    entry_snapshot, next_minute = arm_and_fill_fast_test_entry(application)
    assert entry_snapshot.entry_fill is not None
    first_filled_at = entry_snapshot.entry_fill.filled_at
    flat = str(entry_snapshot.entry_fill.price)

    filled_at: list[datetime] = []
    for offset in range(0, 25, 5):
        flat_candle = candle(
            next_minute + offset,
            open_price=flat,
            close_price=flat,
            high=flat,
            low=flat,
        )
        snapshot = application.process_completed_candle(
            flat_candle, received_at=flat_candle.close_time
        )
        if snapshot.entry_fill is not None:
            filled_at.append(snapshot.entry_fill.filled_at)

    assert filled_at == [first_filled_at + timedelta(minutes=15)]


def test_a_reopened_basket_holds_what_the_previous_runtime_left() -> None:
    """Replaying the Entries states the Basket, rather than restating it."""
    original = buy_only_paper_session()
    entry_snapshot, _, entry_atr = arm_and_fill_buy_only_entry(original)
    assert entry_snapshot.entry_fill is not None
    assert entry_snapshot.basket_id is not None
    entry = entry_snapshot.entry_fill
    assert entry_snapshot.pending_intent is None

    resumed = buy_only_paper_session(
        open_basket=OpenBasketState(
            basket_id=entry_snapshot.basket_id,
            entries=(
                OpenBasketEntry(
                    entry_number=1,
                    price=entry.price,
                    quantity=entry.quantity,
                    filled_at=entry.filled_at,
                    atr=entry_atr,
                ),
            ),
            closed_basket_count=2,
        )
    )

    snapshot = resumed.snapshot
    assert snapshot.basket_id == entry_snapshot.basket_id
    assert snapshot.basket_entry_count == 1
    assert snapshot.take_profit_price == entry_snapshot.take_profit_price
    assert snapshot.closed_basket_count == 2


def test_a_reopened_basket_can_be_closed_by_the_new_runtime() -> None:
    original = buy_only_paper_session()
    entry_snapshot, next_minute, entry_atr = arm_and_fill_buy_only_entry(original)
    assert entry_snapshot.entry_fill is not None
    assert entry_snapshot.basket_id is not None
    assert entry_snapshot.take_profit_price is not None
    entry = entry_snapshot.entry_fill
    target = entry_snapshot.take_profit_price

    resumed = buy_only_paper_session(
        open_basket=OpenBasketState(
            basket_id=entry_snapshot.basket_id,
            entries=(
                OpenBasketEntry(
                    entry_number=2,
                    price=entry.price,
                    quantity=entry.quantity,
                    filled_at=entry.filled_at,
                    atr=entry_atr,
                ),
            ),
            closed_basket_count=0,
        )
    )
    reached = candle(
        next_minute,
        open_price=str(target - Decimal("5")),
        close_price=str(target - Decimal("1")),
        high=str(target),
        low=str(target - Decimal("5")),
    )
    resumed.warm_up_completed_candles(
        [candle(next_minute - 5, open_price="100", close_price="101")],
        received_at=reached.open_time,
    )

    closed = resumed.process_in_progress_candle(
        reached, observed_at=reached.open_time + timedelta(minutes=1)
    )

    assert closed.closed_basket is not None
    assert closed.closed_basket.basket_id == entry_snapshot.basket_id
    assert closed.closed_basket.close_reason is BasketCloseReason.TAKE_PROFIT
    assert closed.closed_basket_count == 1


def test_a_reopened_basket_counts_its_entries_against_the_pair_gate() -> None:
    """The Entry Pair the Basket already used is not handed back."""
    session_id = UUID("00000000-0000-0000-0000-000000000079")
    filled_at = datetime(2026, 1, 1, tzinfo=UTC)
    resumed = buy_only_paper_session(
        open_basket=OpenBasketState(
            basket_id=uuid5(session_id, "basket:1"),
            entries=(
                OpenBasketEntry(
                    entry_number=3,
                    price=Decimal("100"),
                    quantity=Decimal("1"),
                    filled_at=filled_at,
                    atr=Decimal("2.5"),
                ),
                OpenBasketEntry(
                    entry_number=4,
                    price=Decimal("98"),
                    quantity=Decimal("1"),
                    filled_at=filled_at + timedelta(minutes=5),
                    atr=Decimal("2.5"),
                ),
            ),
            closed_basket_count=0,
            settled=SettledEntries(count=2),
        )
    )

    gate = resumed._lifecycle.entry_gate()

    assert resumed.snapshot.basket_entry_count == 2
    assert gate.state is EntryGateState.COOLDOWN


def fill_second_fast_test_entry(
    application: PaperSpotSession,
    first: PaperSpotSessionSnapshot,
    from_minute: int,
    *,
    below: str = "20",
) -> tuple[PaperSpotSessionSnapshot, int]:
    """Arm and fill a second Entry at a price well below the first.

    An Entry that paid less has a nearer target, which is the whole point of
    measuring a target from the price its own Entry paid.
    """
    armed, minute = arm_next_fast_test_entry(
        application, first, from_minute=from_minute
    )
    assert armed.pending_intent is not None
    assert first.entry_fill is not None
    cheap = first.entry_fill.price - Decimal(below)
    entry_candle = candle(
        minute,
        open_price=str(cheap),
        close_price=str(cheap + Decimal("1")),
        high=str(cheap + Decimal("1")),
        low=str(cheap - Decimal("1")),
    )
    snapshot = application.process_completed_candle(
        entry_candle, received_at=entry_candle.close_time
    )
    assert snapshot.entry_fill is not None
    return snapshot, minute + 5


def test_one_entry_takes_profit_while_the_other_stays_open() -> None:
    """Each Entry is sold at its own target, not at one averaged over both."""
    application = fast_test_paper_session()
    first, next_minute = arm_and_fill_fast_test_entry(application)
    first_target = first.take_profit_price
    assert first_target is not None

    second, next_minute = fill_second_fast_test_entry(application, first, next_minute)
    second_target = second.entry_take_profit_price

    assert second.basket_entry_count == 2
    assert second.entry_number == 2
    assert second_target is not None
    assert second_target < first_target
    # The Basket reports the nearest of the two, which is the cheap Entry's.
    assert second.take_profit_price == second_target

    reached = candle(
        next_minute,
        open_price=str(second_target - Decimal("2")),
        close_price=str(second_target - Decimal("1")),
        high=str(second_target),
        low=str(second_target - Decimal("2")),
    )
    sold = application.process_completed_candle(reached, received_at=reached.close_time)

    assert [item.closed.entry_number for item in sold.entry_exits] == [2]
    assert sold.closed_basket is None
    assert sold.basket_entry_count == 1
    # The Entry that stayed keeps the target it was measured for.
    assert sold.take_profit_price == first_target


def test_an_entry_that_sold_at_target_lifts_the_cooldown_it_left() -> None:
    """A Cooldown says the Basket is holding both Entries of a Pair at once.

    The market taking profit on one of them ends that, so the gate the Pair
    closed opens again without the trader having to say anything.
    """
    application = fast_test_paper_session()
    first, next_minute = arm_and_fill_fast_test_entry(application)
    second, next_minute = fill_second_fast_test_entry(application, first, next_minute)
    second_target = second.entry_take_profit_price
    assert second_target is not None
    assert second.entry_gate is not None
    assert second.entry_gate.state is EntryGateState.COOLDOWN

    reached = candle(
        next_minute,
        open_price=str(second_target - Decimal("2")),
        close_price=str(second_target - Decimal("1")),
        high=str(second_target),
        low=str(second_target - Decimal("2")),
    )
    sold = application.process_completed_candle(reached, received_at=reached.close_time)

    assert sold.entry_gate is not None
    assert sold.entry_gate.state is EntryGateState.OPEN


def test_a_reopened_basket_leaves_out_the_entries_that_already_sold() -> None:
    """An Entry sold at its own target is no longer part of the position."""
    session_id = UUID("00000000-0000-0000-0000-000000000079")
    filled_at = datetime(2026, 1, 1, tzinfo=UTC)
    resumed = buy_only_paper_session(
        open_basket=OpenBasketState(
            basket_id=uuid5(session_id, "basket:1"),
            entries=(
                OpenBasketEntry(
                    entry_number=2,
                    price=Decimal("98"),
                    quantity=Decimal("1"),
                    filled_at=filled_at,
                    atr=Decimal("2"),
                ),
            ),
            closed_basket_count=0,
        )
    )

    snapshot = resumed.snapshot

    # Entry 1 sold before the restart; the number it used is not handed back.
    assert snapshot.basket_entry_count == 1
    assert [entry.entry_number for entry in resumed.open_entries] == [2]
    assert resumed._basket is not None
    assert resumed._basket.entry_sequence == 2


def test_minimum_notional_rejection_releases_strategy_to_create_a_new_intent() -> None:
    application = paper_session(min_notional=Decimal("400"))
    first_intent = arm_entry_intent(application)
    rejected_candle = candle(125, open_price="120", close_price="121")

    rejected = application.process_completed_candle(
        rejected_candle, received_at=rejected_candle.close_time
    )

    assert rejected.entry_fill is None
    assert rejected.pending_intent is not None
    assert rejected.pending_intent.intent_id != first_intent.intent_id
    assert rejected.pending_intent.signal_candle == rejected_candle


def test_entry_transition_is_atomic_when_lifecycle_rejects_fill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = paper_session()
    first_intent = arm_entry_intent(application)
    first_fill_candle = candle(
        minute_after(first_intent),
        open_price="120",
        close_price="121",
    )
    first_fill = application.process_completed_candle(
        first_fill_candle,
        received_at=first_fill_candle.close_time,
        backfilled=False,
    )
    assert first_fill.basket_entry_count == 1
    assert first_fill.failed_closed is False

    second_intent = arm_entry_intent(
        application,
        start_minute=minute_after(first_intent) + 5,
        downtrend_candles=60,
    )
    second_fill_candle = candle(
        minute_after(second_intent),
        open_price="100",
        close_price="101",
    )

    def reject_fill(self: EntryPairLifecycle) -> None:
        raise ValueError("entry is blocked by pair lifecycle")

    monkeypatch.setattr(EntryPairLifecycle, "record_fill", reject_fill)

    with pytest.raises(PaperSpotSessionError, match="execution failed") as captured:
        application.process_completed_candle(
            second_fill_candle,
            received_at=second_fill_candle.close_time,
            backfilled=False,
        )

    assert isinstance(captured.value.__cause__, ValueError)
    assert str(captured.value.__cause__) == "entry is blocked by pair lifecycle"
    assert application.snapshot.state is PaperSpotSessionState.FAILED_CLOSED
    assert application.snapshot.failure_reason is PaperSpotFailureReason.EXECUTION_ERROR
    # Said as a plain fact, because what reads it next feeds Spot and Futures
    # alike and cannot ask either one about its own state enum.
    assert application.snapshot.failed_closed is True
    assert application.snapshot.pending_intent is None
    assert application.snapshot.basket_entry_count == 1
    assert application._lifecycle.entry_count == 1


def test_late_entry_failure_does_not_commit_or_persist_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = paper_session()
    pending = arm_entry_intent(application)
    persistent, history = persistent_spot_session(application)
    entry_candle = candle(
        minute_after(pending),
        open_price="120",
        close_price="121",
    )

    def raise_indicator_error(*args: object, **kwargs: object) -> None:
        raise ValueError("indicator transition failed")

    monkeypatch.setattr(WilderIndicators, "update", raise_indicator_error)

    with pytest.raises(PaperSpotSessionError, match="execution failed"):
        persistent.process_completed_candle(
            entry_candle,
            received_at=entry_candle.close_time,
            backfilled=False,
        )

    assert application.snapshot.state is PaperSpotSessionState.FAILED_CLOSED
    assert application.snapshot.basket_id is None
    assert application.snapshot.basket_entry_count == 0
    history.record_entry.assert_not_called()
    history.record_close.assert_not_called()


def test_late_close_failure_keeps_open_basket_and_does_not_persist_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = paper_session()
    pending = arm_entry_intent(application)
    persistent, history = persistent_spot_session(application)
    entry_candle = candle(
        minute_after(pending),
        open_price="120",
        close_price="121",
    )
    entry = persistent.process_completed_candle(
        entry_candle,
        received_at=entry_candle.close_time,
        backfilled=False,
    )
    assert entry.session.basket_entry_count == 1

    def raise_indicator_error(*args: object, **kwargs: object) -> None:
        raise ValueError("indicator transition failed after close")

    monkeypatch.setattr(WilderIndicators, "update", raise_indicator_error)
    close_candle = candle(
        minute_after(pending) + 5,
        open_price="125",
        close_price="130",
        high="1000",
    )

    with pytest.raises(PaperSpotSessionError, match="execution failed"):
        persistent.process_completed_candle(
            close_candle,
            received_at=close_candle.close_time,
            backfilled=False,
        )

    assert application.snapshot.state is PaperSpotSessionState.FAILED_CLOSED
    assert application.snapshot.basket_id == entry.session.basket_id
    assert application.snapshot.basket_entry_count == 1
    history.record_entry.assert_called_once()
    history.record_close.assert_not_called()


def test_strategy_callback_failure_does_not_commit_candidate_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = paper_session()
    first_intent = arm_entry_intent(application)
    first_fill_candle = candle(
        minute_after(first_intent),
        open_price="120",
        close_price="121",
    )
    application.process_completed_candle(
        first_fill_candle,
        received_at=first_fill_candle.close_time,
        backfilled=False,
    )
    second_intent = arm_entry_intent(
        application,
        start_minute=minute_after(first_intent) + 5,
        downtrend_candles=60,
    )
    original_strategy = cast(RsiStepGridStrategy, application._strategy)
    original_callback = RsiStepGridStrategy.on_entry_filled

    def mutate_then_raise(
        self: RsiStepGridStrategy,
        intent_id: str,
    ) -> None:
        original_callback(self, intent_id)
        raise ValueError("strategy transition failed")

    monkeypatch.setattr(RsiStepGridStrategy, "on_entry_filled", mutate_then_raise)
    second_fill_candle = candle(
        minute_after(second_intent),
        open_price="100",
        close_price="101",
    )

    with pytest.raises(PaperSpotSessionError) as captured:
        application.process_completed_candle(
            second_fill_candle,
            received_at=second_fill_candle.close_time,
            backfilled=False,
        )

    assert str(captured.value.__cause__) == "strategy transition failed"
    assert application.snapshot.basket_entry_count == 1
    assert application._lifecycle.entry_count == 1
    assert original_strategy._pending_intent == second_intent
    assert application._strategy is not original_strategy


def test_failed_closed_session_rejects_later_candles_and_warm_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = paper_session()
    pending = arm_entry_intent(application)
    failed_candle = candle(
        minute_after(pending),
        open_price="120",
        close_price="121",
    )

    def raise_execution_error(*args: object, **kwargs: object) -> None:
        raise ValueError("broken execution invariant")

    monkeypatch.setattr(application._executor, "fill_entry", raise_execution_error)
    with pytest.raises(PaperSpotSessionError, match="execution failed"):
        application.process_completed_candle(
            failed_candle,
            received_at=failed_candle.close_time,
            backfilled=False,
        )

    before = application.snapshot
    following = candle(
        minute_after(pending) + 5,
        open_price="121",
        close_price="122",
    )
    after = application.process_completed_candle(
        following,
        received_at=following.close_time,
        backfilled=False,
    )

    assert after.accepted is False
    assert after.state is before.state
    assert after.basket_id == before.basket_id
    assert after.basket_entry_count == before.basket_entry_count
    with pytest.raises(PaperSpotSessionError, match="not active"):
        application.warm_up_completed_candles(
            [following],
            received_at=following.close_time,
        )


def test_closed_two_entry_basket_resets_lifecycle_for_a_new_basket() -> None:
    application = paper_session()
    first_intent = arm_entry_intent(application)
    first_fill_candle = candle(
        minute_after(first_intent), open_price="120", close_price="121"
    )
    application.process_completed_candle(
        first_fill_candle, received_at=first_fill_candle.close_time
    )

    second_intent = arm_entry_intent(
        application,
        start_minute=minute_after(first_intent) + 5,
        downtrend_candles=60,
    )
    second_fill_candle = candle(
        minute_after(second_intent), open_price="100", close_price="101"
    )
    second_fill = application.process_completed_candle(
        second_fill_candle, received_at=second_fill_candle.close_time
    )
    assert second_fill.basket_entry_count == 2

    close_candle = candle(
        minute_after(second_intent) + 5,
        open_price="100",
        close_price="101",
        high="1000",
    )
    closed = application.process_completed_candle(
        close_candle, received_at=close_candle.close_time
    )
    assert closed.closed_basket is not None
    assert closed.closed_basket.entry_count == 2
    # One candle ran past both targets, so both Entries sold on it — the
    # cheaper Entry first, because the market reaches its target first — and
    # the Basket closed with the last of them.
    assert [item.closed.entry_number for item in closed.entry_exits] == [2, 1]
    assert closed.closed_basket_count == 1

    new_intent = arm_entry_intent(
        application,
        start_minute=minute_after(second_intent) + 10,
        downtrend_candles=60,
    )
    new_fill_candle = candle(
        minute_after(new_intent), open_price="120", close_price="121"
    )
    new_fill = application.process_completed_candle(
        new_fill_candle, received_at=new_fill_candle.close_time
    )

    assert new_fill.entry_fill is not None
    assert new_fill.basket_entry_count == 1
    assert new_fill.exit_fill is None
    assert new_fill.closed_basket is None
    assert new_fill.closed_basket_count == 1

    second_close_candle = candle(
        minute_after(new_intent) + 5,
        open_price="100",
        close_price="101",
        high="1000",
    )
    second_closed = application.process_completed_candle(
        second_close_candle, received_at=second_close_candle.close_time
    )

    assert second_closed.closed_basket is not None
    assert second_closed.entry_exits
    assert second_closed.closed_basket_count == 2


def paper_session(*, min_notional: Decimal = Decimal("5")) -> PaperSpotSession:
    return PaperSpotSession(
        session_config(),
        MarketDataConfig(symbol="BTCUSDT", timeframe="5m"),
        SymbolRules(
            symbol="BTCUSDT",
            tick_size=Decimal("0.01"),
            step_size=Decimal("0.001"),
            min_notional=min_notional,
        ),
        RsiStepGridPreset.v1(),
    )


def _two_entry_basket(*, waived_pair: int | None = 1) -> OpenBasketState:
    """Entries at 100 and 110, a day apart so the Entry Pair lets both in.

    With an ATR of 2 and a multiplier of 3 their targets are 106 and 116, so a
    price between them reaches one and leaves the other holding. The pair
    these two fills complete is waived by default, so a manual sale is not
    blocked by the Cooldown they leave behind — pass `waived_pair=None` to
    get the basket exactly as its Cooldown stands.
    """
    return OpenBasketState(
        basket_id=UUID("00000000-0000-0000-0000-0000000000c1"),
        entries=(
            OpenBasketEntry(
                entry_number=1,
                price=Decimal("100"),
                quantity=Decimal("2"),
                filled_at=datetime(2026, 8, 1, 12, tzinfo=UTC),
                atr=Decimal("2"),
            ),
            OpenBasketEntry(
                entry_number=2,
                price=Decimal("110"),
                quantity=Decimal("2"),
                filled_at=datetime(2026, 8, 2, 12, tzinfo=UTC),
                atr=Decimal("2"),
            ),
        ),
        closed_basket_count=0,
        waived_pair=waived_pair,
    )


def _four_entry_basket() -> OpenBasketState:
    """Two Pairs: the first already resolved, the second is what is cooling down.

    A Basket holding four Entries could only have gotten here by the first
    Pair's Cooldown having been lifted before the second Pair could form — so
    Entries 1 and 2 read Open even though the Basket's own gate is Cooldown.
    """
    return OpenBasketState(
        basket_id=UUID("00000000-0000-0000-0000-0000000000c2"),
        entries=tuple(
            OpenBasketEntry(
                entry_number=number,
                price=Decimal("100") + Decimal(number) * Decimal("10"),
                quantity=Decimal("1"),
                filled_at=datetime(2026, 8, number, 12, tzinfo=UTC),
                atr=Decimal("2"),
            )
            for number in range(1, 5)
        ),
        closed_basket_count=0,
        waived_pair=None,
    )


def test_one_entry_is_sold_on_demand_and_the_rest_left_holding() -> None:
    """The sale a target makes, at a price nobody promised.

    The Entry hands its place back the way a target sale does, so what is left
    is a Basket that still holds the other Entry — not a Position closed
    because one row of it was.
    """
    session = buy_only_paper_session(open_basket=_two_entry_basket())

    snapshot = session.close_entry(
        1,
        price=Decimal("105.678"),
        observed_at=datetime(2026, 8, 3, tzinfo=UTC),
    )

    assert snapshot.accepted is True
    assert len(snapshot.entry_exits) == 1
    sold = snapshot.entry_exits[0]
    assert sold.fill.entry_number == 1
    # Floored to the tick, against the seller, the way every sale here rounds.
    assert sold.fill.price == Decimal("105.67")
    assert sold.closed.realized_pnl == Decimal("11.34")
    assert snapshot.closed_basket is None
    assert snapshot.basket_entry_count == 1
    assert [entry.entry_number for entry in session.open_entries] == [2]


def test_selling_the_last_entry_closes_the_basket_as_a_manual_close() -> None:
    session = buy_only_paper_session(open_basket=_two_entry_basket())
    session.close_entry(
        1, price=Decimal("105"), observed_at=datetime(2026, 8, 3, tzinfo=UTC)
    )

    snapshot = session.close_entry(
        2, price=Decimal("120"), observed_at=datetime(2026, 8, 4, tzinfo=UTC)
    )

    assert snapshot.closed_basket is not None
    assert snapshot.closed_basket.close_reason is BasketCloseReason.MANUAL
    assert snapshot.basket_entry_count == 0
    assert session.open_entries == ()


def test_an_entry_already_gone_sells_nothing_twice() -> None:
    """The row a hand reaches for redraws on every tick.

    An unaccepted snapshot is what a stale row gets, rather than a second sale
    of an Entry the Basket has already let go, or an error over a Position
    that is doing nothing wrong.
    """
    session = buy_only_paper_session(open_basket=_two_entry_basket())
    session.close_entry(
        1, price=Decimal("105"), observed_at=datetime(2026, 8, 3, tzinfo=UTC)
    )

    again = session.close_entry(
        1, price=Decimal("105"), observed_at=datetime(2026, 8, 3, tzinfo=UTC)
    )
    unknown = session.close_entry(
        9, price=Decimal("105"), observed_at=datetime(2026, 8, 3, tzinfo=UTC)
    )

    assert again.accepted is False
    assert unknown.accepted is False
    assert [entry.entry_number for entry in session.open_entries] == [2]


def test_manual_close_ends_the_cooldown_the_pair_it_sold_from_left() -> None:
    """The trader can always sell their way out of a Cooldown.

    A Cooldown holds while the Basket holds both Entries of a Pair, so selling
    either of them ends it — refusing this sale left a trader with a Cooldown
    they could only leave by waiving it.
    """
    session = buy_only_paper_session(open_basket=_two_entry_basket(waived_pair=None))

    snapshot = session.close_entry(
        1, price=Decimal("105"), observed_at=datetime(2026, 8, 3, tzinfo=UTC)
    )

    assert snapshot.accepted is True
    assert session._lifecycle.entry_gate().state is EntryGateState.OPEN
    assert [entry.entry_number for entry in session.open_entries] == [2]


def test_manual_close_succeeds_once_a_cooldown_is_waived() -> None:
    session = buy_only_paper_session(open_basket=_two_entry_basket(waived_pair=None))
    session.waive_cooldown(datetime(2026, 8, 3, tzinfo=UTC))

    snapshot = session.close_entry(
        1, price=Decimal("105"), observed_at=datetime(2026, 8, 3, tzinfo=UTC)
    )

    assert snapshot.accepted is True
    assert [entry.entry_number for entry in session.open_entries] == [2]


def test_manual_close_from_an_earlier_pair_leaves_a_later_cooldown_standing() -> None:
    """A sale ends the Cooldown of its own Pair, not of the one after it.

    Entries 1 and 2 are the first Pair, resolved long ago; 3 and 4 are the
    Pair now cooling down. Selling Entry 1 does not free a gate the Pair after
    it closed — the Basket is still holding both of Entries 3 and 4.
    """
    session = buy_only_paper_session(open_basket=_four_entry_basket())

    resolved_pair = session.close_entry(
        1, price=Decimal("105"), observed_at=datetime(2026, 8, 5, tzinfo=UTC)
    )

    assert resolved_pair.accepted is True
    assert session._lifecycle.entry_gate().state is EntryGateState.COOLDOWN
    assert [entry.entry_number for entry in session.open_entries] == [2, 3, 4]

    cooling_pair = session.close_entry(
        3, price=Decimal("135"), observed_at=datetime(2026, 8, 5, tzinfo=UTC)
    )

    assert cooling_pair.accepted is True
    assert session._lifecycle.entry_gate().state is EntryGateState.OPEN


def test_a_session_holding_nothing_has_no_entry_to_sell() -> None:
    session = buy_only_paper_session()

    snapshot = session.close_entry(
        1, price=Decimal("105"), observed_at=datetime(2026, 8, 3, tzinfo=UTC)
    )

    assert snapshot.accepted is False


def buy_only_paper_session(
    *,
    min_notional: Decimal = Decimal("5"),
    open_basket: OpenBasketState | None = None,
) -> PaperSpotSession:
    return PaperSpotSession(
        session_config(preset_version="rsi-buy-only-v1"),
        MarketDataConfig(symbol="BTCUSDT", timeframe="5m"),
        SymbolRules(
            symbol="BTCUSDT",
            tick_size=Decimal("0.01"),
            step_size=Decimal("0.001"),
            min_notional=min_notional,
        ),
        RsiBuyOnlyPreset.v1(),
        open_basket=open_basket,
    )


def fast_test_paper_session(
    *, min_notional: Decimal = Decimal("5")
) -> PaperSpotSession:
    return PaperSpotSession(
        session_config(preset_version="fast-test-v1"),
        MarketDataConfig(symbol="BTCUSDT", timeframe="5m"),
        SymbolRules(
            symbol="BTCUSDT",
            tick_size=Decimal("0.01"),
            step_size=Decimal("0.001"),
            min_notional=min_notional,
        ),
        FastTestPreset.v1(),
    )


def arm_and_fill_fast_test_entry(
    application: PaperSpotSession,
) -> tuple[PaperSpotSessionSnapshot, int]:
    """Feed a fast-test session until its indicators are warm, then fill.

    Returns the fill snapshot and the next unused candle minute offset.
    """
    close = Decimal("100")
    minute = 0
    snapshot: PaperSpotSessionSnapshot | None = None
    for _ in range(FastTestPreset.v1().minimum_warm_up_candles + 5):
        candle_value = candle(minute, open_price=str(close), close_price=str(close + 1))
        snapshot = application.process_completed_candle(
            candle_value, received_at=candle_value.close_time
        )
        minute += 5
        if snapshot.pending_intent is not None:
            break
        close += 1
    else:
        raise AssertionError("expected a pending entry intent")

    fill_candle = candle(minute, open_price=str(close + 1), close_price=str(close + 2))
    snapshot = application.process_completed_candle(
        fill_candle, received_at=fill_candle.close_time
    )
    assert snapshot.entry_fill is not None
    return snapshot, minute + 5


def arm_next_fast_test_entry(
    application: PaperSpotSession,
    filled: PaperSpotSessionSnapshot,
    *,
    from_minute: int,
) -> tuple[PaperSpotSessionSnapshot, int]:
    """Wait out the candles a fast-test session keeps between two Entries.

    Feeds candles flat at the Entry Price, which reach no target, until the
    next Intent is pending. Returns that snapshot and the next unused minute.
    """
    assert filled.entry_fill is not None
    flat = str(filled.entry_fill.price)
    minute = from_minute
    for _ in range(10):
        flat_candle = candle(
            minute, open_price=flat, close_price=flat, high=flat, low=flat
        )
        snapshot = application.process_completed_candle(
            flat_candle, received_at=flat_candle.close_time
        )
        minute += 5
        if snapshot.pending_intent is not None:
            return snapshot, minute
    raise AssertionError("expected a pending entry intent")


def arm_and_fill_buy_only_entry(
    application: PaperSpotSession,
) -> tuple[PaperSpotSessionSnapshot, int, Decimal]:
    """Feed an uptrend into a Buy Only session until entry fills.

    Returns the fill snapshot, the next unused candle minute offset, and the
    ATR the Entry was read against, which its take profit was measured from.
    """
    close = Decimal("100")
    minute = 0
    snapshot: PaperSpotSessionSnapshot | None = None
    for _ in range(30):
        candle_value = candle(minute, open_price=str(close), close_price=str(close + 1))
        snapshot = application.process_completed_candle(
            candle_value, received_at=candle_value.close_time
        )
        minute += 5
        if snapshot.pending_intent is not None:
            break
        close += 1
    else:
        raise AssertionError("expected a pending entry intent")

    assert snapshot.pending_intent is not None
    entry_atr = snapshot.pending_intent.atr
    fill_candle = candle(minute, open_price=str(close + 1), close_price=str(close + 2))
    snapshot = application.process_completed_candle(
        fill_candle, received_at=fill_candle.close_time
    )
    assert snapshot.entry_fill is not None
    assert snapshot.entry_atr == entry_atr
    return snapshot, minute + 5, entry_atr


def persistent_spot_session(
    application: PaperSpotSession,
) -> tuple[SessionPersistenceCoordinator[PaperSpotSessionSnapshot], Mock]:
    history_mock: Mock = create_autospec(PaperSpotSQLiteHistory, instance=True)
    history_mock.session_identity = application.identity
    history = cast(PaperSpotSQLiteHistory, history_mock)
    return create_persistent_paper_spot_session(application, history), history_mock


def arm_entry_intent(
    application: PaperSpotSession,
    *,
    start_minute: int = 0,
    downtrend_candles: int = 15,
) -> EntryIntent:
    close = Decimal("100")
    downtrend_end = start_minute + (downtrend_candles * 5)
    for minute in range(start_minute, downtrend_end, 5):
        candle_value = candle(minute, open_price=str(close + 1), close_price=str(close))
        application.process_completed_candle(
            candle_value, received_at=candle_value.close_time
        )
        close -= Decimal("1")

    for minute in range(downtrend_end, downtrend_end + 125, 5):
        candle_value = candle(minute, open_price=str(close), close_price=str(close + 1))
        snapshot = application.process_completed_candle(
            candle_value, received_at=candle_value.close_time
        )
        if snapshot.pending_intent is not None:
            return snapshot.pending_intent
        close += Decimal("1")

    raise AssertionError("expected a pending entry intent")


def candle(
    minute: int,
    *,
    open_price: str,
    close_price: str,
    high: str | None = None,
    low: str | None = None,
) -> Candle:
    open_decimal = Decimal(open_price)
    close_decimal = Decimal(close_price)
    return Candle(
        symbol="BTCUSDT",
        timeframe="5m",
        open_time=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=minute),
        open=open_decimal,
        high=(
            max(open_decimal, close_decimal) + Decimal("1")
            if high is None
            else Decimal(high)
        ),
        low=(
            min(open_decimal, close_decimal) - Decimal("1")
            if low is None
            else Decimal(low)
        ),
        close=close_decimal,
    )


def indicator_ready_candles_with_entry_signal() -> list[Candle]:
    candles: list[Candle] = []
    close = Decimal("100")

    for minute in range(0, 75, 5):
        candles.append(
            candle(minute, open_price=str(close + 1), close_price=str(close))
        )
        close -= Decimal("1")

    for minute in range(75, 125, 5):
        candles.append(
            candle(minute, open_price=str(close), close_price=str(close + 1))
        )
        close += Decimal("1")

    return candles


def next_candle_after(previous: Candle) -> Candle:
    next_open_time = previous.open_time + timedelta(minutes=5)
    return Candle(
        symbol=previous.symbol,
        timeframe=previous.timeframe,
        open_time=next_open_time,
        open=previous.close,
        high=previous.close + Decimal("2"),
        low=previous.close - Decimal("1"),
        close=previous.close + Decimal("1"),
    )


def minute_after(intent: EntryIntent) -> int:
    origin = datetime(2026, 1, 1, tzinfo=UTC)
    return int((intent.signal_candle.open_time - origin).total_seconds() / 60) + 5


def session_config(**overrides: object) -> SessionConfig:
    values: dict[str, object] = {
        "session_id": UUID("00000000-0000-0000-0000-000000000079"),
        "preset_version": "rsi-step-grid-v1",
        "market_type": MarketType.SPOT,
        "trade_mode": TradeMode.PAPER,
        # 1000 over four Entries was 250 an Entry: the same size, stated the
        # way the form states it now.
        "capital_per_entry": Decimal("250"),
        "entry_policy": EntryPolicy(max_entries=4),
    }
    values.update(overrides)
    return SessionConfig(**values)  # type: ignore[arg-type]
