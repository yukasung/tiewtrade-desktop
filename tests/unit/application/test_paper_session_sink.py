from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Lock
from uuid import UUID, uuid4

import pytest

from tiewtrade.application.paper_session_setup import ConfiguredPaperSession
from tiewtrade.application.paper_session_sink import (
    PaperRuntimeSink,
    PaperSessionFailedClosedError,
    entry_opened_notice,
)
from tiewtrade.application.session_persistence import (
    PersistenceState,
    PersistentSessionSnapshot,
)
from tiewtrade.application.trading_workspace import BasketSnapshot
from tiewtrade.execution.paper_spot import PaperSpotEntryFill
from tiewtrade.market_data.candle import Candle
from tiewtrade.market_data.config import MarketDataConfig
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


class FakeCoordinator:
    def __init__(self, snapshot: FakeSnapshot) -> None:
        self._snapshot = snapshot
        self.completed_candles: list[Candle] = []
        self.in_progress_candles: list[Candle] = []

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

    def _persistent(self) -> PersistentSessionSnapshot[FakeSnapshot]:
        return PersistentSessionSnapshot(
            session=self._snapshot,
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


def sink_for(
    snapshot: FakeSnapshot,
    *,
    published: list[BasketSnapshot | None] | None = None,
    notify_entry_opened: Callable[[FakeSnapshot], None] | None = None,
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
        notify_entry_opened=notify_entry_opened,
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


def test_an_entry_that_filled_is_said_out_loud() -> None:
    fill = entry_fill()
    notified: list[FakeSnapshot] = []
    sink, _ = sink_for(
        replace(FakeSnapshot(), entry_fill=fill, entry_number=1),
        notify_entry_opened=notified.append,
    )

    asyncio.run(
        sink.process_completed(
            candle_at(5),
            received_at=candle_at(5).close_time,
            backfilled=False,
        )
    )

    assert [snapshot.entry_fill for snapshot in notified] == [fill]


def test_a_candle_that_opened_no_entry_says_nothing() -> None:
    notified: list[FakeSnapshot] = []
    sink, _ = sink_for(FakeSnapshot(), notify_entry_opened=notified.append)

    asyncio.run(
        sink.process_completed(
            candle_at(5),
            received_at=candle_at(5).close_time,
            backfilled=False,
        )
    )

    assert notified == []


def test_saying_an_entry_out_loud_holds_nothing_the_next_candle_needs() -> None:
    """The notice is sent with the entry lock let go, not while it is held.

    Whatever carries a message off this machine is slower than a candle, and
    a Session that had to wait for one would be a Session the market could
    outrun. The lock being free at that moment is the whole guarantee.
    """
    entry_lock = Lock()
    held: list[bool] = []
    sink, _ = sink_for(
        replace(FakeSnapshot(), entry_fill=entry_fill(), entry_number=1),
        notify_entry_opened=lambda snapshot: held.append(entry_lock.locked()),
        entry_lock=entry_lock,
    )

    asyncio.run(
        sink.process_completed(
            candle_at(5),
            received_at=candle_at(5).close_time,
            backfilled=False,
        )
    )

    assert held == [False]


def test_a_notice_that_could_not_be_sent_does_not_stop_the_bot() -> None:
    """Trading survives the telling of it, whatever the telling does.

    A notifier is meant never to raise, and the sink does not take its word
    for it: an Entry is already filled and persisted by this point, and the
    Runtime failing on a message would throw away a working Session over
    something no trading decision depends on.
    """

    def notify(snapshot: FakeSnapshot) -> None:
        raise RuntimeError("LINE is unreachable")

    published: list[BasketSnapshot | None] = []
    sink, coordinator = sink_for(
        replace(FakeSnapshot(), entry_fill=entry_fill(), entry_number=1),
        published=published,
        notify_entry_opened=notify,
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
    assert notice.symbol == "BTCUSDT"
    assert notice.timeframe == "5m"
    assert notice.basket_id == basket_id
    assert notice.entry_number == 3
    assert notice.price == Decimal("101")
    assert notice.quantity == Decimal("0.5")
    assert notice.notional == Decimal("50.5")
    assert notice.take_profit_price == Decimal("110")


def test_a_snapshot_with_no_fill_in_it_reads_as_no_notice() -> None:
    assert entry_opened_notice(FakeSnapshot(), configured_session()) is None
