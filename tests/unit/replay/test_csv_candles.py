from decimal import Decimal
from pathlib import Path

import pytest

from tiewtrade.market_data.config import MarketDataConfig
from tiewtrade.replay.csv_candles import load_candles_csv


def write_csv(tmp_path: Path, contents: str) -> Path:
    path = tmp_path / "candles.csv"
    path.write_text(contents, encoding="utf-8")
    return path


def test_loads_utc_candle_with_market_identity_from_configuration(
    tmp_path: Path,
) -> None:
    path = write_csv(
        tmp_path,
        "open_time,open,high,low,close\n"
        "2026-01-01T00:00:00+00:00,100.10,102.20,99.90,101.00\n",
    )

    candles = load_candles_csv(path, MarketDataConfig(symbol="ETHUSDT", timeframe="5m"))

    assert isinstance(candles, tuple)
    assert candles[0].symbol == "ETHUSDT"
    assert candles[0].timeframe == "5m"
    assert candles[0].open == Decimal("100.10")
    assert candles[0].open.as_tuple().exponent == -2
    assert not hasattr(candles[0], "volume")


@pytest.mark.parametrize(
    "header",
    [
        "open_time,open,high,low,close,volume",
        "open_time,open,high,low",
        "open,open_time,high,low,close",
    ],
)
def test_rejects_missing_extra_or_reordered_header(tmp_path: Path, header: str) -> None:
    path = write_csv(tmp_path, f"{header}\n")

    with pytest.raises(
        ValueError, match="CSV header must be open_time,open,high,low,close"
    ):
        load_candles_csv(path, MarketDataConfig(symbol="ETHUSDT", timeframe="5m"))


@pytest.mark.parametrize(
    "row",
    [
        "2026-01-01T00:00:00,100,102,99,101",
        "2026-01-01T00:00:00+00:00,not-a-decimal,102,99,101",
    ],
)
def test_rejects_invalid_row_with_its_csv_row_number(tmp_path: Path, row: str) -> None:
    path = write_csv(
        tmp_path,
        f"open_time,open,high,low,close\n{row}\n",
    )

    with pytest.raises(ValueError, match="invalid CSV row 2:"):
        load_candles_csv(path, MarketDataConfig(symbol="ETHUSDT", timeframe="5m"))


def test_rejects_row_with_transport_volume_as_a_sixth_column(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path,
        "open_time,open,high,low,close\n2026-01-01T00:00:00+00:00,100,102,99,101,10\n",
    )

    with pytest.raises(ValueError, match="invalid CSV row 2: expected 5 columns"):
        load_candles_csv(path, MarketDataConfig(symbol="ETHUSDT", timeframe="5m"))


@pytest.mark.parametrize("field", ["open", "high", "low", "close"])
@pytest.mark.parametrize("non_finite", ["NaN", "Infinity", "-Infinity"])
def test_rejects_non_finite_ohlc_values_with_its_csv_row_number(
    tmp_path: Path, field: str, non_finite: str
) -> None:
    values = {
        "open": "100",
        "high": "102",
        "low": "99",
        "close": "101",
    }
    values[field] = non_finite
    path = write_csv(
        tmp_path,
        "open_time,open,high,low,close\n"
        "2026-01-01T00:00:00+00:00,"
        f"{values['open']},{values['high']},{values['low']},"
        f"{values['close']}\n",
    )

    with pytest.raises(
        ValueError, match="invalid CSV row 2: OHLC values must be finite"
    ):
        load_candles_csv(path, MarketDataConfig(symbol="ETHUSDT", timeframe="5m"))


def test_rejects_header_only_csv(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "open_time,open,high,low,close\n")

    with pytest.raises(ValueError, match="CSV must contain at least one candle"):
        load_candles_csv(path, MarketDataConfig(symbol="ETHUSDT", timeframe="5m"))
