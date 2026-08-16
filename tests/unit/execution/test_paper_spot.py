import logging
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from tiewtrade.execution.paper_spot import ENTRY_REJECTED_EVENT, PaperSpotExecutor
from tiewtrade.market_data.candle import Candle
from tiewtrade.strategies.entry_intent import EntryIntent
from tiewtrade.strategies.rsi_step_grid.preset import RsiStepGridPreset
from tiewtrade.trading.basket import Basket
from tiewtrade.trading.entry_policy import EntryPolicy
from tiewtrade.trading.session_config import (
    MarketType,
    SessionConfig,
    TradeMode,
)
from tiewtrade.trading.spot_policy import SpotTradingPolicy
from tiewtrade.trading.symbol_rules import SymbolRules


def test_entry_fill_uses_session_capital_costs_and_symbol_rules() -> None:
    session = SessionConfig(
        session_id=UUID("00000000-0000-0000-0000-000000000079"),
        preset_version="rsi-step-grid-v1",
        market_type=MarketType.SPOT,
        trade_mode=TradeMode.PAPER,
        available_capital=Decimal("1000"),
        slippage_bps=Decimal("25"),
        entry_policy=EntryPolicy(max_entries=4),
        spot_policy=SpotTradingPolicy(trading_capital_ratio=Decimal("0.6")),
    )
    executor = PaperSpotExecutor(
        session,
        SymbolRules(
            symbol="BTCUSDT",
            tick_size=Decimal("0.01"),
            step_size=Decimal("0.001"),
            min_notional=Decimal("5"),
        ),
    )
    signal_candle = candle_at(0, open_price="100", high="102", low="99", close="101")
    fill_candle = candle_at(5, open_price="101.23", high="103", low="100", close="102")

    fill = executor.fill_entry(intent(session, signal_candle), fill_candle)

    assert fill is not None
    assert fill.price == Decimal("101.49")
    assert fill.quantity == Decimal("1.477")
    assert fill.filled_at == fill_candle.open_time
    entry_order_id = f"entry:{intent(session, signal_candle).intent_id}"
    assert fill.order_id == entry_order_id
    assert fill.fill_id == f"paper:{session.session_id}:{entry_order_id}:fill"


def test_entry_fill_rounds_buy_slippage_up_to_the_next_tick() -> None:
    session = SessionConfig(
        session_id=UUID("00000000-0000-0000-0000-000000000079"),
        preset_version="rsi-step-grid-v1",
        market_type=MarketType.SPOT,
        trade_mode=TradeMode.PAPER,
        available_capital=Decimal("1000"),
        slippage_bps=Decimal("0.5"),
        entry_policy=EntryPolicy(max_entries=4),
        spot_policy=SpotTradingPolicy(trading_capital_ratio=Decimal("0.6")),
    )
    executor = PaperSpotExecutor(
        session,
        SymbolRules(
            symbol="BTCUSDT",
            tick_size=Decimal("0.01"),
            step_size=Decimal("0.001"),
            min_notional=Decimal("5"),
        ),
    )
    signal_candle = candle_at(0, open_price="100", high="102", low="99", close="101")
    fill_candle = candle_at(5, open_price="100", high="102", low="99", close="101")

    fill = executor.fill_entry(intent(session, signal_candle), fill_candle)

    assert fill is not None
    assert fill.price == Decimal("100.01")


def test_entry_fill_rejects_quantity_below_minimum_notional() -> None:
    session = SessionConfig(
        session_id=UUID("00000000-0000-0000-0000-000000000079"),
        preset_version="rsi-step-grid-v1",
        market_type=MarketType.SPOT,
        trade_mode=TradeMode.PAPER,
        available_capital=Decimal("100"),
        slippage_bps=Decimal("0"),
        entry_policy=EntryPolicy(max_entries=4),
        spot_policy=SpotTradingPolicy(trading_capital_ratio=Decimal("0.6")),
    )
    executor = PaperSpotExecutor(
        session,
        SymbolRules(
            symbol="BTCUSDT",
            tick_size=Decimal("0.01"),
            step_size=Decimal("0.001"),
            min_notional=Decimal("20"),
        ),
    )
    signal_candle = candle_at(0, open_price="100", high="102", low="99", close="101")
    fill_candle = candle_at(5, open_price="100", high="102", low="99", close="101")

    assert executor.fill_entry(intent(session, signal_candle), fill_candle) is None


def test_rejected_entry_warns_with_the_numbers_needed_to_fix_the_setup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A silently rejected entry is indistinguishable from a dead bot, so the
    rejection must say what the budget was and what one lot actually costs.
    """
    session = SessionConfig(
        session_id=UUID("00000000-0000-0000-0000-000000000079"),
        preset_version="rsi-buy-only-v1",
        market_type=MarketType.SPOT,
        trade_mode=TradeMode.PAPER,
        available_capital=Decimal("100"),
        slippage_bps=Decimal("0"),
        entry_policy=EntryPolicy(max_entries=10),
        spot_policy=SpotTradingPolicy(trading_capital_ratio=Decimal("0.8")),
    )
    executor = PaperSpotExecutor(
        session,
        SymbolRules(
            symbol="BTCUSDT",
            tick_size=Decimal("0.01"),
            step_size=Decimal("0.001"),
            min_notional=Decimal("5"),
        ),
    )
    signal_candle = candle_at(
        0, open_price="64000", high="64100", low="63900", close="64050"
    )
    fill_candle = candle_at(
        5, open_price="64000", high="64100", low="63900", close="64050"
    )

    with caplog.at_level(logging.WARNING, logger="tiewtrade.execution.paper_spot"):
        assert executor.fill_entry(intent(session, signal_candle), fill_candle) is None

    records = [
        record
        for record in caplog.records
        if getattr(record, "event_name", None) == ENTRY_REJECTED_EVENT
    ]
    assert len(records) == 1
    record = records[0]
    assert record.levelno == logging.WARNING
    assert getattr(record, "entry_budget_notional") == "8.0"  # noqa: B009
    assert getattr(record, "quantity_after_lot_rounding") == "0.000"  # noqa: B009
    assert getattr(record, "minimum_affordable_entry_notional") == "64.000"  # noqa: B009


def test_successful_entry_does_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    executor = PaperSpotExecutor(spot_session(), spot_rules())
    signal_candle = candle_at(0, open_price="100", high="102", low="99", close="101")
    fill_candle = candle_at(5, open_price="100", high="102", low="99", close="101")

    with caplog.at_level(logging.WARNING, logger="tiewtrade.execution.paper_spot"):
        assert executor.fill_entry(intent(spot_session(), signal_candle), fill_candle)

    assert caplog.records == []


def test_entry_fill_rejects_a_lowercase_near_match() -> None:
    executor = PaperSpotExecutor(spot_session(), spot_rules())
    signal_candle = candle_at(0, open_price="100", high="102", low="99", close="101")
    fill_candle = replace(
        candle_at(5, open_price="101", high="103", low="100", close="102"),
        symbol="btcusdt",
    )

    with pytest.raises(ValueError) as error:
        executor.fill_entry(intent(spot_session(), signal_candle), fill_candle)

    assert str(error.value) == (
        "candle symbol must match SymbolRules.symbol: candle='btcusdt', rules='BTCUSDT'"
    )


def test_take_profit_target_touch_uses_sell_floor_price_and_exact_fee() -> None:
    session = SessionConfig(
        session_id=UUID("00000000-0000-0000-0000-000000000079"),
        preset_version="rsi-step-grid-v1",
        market_type=MarketType.SPOT,
        trade_mode=TradeMode.PAPER,
        available_capital=Decimal("1000"),
        slippage_bps=Decimal("25"),
        entry_policy=EntryPolicy(max_entries=4),
        spot_policy=SpotTradingPolicy(trading_capital_ratio=Decimal("0.6")),
    )
    rules = SymbolRules(
        symbol="BTCUSDT",
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.001"),
        min_notional=Decimal("5"),
    )
    basket = Basket(
        UUID("00000000-0000-0000-0000-000000000092"),
        EntryPolicy(max_entries=4),
        Decimal("1"),
    )
    basket.add_entry(
        price=Decimal("100"),
        quantity=Decimal("2"),
        filled_at=datetime(2026, 1, 1, tzinfo=UTC),
        atr=Decimal("1"),
        tick_size=rules.tick_size,
    )
    touch_candle = candle_at(5, open_price="100", high="101", low="99", close="100")

    fills = PaperSpotExecutor(session, rules).fill_take_profit(
        basket, touch_candle, filled_at=touch_candle.close_time
    )

    assert len(fills) == 1
    fill = fills[0]
    assert fill.price == Decimal("100.74")
    assert fill.quantity == Decimal("2")
    assert fill.filled_at == touch_candle.close_time
    # The Fill names the Entry it sold, which is what lets the others stay.
    assert fill.entry_number == 1
    exit_order_id = f"take-profit:{basket.basket_id}:1"
    assert fill.order_id == exit_order_id
    assert fill.fill_id == f"paper:{session.session_id}:{exit_order_id}:fill"


def test_take_profit_rejects_a_padded_near_match() -> None:
    rules = spot_rules()
    basket = Basket(
        UUID("00000000-0000-0000-0000-000000000092"),
        EntryPolicy(max_entries=4),
        Decimal("1"),
    )
    basket.add_entry(
        price=Decimal("100"),
        quantity=Decimal("2"),
        filled_at=datetime(2026, 1, 1, tzinfo=UTC),
        atr=Decimal("1"),
        tick_size=rules.tick_size,
    )
    candle = replace(
        candle_at(5, open_price="100", high="101", low="99", close="100"),
        symbol=" BTCUSDT ",
    )

    with pytest.raises(ValueError) as error:
        PaperSpotExecutor(spot_session(), rules).fill_take_profit(
            basket, candle, filled_at=candle.close_time
        )

    assert str(error.value) == (
        "candle symbol must match SymbolRules.symbol: "
        "candle=' BTCUSDT ', rules='BTCUSDT'"
    )


def test_take_profit_rejects_a_non_positive_sell_price_after_quantization() -> None:
    session = SessionConfig(
        session_id=UUID("00000000-0000-0000-0000-000000000079"),
        preset_version="rsi-step-grid-v1",
        market_type=MarketType.SPOT,
        trade_mode=TradeMode.PAPER,
        available_capital=Decimal("1000"),
        slippage_bps=Decimal("9999.999"),
        entry_policy=EntryPolicy(max_entries=4),
        spot_policy=SpotTradingPolicy(trading_capital_ratio=Decimal("0.6")),
    )
    rules = SymbolRules(
        symbol="BTCUSDT",
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.001"),
        min_notional=Decimal("0.001"),
    )
    basket = Basket(
        UUID("00000000-0000-0000-0000-000000000092"),
        EntryPolicy(max_entries=4),
        Decimal("1"),
    )
    basket.add_entry(
        price=Decimal("0.01"),
        quantity=Decimal("1"),
        filled_at=datetime(2026, 1, 1, tzinfo=UTC),
        atr=Decimal("0"),
        tick_size=rules.tick_size,
    )
    touch_candle = candle_at(5, open_price="1", high="1", low="0.01", close="1")

    assert (
        PaperSpotExecutor(session, rules).fill_take_profit(
            basket, touch_candle, filled_at=touch_candle.close_time
        )
        == ()
    )


def test_stop_loss_target_touch_uses_sell_floor_price_and_exact_fee() -> None:
    session = SessionConfig(
        session_id=UUID("00000000-0000-0000-0000-000000000079"),
        preset_version="rsi-buy-only-v1",
        market_type=MarketType.SPOT,
        trade_mode=TradeMode.PAPER,
        available_capital=Decimal("1000"),
        slippage_bps=Decimal("25"),
        entry_policy=EntryPolicy(max_entries=4),
        spot_policy=SpotTradingPolicy(trading_capital_ratio=Decimal("0.6")),
    )
    rules = SymbolRules(
        symbol="BTCUSDT",
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.001"),
        min_notional=Decimal("5"),
    )
    basket = Basket(
        UUID("00000000-0000-0000-0000-000000000092"),
        EntryPolicy(max_entries=4),
        Decimal("1"),
        stop_loss_atr_multiplier=Decimal("1"),
    )
    basket.add_entry(
        price=Decimal("100"),
        quantity=Decimal("2"),
        filled_at=datetime(2026, 1, 1, tzinfo=UTC),
        atr=Decimal("1"),
        tick_size=rules.tick_size,
    )
    touch_candle = candle_at(5, open_price="99", high="100", low="98", close="99")

    fill = PaperSpotExecutor(session, rules).fill_stop_loss(
        basket, touch_candle, filled_at=touch_candle.close_time
    )

    assert fill is not None
    assert fill.price == Decimal("98.75")
    assert fill.quantity == Decimal("2")
    assert fill.filled_at == touch_candle.close_time
    exit_order_id = f"stop-loss:{basket.basket_id}"
    assert fill.order_id == exit_order_id
    assert fill.fill_id == f"paper:{session.session_id}:{exit_order_id}:fill"


def test_stop_loss_does_not_trigger_when_basket_has_no_multiplier_configured() -> None:
    rules = spot_rules()
    basket = Basket(
        UUID("00000000-0000-0000-0000-000000000092"),
        EntryPolicy(max_entries=4),
        Decimal("1"),
    )
    basket.add_entry(
        price=Decimal("100"),
        quantity=Decimal("2"),
        filled_at=datetime(2026, 1, 1, tzinfo=UTC),
        atr=Decimal("1"),
        tick_size=rules.tick_size,
    )
    touch_candle = candle_at(5, open_price="90", high="91", low="80", close="90")

    fill = PaperSpotExecutor(spot_session(), rules).fill_stop_loss(
        basket, touch_candle, filled_at=touch_candle.close_time
    )

    assert fill is None


def test_stop_loss_does_not_trigger_when_candle_low_stays_above_target() -> None:
    rules = spot_rules()
    basket = Basket(
        UUID("00000000-0000-0000-0000-000000000092"),
        EntryPolicy(max_entries=4),
        Decimal("1"),
        stop_loss_atr_multiplier=Decimal("1"),
    )
    basket.add_entry(
        price=Decimal("100"),
        quantity=Decimal("2"),
        filled_at=datetime(2026, 1, 1, tzinfo=UTC),
        atr=Decimal("1"),
        tick_size=rules.tick_size,
    )
    above_target_candle = candle_at(
        5, open_price="100", high="101", low="99.5", close="100"
    )

    fill = PaperSpotExecutor(spot_session(), rules).fill_stop_loss(
        basket, above_target_candle, filled_at=above_target_candle.close_time
    )

    assert fill is None


def test_stop_loss_rejects_a_padded_near_match() -> None:
    rules = spot_rules()
    basket = Basket(
        UUID("00000000-0000-0000-0000-000000000092"),
        EntryPolicy(max_entries=4),
        Decimal("1"),
        stop_loss_atr_multiplier=Decimal("1"),
    )
    basket.add_entry(
        price=Decimal("100"),
        quantity=Decimal("2"),
        filled_at=datetime(2026, 1, 1, tzinfo=UTC),
        atr=Decimal("1"),
        tick_size=rules.tick_size,
    )
    candle = replace(
        candle_at(5, open_price="99", high="100", low="98", close="99"),
        symbol=" BTCUSDT ",
    )

    with pytest.raises(ValueError) as error:
        PaperSpotExecutor(spot_session(), rules).fill_stop_loss(
            basket, candle, filled_at=candle.close_time
        )

    assert str(error.value) == (
        "candle symbol must match SymbolRules.symbol: "
        "candle=' BTCUSDT ', rules='BTCUSDT'"
    )


def spot_session() -> SessionConfig:
    return SessionConfig(
        session_id=UUID("00000000-0000-0000-0000-000000000079"),
        preset_version="rsi-step-grid-v1",
        market_type=MarketType.SPOT,
        trade_mode=TradeMode.PAPER,
        available_capital=Decimal("1000"),
        slippage_bps=Decimal("0"),
        entry_policy=EntryPolicy(max_entries=4),
        spot_policy=SpotTradingPolicy(trading_capital_ratio=Decimal("0.6")),
    )


def spot_rules() -> SymbolRules:
    return SymbolRules(
        symbol="BTCUSDT",
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.001"),
        min_notional=Decimal("5"),
    )


def candle_at(
    minute: int,
    *,
    open_price: str,
    high: str,
    low: str,
    close: str,
) -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timeframe="5m",
        open_time=datetime(2026, 1, 1, 0, minute, tzinfo=UTC),
        open=Decimal(open_price),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
    )


def intent(session: SessionConfig, signal_candle: Candle) -> EntryIntent:
    return EntryIntent(
        intent_id="intent-1",
        session_id=session.session_id,
        preset_version=RsiStepGridPreset.v1().version,
        entry_number=1,
        signal_candle=signal_candle,
        atr=Decimal("2"),
    )
