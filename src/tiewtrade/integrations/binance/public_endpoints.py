from __future__ import annotations

from dataclasses import dataclass

from tiewtrade.market_data.config import MarketDataConfig
from tiewtrade.trading.session_config import MarketType


class BinanceMarketDataPayloadError(ValueError):
    """Raised when Binance public market data cannot be safely normalized."""


@dataclass(frozen=True, slots=True)
class BinancePublicEndpoints:
    rest_klines_url: str
    websocket_base_url: str

    @classmethod
    def for_market_type(cls, market_type: MarketType) -> BinancePublicEndpoints:
        """The public endpoints Binance TH serves this Market Type from.

        Binance TH is a Spot exchange. It publishes no Futures klines and no
        Futures stream, so a Futures Session has nowhere to read a market
        from here — and reading one from a different exchange would price a
        Basket against a book it will never trade on. Saying so is the
        honest answer; a fallback would only hide it until the numbers
        disagreed.
        """
        if market_type is MarketType.SPOT:
            return cls(
                "https://api.binance.th/api/v1/klines",
                "wss://nbstream.binance.th/w3w/wsa/stream",
            )
        if market_type is MarketType.FUTURES:
            raise BinanceMarketDataPayloadError(
                "Binance TH publishes no Futures market data"
            )
        raise BinanceMarketDataPayloadError("unsupported Binance market type")


def kline_stream_name(config: MarketDataConfig) -> str:
    """The stream this market is published under, as Binance TH names it.

    Symbols travel through the domain the way the exchange writes them —
    BTCUSDT — and travel through a stream name lowercased, because that is
    the only spelling Binance TH answers to.
    """
    return f"{config.symbol.lower()}@kline_{config.timeframe}"
