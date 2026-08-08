from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tiewtrade.integrations.binance.kline_parser import (
    parse_rest_kline,
    parse_websocket_kline,
)
from tiewtrade.integrations.binance.public_endpoints import (
    BinanceMarketDataPayloadError,
)
from tiewtrade.market_data.config import MarketDataConfig


def config(*, symbol: str = "BTCUSDT", timeframe: str = "5m") -> MarketDataConfig:
    return MarketDataConfig(symbol=symbol, timeframe=timeframe)


def closed_kline_payload(
    *, symbol: str = "BTCUSDT", timeframe: str = "5m"
) -> dict[str, object]:
    return {
        "s": symbol,
        "k": {
            "t": 1767225600000,
            "s": symbol,
            "i": timeframe,
            "o": "100.10",
            "h": "102.20",
            "l": "99.90",
            "c": "101.30",
            "v": "12.50",
            "x": True,
        },
    }


def open_kline_payload() -> dict[str, object]:
    payload = closed_kline_payload()
    kline = payload["k"]
    assert isinstance(kline, dict)
    kline["x"] = False
    return payload


def test_rest_kline_maps_ohlc_and_ignores_transport_volume() -> None:
    candle = parse_rest_kline(
        [1767225600000, "100.10", "102.20", "99.90", "101.30", object()],
        config(),
    )

    assert candle.symbol == "BTCUSDT"
    assert candle.timeframe == "5m"
    assert candle.open_time == datetime(2026, 1, 1, tzinfo=UTC)
    assert candle.open == Decimal("100.10")
    assert candle.high == Decimal("102.20")
    assert candle.low == Decimal("99.90")
    assert candle.close == Decimal("101.30")
    assert not hasattr(candle, "volume")


def test_rest_kline_rejects_timestamp_with_subsecond_remainder() -> None:
    with pytest.raises(
        BinanceMarketDataPayloadError,
        match="invalid Binance",
    ) as captured:
        parse_rest_kline(
            [1767225600001, "100.10", "102.20", "99.90", "101.30", "12.50"],
            config(),
        )

    assert isinstance(captured.value.__cause__, ValueError)
    assert str(captured.value.__cause__) == (
        "open_time timestamp milliseconds must align to a whole second"
    )


def test_rest_kline_preserves_public_error_for_out_of_range_open_time() -> None:
    with pytest.raises(BinanceMarketDataPayloadError) as captured:
        parse_rest_kline(
            [10**20, "100.10", "102.20", "99.90", "101.30", "12.50"],
            config(),
        )

    assert str(captured.value) == "invalid Binance market-data payload"
    assert isinstance(captured.value.__cause__, ValueError)
    assert str(captured.value.__cause__) == "open_time timestamp is out of range"
    assert isinstance(captured.value.__cause__.__cause__, OSError)


def test_rest_kline_preserves_public_error_for_value_error_timestamp_range() -> None:
    with pytest.raises(BinanceMarketDataPayloadError) as captured:
        parse_rest_kline(
            [10**15, "100.10", "102.20", "99.90", "101.30", "12.50"],
            config(),
        )

    assert str(captured.value) == "invalid Binance market-data payload"
    assert isinstance(captured.value.__cause__, ValueError)
    assert str(captured.value.__cause__) == "open_time timestamp is out of range"
    assert isinstance(captured.value.__cause__.__cause__, ValueError)


def test_rest_kline_preserves_public_error_with_open_field_diagnostic() -> None:
    with pytest.raises(BinanceMarketDataPayloadError) as captured:
        parse_rest_kline(
            [1767225600000, 100.10, "102.20", "99.90", "101.30", "12.50"],
            config(),
        )

    assert str(captured.value) == "invalid Binance market-data payload"
    assert isinstance(captured.value.__cause__, ValueError)
    assert str(captured.value.__cause__) == "open must be a finite decimal string"


def test_closed_websocket_kline_maps_to_closed_update() -> None:
    update = parse_websocket_kline(closed_kline_payload(), config())

    assert update.is_closed is True
    assert update.candle.open_time == datetime(2026, 1, 1, tzinfo=UTC)
    assert update.candle.close == Decimal("101.30")


def test_closed_websocket_kline_ignores_transport_volume() -> None:
    payload = closed_kline_payload()
    kline = payload["k"]
    assert isinstance(kline, dict)
    kline["v"] = object()

    update = parse_websocket_kline(payload, config())

    assert update.is_closed is True
    assert update.candle.close == Decimal("101.30")
    assert not hasattr(update.candle, "volume")


def test_open_websocket_kline_maps_to_open_update() -> None:
    update = parse_websocket_kline(open_kline_payload(), config())

    assert update.is_closed is False
    assert update.candle.open_time == datetime(2026, 1, 1, tzinfo=UTC)
    assert not hasattr(update.candle, "volume")


def test_websocket_kline_preserves_public_error_for_out_of_range_open_time() -> None:
    payload = closed_kline_payload()
    kline = payload["k"]
    assert isinstance(kline, dict)
    kline["t"] = 10**20

    with pytest.raises(BinanceMarketDataPayloadError) as captured:
        parse_websocket_kline(payload, config())

    assert str(captured.value) == "invalid Binance market-data payload"
    assert isinstance(captured.value.__cause__, ValueError)
    assert str(captured.value.__cause__) == "k.t timestamp is out of range"
    assert isinstance(captured.value.__cause__.__cause__, OSError)


def test_websocket_kline_preserves_public_error_for_value_error_range() -> None:
    payload = closed_kline_payload()
    kline = payload["k"]
    assert isinstance(kline, dict)
    kline["t"] = 10**15

    with pytest.raises(BinanceMarketDataPayloadError) as captured:
        parse_websocket_kline(payload, config())

    assert str(captured.value) == "invalid Binance market-data payload"
    assert isinstance(captured.value.__cause__, ValueError)
    assert str(captured.value.__cause__) == "k.t timestamp is out of range"
    assert isinstance(captured.value.__cause__.__cause__, ValueError)


def test_websocket_kline_preserves_public_error_with_closed_field_diagnostic() -> None:
    payload = closed_kline_payload()
    kline = payload["k"]
    assert isinstance(kline, dict)
    kline["x"] = "true"

    with pytest.raises(BinanceMarketDataPayloadError) as captured:
        parse_websocket_kline(payload, config())

    assert str(captured.value) == "invalid Binance market-data payload"
    assert isinstance(captured.value.__cause__, ValueError)
    assert str(captured.value.__cause__) == "k.x must be a boolean"


@pytest.mark.parametrize(
    ("payload", "market_data_config"),
    [
        (closed_kline_payload(symbol="ETHUSDT"), config()),
        (closed_kline_payload(timeframe="3m"), config()),
    ],
)
def test_websocket_kline_with_mismatched_identity_is_rejected(
    payload: dict[str, object], market_data_config: MarketDataConfig
) -> None:
    with pytest.raises(BinanceMarketDataPayloadError, match="invalid Binance"):
        parse_websocket_kline(payload, market_data_config)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        [1767225600000, "100.10", "102.20"],
        [1767225600000, "invalid", "102.20", "99.90", "101.30", "12.50"],
    ],
)
def test_malformed_rest_kline_is_rejected(payload: list[object]) -> None:
    with pytest.raises(BinanceMarketDataPayloadError, match="invalid Binance"):
        parse_rest_kline(payload, config())


def test_rest_kline_requires_only_timestamp_and_ohlc_fields() -> None:
    with pytest.raises(BinanceMarketDataPayloadError) as captured:
        parse_rest_kline(
            [1767225600000, "100.10", "102.20", "99.90"],
            config(),
        )

    assert isinstance(captured.value.__cause__, ValueError)
    assert str(captured.value.__cause__) == (
        "REST kline payload must contain at least 5 fields"
    )

    candle = parse_rest_kline(
        [1767225600000, "100.10", "102.20", "99.90", "101.30"],
        config(),
    )

    assert candle.close == Decimal("101.30")


def test_malformed_websocket_kline_is_rejected() -> None:
    with pytest.raises(BinanceMarketDataPayloadError, match="invalid Binance"):
        parse_websocket_kline({"s": "BTCUSDT", "k": {"x": True}}, config())
