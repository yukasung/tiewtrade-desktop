from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tiewtrade.application.paper_basket_projection import (
    futures_basket_snapshot,
    position_after_snapshot,
    position_without_entries,
    spot_basket_snapshot,
)
from tiewtrade.application.paper_futures_session import (
    PaperFuturesSessionSnapshot,
    PaperFuturesSessionState,
)
from tiewtrade.application.paper_spot_session import (
    PaperSpotSessionSnapshot,
    PaperSpotSessionState,
)
from tiewtrade.application.trading_workspace import BasketEntryGate
from tiewtrade.execution.paper_futures import PaperFuturesEntryFill
from tiewtrade.execution.paper_spot import PaperSpotEntryFill
from tiewtrade.market_data.candle import Candle
from tiewtrade.trading.capital import FuturesCapitalPlan
from tiewtrade.trading.entry_pair import EntryGate, EntryGateState
from tiewtrade.trading.position import PositionSide

FILLED_AT = datetime(2026, 1, 1, 9, tzinfo=UTC)
OBSERVED_AT = datetime(2026, 1, 1, 10, tzinfo=UTC)
OPEN_GATE = EntryGate(EntryGateState.OPEN)


def spot_fill(price: str, quantity: str, *, minute: int = 0) -> PaperSpotEntryFill:
    return PaperSpotEntryFill(
        order_id=f"order-{minute}",
        fill_id=f"fill-{minute}",
        intent_id=f"intent-{minute}",
        price=Decimal(price),
        quantity=Decimal(quantity),
        filled_at=FILLED_AT.replace(minute=minute),
    )


def futures_fill(price: str, quantity: str) -> PaperFuturesEntryFill:
    return PaperFuturesEntryFill(
        order_id="order",
        fill_id="fill",
        intent_id="intent",
        side=PositionSide.SHORT,
        price=Decimal(price),
        quantity=Decimal(quantity),
        filled_at=FILLED_AT,
    )


def candle(close: str) -> Candle:
    price = Decimal(close)
    return Candle(
        symbol="BTCUSDT",
        timeframe="5m",
        open_time=OBSERVED_AT,
        open=price,
        high=price,
        low=price,
        close=price,
    )


def spot_snapshot(
    *,
    basket_entry_count: int,
    take_profit_price: Decimal | None = Decimal("110"),
    entry_gate: EntryGate | None = OPEN_GATE,
    entry_completed_pairs: int = 0,
) -> PaperSpotSessionSnapshot:
    return PaperSpotSessionSnapshot(
        accepted=True,
        state=PaperSpotSessionState.ACTIVE,
        failure_reason=None,
        pending_intent=None,
        entry_fill=None,
        entry_atr=None,
        entry_number=None,
        entry_exits=(),
        exit_fill=None,
        closed_basket=None,
        closed_basket_count=0,
        basket_id=None,
        basket_entry_count=basket_entry_count,
        take_profit_price=take_profit_price,
        entry_targets=(),
        entry_gate=entry_gate,
        waived_pair=None,
        waived_at=None,
        entry_completed_pairs=entry_completed_pairs,
    )


def futures_snapshot(
    *,
    basket_entry_count: int = 1,
    position_side: PositionSide | None = PositionSide.SHORT,
) -> PaperFuturesSessionSnapshot:
    return PaperFuturesSessionSnapshot(
        accepted=True,
        state=PaperFuturesSessionState.ACTIVE,
        pending_intent=None,
        entry_fill=None,
        entry_number=None,
        entry_exits=(),
        closed_basket=None,
        basket_id=None,
        basket_entry_count=basket_entry_count,
        position_side=position_side,
        take_profit_price=Decimal("90"),
        entry_targets=(),
        account_equity=Decimal("200000"),
        capital_plan=FuturesCapitalPlan(
            initial_margin_per_entry=Decimal("10000"),
            target_notional_per_entry=Decimal("30000"),
        ),
        failure_reason=None,
        entry_gate=OPEN_GATE,
        entry_completed_pairs=0,
    )


def test_each_entry_is_kept_beside_the_average_it_feeds() -> None:
    """The average is derived; the Entries are what actually happened."""
    first = position_after_snapshot(
        None,
        entry_fill=spot_fill("100", "1", minute=0),
        basket_entry_count=1,
        entry_number=1,
    )
    second = position_after_snapshot(
        first,
        entry_fill=spot_fill("120", "1", minute=5),
        basket_entry_count=2,
        entry_number=2,
    )

    assert second is not None
    assert second.total_quantity == Decimal("2")
    assert second.average_entry_price == Decimal("110")
    assert [entry.price for entry in second.entries] == [
        Decimal("100"),
        Decimal("120"),
    ]
    assert [entry.entry_number for entry in second.entries] == [1, 2]


def test_a_candle_without_a_fill_leaves_the_position_untouched() -> None:
    opened = position_after_snapshot(
        None,
        entry_fill=spot_fill("100", "1"),
        basket_entry_count=1,
        entry_number=1,
    )

    unchanged = position_after_snapshot(opened, entry_fill=None, basket_entry_count=1)

    assert unchanged is opened


def test_a_closed_basket_leaves_no_position() -> None:
    opened = position_after_snapshot(
        None,
        entry_fill=spot_fill("100", "1"),
        basket_entry_count=1,
        entry_number=1,
    )

    assert (
        position_after_snapshot(opened, entry_fill=None, basket_entry_count=0) is None
    )


def test_an_open_basket_without_position_facts_fails_closed() -> None:
    """Reporting a Basket whose position is unknown would invent one."""
    with pytest.raises(ValueError, match="authoritative position facts"):
        position_after_snapshot(None, entry_fill=None, basket_entry_count=1)


def test_spot_marks_every_entry_on_its_own_price() -> None:
    position = position_after_snapshot(
        position_after_snapshot(
            None,
            entry_fill=spot_fill("100", "1", minute=0),
            basket_entry_count=1,
            entry_number=1,
        ),
        entry_fill=spot_fill("120", "1", minute=5),
        basket_entry_count=2,
        entry_number=2,
    )

    basket = spot_basket_snapshot(
        spot_snapshot(basket_entry_count=2), position, candle("110"), OBSERVED_AT
    )

    assert basket is not None
    assert basket.market_type == "spot"
    # Bought at 100 and at 120, marked at 110: one Entry up, the other down,
    # which the average alone reports as nothing happening at all.
    assert [entry.unrealized_pnl for entry in basket.entries] == [
        Decimal("10"),
        Decimal("-10"),
    ]
    assert basket.unrealized_pnl == Decimal("0")
    marked = [
        entry.unrealized_pnl
        for entry in basket.entries
        if entry.unrealized_pnl is not None
    ]
    assert len(marked) == len(basket.entries)
    assert sum(marked, Decimal("0")) == basket.unrealized_pnl
    assert [entry.notional for entry in basket.entries] == [
        Decimal("100"),
        Decimal("120"),
    ]


def test_a_basket_shows_the_entry_that_outlived_the_one_that_sold() -> None:
    """Entry 1 sold at its target; Entry 2 is the whole open position now.

    The Basket counts one Entry and calls it 2, because a number is not handed
    back when an Entry leaves. A snapshot that could not say that took the
    Position tab down every candle after a Take Profit.
    """
    position = position_without_entries(
        position_after_snapshot(
            position_after_snapshot(
                None,
                entry_fill=spot_fill("100", "1", minute=0),
                basket_entry_count=1,
                entry_number=1,
            ),
            entry_fill=spot_fill("120", "1", minute=5),
            basket_entry_count=2,
            entry_number=2,
        ),
        [1],
    )

    basket = spot_basket_snapshot(
        spot_snapshot(basket_entry_count=1), position, candle("110"), OBSERVED_AT
    )

    assert basket is not None
    assert basket.entry_count == 1
    assert [entry.entry_number for entry in basket.entries] == [2]
    assert basket.entries[0].unrealized_pnl == Decimal("-10")


def test_a_short_basket_marks_its_entries_the_way_round_it_earns() -> None:
    """A Futures short gains as price falls, and every Entry has to say so."""
    position = position_after_snapshot(
        None,
        entry_fill=futures_fill("100", "2"),
        basket_entry_count=1,
        entry_number=1,
    )

    basket = futures_basket_snapshot(
        futures_snapshot(), position, candle("90"), OBSERVED_AT
    )

    assert basket is not None
    assert basket.market_type == "futures"
    assert basket.unrealized_pnl == Decimal("20")
    assert [entry.unrealized_pnl for entry in basket.entries] == [Decimal("20")]


def test_a_basket_with_no_entries_is_no_basket() -> None:
    assert (
        spot_basket_snapshot(
            spot_snapshot(basket_entry_count=0), None, candle("110"), OBSERVED_AT
        )
        is None
    )


def test_a_futures_basket_without_a_side_fails_closed() -> None:
    """Which way a Futures Basket earns cannot be guessed."""
    position = position_after_snapshot(
        None,
        entry_fill=futures_fill("100", "2"),
        basket_entry_count=1,
        entry_number=1,
    )

    with pytest.raises(ValueError, match="position_side"):
        futures_basket_snapshot(
            futures_snapshot(position_side=None),
            position,
            candle("90"),
            OBSERVED_AT,
        )


def test_a_cooling_basket_carries_the_month_it_reopens() -> None:
    position = position_after_snapshot(
        None,
        entry_fill=spot_fill("100", "1"),
        basket_entry_count=1,
        entry_number=1,
    )
    basket = spot_basket_snapshot(
        spot_snapshot(
            basket_entry_count=1,
            entry_gate=EntryGate(EntryGateState.COOLDOWN),
        ),
        position,
        candle("110"),
        OBSERVED_AT,
    )

    assert basket is not None
    assert basket.entry_gate is BasketEntryGate.COOLDOWN


def test_a_basket_snapshot_carries_the_pairs_it_has_completed() -> None:
    """The count a row reads its own gate against travels with the Basket."""
    position = position_after_snapshot(
        None,
        entry_fill=spot_fill("100", "1"),
        basket_entry_count=1,
        entry_number=1,
    )
    basket = spot_basket_snapshot(
        spot_snapshot(basket_entry_count=1, entry_completed_pairs=2),
        position,
        candle("110"),
        OBSERVED_AT,
    )

    assert basket is not None
    assert basket.completed_pairs == 2


@pytest.mark.parametrize(
    ("take_profit_price", "entry_gate", "expected"),
    [
        pytest.param(
            None, EntryGate(EntryGateState.OPEN), "take_profit_price", id="tp"
        ),
        pytest.param(Decimal("110"), None, "entry_gate", id="gate"),
    ],
)
def test_an_open_basket_missing_a_required_fact_fails_closed(
    take_profit_price: Decimal | None,
    entry_gate: EntryGate | None,
    expected: str,
) -> None:
    position = position_after_snapshot(
        None,
        entry_fill=spot_fill("100", "1"),
        basket_entry_count=1,
        entry_number=1,
    )

    with pytest.raises(ValueError, match=expected):
        spot_basket_snapshot(
            spot_snapshot(
                basket_entry_count=1,
                take_profit_price=take_profit_price,
                entry_gate=entry_gate,
            ),
            position,
            candle("110"),
            OBSERVED_AT,
        )
