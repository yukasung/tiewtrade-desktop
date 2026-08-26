from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest

from tiewtrade.application.public_market_data_runtime import (
    create_public_market_data_runtime,
)
from tiewtrade.integrations.binance.public_endpoints import (
    BinanceMarketDataPayloadError,
    BinancePublicEndpoints,
)
from tiewtrade.market_data.candle import Candle
from tiewtrade.market_data.candle_source import MarketDataKlineUpdate
from tiewtrade.market_data.config import MarketDataConfig
from tiewtrade.market_data.runtime import MarketDataRuntime
from tiewtrade.strategies.rsi_step_grid.preset import RsiStepGridPreset
from tiewtrade.trading.entry_policy import EntryPolicy
from tiewtrade.trading.futures_policy import FuturesTradingPolicy
from tiewtrade.trading.session_config import MarketType, SessionConfig, TradeMode


class FakePublicCandleSource:
    def __init__(self) -> None:
        self.requested_warm_up_counts: list[int] = []

    async def load_recent(
        self,
        config: MarketDataConfig,
        *,
        count: int,
        completed_before: datetime,
    ) -> tuple[Candle, ...]:
        self.requested_warm_up_counts.append(count)
        return ()

    async def load_range(
        self,
        config: MarketDataConfig,
        *,
        start: datetime,
        end: datetime,
    ) -> tuple[Candle, ...]:
        raise AssertionError("unit composition must not backfill market data")

    def stream_klines(
        self, config: MarketDataConfig
    ) -> AsyncIterator[MarketDataKlineUpdate]:
        return self._stream_klines()

    async def close(self) -> None:
        return None

    async def _stream_klines(self) -> AsyncIterator[MarketDataKlineUpdate]:
        if False:
            yield


class RecordingSink:
    async def warm_up(
        self, candles: tuple[Candle, ...], *, received_at: datetime
    ) -> None:
        return None

    async def process_completed(
        self, candle: Candle, *, received_at: datetime, backfilled: bool
    ) -> None:
        return None

    async def process_in_progress(
        self, candle: Candle, *, received_at: datetime
    ) -> None:
        del candle, received_at


def test_session_market_type_selects_public_endpoint_before_source_creation() -> None:
    market_type = MarketType.SPOT
    expected_rest_url = "https://api.binance.th/api/v1/klines"
    expected_websocket_url = "wss://nbstream.binance.th/w3w/wsa/stream"
    selected_endpoints: list[BinancePublicEndpoints] = []
    fake_source = FakePublicCandleSource()

    def source_factory(
        endpoints: BinancePublicEndpoints,
    ) -> FakePublicCandleSource:
        selected_endpoints.append(endpoints)
        return fake_source

    runtime = create_public_market_data_runtime(
        session=session_config(market_type),
        market_data=MarketDataConfig(symbol="ETHUSDT", timeframe="15m"),
        preset=RsiStepGridPreset.v1(),
        sink=RecordingSink(),
        source_factory=source_factory,
    )

    assert isinstance(runtime, MarketDataRuntime)
    assert selected_endpoints == [
        BinancePublicEndpoints(
            rest_klines_url=expected_rest_url,
            websocket_base_url=expected_websocket_url,
        )
    ]
    asyncio.run(runtime.run())
    assert fake_source.requested_warm_up_counts == [15]


def test_a_futures_session_has_no_public_market_to_read() -> None:
    """Binance TH lists no Futures market, so composition stops here."""
    with pytest.raises(BinanceMarketDataPayloadError):
        create_public_market_data_runtime(
            session=session_config(MarketType.FUTURES),
            market_data=MarketDataConfig(symbol="ETHUSDT", timeframe="15m"),
            preset=RsiStepGridPreset.v1(),
            sink=RecordingSink(),
            source_factory=lambda endpoints: FakePublicCandleSource(),
        )


def session_config(market_type: MarketType) -> SessionConfig:
    return SessionConfig(
        session_id=UUID("00000000-0000-0000-0000-000000000099"),
        preset_version="rsi-step-grid-v1",
        market_type=market_type,
        trade_mode=TradeMode.PAPER,
        capital_per_entry=Decimal("1000"),
        entry_policy=EntryPolicy(max_entries=4),
        futures_policy=(
            FuturesTradingPolicy.v1(leverage=3)
            if market_type is MarketType.FUTURES
            else None
        ),
    )
