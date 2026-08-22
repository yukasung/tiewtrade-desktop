from dataclasses import fields, replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tests.support.paper_session_setup import configured_spot_session
from tests.support.workspace_snapshots import basket_entries
from tiewtrade.application.trading_workspace import (
    BasketEntryGate,
    BasketSnapshot,
    BotRuntimeState,
    DataFreshness,
    PositionBasketTabSnapshot,
    TradingWorkspaceSnapshot,
    WorkspaceReadState,
    WorkspaceTabState,
    configured_workspace_snapshot,
    empty_position_basket_tab,
    empty_workspace_snapshot,
    failed_position_basket_tab,
    failed_workspace_snapshot,
    loading_position_basket_tab,
    loading_workspace_snapshot,
    paper_runtime_blocked_workspace_snapshot,
    paper_runtime_workspace_snapshot,
    ready_position_basket_tab,
    stale_position_basket_tab,
    stale_workspace_snapshot,
)
from tiewtrade.trading.entry_pair import EntryGateState

OBSERVED_AT = datetime(2026, 8, 1, 12, tzinfo=UTC)


def test_configured_workspace_exposes_only_position_projection() -> None:
    snapshot = configured_workspace_snapshot(
        configured_spot_session(), observed_at_utc=OBSERVED_AT
    )

    assert snapshot.read_state is WorkspaceReadState.READY
    assert snapshot.header is not None
    assert snapshot.header.symbol == "BTCUSDT"
    assert snapshot.position_basket == empty_position_basket_tab()
    assert [field.name for field in fields(snapshot)] == [
        "read_state",
        "header",
        "position_basket",
        "data_as_of_utc",
        "message",
    ]


def test_workspace_state_changes_preserve_position_basket_facts() -> None:
    configured = configured_workspace_snapshot(
        configured_spot_session(), observed_at_utc=OBSERVED_AT
    )
    basket = _basket()
    ready = replace(
        configured,
        position_basket=ready_position_basket_tab(basket, observed_at_utc=OBSERVED_AT),
    )

    for snapshot in (
        loading_workspace_snapshot(ready),
        failed_workspace_snapshot(ready, "Workspace data is unavailable"),
        stale_workspace_snapshot(ready),
    ):
        assert snapshot.basket is basket


def test_position_basket_helpers_preserve_and_validate_basket_data() -> None:
    ready = ready_position_basket_tab(_basket(), observed_at_utc=OBSERVED_AT)

    assert loading_position_basket_tab(ready).basket is ready.basket
    assert stale_position_basket_tab(ready).data_as_of_utc is OBSERVED_AT
    assert (
        failed_position_basket_tab(ready, "Position data is unavailable").message
        == "Position data is unavailable"
    )
    with pytest.raises(ValueError, match="message must be sanitized"):
        failed_position_basket_tab(ready, "sqlite failure at /private/tmp")


def test_workspace_rejects_ready_state_without_header() -> None:
    with pytest.raises(ValueError, match="READY snapshot requires a header"):
        TradingWorkspaceSnapshot(
            read_state=WorkspaceReadState.READY,
            header=None,
            position_basket=empty_position_basket_tab(),
            data_as_of_utc=OBSERVED_AT,
        )


def test_paper_runtime_projection_and_blocked_state_keep_basket() -> None:
    configured = configured_workspace_snapshot(
        configured_spot_session(), observed_at_utc=OBSERVED_AT
    )
    running = paper_runtime_workspace_snapshot(
        configured,
        basket=_basket(),
        observed_at_utc=OBSERVED_AT,
    )
    blocked = paper_runtime_blocked_workspace_snapshot(running)

    assert running.header is not None
    assert running.header.runtime_state is BotRuntimeState.RUNNING
    assert running.header.data_freshness is DataFreshness.FRESH
    assert running.basket == _basket()
    assert blocked.header is not None
    assert blocked.header.runtime_state is BotRuntimeState.BLOCKED
    assert blocked.basket == _basket()


def test_empty_workspace_has_no_header_or_basket() -> None:
    snapshot = empty_workspace_snapshot(observed_at_utc=OBSERVED_AT)

    assert snapshot.read_state is WorkspaceReadState.EMPTY
    assert snapshot.header is None
    assert snapshot.basket is None


def test_position_basket_tab_rejects_invalid_ready_state() -> None:
    with pytest.raises(ValueError, match="READY tab requires durable data"):
        PositionBasketTabSnapshot(
            state=WorkspaceTabState.READY,
            basket=None,
            data_as_of_utc=OBSERVED_AT,
        )


def test_workspace_gate_mirrors_every_domain_gate_state() -> None:
    """Producers restate a domain gate as BasketEntryGate(state)."""
    assert {gate.value for gate in BasketEntryGate} == {
        state.value for state in EntryGateState
    }


def test_a_basket_keeps_the_numbers_of_the_entries_that_sold_out_of_it() -> None:
    """An Entry sold at its target does not hand its number back.

    A Basket down to Entry 2 alone counts one open Entry and calls it 2, which
    demanding 1..entry_count read as impossible — and the whole Position tab
    went with it.
    """
    remaining = replace(basket_entries("66000", "66000")[1], entry_number=2)

    basket = replace(_basket(), entry_count=1, entries=(remaining,))

    assert tuple(entry.entry_number for entry in basket.entries) == (2,)


@pytest.mark.parametrize(
    "numbers",
    [(1, 1), (2, 1)],
)
def test_a_basket_rejects_entries_that_repeat_or_go_backwards(
    numbers: tuple[int, int],
) -> None:
    """Two rows for one Entry, or rows out of the order they were taken."""
    entries = tuple(
        replace(entry, entry_number=number)
        for entry, number in zip(basket_entries("66000", "66000"), numbers, strict=True)
    )

    with pytest.raises(ValueError, match="without repeats"):
        replace(_basket(), entries=entries)


def test_a_basket_rejects_more_entries_than_it_counts() -> None:
    with pytest.raises(ValueError, match="one Entry per entry_count"):
        replace(_basket(), entry_count=1)


def test_a_basket_with_no_price_carries_no_reading_that_needs_one() -> None:
    """A Basket read before its first candle is unmarked throughout."""
    unmarked = replace(
        _basket(),
        entries=tuple(
            replace(entry, unrealized_pnl=None) for entry in _basket().entries
        ),
        current_price=None,
        unrealized_pnl=None,
    )

    assert unmarked.current_price is None
    assert all(entry.unrealized_pnl is None for entry in unmarked.entries)


def test_a_basket_is_marked_throughout_or_nowhere() -> None:
    """Half a marking is a reading no price could have produced."""
    with pytest.raises(ValueError, match="unrealized_pnl belongs to a marked Basket"):
        replace(_basket(), current_price=None)

    with pytest.raises(ValueError, match="every Entry is marked with the Basket"):
        replace(
            _basket(),
            entries=tuple(
                replace(entry, unrealized_pnl=None) for entry in _basket().entries
            ),
        )


def _basket() -> BasketSnapshot:
    return BasketSnapshot(
        symbol="BTCUSDT",
        market_type="spot",
        entry_count=2,
        entries=basket_entries(
            "66000", "66000", quantity="0.003", current_price="66321"
        ),
        total_quantity=Decimal("0.006"),
        average_entry_price=Decimal("66000"),
        current_price=Decimal("66321"),
        take_profit_price=Decimal("67000"),
        unrealized_pnl=Decimal("1.926"),
        entry_gate=BasketEntryGate.OPEN,
        updated_at_utc=OBSERVED_AT,
    )
