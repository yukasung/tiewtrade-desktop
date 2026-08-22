from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from tests.support.paper_session_setup import (
    configured_futures_session,
    configured_spot_session,
)
from tiewtrade.application.paper_manual_close import ClosePaperPosition
from tiewtrade.trading.session_config import MarketType, TradeMode
from tiewtrade.trading.trade_history import (
    BasketResult,
    BasketStatus,
    FillSide,
    FillSource,
    TradeFill,
)


def test_manual_close_records_exit_fill_before_replacing_session() -> None:
    session = configured_spot_session()
    basket_id = UUID("00000000-0000-0000-0000-000000000456")
    opened_at = datetime(2026, 8, 9, 8, 0, tzinfo=UTC)
    closed_at = datetime(2026, 8, 9, 9, 0, tzinfo=UTC)
    basket = BasketResult(
        basket_id=basket_id,
        session_id=session.config.session_id,
        trade_mode=TradeMode.PAPER,
        market_type=MarketType.SPOT,
        symbol="BTCUSDT",
        timeframe="5m",
        strategy_preset_version=session.config.preset_version,
        opened_at_utc=opened_at,
        closed_at_utc=None,
        entry_count=1,
        invested_notional=Decimal("100"),
        realized_pnl=Decimal("0"),
        status=BasketStatus.OPEN,
    )
    entry = TradeFill(
        fill_id="entry-fill",
        basket_id=basket_id,
        session_id=session.config.session_id,
        order_id="entry-order",
        exchange_trade_id=None,
        side=FillSide.BUY,
        entry_number=1,
        filled_at_utc=opened_at,
        price=Decimal("100"),
        quantity=Decimal("1"),
        notional=Decimal("100"),
        realized_pnl=Decimal("0"),
        source=FillSource.PAPER_EXECUTOR,
    )
    recorded: list[tuple[BasketResult, TradeFill]] = []
    close = ClosePaperPosition(
        find_open_basket=lambda _session_id: basket,
        list_fills=lambda _basket_id: (entry,),
        record_closed=lambda closed, fill: recorded.append((closed, fill)),
        clock=lambda: closed_at,
    )

    close.execute(
        session,
        market_price=lambda: Decimal("110"),
        tick_size=Decimal("0.01"),
    )

    closed, fill = recorded[0]
    assert closed.status is BasketStatus.CLOSED
    assert closed.closed_at_utc == closed_at
    assert fill.order_id == f"manual-close:{basket_id}"
    assert fill.side is FillSide.SELL
    assert fill.entry_number is None
    assert fill.price == Decimal("110")
    assert closed.realized_pnl == fill.realized_pnl


def test_manual_close_fails_when_history_does_not_record_close() -> None:
    session = configured_spot_session()
    basket_id = UUID("00000000-0000-0000-0000-000000000456")
    opened_at = datetime(2026, 8, 9, 8, 0, tzinfo=UTC)
    basket = BasketResult(
        basket_id=basket_id,
        session_id=session.config.session_id,
        trade_mode=TradeMode.PAPER,
        market_type=MarketType.SPOT,
        symbol="BTCUSDT",
        timeframe="5m",
        strategy_preset_version=session.config.preset_version,
        opened_at_utc=opened_at,
        closed_at_utc=None,
        entry_count=1,
        invested_notional=Decimal("100"),
        realized_pnl=Decimal("0"),
        status=BasketStatus.OPEN,
    )
    entry = TradeFill(
        fill_id="entry-fill",
        basket_id=basket_id,
        session_id=session.config.session_id,
        order_id="entry-order",
        exchange_trade_id=None,
        side=FillSide.BUY,
        entry_number=1,
        filled_at_utc=opened_at,
        price=Decimal("100"),
        quantity=Decimal("1"),
        notional=Decimal("100"),
        realized_pnl=Decimal("0"),
        source=FillSource.PAPER_EXECUTOR,
    )
    close = ClosePaperPosition(
        find_open_basket=lambda _session_id: basket,
        list_fills=lambda _basket_id: (entry,),
        record_closed=lambda _closed, _fill: False,
    )

    with pytest.raises(RuntimeError, match="not recorded"):
        close.execute(
            session,
            market_price=lambda: Decimal("110"),
            tick_size=Decimal("0.01"),
        )


def test_manual_close_is_idempotent_after_basket_was_already_closed() -> None:
    session = configured_spot_session()
    close = ClosePaperPosition(
        find_open_basket=lambda _session_id: None,
        list_fills=lambda _basket_id: pytest.fail("fills must not be read"),
        record_closed=lambda _closed, _fill: pytest.fail("close must not repeat"),
    )

    close.execute(
        session,
        market_price=lambda: pytest.fail("market price must not be read"),
        tick_size=Decimal("0.01"),
    )


def test_manual_close_quantizes_futures_short_exit_against_position() -> None:
    session = configured_futures_session(leverage=3)
    basket_id = UUID("00000000-0000-0000-0000-000000000457")
    opened_at = datetime(2026, 8, 9, 8, 0, tzinfo=UTC)
    basket = BasketResult(
        basket_id=basket_id,
        session_id=session.config.session_id,
        trade_mode=TradeMode.PAPER,
        market_type=MarketType.FUTURES,
        symbol="BTCUSDT",
        timeframe="5m",
        strategy_preset_version=session.config.preset_version,
        opened_at_utc=opened_at,
        closed_at_utc=None,
        entry_count=1,
        invested_notional=Decimal("120"),
        realized_pnl=Decimal("0"),
        status=BasketStatus.OPEN,
        leverage=3,
    )
    entry = TradeFill(
        fill_id="short-entry-fill",
        basket_id=basket_id,
        session_id=session.config.session_id,
        order_id="short-entry-order",
        exchange_trade_id=None,
        side=FillSide.SELL,
        entry_number=1,
        filled_at_utc=opened_at,
        price=Decimal("120"),
        quantity=Decimal("1"),
        notional=Decimal("120"),
        realized_pnl=Decimal("0"),
        source=FillSource.PAPER_EXECUTOR,
    )
    recorded: list[tuple[BasketResult, TradeFill]] = []
    close = ClosePaperPosition(
        find_open_basket=lambda _session_id: basket,
        list_fills=lambda _basket_id: (entry,),
        record_closed=lambda closed, fill: recorded.append((closed, fill)),
    )

    close.execute(
        session,
        market_price=lambda: Decimal("110"),
        tick_size=Decimal("0.1"),
    )

    closed, fill = recorded[0]
    assert fill.side is FillSide.BUY
    assert fill.price == Decimal("110")
    assert closed.realized_pnl == Decimal("10")


def test_manual_close_rejects_live_session_before_reading_history() -> None:
    paper_session = configured_spot_session()
    live_session = replace(
        paper_session,
        config=replace(paper_session.config, trade_mode=TradeMode.LIVE),
    )
    close = ClosePaperPosition(
        find_open_basket=lambda _session_id: pytest.fail("history must not be read"),
        list_fills=lambda _basket_id: pytest.fail("fills must not be read"),
        record_closed=lambda _closed, _fill: pytest.fail("close must not run"),
    )

    with pytest.raises(ValueError, match="Paper Session"):
        close.execute(
            live_session,
            market_price=lambda: Decimal("110"),
            tick_size=Decimal("0.01"),
        )


def _basket_with_two_entries() -> tuple[BasketResult, tuple[TradeFill, ...]]:
    """Two Entries taken at 100 and 110; the first has already been sold.

    The store has been counting what that sale realised, the way it counts
    every Entry Exit.
    """
    session = configured_spot_session()
    basket_id = UUID("00000000-0000-0000-0000-000000000458")
    opened_at = datetime(2026, 8, 9, 8, 0, tzinfo=UTC)

    def entry(number: int, price: str, minutes: int) -> TradeFill:
        return TradeFill(
            fill_id=f"entry-fill-{number}",
            basket_id=basket_id,
            session_id=session.config.session_id,
            order_id=f"entry-order-{number}",
            exchange_trade_id=None,
            side=FillSide.BUY,
            entry_number=number,
            filled_at_utc=opened_at + timedelta(minutes=minutes),
            price=Decimal(price),
            quantity=Decimal("1"),
            notional=Decimal(price),
            realized_pnl=Decimal("0"),
            source=FillSource.PAPER_EXECUTOR,
        )

    sale = TradeFill(
        fill_id="exit-fill-1",
        basket_id=basket_id,
        session_id=session.config.session_id,
        order_id="close-entry:1",
        exchange_trade_id=None,
        side=FillSide.SELL,
        entry_number=None,
        filled_at_utc=opened_at + timedelta(minutes=30),
        price=Decimal("106"),
        quantity=Decimal("1"),
        notional=Decimal("106"),
        realized_pnl=Decimal("6"),
        source=FillSource.PAPER_EXECUTOR,
        closes_entry_number=1,
    )
    basket = BasketResult(
        basket_id=basket_id,
        session_id=session.config.session_id,
        trade_mode=TradeMode.PAPER,
        market_type=MarketType.SPOT,
        symbol="BTCUSDT",
        timeframe="5m",
        strategy_preset_version=session.config.preset_version,
        opened_at_utc=opened_at,
        closed_at_utc=None,
        entry_count=2,
        invested_notional=Decimal("210"),
        realized_pnl=Decimal("6"),
        status=BasketStatus.OPEN,
    )
    return basket, (entry(1, "100", 0), entry(2, "110", 10), sale)


def test_closing_at_market_leaves_out_the_entry_that_already_sold() -> None:
    """An Entry that has gone is not part of the position to be closed.

    Counted again, its quantity went into the exit Fill a second time — a
    Basket of one remaining unit sold as two — and the result written over
    what that Entry had already made.
    """
    basket, fills = _basket_with_two_entries()
    recorded: list[tuple[BasketResult, TradeFill]] = []
    close = ClosePaperPosition(
        find_open_basket=lambda _session_id: basket,
        list_fills=lambda _basket_id: fills,
        record_closed=lambda closed, fill: recorded.append((closed, fill)),
        clock=lambda: datetime(2026, 8, 9, 10, 0, tzinfo=UTC),
    )

    close.execute(
        configured_spot_session(),
        market_price=lambda: Decimal("120"),
        tick_size=Decimal("0.01"),
    )

    closed, fill = recorded[0]
    # Entry 2 alone: one unit, and the 10 it made from 110 to 120.
    assert fill.quantity == Decimal("1")
    assert fill.realized_pnl == Decimal("10")
    # The Basket keeps one result: 6 from the Entry that went, 10 from this.
    assert closed.realized_pnl == Decimal("16")
    assert closed.status is BasketStatus.CLOSED


def test_a_basket_whose_entries_have_all_sold_has_nothing_to_close() -> None:
    """Closing what is not there would write an exit Fill for no quantity."""
    basket, fills = _basket_with_two_entries()
    sold_out = (
        *fills,
        replace(
            fills[2],
            fill_id="exit-fill-2",
            order_id="close-entry:2",
            closes_entry_number=2,
        ),
    )
    close = ClosePaperPosition(
        find_open_basket=lambda _session_id: basket,
        list_fills=lambda _basket_id: sold_out,
        record_closed=lambda _closed, _fill: pytest.fail("nothing to close"),
    )

    with pytest.raises(ValueError, match="requires Entry Fills"):
        close.execute(
            configured_spot_session(),
            market_price=lambda: Decimal("120"),
            tick_size=Decimal("0.01"),
        )
