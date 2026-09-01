from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal

from tiewtrade.application.latest_market_price import (
    LatestMarketPrice,
    MarketPriceUnavailableError,
)
from tiewtrade.application.paper_session_setup import ConfiguredPaperSession
from tiewtrade.market_data.source_errors import MarketDataSourceError

QuoteLatestPrice = Callable[[ConfiguredPaperSession], Decimal]

_UNAVAILABLE_MESSAGE = "A fresh market price is unavailable"


class ManualClosePrice:
    """Answers the price a manual close executes at.

    The live feed states a price only while it watches the Session's own
    market: a chart pointed at another symbol, a feed that has just started,
    and a feed that stalled all leave that fact absent or stale. What the
    chart happens to show is not a reason a position cannot be closed, so an
    unusable fact falls through to a quote read straight from the exchange.
    """

    def __init__(
        self,
        *,
        observed: LatestMarketPrice,
        quote: QuoteLatestPrice,
        max_age: timedelta,
    ) -> None:
        self._observed = observed
        self._quote = quote
        self._max_age = max_age

    def get(self, session: ConfiguredPaperSession) -> Decimal:
        try:
            return self._observed.get(
                session.config.session_id,
                max_age=self._max_age,
            )
        except RuntimeError:
            pass
        try:
            return self._quote(session)
        except MarketDataSourceError as error:
            raise MarketPriceUnavailableError(_UNAVAILABLE_MESSAGE) from error
