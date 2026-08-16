from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from tests.support.trade_history_records import basket_result, trade_fill
from tiewtrade.application.trade_history import (
    TradeFillHistoryItem,
    TradeHistoryFilter,
)
from tiewtrade.trading.session_config import MarketType, TradeMode
from tiewtrade.trading.trade_history import FillSide
from tiewtrade.ui.table_column import Column
from tiewtrade.ui.theme import NEGATIVE_COLOR, POSITIVE_COLOR
from tiewtrade.ui.trade_history_presenter import (
    BASKET_COLUMNS,
    FILL_COLUMNS,
    QuickRange,
    TradeHistoryFilterValues,
    quick_range_dates,
    trade_history_filter,
    usdt_text,
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
        from_date=date(2026, 1, 2),
        to_date=date(2026, 1, 3),
    )

    assert trade_history_filter(values) == TradeHistoryFilter(
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
        (Decimal("19.580000000000000001"), "19.58 USDT"),
        (Decimal("-0.2"), "-0.20 USDT"),
        (Decimal("0"), "0.00 USDT"),
    ],
)
def test_a_recorded_figure_reads_to_the_cent_whatever_it_was_stored_as(
    value: Decimal, expected: str
) -> None:
    """The record keeps every place; the column shows the two that are read."""
    assert usdt_text(value) == expected


def test_basket_columns_format_utc_and_every_column() -> None:
    basket = basket_result()

    assert read(BASKET_COLUMNS, basket) == (
        "2026-01-01 07:00:00",
        "Spot",
        "BTC/USDT",
        "5m",
        "200.00 USDT",
        "20.00 USDT",
    )


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
        "BTC/USDT",
        "Buy",
        "100",
        "2",
        "200.00 USDT",
        # A Buy has realised nothing yet, so the column says so.
        "—",
    )


@pytest.mark.parametrize(
    ("realized_pnl", "expected"),
    [
        pytest.param(Decimal("1"), POSITIVE_COLOR, id="profit"),
        pytest.param(Decimal("-1"), NEGATIVE_COLOR, id="loss"),
        pytest.param(Decimal("0"), None, id="break-even"),
    ],
)
def test_pnl_colour_is_read_from_the_figure_not_from_its_label(
    realized_pnl: Decimal, expected: str | None
) -> None:
    """Rewording the label cannot silently drop the colour that goes with it."""
    basket = basket_result(
        realized_pnl=realized_pnl,
    )

    assert colour_of(BASKET_COLUMNS, "Profit / Loss", basket) == expected


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


@pytest.mark.parametrize(
    ("kind", "expected_from"),
    [
        pytest.param(QuickRange.ONE_DAY, date(2026, 8, 15), id="1-day"),
        pytest.param(QuickRange.ONE_WEEK, date(2026, 8, 9), id="1-week"),
        pytest.param(QuickRange.ONE_MONTH, date(2026, 7, 16), id="1-month"),
        pytest.param(QuickRange.THREE_MONTHS, date(2026, 5, 16), id="3-months"),
    ],
)
def test_a_quick_range_counts_its_period_inclusively_up_to_today(
    kind: QuickRange, expected_from: date
) -> None:
    """A week means seven days of trading, today included."""
    assert quick_range_dates(kind, date(2026, 8, 15)) == (
        expected_from,
        date(2026, 8, 15),
    )


def test_a_quick_range_lands_on_a_day_the_shorter_month_has() -> None:
    """A month before the 31st is not the 31st of a month with 28 days."""
    assert quick_range_dates(QuickRange.ONE_MONTH, date(2026, 3, 31))[0] == date(
        2026, 3, 1
    )
    assert quick_range_dates(QuickRange.THREE_MONTHS, date(2026, 5, 31))[0] == date(
        2026, 3, 1
    )


def test_a_quick_range_crosses_the_year() -> None:
    assert quick_range_dates(QuickRange.THREE_MONTHS, date(2026, 1, 10))[0] == date(
        2025, 10, 11
    )
