import threading
from decimal import Decimal
from typing import cast

import pytest
from PySide6.QtCore import QCoreApplication, QThreadPool
from pytestqt.qtbot import QtBot

from tests.support.trade_history_records import basket_result, trade_fill
from tiewtrade.application.trade_history import (
    BasketHistoryResult,
    TradeFillHistoryItem,
    TradeHistoryFilter,
)
from tiewtrade.trading.session_config import MarketType, TradeMode
from tiewtrade.trading.trade_history import BasketResult, BasketStatus
from tiewtrade.ui.background_task import BackgroundTask
from tiewtrade.ui.trade_history_presenter import TradeHistoryFilterValues
from tiewtrade.ui.trade_history_workflow import (
    HistoryTab,
    ListBasketHistory,
    TradeHistoryWorkflow,
)


def result_with(*items: BasketResult) -> BasketHistoryResult:
    return BasketHistoryResult(
        items=tuple(items),
        net_realized_pnl=sum(
            (
                item.net_realized_pnl
                for item in items
                if item.status is BasketStatus.CLOSED
            ),
            start=Decimal("0"),
        ),
    )


def workflow_with(
    list_basket_history: ListBasketHistory,
) -> tuple[TradeHistoryWorkflow, QThreadPool]:
    """A workflow on a real pool, so a reply really does arrive off the Qt thread."""
    pool = QThreadPool()
    pool.setMaxThreadCount(2)
    return (
        TradeHistoryWorkflow(
            list_basket_history=list_basket_history,
            list_trade_fills=lambda _filters: (),
            thread_pool=pool,
        ),
        pool,
    )


class ImmediateThreadPool:
    def start(self, task: BackgroundTask) -> None:
        task.run()


def immediate_thread_pool() -> QThreadPool:
    return cast(QThreadPool, ImmediateThreadPool())


class HoldingThreadPool:
    def __init__(self) -> None:
        self.tasks: list[BackgroundTask] = []

    def start(self, task: BackgroundTask) -> None:
        self.tasks.append(task)


def holding_thread_pool() -> tuple[QThreadPool, HoldingThreadPool]:
    pool = HoldingThreadPool()
    return cast(QThreadPool, pool), pool


def test_selecting_trade_tab_queries_cross_basket_fill_history(qtbot: QtBot) -> None:
    item = TradeFillHistoryItem(
        fill=trade_fill(),
        symbol="BTCUSDT",
        timeframe="5m",
        market_type=MarketType.SPOT,
        trade_mode=TradeMode.PAPER,
    )
    requested: list[TradeHistoryFilter] = []

    def list_trade_fills(
        filters: TradeHistoryFilter,
    ) -> tuple[TradeFillHistoryItem, ...]:
        requested.append(filters)
        return (item,)

    workflow = TradeHistoryWorkflow(
        list_basket_history=lambda _filters: BasketHistoryResult((), Decimal("0")),
        list_trade_fills=list_trade_fills,
        thread_pool=immediate_thread_pool(),
    )
    rows: list[tuple[TradeFillHistoryItem, ...]] = []
    workflow.trades_ready.connect(rows.append)

    workflow.select_tab(HistoryTab.TRADES)

    assert requested == [TradeHistoryFilter()]
    assert rows == [(item,)]


def test_start_loads_every_basket_the_filters_match() -> None:
    basket = basket_result()
    requested: list[TradeHistoryFilter] = []

    def list_basket_history(filters: TradeHistoryFilter) -> BasketHistoryResult:
        requested.append(filters)
        return BasketHistoryResult((basket,), Decimal("12.50"))

    workflow = TradeHistoryWorkflow(
        list_basket_history=list_basket_history,
        list_trade_fills=lambda _filters: (),
        thread_pool=immediate_thread_pool(),
    )
    results: list[BasketHistoryResult] = []
    events: list[str] = []
    workflow.baskets_loading.connect(
        lambda loading: events.append(f"loading:{loading}")
    )
    workflow.baskets_ready.connect(results.append)

    workflow.start()

    assert requested == [TradeHistoryFilter()]
    assert results == [BasketHistoryResult((basket,), Decimal("12.50"))]
    assert events == ["loading:True", "loading:False"]


def test_trade_query_has_independent_loading_empty_error_and_retry() -> None:
    attempts = 0

    def list_trade_fills(
        filters: TradeHistoryFilter,
    ) -> tuple[TradeFillHistoryItem, ...]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary")
        return ()

    workflow = TradeHistoryWorkflow(
        list_basket_history=lambda _filters: BasketHistoryResult((), Decimal("0")),
        list_trade_fills=list_trade_fills,
        thread_pool=immediate_thread_pool(),
    )
    loading: list[bool] = []
    errors: list[str] = []
    empty: list[None] = []
    workflow.trades_loading.connect(loading.append)
    workflow.trades_unavailable.connect(errors.append)
    workflow.trades_empty.connect(lambda: empty.append(None))

    workflow.select_tab(HistoryTab.TRADES)
    workflow.retry_fills()

    assert attempts == 2
    assert loading == [True, False, True, False]
    assert errors == ["Trade History unavailable"]
    assert empty == [None]


def test_trade_query_ignores_basket_only_status_filter() -> None:
    requested: list[TradeHistoryFilter] = []

    def list_trade_fills(
        filters: TradeHistoryFilter,
    ) -> tuple[TradeFillHistoryItem, ...]:
        requested.append(filters)
        return ()

    workflow = TradeHistoryWorkflow(
        list_basket_history=lambda _filters: BasketHistoryResult((), Decimal("0")),
        list_trade_fills=list_trade_fills,
        thread_pool=immediate_thread_pool(),
    )
    workflow.apply_filters(TradeHistoryFilterValues(status="closed"))

    workflow.select_tab(HistoryTab.TRADES)

    assert requested == [TradeHistoryFilter()]


def test_trade_channel_runs_one_query_and_coalesces_latest_filter() -> None:
    qt_pool, holding = holding_thread_pool()
    requested: list[TradeHistoryFilter] = []

    def list_trade_fills(
        filters: TradeHistoryFilter,
    ) -> tuple[TradeFillHistoryItem, ...]:
        requested.append(filters)
        return ()

    workflow = TradeHistoryWorkflow(
        list_basket_history=lambda _filters: BasketHistoryResult((), Decimal("0")),
        list_trade_fills=list_trade_fills,
        thread_pool=qt_pool,
    )
    workflow.select_tab(HistoryTab.TRADES)
    workflow.apply_filters(TradeHistoryFilterValues(symbol="BTCUSDT"))
    workflow.apply_filters(TradeHistoryFilterValues(symbol="ETHUSDT"))

    assert len(holding.tasks) == 1
    holding.tasks[0].run()
    assert len(holding.tasks) == 2
    holding.tasks[1].run()

    assert requested == [
        TradeHistoryFilter(),
        TradeHistoryFilter(symbol="ETHUSDT"),
    ]


def test_trade_loading_callback_cannot_start_a_second_query() -> None:
    qt_pool, holding = holding_thread_pool()
    workflow = TradeHistoryWorkflow(
        list_basket_history=lambda _filters: BasketHistoryResult((), Decimal("0")),
        list_trade_fills=lambda _filters: (),
        thread_pool=qt_pool,
    )
    reapplied = False

    def reapply_during_loading(loading: bool) -> None:
        nonlocal reapplied
        if loading and not reapplied:
            reapplied = True
            workflow.apply_filters(TradeHistoryFilterValues())

    workflow.trades_loading.connect(reapply_during_loading)

    workflow.select_tab(HistoryTab.TRADES)

    assert len(holding.tasks) == 1


def test_basket_loading_callback_cannot_start_a_second_query() -> None:
    qt_pool, holding = holding_thread_pool()
    workflow = TradeHistoryWorkflow(
        list_basket_history=lambda _filters: BasketHistoryResult((), Decimal("0")),
        list_trade_fills=lambda _filters: (),
        thread_pool=qt_pool,
    )
    reapplied = False

    def reapply_during_loading(loading: bool) -> None:
        nonlocal reapplied
        if loading and not reapplied:
            reapplied = True
            workflow.apply_filters(TradeHistoryFilterValues())

    workflow.baskets_loading.connect(reapply_during_loading)

    workflow.start()

    assert len(holding.tasks) == 1


def test_basket_failure_is_sanitized_and_retry_reuses_latest_filters(
    qtbot: QtBot,
) -> None:
    """A failure names no path, and a retry asks the question that failed."""
    calls: list[TradeHistoryFilter] = []

    def fail_then_succeed(filters: TradeHistoryFilter) -> BasketHistoryResult:
        calls.append(filters)
        if len(calls) == 1:
            raise RuntimeError("SQLite failed at /private/tmp/history.sqlite3")
        return result_with()

    workflow, pool = workflow_with(fail_then_succeed)
    messages: list[str] = []
    workflow.baskets_unavailable.connect(messages.append)
    workflow.apply_filters(TradeHistoryFilterValues(symbol="BTCUSDT"))
    qtbot.waitUntil(lambda: messages == ["Basket History unavailable"])

    workflow.retry_baskets()

    qtbot.waitUntil(lambda: len(calls) == 2)
    assert calls[1] == calls[0]
    assert "/private/tmp" not in messages[0]
    assert pool.waitForDone(1_000)


@pytest.mark.parametrize(
    "malformed",
    [
        pytest.param(cast(BasketHistoryResult, object()), id="wrong-type"),
        pytest.param(
            BasketHistoryResult(
                items=(basket_result(),),
                net_realized_pnl=Decimal("NaN"),
            ),
            id="not-a-number",
        ),
        pytest.param(
            BasketHistoryResult(
                items=cast(tuple[BasketResult, ...], (object(),)),
                net_realized_pnl=Decimal("0"),
            ),
            id="not-a-basket",
        ),
    ],
)
def test_a_malformed_result_fails_closed(
    qtbot: QtBot, malformed: BasketHistoryResult
) -> None:
    """A reply that is not the shape promised is reported, never painted."""
    workflow, pool = workflow_with(lambda _filters: malformed)
    messages: list[str] = []
    workflow.baskets_unavailable.connect(messages.append)

    workflow.start()

    qtbot.waitUntil(lambda: messages == ["Basket History unavailable"])
    assert pool.waitForDone(1_000)


def test_only_the_latest_filters_run_after_the_active_request(qtbot: QtBot) -> None:
    started = threading.Event()
    release = threading.Event()
    calls: list[str | None] = []

    def delayed(filters: TradeHistoryFilter) -> BasketHistoryResult:
        calls.append(filters.symbol)
        if len(calls) == 1:
            started.set()
            assert release.wait(timeout=2)
        return result_with()

    workflow, pool = workflow_with(delayed)
    workflow.start()
    qtbot.waitUntil(started.is_set)

    workflow.apply_filters(TradeHistoryFilterValues(symbol="OLD"))
    workflow.apply_filters(TradeHistoryFilterValues(symbol="BTCUSDT"))
    release.set()

    # The filter changed twice while one query ran: only the last one is asked.
    qtbot.waitUntil(lambda: len(calls) == 2)
    assert calls == [None, "BTCUSDT"]
    assert pool.waitForDone(1_000)


def test_close_suppresses_a_late_result(qtbot: QtBot) -> None:
    started = threading.Event()
    release = threading.Event()

    def delayed(filters: TradeHistoryFilter) -> BasketHistoryResult:
        started.set()
        assert release.wait(timeout=2)
        return result_with(basket_result())

    workflow, pool = workflow_with(delayed)
    results: list[BasketHistoryResult] = []
    workflow.baskets_ready.connect(results.append)
    workflow.start()
    qtbot.waitUntil(started.is_set)

    workflow.close()
    workflow.start()
    workflow.apply_filters(TradeHistoryFilterValues(symbol="BTCUSDT"))
    release.set()

    assert pool.waitForDone(1_000)
    QCoreApplication.processEvents()

    # The window is gone; a reply that arrives after it must reach no one.
    assert results == []
