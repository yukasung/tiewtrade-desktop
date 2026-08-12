from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from tests.support.trade_history_records import basket_result, trade_fill
from tiewtrade.application.trade_history import (
    TradeFillHistoryItem,
    TradeHistoryFilter,
)
from tiewtrade.trading.session_config import MarketType, TradeMode
from tiewtrade.trading.trade_history import BasketStatus, FillSide, FillSource
from tiewtrade.ui.table_column import Column
from tiewtrade.ui.theme import NEGATIVE_COLOR, POSITIVE_COLOR
from tiewtrade.ui.trade_history_presenter import (
    BASKET_COLUMNS,
    FILL_COLUMNS,
    TradeHistoryFilterValues,
    pnl_text,
    trade_history_filter,
)


def read[Record](
    columns: tuple[Column[Record], ...], record: Record
) -> tuple[str, ...]:
    """Every column's reading of one record, in column order."""
    return tuple(column.text(record) for column in columns)


def colour_of[Record](
    columns: tuple[Column[Record], ...], header: str, record: Record
) -> str | None:
    column = next(column for column in columns if column.header == header)
    return column.colour(record)


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


def test_basket_columns_format_utc_and_every_column() -> None:
    basket = basket_result()

    assert read(BASKET_COLUMNS, basket) == (
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


def test_fill_columns_name_a_binance_source() -> None:
    item = TradeFillHistoryItem(
        fill=trade_fill(source=FillSource.BINANCE),
        symbol="BTCUSDT",
        timeframe="5m",
        market_type=MarketType.SPOT,
        trade_mode=TradeMode.PAPER,
    )

    assert read(FILL_COLUMNS, item)[-1] == "Binance"


def test_fill_columns_include_symbol_and_total_for_cross_basket_history() -> None:
    item = TradeFillHistoryItem(
        fill=trade_fill(),
        symbol="BTCUSDT",
        timeframe="5m",
        market_type=MarketType.SPOT,
        trade_mode=TradeMode.PAPER,
    )

    assert read(FILL_COLUMNS, item) == (
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


@pytest.mark.parametrize(
    ("net_realized_pnl", "expected"),
    [
        pytest.param(Decimal("1"), POSITIVE_COLOR, id="profit"),
        pytest.param(Decimal("-1"), NEGATIVE_COLOR, id="loss"),
        pytest.param(Decimal("0"), None, id="break-even"),
    ],
)
def test_pnl_colour_is_read_from_the_figure_not_from_its_label(
    net_realized_pnl: Decimal, expected: str | None
) -> None:
    """Rewording the label cannot silently drop the colour that goes with it."""
    basket = basket_result(
        gross_realized_pnl=net_realized_pnl,
        trading_fees=Decimal("0"),
        funding_fee=Decimal("0"),
        net_realized_pnl=net_realized_pnl,
    )

    assert colour_of(BASKET_COLUMNS, "Net PnL", basket) == expected


@pytest.mark.parametrize(
    ("side", "expected"),
    [
        pytest.param(FillSide.BUY, POSITIVE_COLOR, id="buy"),
        pytest.param(FillSide.SELL, NEGATIVE_COLOR, id="sell"),
    ],
)
def test_side_colour_is_read_from_the_side_itself(
    side: FillSide, expected: str
) -> None:
    item = TradeFillHistoryItem(
        fill=trade_fill(side=side),
        symbol="BTCUSDT",
        timeframe="5m",
        market_type=MarketType.SPOT,
        trade_mode=TradeMode.PAPER,
    )

    assert colour_of(FILL_COLUMNS, "Side", item) == expected
