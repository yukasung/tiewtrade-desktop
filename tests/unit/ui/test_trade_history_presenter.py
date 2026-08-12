from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from tests.support.trade_history_records import basket_result, trade_fill
from tiewtrade.application.trade_history import (
    TradeFillHistoryItem,
    TradeHistoryFilter,
)
from tiewtrade.trading.session_config import MarketType, TradeMode
from tiewtrade.trading.trade_history import BasketStatus, FillSource
from tiewtrade.ui.trade_history_presenter import (
    TradeHistoryFilterValues,
    basket_rows,
    pnl_text,
    trade_fill_rows,
    trade_history_filter,
)


def test_filter_values_build_inclusive_utc_date_range() -> None:
    values = TradeHistoryFilterValues(
        symbol="BTCUSDT",
        timeframe="5m",
        market_type="spot",
        trade_mode="paper",
        status="closed",
        from_date=date(2026, 1, 2),
        to_date=date(2026, 1, 3),
    )

    assert trade_history_filter(values) == TradeHistoryFilter(
        symbol="BTCUSDT",
        timeframe="5m",
        market_type=MarketType.SPOT,
        trade_mode=TradeMode.PAPER,
        status=BasketStatus.CLOSED,
        # A Bangkok day starts seven hours before the UTC one.
        opened_from_utc=datetime(2026, 1, 1, 17, 0, tzinfo=UTC),
        opened_before_utc=datetime(2026, 1, 3, 17, 0, tzinfo=UTC),
    )


def test_filter_values_reject_from_after_to() -> None:
    values = TradeHistoryFilterValues(
        from_date=date(2026, 1, 3),
        to_date=date(2026, 1, 2),
    )

    with pytest.raises(ValueError, match="From Date must not be after To Date"):
        trade_history_filter(values)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("19.580000000000000001"), "19.580000000000000001 USDT · Profit"),
        (Decimal("-0.2"), "-0.2 USDT · Loss"),
        (Decimal("0"), "0.00 USDT · Break-even"),
    ],
)
def test_pnl_text_preserves_decimal_and_adds_semantic_label(
    value: Decimal, expected: str
) -> None:
    assert pnl_text(value) == expected


def test_basket_rows_format_utc_and_all_columns() -> None:
    basket = basket_result()

    assert basket_rows((basket,))[0].basket_id == basket.basket_id
    assert basket_rows((basket,))[0].values == (
        "2026-01-01 07:00:00",
        "Paper",
        "Spot",
        "BTCUSDT",
        "5m",
        "1",
        "200 USDT",
        "20 USDT",
        "0.42 USDT",
        "0.00 USDT",
        "19.58 USDT · Profit",
        "Closed",
    )


def test_trade_fill_rows_name_a_binance_source() -> None:
    item = TradeFillHistoryItem(
        fill=trade_fill(source=FillSource.BINANCE),
        symbol="BTCUSDT",
        timeframe="5m",
        market_type=MarketType.SPOT,
        trade_mode=TradeMode.PAPER,
    )

    assert trade_fill_rows((item,))[0].values[-1] == "Binance"


def test_trade_fill_rows_include_symbol_and_total_for_cross_basket_history() -> None:
    item = TradeFillHistoryItem(
        fill=trade_fill(),
        symbol="BTCUSDT",
        timeframe="5m",
        market_type=MarketType.SPOT,
        trade_mode=TradeMode.PAPER,
    )

    assert trade_fill_rows((item,))[0].values == (
        "2026-01-01 07:00:00",
        "BTCUSDT",
        "Buy",
        "100",
        "2",
        "0.2 USDT",
        "200 USDT",
        "0.00 USDT · Break-even",
        "Paper Executor",
    )
