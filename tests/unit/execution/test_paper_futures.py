from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from tiewtrade.execution.paper_futures import PaperFuturesExecutor
from tiewtrade.market_data.candle import Candle
from tiewtrade.strategies.entry_intent import EntryIntent
from tiewtrade.trading.basket import Basket, BasketCloseReason
from tiewtrade.trading.entry_policy import EntryPolicy
from tiewtrade.trading.futures_policy import FuturesTradingPolicy
from tiewtrade.trading.position import PositionSide
from tiewtrade.trading.session_config import MarketType, SessionConfig, TradeMode
from tiewtrade.trading.symbol_rules import SymbolRules


def test_long_entry_fills_at_the_next_open_for_the_target_notional() -> None:
    executor = make_executor(leverage=3)
    intent = long_intent()
    fill_candle = candle(minute=5, open="100")

    fill = executor.fill_entry(intent, fill_candle)

    assert fill is not None
    assert intent.signal_candle.open_time != fill_candle.open_time
    assert fill.side is PositionSide.LONG
    assert fill.price == Decimal("100")
    assert fill.quantity == Decimal("300.0")
    assert fill.filled_at == fill_candle.open_time


def test_short_entry_fills_at_the_next_open() -> None:
    executor = make_executor(leverage=3)

    fill = executor.fill_entry(short_intent(), candle(minute=5, open="100"))

    assert fill is not None
    assert fill.side is PositionSide.SHORT
    assert fill.price == Decimal("100")
    assert fill.quantity == Decimal("300.0")


def test_entry_below_min_notional_returns_none() -> None:
    executor = make_executor(min_notional="50000", leverage=1)

    assert executor.fill_entry(long_intent(), candle(open="100")) is None


def test_entry_rejects_a_lowercase_near_match() -> None:
    current = replace(candle(minute=5, open="100"), symbol="btcusdt")

    with pytest.raises(ValueError) as error:
        make_executor().fill_entry(long_intent(), current)

    assert str(error.value) == (
        "candle symbol must match SymbolRules.symbol: candle='btcusdt', rules='BTCUSDT'"
    )


def test_repeating_the_same_entry_produces_the_same_fill_identity() -> None:
    executor = make_executor(leverage=3)
    intent = long_intent()
    current = candle(minute=5, open="100")

    first = executor.fill_entry(intent, current)
    second = executor.fill_entry(intent, current)

    assert first is not None
    assert first == second
    assert first.order_id == "entry:intent-long"
    assert first.fill_id == (
        "paper:00000000-0000-0000-0000-000000000107:entry:intent-long:fill"
    )


def test_long_take_profit_fills_at_its_target() -> None:
    executor = make_executor()
    basket = long_basket(entry_price="100", take_profit="106")
    current = candle(open="105", high="107", low="104")

    fills = executor.fill_take_profit(basket, current)

    assert len(fills) == 1
    fill = fills[0]
    assert fill.side is PositionSide.LONG
    assert fill.close_reason is BasketCloseReason.TAKE_PROFIT
    assert fill.price == Decimal("106")
    assert fill.quantity == Decimal("2")
    assert fill.entry_number == 1
    assert fill.filled_at == current.close_time
    assert fill.order_id == ("take_profit:00000000-0000-0000-0000-000000000108:1")
    assert fill.fill_id == (
        "paper:00000000-0000-0000-0000-000000000107:"
        "take_profit:00000000-0000-0000-0000-000000000108:1:fill"
    )
    assert fills == executor.fill_take_profit(basket, current)


def test_short_take_profit_fills_at_its_target() -> None:
    executor = make_executor()
    basket = short_basket(entry_price="100", take_profit="94")
    current = candle(open="95", high="96", low="93")

    fills = executor.fill_take_profit(basket, current)

    assert len(fills) == 1
    fill = fills[0]
    assert fill.side is PositionSide.SHORT
    assert fill.close_reason is BasketCloseReason.TAKE_PROFIT
    assert fill.price == Decimal("94")
    assert fill.quantity == Decimal("2")
    assert fill.entry_number == 1
    assert fill.order_id == ("take_profit:00000000-0000-0000-0000-000000000108:1")
    assert fill.fill_id == (
        "paper:00000000-0000-0000-0000-000000000107:"
        "take_profit:00000000-0000-0000-0000-000000000108:1:fill"
    )


def test_each_pair_leaves_at_the_target_its_own_rule_gave_it() -> None:
    """One candle, two prices: a Pair at its own target, the rest at the net.

    Pair 2 paid 60 and leaves at 66; Pair 1 paid 105 and never reached its
    111, so the Basket's own 96 carries it out. Booked as one Fill per Entry,
    each naming the Entry it sold and the price that Entry got.
    """
    executor = make_executor()
    swept = pair_basket()
    current = candle(open="95", high="96", low="94")

    fills = executor.fill_take_profit(swept, current)

    assert [(fill.entry_number, fill.price) for fill in fills] == [
        (3, Decimal("66")),
        (1, Decimal("96")),
        (2, Decimal("96")),
    ]
    assert {fill.order_id for fill in fills} == {
        "take_profit:00000000-0000-0000-0000-000000000108:1",
        "take_profit:00000000-0000-0000-0000-000000000108:2",
        "take_profit:00000000-0000-0000-0000-000000000108:3",
    }


def test_a_pair_that_reached_its_target_goes_without_the_others() -> None:
    executor = make_executor()
    held = pair_basket()

    fills = executor.fill_take_profit(held, candle(open="65", high="66", low="64"))

    assert [(fill.entry_number, fill.price) for fill in fills] == [(3, Decimal("66"))]


def test_take_profit_sells_nothing_until_the_target_is_touched() -> None:
    executor = make_executor()

    assert (
        executor.fill_take_profit(
            long_basket(entry_price="100", take_profit="106"),
            candle(open="100", high="105", low="99"),
        )
        == ()
    )
    assert (
        executor.fill_take_profit(
            short_basket(entry_price="100", take_profit="94"),
            candle(open="100", high="101", low="95"),
        )
        == ()
    )


def test_take_profit_rejects_a_padded_near_match() -> None:
    current = replace(
        candle(open="105", high="107", low="104"),
        symbol=" BTCUSDT ",
    )

    with pytest.raises(ValueError) as error:
        make_executor().fill_take_profit(
            long_basket(entry_price="100", take_profit="106"),
            current,
        )

    assert str(error.value) == (
        "candle symbol must match SymbolRules.symbol: "
        "candle=' BTCUSDT ', rules='BTCUSDT'"
    )


def test_executor_rejects_non_paper_or_non_futures_configuration() -> None:
    session = futures_session()
    rules = symbol_rules()

    with pytest.raises(ValueError, match="Paper Futures"):
        PaperFuturesExecutor(replace(session, trade_mode=TradeMode.LIVE), rules)

    spot_session = SessionConfig(
        session_id=session.session_id,
        preset_version=session.preset_version,
        market_type=MarketType.SPOT,
        trade_mode=TradeMode.PAPER,
        capital_per_entry=session.capital_per_entry,
        entry_policy=session.entry_policy,
    )
    with pytest.raises(ValueError, match="Paper Futures"):
        PaperFuturesExecutor(spot_session, rules)


def make_executor(
    *,
    leverage: int = 1,
    min_notional: str = "5",
) -> PaperFuturesExecutor:
    return PaperFuturesExecutor(
        futures_session(
            leverage=leverage,
        ),
        symbol_rules(min_notional=min_notional),
    )


def futures_session(*, leverage: int = 1) -> SessionConfig:
    return SessionConfig(
        session_id=UUID("00000000-0000-0000-0000-000000000107"),
        preset_version="rsi-step-grid-v1",
        market_type=MarketType.FUTURES,
        trade_mode=TradeMode.PAPER,
        capital_per_entry=Decimal("10000.0"),
        entry_policy=EntryPolicy(max_entries=2),
        futures_policy=FuturesTradingPolicy.v1(leverage=leverage),
    )


def symbol_rules(*, min_notional: str = "5") -> SymbolRules:
    return SymbolRules(
        symbol="BTCUSDT",
        tick_size=Decimal("0.1"),
        step_size=Decimal("0.1"),
        min_notional=Decimal(min_notional),
    )


def candle(
    *,
    open: str,
    high: str | None = None,
    low: str | None = None,
    minute: int = 0,
) -> Candle:
    open_price = Decimal(open)
    return Candle(
        symbol="BTCUSDT",
        timeframe="5m",
        open_time=datetime(2026, 1, 1, 0, minute, tzinfo=UTC),
        open=open_price,
        high=Decimal(high) if high is not None else open_price,
        low=Decimal(low) if low is not None else open_price,
        close=open_price,
    )


def long_intent() -> EntryIntent:
    return entry_intent(PositionSide.LONG)


def short_intent() -> EntryIntent:
    return entry_intent(PositionSide.SHORT)


def entry_intent(side: PositionSide) -> EntryIntent:
    session = futures_session()
    return EntryIntent(
        intent_id=f"intent-{side}",
        session_id=session.session_id,
        preset_version=session.preset_version,
        entry_number=1,
        signal_candle=candle(open="100"),
        atr=Decimal("2"),
        side=side,
    )


def long_basket(*, entry_price: str, take_profit: str) -> Basket:
    return basket(
        position_side=PositionSide.LONG,
        entry_price=entry_price,
        take_profit=take_profit,
    )


def short_basket(*, entry_price: str, take_profit: str) -> Basket:
    return basket(
        position_side=PositionSide.SHORT,
        entry_price=entry_price,
        take_profit=take_profit,
    )


def basket(
    *, position_side: PositionSide, entry_price: str, take_profit: str
) -> Basket:
    result = Basket(
        basket_id=UUID("00000000-0000-0000-0000-000000000108"),
        policy=EntryPolicy(max_entries=2),
        take_profit_atr_multiplier=Decimal("3"),
        position_side=position_side,
    )
    result.add_entry(
        price=Decimal(entry_price),
        quantity=Decimal("2"),
        filled_at=datetime(2026, 1, 1, tzinfo=UTC),
        atr=Decimal("2"),
        tick_size=Decimal("0.1"),
        position_side=position_side,
    )
    assert result.take_profit_price == Decimal(take_profit)
    return result


def pair_basket() -> Basket:
    """Two Pairs at different averages, so their targets differ.

    Entries 1 and 2 make Pair 1, which paid 105 and leaves at 111. Entry 3
    opens Pair 2 alone at 60 and leaves at 66. The Basket paid 90 across all
    three, so the net it takes everything at sits at 96, between them.
    """
    result = Basket(
        basket_id=UUID("00000000-0000-0000-0000-000000000108"),
        policy=EntryPolicy(max_entries=4),
        take_profit_atr_multiplier=Decimal("3"),
    )
    for index, price in enumerate(("100", "110", "60")):
        result.add_entry(
            price=Decimal(price),
            quantity=Decimal("2"),
            filled_at=datetime(2026, 1, 1, 0, 5 * index, tzinfo=UTC),
            atr=Decimal("2"),
            tick_size=Decimal("0.1"),
        )
    assert result.pair_take_profit_price(1) == Decimal("111")
    assert result.pair_take_profit_price(2) == Decimal("66")
    assert result.take_profit_price == Decimal("96")
    return result
