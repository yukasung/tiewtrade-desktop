from dataclasses import fields, replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tests.support.paper_session_setup import configured_spot_session
from tiewtrade.application.trading_workspace import (
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
        failed_position_basket_tab(
            ready, "Position / Basket data is unavailable"
        ).message
        == "Position / Basket data is unavailable"
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


def _basket() -> BasketSnapshot:
    return BasketSnapshot(
        symbol="BTCUSDT",
        market_type="spot",
        entry_count=2,
        total_quantity=Decimal("0.006"),
        average_entry_price=Decimal("66000"),
        current_price=Decimal("66321"),
        take_profit_price=Decimal("67000"),
        unrealized_pnl=Decimal("1.926"),
        liquidation_price=None,
        lifecycle="active_pair",
        updated_at_utc=OBSERVED_AT,
    )
