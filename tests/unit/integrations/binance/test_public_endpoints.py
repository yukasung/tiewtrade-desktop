import pytest

from tiewtrade.integrations.binance.public_endpoints import (
    BinanceMarketDataPayloadError,
    BinancePublicEndpoints,
)
from tiewtrade.trading.session_config import MarketType


def test_spot_reads_the_thai_public_market_data_endpoints() -> None:
    spot = BinancePublicEndpoints.for_market_type(MarketType.SPOT)

    assert spot.rest_klines_url == "https://api.binance.th/api/v1/klines"
    assert spot.websocket_base_url == "wss://nbstream.binance.th/w3w/wsa/stream"


def test_futures_has_no_thai_public_market_data_endpoint() -> None:
    with pytest.raises(BinanceMarketDataPayloadError):
        BinancePublicEndpoints.for_market_type(MarketType.FUTURES)
