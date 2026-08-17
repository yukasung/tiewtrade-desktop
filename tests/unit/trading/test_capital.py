from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from tiewtrade.trading.capital import FuturesCapitalPlan, SpotCapitalPlan
from tiewtrade.trading.entry_policy import EntryPolicy
from tiewtrade.trading.futures_policy import FuturesTradingPolicy
from tiewtrade.trading.symbol_rules import SymbolRules


def test_a_spot_entry_spends_what_the_trader_stated() -> None:
    """No division: the figure asked for is the figure an Entry spends."""
    plan = SpotCapitalPlan.from_capital_per_entry(Decimal("1000"))

    assert plan.entry_notional == Decimal("1000")
    # How many Entries a Basket may take is a gate, not a divisor.
    assert not hasattr(plan, "capital_per_entry")
    assert not hasattr(plan, "trading_capital")
    assert not hasattr(plan, "reserve")


def test_spot_capital_plan_is_immutable() -> None:
    plan = SpotCapitalPlan.from_capital_per_entry(Decimal("1000"))

    with pytest.raises(FrozenInstanceError):
        plan.entry_notional = Decimal("1")  # type: ignore[misc]


@pytest.mark.parametrize("max_entries", [1, 3, 22])
def test_spot_capital_requires_even_entries_between_two_and_twenty(
    max_entries: int,
) -> None:
    with pytest.raises(ValueError, match="max_entries"):
        EntryPolicy(max_entries=max_entries)


@pytest.mark.parametrize(
    "capital_per_entry",
    [
        Decimal("0"),
        Decimal("-1"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
    ],
)
def test_spot_capital_plan_rejects_invalid_capital_per_entry(
    capital_per_entry: Decimal,
) -> None:
    with pytest.raises(ValueError, match="capital per Entry"):
        SpotCapitalPlan.from_capital_per_entry(capital_per_entry)


def test_a_futures_entry_commits_the_margin_and_opens_it_levered() -> None:
    """The figure stated is what leaves the wallet, whatever the leverage."""
    plan = FuturesCapitalPlan.from_capital_per_entry(
        Decimal("10000"), FuturesTradingPolicy.v1(leverage=3)
    )

    assert plan.initial_margin_per_entry == Decimal("10000")
    assert plan.target_notional_per_entry == Decimal("30000")
    # The wallet ratios sized nothing here any more, so the plan does not
    # carry halves of a total it was never given.
    assert not hasattr(plan, "trading_capital")
    assert not hasattr(plan, "collateral_buffer")


def test_futures_capital_plan_is_immutable() -> None:
    plan = FuturesCapitalPlan.from_capital_per_entry(
        Decimal("10000"), FuturesTradingPolicy.v1(leverage=3)
    )

    with pytest.raises(FrozenInstanceError):
        plan.initial_margin_per_entry = Decimal("1")  # type: ignore[misc]


@pytest.mark.parametrize(
    "capital_per_entry",
    [
        Decimal("0"),
        Decimal("-1"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
    ],
)
def test_futures_capital_plan_rejects_invalid_capital_per_entry(
    capital_per_entry: Decimal,
) -> None:
    with pytest.raises(ValueError, match="capital per Entry"):
        FuturesCapitalPlan.from_capital_per_entry(
            capital_per_entry, FuturesTradingPolicy.v1(leverage=3)
        )


@pytest.mark.parametrize("symbol", ["", "   "])
def test_symbol_rules_require_a_non_blank_symbol(symbol: str) -> None:
    with pytest.raises(ValueError, match="symbol must not be blank"):
        SymbolRules(
            symbol=symbol,
            tick_size=Decimal("0.01"),
            step_size=Decimal("0.001"),
            min_notional=Decimal("5"),
        )


def test_symbol_rules_preserve_a_nonblank_padded_symbol() -> None:
    rules = SymbolRules(
        symbol=" BTCUSDT ",
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.001"),
        min_notional=Decimal("5"),
    )

    assert rules.symbol == " BTCUSDT "


def test_symbol_rules_round_quantity_and_price_down() -> None:
    rules = SymbolRules(
        symbol="BTCUSDT",
        tick_size=Decimal("0.10"),
        step_size=Decimal("0.001"),
        min_notional=Decimal("5"),
    )

    assert rules.floor_price(Decimal("101.29")) == Decimal("101.20")
    assert rules.floor_quantity(Decimal("0.1239")) == Decimal("0.123")


def test_symbol_rules_require_positive_exchange_filters() -> None:
    with pytest.raises(ValueError, match="tick_size"):
        SymbolRules(
            symbol="BTCUSDT",
            tick_size=Decimal("0"),
            step_size=Decimal("0.001"),
            min_notional=Decimal("5"),
        )


def test_symbol_rules_check_notional_after_rounding() -> None:
    rules = SymbolRules(
        symbol="BTCUSDT",
        tick_size=Decimal("0.10"),
        step_size=Decimal("0.001"),
        min_notional=Decimal("5"),
    )

    assert rules.meets_min_notional(price=Decimal("100"), quantity=Decimal("0.050"))
    assert not rules.meets_min_notional(price=Decimal("100"), quantity=Decimal("0.049"))
