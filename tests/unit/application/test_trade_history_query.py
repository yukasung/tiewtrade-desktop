from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tests.support.trade_history_records import trade_fill
from tiewtrade.application.trade_history import (
    BasketHistoryResult,
    PageRequest,
    TradeFillHistoryItem,
    TradeHistoryFilter,
)
from tiewtrade.trading.session_config import MarketType, TradeMode


@pytest.mark.parametrize(
    "create_filter",
    [
        lambda value: TradeHistoryFilter(opened_from_utc=value),
        lambda value: TradeHistoryFilter(opened_before_utc=value),
    ],
)
def test_trade_history_filter_requires_utc_bounds(
    create_filter: Callable[[datetime], TradeHistoryFilter],
) -> None:
    with pytest.raises(ValueError, match="UTC"):
        create_filter(datetime(2026, 1, 1))


def test_trade_history_filter_rejects_empty_market_identity() -> None:
    with pytest.raises(ValueError, match="symbol"):
        TradeHistoryFilter(symbol="")

    with pytest.raises(ValueError, match="timeframe"):
        TradeHistoryFilter(timeframe="")


def test_trade_history_filter_requires_ordered_time_bounds() -> None:
    boundary = datetime(2026, 1, 1, tzinfo=UTC)

    with pytest.raises(ValueError, match="before"):
        TradeHistoryFilter(
            opened_from_utc=boundary,
            opened_before_utc=boundary,
        )


@pytest.mark.parametrize(
    ("page", "page_size", "field"),
    [(0, 10, "page"), (1, 0, "page_size")],
)
def test_page_request_requires_positive_values(
    page: int,
    page_size: int,
    field: str,
) -> None:
    with pytest.raises(ValueError, match=field):
        PageRequest(page=page, page_size=page_size)


def test_page_request_calculates_offset() -> None:
    request = PageRequest(page=3, page_size=25)

    assert request.offset == 50


def test_history_read_models_preserve_durable_facts() -> None:
    item = TradeFillHistoryItem(
        fill=trade_fill(),
        symbol="BTCUSDT",
        timeframe="5m",
        market_type=MarketType.SPOT,
        trade_mode=TradeMode.PAPER,
    )

    assert item.fill.notional == Decimal("200")
    assert BasketHistoryResult(items=(), net_realized_pnl=Decimal("0")).items == ()
