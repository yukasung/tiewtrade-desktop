from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Lock

import pytest

from tiewtrade.application.paper_session_sink import (
    PaperRuntimeSink,
    PaperSessionFailedClosedError,
)
from tiewtrade.application.session_persistence import (
    PersistenceState,
    PersistentSessionSnapshot,
)
from tiewtrade.application.trading_workspace import BasketSnapshot
from tiewtrade.market_data.candle import Candle


@dataclass(frozen=True, slots=True)
class FakeSnapshot:
    """The little a sink asks of a Session snapshot, whichever market wrote it."""

    accepted: bool = True
    failed_closed: bool = False
    entry_fill: None = None
    basket_entry_count: int = 0
    entry_number: int | None = None
    entry_take_profit_price: Decimal | None = None
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
        entry_lock=Lock(),
        entries_enabled=lambda: True,
        current_snapshot=lambda: snapshot,
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
