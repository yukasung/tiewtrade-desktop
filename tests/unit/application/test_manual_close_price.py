from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tests.support.paper_session_setup import configured_spot_session
from tiewtrade.application.latest_market_price import LatestMarketPrice
from tiewtrade.application.manual_close_price import (
    ManualClosePrice,
    QuoteLatestPrice,
)
from tiewtrade.application.paper_session_setup import ConfiguredPaperSession
from tiewtrade.market_data.source_errors import (
    MarketDataFailureKind,
    MarketDataRetryableError,
)

MAX_AGE = timedelta(seconds=30)


def recording_quote(
    quoted: list[ConfiguredPaperSession],
) -> QuoteLatestPrice:
    def quote(session: ConfiguredPaperSession) -> Decimal:
        quoted.append(session)
        return Decimal("202")

    return quote


def test_observed_price_answers_a_close_without_asking_the_exchange() -> None:
    session = configured_spot_session()
    observed = LatestMarketPrice()
    observed.observe(session.config.session_id, Decimal("101"))
    quoted: list[ConfiguredPaperSession] = []

    price = ManualClosePrice(
        observed=observed,
        quote=recording_quote(quoted),
        max_age=MAX_AGE,
    ).get(session)

    assert price == Decimal("101")
    assert quoted == []


def test_stale_observed_price_falls_through_to_an_exchange_quote() -> None:
    session = configured_spot_session()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    observed = LatestMarketPrice(clock=lambda: now)
    observed.observe(session.config.session_id, Decimal("101"))
    now += timedelta(seconds=31)

    price = ManualClosePrice(
        observed=observed,
        quote=lambda _session: Decimal("202"),
        max_age=MAX_AGE,
    ).get(session)

    assert price == Decimal("202")


def test_unobserved_market_is_quoted_for_the_sessions_own_symbol() -> None:
    """A chart pointed elsewhere leaves the Session's price unobserved."""
    session = configured_spot_session()
    quoted: list[ConfiguredPaperSession] = []

    price = ManualClosePrice(
        observed=LatestMarketPrice(),
        quote=recording_quote(quoted),
        max_age=MAX_AGE,
    ).get(session)

    assert price == Decimal("202")
    assert quoted == [session]


def test_unreachable_market_data_states_the_price_is_unavailable() -> None:
    def unreachable(_session: ConfiguredPaperSession) -> Decimal:
        raise MarketDataRetryableError(
            "Binance market-data transport failed",
            kind=MarketDataFailureKind.TRANSPORT,
        )

    manual_close_price = ManualClosePrice(
        observed=LatestMarketPrice(),
        quote=unreachable,
        max_age=MAX_AGE,
    )

    with pytest.raises(RuntimeError, match="fresh market price is unavailable"):
        manual_close_price.get(configured_spot_session())
