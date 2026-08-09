from dataclasses import dataclass

from tiewtrade.application.paper_symbols import validate_paper_symbol
from tiewtrade.market_data.config import MarketDataConfig


@dataclass(frozen=True, slots=True)
class ChartViewSelection:
    """Read-only market-data identity selected for chart presentation."""

    symbol: str
    timeframe: str

    def __post_init__(self) -> None:
        validate_paper_symbol(self.symbol)
        MarketDataConfig(symbol=self.symbol, timeframe=self.timeframe)

    def market_data_config(self) -> MarketDataConfig:
        return MarketDataConfig(symbol=self.symbol, timeframe=self.timeframe)
