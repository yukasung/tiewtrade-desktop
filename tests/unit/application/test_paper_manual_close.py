from dataclasses import replace
from datetime import UTC, datetime
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
        realized_pnl=Decimal("-0.1"),
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
        realized_pnl=Decimal("-0.1"),
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
        realized_pnl=Decimal("-0.12"),
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
