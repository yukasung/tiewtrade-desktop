from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Lock
from uuid import UUID, uuid4

import pytest

from tiewtrade.application.paper_session_notices import (
    basket_closed_notice,
    entry_opened_notice,
    snapshot_notices,
)
from tiewtrade.application.paper_session_setup import ConfiguredPaperSession
from tiewtrade.application.paper_session_sink import (
    PaperRuntimeSink,
    PaperSessionFailedClosedError,
)
from tiewtrade.application.session_notice import (
    BasketClosedNotice,
    CooldownCancelledNotice,
    CooldownEndedNotice,
    CooldownStartedNotice,
    EntryOpenedNotice,
    SessionNotice,
)
from tiewtrade.application.session_persistence import (
    PersistenceState,
    PersistentSessionSnapshot,
)
from tiewtrade.application.trading_workspace import BasketSnapshot
from tiewtrade.execution.paper_spot import PaperSpotEntryFill
from tiewtrade.market_data.candle import Candle
from tiewtrade.market_data.config import MarketDataConfig
from tiewtrade.trading.basket import BasketCloseReason, ClosedBasket
from tiewtrade.trading.entry_pair import EntryGate, EntryGateState
from tiewtrade.trading.entry_policy import EntryPolicy
from tiewtrade.trading.session_config import MarketType, SessionConfig, TradeMode


@dataclass(frozen=True, slots=True)
class FakeSnapshot:
    """The little a sink asks of a Session snapshot, whichever market wrote it."""

    accepted: bool = True
    failed_closed: bool = False
    entry_fill: PaperSpotEntryFill | None = None
    basket_entry_count: int = 0
    entry_number: int | None = None
    basket_id: UUID | None = None
    take_profit_price: Decimal | None = None
    sold_entry_numbers: tuple[int, ...] = ()
    closed_basket: ClosedBasket | None = None
    entry_gate: EntryGate | None = None
    entry_completed_pairs: int = 0
    cooldown_ends_at: datetime | None = None


class FakeCoordinator:
    def __init__(self, snapshot: FakeSnapshot) -> None:
        self.snapshot = snapshot
        self.completed_candles: list[Candle] = []
        self.in_progress_candles: list[Candle] = []
        self.closed_entries: list[int] = []

    def process_completed_candle(
        self,
        candle: Candle,
        *,
        received_at: datetime,
        backfilled: bool,
    ) -> PersistentSessionSnapshot[FakeSnapshot]:
        del received_at, backfilled
        self.completed_candles.append(candle)
        return self._persistent()

    def process_in_progress_candle(
        self,
        candle: Candle,
        *,
        observed_at: datetime,
    ) -> PersistentSessionSnapshot[FakeSnapshot]:
        del observed_at
        self.in_progress_candles.append(candle)
        return self._persistent()

    def waive_cooldown(
        self,
        observed_at: datetime,
    ) -> PersistentSessionSnapshot[FakeSnapshot]:
        del observed_at
        return self._persistent()

    def close_entry(
        self,
        entry_number: int,
        *,
        price: Decimal,
        observed_at: datetime,
    ) -> PersistentSessionSnapshot[FakeSnapshot]:
        del price, observed_at
        self.closed_entries.append(entry_number)
        return self._persistent()

    def _persistent(self) -> PersistentSessionSnapshot[FakeSnapshot]:
        return PersistentSessionSnapshot(
            session=self.snapshot,
            persistence_state=PersistenceState.READY,
        )


def entry_fill() -> PaperSpotEntryFill:
    return PaperSpotEntryFill(
        order_id="order-1",
        fill_id="fill-1",
        intent_id="intent-1",
        price=Decimal("101"),
        quantity=Decimal("0.5"),
        filled_at=datetime(2026, 8, 2, tzinfo=UTC),
    )


def candle_at(minute: int) -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timeframe="5m",
        open_time=datetime(2026, 8, 2, tzinfo=UTC) + timedelta(minutes=minute),
        open=Decimal("100"),
        high=Decimal("102"),
        low=Decimal("99"),
        close=Decimal("101"),
    )


NOTICE_CLOCK = datetime(2026, 8, 2, 12, tzinfo=UTC)


def sink_for(
    snapshot: FakeSnapshot,
    *,
    published: list[BasketSnapshot | None] | None = None,
    notify: Callable[[SessionNotice], None] | None = None,
    entry_lock: Lock | None = None,
) -> tuple[PaperRuntimeSink[FakeSnapshot], FakeCoordinator]:
    coordinator = FakeCoordinator(snapshot)
    record = published if published is not None else []

    def publish(
        basket: BasketSnapshot | None,
        candle: Candle,
        observed_at_utc: datetime,
    ) -> None:
        del candle, observed_at_utc
        record.append(basket)

    sink = PaperRuntimeSink(
        warm_up=lambda candles, received_at: None,
        persistent=coordinator,
        basket=lambda snapshot, position, candle, observed_at: None,
        publish=publish,
        entry_lock=entry_lock if entry_lock is not None else Lock(),
        entries_enabled=lambda: True,
        current_snapshot=lambda: snapshot,
        close_entry=lambda entry_number, price, observed_at: coordinator.close_entry(
            entry_number, price=price, observed_at=observed_at
        ),
        waive_cooldown=coordinator.waive_cooldown,
        notices=snapshot_notices(
            configured_session(), notify, clock=lambda: NOTICE_CLOCK
        ),
    )
    return sink, coordinator


def test_a_session_that_failed_closed_stops_the_candles_reaching_it() -> None:
    """A dead Session must take the Runtime feeding it down with it.

    It answers an unaccepted snapshot to everything from here on, which the
    Runtime reads as a quiet market: LIVE on screen, a Bot that will never
    trade again, and nothing said. Refusing the candle is what turns that
    into the Blocked the trader can see.
    """
    sink, _ = sink_for(FakeSnapshot(accepted=False, failed_closed=True))

    with pytest.raises(PaperSessionFailedClosedError):
        asyncio.run(
            sink.process_completed(
                candle_at(5),
                received_at=candle_at(5).close_time,
                backfilled=False,
            )
        )


def test_a_failed_closed_session_refuses_the_forming_candle_too() -> None:
    sink, _ = sink_for(FakeSnapshot(accepted=False, failed_closed=True))

    with pytest.raises(PaperSessionFailedClosedError):
        asyncio.run(
            sink.process_in_progress(
                candle_at(5),
                received_at=candle_at(5).open_time,
            )
        )


def test_a_running_session_still_takes_candles_and_publishes() -> None:
    published: list[BasketSnapshot | None] = []
    sink, coordinator = sink_for(FakeSnapshot(), published=published)

    asyncio.run(
        sink.process_completed(
            candle_at(5),
            received_at=candle_at(5).close_time,
            backfilled=False,
        )
    )

    assert coordinator.completed_candles == [candle_at(5)]
    assert published == [None]


def test_a_forming_candle_the_session_did_not_act_on_is_not_published() -> None:
    published: list[BasketSnapshot | None] = []
    sink, coordinator = sink_for(
        replace(FakeSnapshot(), accepted=False), published=published
    )

    asyncio.run(
        sink.process_in_progress(
            candle_at(5),
            received_at=candle_at(5).open_time,
        )
    )

    assert coordinator.in_progress_candles == [candle_at(5)]
    assert published == []


BASKET_ID = UUID("00000000-0000-0000-0000-0000000000b1")


def opened_entry(**overrides: object) -> FakeSnapshot:
    return replace(
        FakeSnapshot(entry_fill=entry_fill(), entry_number=1, basket_id=BASKET_ID),
        **overrides,  # type: ignore[arg-type]
    )


def closed_basket(
    reason: BasketCloseReason = BasketCloseReason.TAKE_PROFIT,
) -> ClosedBasket:
    return ClosedBasket(
        basket_id=BASKET_ID,
        entry_count=2,
        average_entry_price=Decimal("100"),
        exit_price=Decimal("110"),
        realized_pnl=Decimal("20"),
        exit_realized_pnl=Decimal("20"),
        closed_at=datetime(2026, 8, 2, 11, tzinfo=UTC),
        close_reason=reason,
    )


def run_completed(sink: PaperRuntimeSink[FakeSnapshot], minute: int = 5) -> None:
    asyncio.run(
        sink.process_completed(
            candle_at(minute),
            received_at=candle_at(minute).close_time,
            backfilled=False,
        )
    )


def test_an_entry_that_filled_is_said_out_loud() -> None:
    notices: list[SessionNotice] = []
    sink, _ = sink_for(opened_entry(), notify=notices.append)

    run_completed(sink)

    assert [type(notice) for notice in notices] == [EntryOpenedNotice]


def test_a_candle_that_opened_no_entry_says_nothing() -> None:
    notices: list[SessionNotice] = []
    sink, _ = sink_for(FakeSnapshot(), notify=notices.append)

    run_completed(sink)

    assert notices == []


def test_a_basket_that_closed_on_a_candle_is_said_out_loud() -> None:
    notices: list[SessionNotice] = []
    sink, _ = sink_for(
        replace(FakeSnapshot(), basket_id=BASKET_ID, closed_basket=closed_basket()),
        notify=notices.append,
    )

    run_completed(sink)

    assert len(notices) == 1
    notice = notices[0]
    assert isinstance(notice, BasketClosedNotice)
    assert notice.realized_pnl == Decimal("20")
    assert notice.close_reason is BasketCloseReason.TAKE_PROFIT


def test_a_candle_that_both_filled_and_closed_says_each_in_its_own_message() -> None:
    """Opening and closing are two messages, in the order they happened.

    One candle can fill an Entry into a new Basket and take the last one out,
    and a reader who is told about both in one line cannot tell which figure
    belongs to which.
    """
    notices: list[SessionNotice] = []
    sink, _ = sink_for(
        opened_entry(closed_basket=closed_basket()),
        notify=notices.append,
    )

    run_completed(sink)

    assert [type(notice) for notice in notices] == [
        EntryOpenedNotice,
        BasketClosedNotice,
    ]


def test_a_basket_that_closed_between_candles_is_still_said_out_loud() -> None:
    """A target reached on a forming candle is a Position gone, all the same."""
    notices: list[SessionNotice] = []
    sink, _ = sink_for(
        replace(
            FakeSnapshot(),
            basket_id=BASKET_ID,
            closed_basket=closed_basket(BasketCloseReason.TAKE_PROFIT),
        ),
        notify=notices.append,
    )

    asyncio.run(
        sink.process_in_progress(candle_at(5), received_at=candle_at(5).open_time)
    )

    assert [type(notice) for notice in notices] == [BasketClosedNotice]


def test_selling_the_last_entry_says_the_position_closed() -> None:
    notices: list[SessionNotice] = []
    sink, coordinator = sink_for(
        replace(
            FakeSnapshot(),
            basket_id=BASKET_ID,
            closed_basket=closed_basket(BasketCloseReason.MANUAL),
        ),
        notify=notices.append,
    )
    run_completed(sink)

    assert sink.close_entry(
        1, price=Decimal("110"), observed_at=datetime(2026, 8, 2, 11, tzinfo=UTC)
    )

    assert coordinator.closed_entries == [1]
    # Once for the candle that carried the same snapshot, once for the sale.
    assert [type(notice) for notice in notices] == [
        BasketClosedNotice,
        BasketClosedNotice,
    ]


def test_a_gate_that_just_closed_says_a_cooldown_started() -> None:
    notices: list[SessionNotice] = []
    sink, _ = sink_for(
        opened_entry(
            entry_gate=EntryGate(EntryGateState.COOLDOWN),
            entry_completed_pairs=1,
            cooldown_ends_at=None,
            basket_entry_count=2,
        ),
        notify=notices.append,
    )

    run_completed(sink)

    assert [type(notice) for notice in notices] == [
        EntryOpenedNotice,
        CooldownStartedNotice,
    ]
    cooldown = notices[1]
    assert isinstance(cooldown, CooldownStartedNotice)
    assert cooldown.completed_pairs == 1
    assert cooldown.open_entry_count == 2
    assert cooldown.started_at == NOTICE_CLOCK


def test_a_cooldown_that_goes_on_standing_is_only_said_once() -> None:
    """A Cooldown does not lapse, so every candle after it reads the same gate.

    Saying it again each candle would be saying nothing new to a trader whose
    phone is already holding the message.
    """
    notices: list[SessionNotice] = []
    sink, _ = sink_for(
        replace(
            FakeSnapshot(),
            basket_id=BASKET_ID,
            entry_gate=EntryGate(EntryGateState.COOLDOWN),
        ),
        notify=notices.append,
    )

    run_completed(sink, minute=5)
    run_completed(sink, minute=10)

    assert [type(notice) for notice in notices] == [CooldownStartedNotice]


def test_a_cooldown_already_standing_when_the_bot_started_is_not_news() -> None:
    """A Basket taken back may already be cooling, which nobody just did."""
    notices: list[SessionNotice] = []
    snapshot = replace(
        FakeSnapshot(),
        basket_id=BASKET_ID,
        entry_gate=EntryGate(EntryGateState.COOLDOWN),
    )
    sink, _ = sink_for(snapshot, notify=notices.append)

    asyncio.run(sink.warm_up((candle_at(0),), received_at=candle_at(0).close_time))
    run_completed(sink)

    assert notices == []


def test_lifting_a_cooldown_says_so() -> None:
    notices: list[SessionNotice] = []
    sink, _ = sink_for(
        replace(FakeSnapshot(), basket_id=BASKET_ID, entry_completed_pairs=2),
        notify=notices.append,
    )
    run_completed(sink)

    assert sink.waive_cooldown(datetime(2026, 8, 2, 11, tzinfo=UTC))

    assert [type(notice) for notice in notices] == [CooldownCancelledNotice]
    cancelled = notices[0]
    assert isinstance(cancelled, CooldownCancelledNotice)
    assert cancelled.completed_pairs == 2
    assert cancelled.cancelled_at == NOTICE_CLOCK


def test_saying_an_entry_out_loud_holds_nothing_the_next_candle_needs() -> None:
    """The notice is sent with the entry lock let go, not while it is held.

    Whatever carries a message off this machine is slower than a candle, and
    a Session that had to wait for one would be a Session the market could
    outrun. The lock being free at that moment is the whole guarantee.
    """
    entry_lock = Lock()
    held: list[bool] = []
    sink, _ = sink_for(
        opened_entry(),
        notify=lambda notice: held.append(entry_lock.locked()),
        entry_lock=entry_lock,
    )

    run_completed(sink)

    assert held == [False]


def test_a_notice_that_could_not_be_sent_does_not_stop_the_bot() -> None:
    """Trading survives the telling of it, whatever the telling does.

    A notifier is meant never to raise, and the sink does not take its word
    for it: an Entry is already filled and persisted by this point, and the
    Runtime failing on a message would throw away a working Session over
    something no trading decision depends on.
    """

    def notify(notice: SessionNotice) -> None:
        raise RuntimeError("LINE is unreachable")

    published: list[BasketSnapshot | None] = []
    sink, coordinator = sink_for(
        opened_entry(),
        published=published,
        notify=notify,
    )

    asyncio.run(
        sink.process_completed(
            candle_at(5),
            received_at=candle_at(5).close_time,
            backfilled=False,
        )
    )

    assert coordinator.completed_candles == [candle_at(5)]
    assert published == [None]


def configured_session() -> ConfiguredPaperSession:
    return ConfiguredPaperSession(
        config=SessionConfig(
            session_id=UUID("00000000-0000-0000-0000-000000000138"),
            preset_version="rsi-step-grid-v1",
            market_type=MarketType.SPOT,
            trade_mode=TradeMode.PAPER,
            capital_per_entry=Decimal("1000"),
            entry_policy=EntryPolicy(max_entries=10),
        ),
        market_data=MarketDataConfig(symbol="BTCUSDT", timeframe="5m"),
        created_at_utc=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_a_notice_says_which_market_and_which_entry_of_which_basket() -> None:
    basket_id = uuid4()
    snapshot = replace(
        FakeSnapshot(),
        entry_fill=entry_fill(),
        entry_number=3,
        basket_id=basket_id,
        take_profit_price=Decimal("110"),
    )

    notice = entry_opened_notice(snapshot, configured_session())

    assert notice is not None
    assert notice.market.symbol == "BTCUSDT"
    assert notice.market.timeframe == "5m"
    assert notice.basket_id == basket_id
    assert notice.entry_number == 3
    assert notice.price == Decimal("101")
    assert notice.quantity == Decimal("0.5")
    assert notice.notional == Decimal("50.5")
    assert notice.take_profit_price == Decimal("110")


def test_a_snapshot_with_no_fill_in_it_reads_as_no_notice() -> None:
    assert entry_opened_notice(FakeSnapshot(), configured_session()) is None


def test_a_snapshot_that_let_no_basket_go_reads_as_no_notice() -> None:
    assert basket_closed_notice(FakeSnapshot(), configured_session()) is None


COOLING = EntryGate(EntryGateState.COOLDOWN)
OPEN = EntryGate(EntryGateState.OPEN)
LIFTS_AT = datetime(2026, 8, 5, tzinfo=UTC)


def test_a_cooldown_that_waits_says_the_day_it_lifts_on() -> None:
    notices: list[SessionNotice] = []
    sink, _ = sink_for(
        opened_entry(
            entry_gate=COOLING,
            entry_completed_pairs=1,
            cooldown_ends_at=LIFTS_AT,
            basket_entry_count=2,
        ),
        notify=notices.append,
    )

    run_completed(sink)

    cooldown = notices[1]
    assert isinstance(cooldown, CooldownStartedNotice)
    assert cooldown.ends_at == LIFTS_AT


def test_a_cooldown_that_lapsed_on_its_own_is_said_out_loud() -> None:
    """Nobody pressed anything, so nothing else would tell the trader."""
    notices: list[SessionNotice] = []
    sink, coordinator = sink_for(
        replace(
            FakeSnapshot(),
            basket_id=BASKET_ID,
            entry_gate=COOLING,
            entry_completed_pairs=1,
            cooldown_ends_at=LIFTS_AT,
        ),
        notify=notices.append,
    )
    run_completed(sink)
    notices.clear()

    coordinator.snapshot = replace(coordinator.snapshot, entry_gate=OPEN)
    run_completed(sink, minute=10)

    assert [type(notice) for notice in notices] == [CooldownEndedNotice]
    ended = notices[0]
    assert isinstance(ended, CooldownEndedNotice)
    assert ended.completed_pairs == 1
    assert ended.ended_at == NOTICE_CLOCK


def test_a_cooldown_a_sale_broke_up_is_not_said_to_have_lapsed() -> None:
    """The trader ended that one by selling, and knows they did.

    A Pair a sale broke up names no day it lifts on, which is exactly what
    tells a Cooldown that ran out from one the trader ended.
    """
    notices: list[SessionNotice] = []
    sink, coordinator = sink_for(
        replace(
            FakeSnapshot(),
            basket_id=BASKET_ID,
            entry_gate=COOLING,
            entry_completed_pairs=1,
            cooldown_ends_at=LIFTS_AT,
        ),
        notify=notices.append,
    )
    run_completed(sink)
    notices.clear()

    coordinator.snapshot = replace(
        coordinator.snapshot, entry_gate=OPEN, cooldown_ends_at=None
    )
    run_completed(sink, minute=10)

    assert notices == []


def test_a_cooldown_the_trader_cancelled_is_not_said_to_have_lapsed_after() -> None:
    """Cancelling already says its own piece; the next candle adds nothing."""
    notices: list[SessionNotice] = []
    sink, coordinator = sink_for(
        replace(
            FakeSnapshot(),
            basket_id=BASKET_ID,
            entry_gate=COOLING,
            entry_completed_pairs=1,
            cooldown_ends_at=LIFTS_AT,
        ),
        notify=notices.append,
    )
    run_completed(sink)
    coordinator.snapshot = replace(coordinator.snapshot, entry_gate=OPEN)
    assert sink.waive_cooldown(datetime(2026, 8, 2, 11, tzinfo=UTC))
    notices.clear()

    run_completed(sink, minute=10)

    assert notices == []
