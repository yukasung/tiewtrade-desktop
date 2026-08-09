import pytest

from tiewtrade.application.chart_view_selection import ChartViewSelection
from tiewtrade.market_data.config import MarketDataConfig


def test_chart_view_selection_builds_market_data_config() -> None:
    selection = ChartViewSelection(symbol="ETHUSDT", timeframe="1m")

    assert selection.market_data_config() == MarketDataConfig(
        symbol="ETHUSDT", timeframe="1m"
    )


@pytest.mark.parametrize("symbol", ["DOGEUSDT", ""])
def test_chart_view_selection_rejects_unsupported_symbol(symbol: str) -> None:
    with pytest.raises(ValueError, match="Paper symbol is not supported"):
        ChartViewSelection(symbol=symbol, timeframe="5m")


def test_chart_view_selection_rejects_unsupported_timeframe() -> None:
    with pytest.raises(ValueError, match="supported V1 timeframe"):
        ChartViewSelection(symbol="BTCUSDT", timeframe="7m")
