from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QMessageBox, QWidget
from pytestqt.qtbot import QtBot

from tests.support.paper_session_setup import configured_spot_session
from tests.support.workspace_snapshots import basket_entries
from tiewtrade.application.chart_data import ChartRange, ChartReadState, ChartSnapshot
from tiewtrade.application.trading_workspace import (
    BasketEntryGate,
    BasketSnapshot,
    configured_workspace_snapshot,
    ready_position_basket_tab,
)
from tiewtrade.ui.candlestick_chart import CandlestickChartWidget
from tiewtrade.ui.trade_history_workflow import HistoryTab
from tiewtrade.ui.trading_workspace import TradingWorkspace, WorkspaceTab


def test_workspace_places_existing_features_in_one_screen(qtbot: QtBot) -> None:
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)
    workspace.resize(1200, 700)
    workspace.show()

    assert isinstance(workspace.chart, CandlestickChartWidget)
    assert [
        workspace.tabs.tabText(index) for index in range(workspace.tabs.count())
    ] == [
        "Position",
        "Position History",
        "Trade History",
    ]
    assert workspace.position_basket.state_label.text() == ("No open Position")
    assert workspace.setup.isVisible()


def test_workspace_places_candlestick_chart_above_tables_with_docked_bot_control(
    qtbot: QtBot,
) -> None:
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)
    workspace.resize(1200, 700)
    workspace.show()

    qtbot.waitUntil(lambda: workspace.chart.isVisible())

    # The tab strip sits inside its own container now, so both are read in
    # the workspace's own coordinates rather than their parents'.
    chart = _widget_rect_in_viewport(workspace.chart, workspace)
    tabs = _widget_rect_in_viewport(workspace.tabs, workspace)
    assert chart.bottom() < tabs.top()
    assert workspace.bot_control.isVisible()


def test_chart_snapshot_changes_only_the_chart(qtbot: QtBot) -> None:
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)
    workspace.resize(1200, 700)
    workspace.show()
    snapshot = ChartSnapshot(
        session=configured_spot_session(),
        chart_range=ChartRange(
            datetime(2026, 8, 2, 0, tzinfo=UTC),
            datetime(2026, 8, 2, 0, 20, tzinfo=UTC),
        ),
        observed_at_utc=datetime(2026, 8, 2, 0, 20, tzinfo=UTC),
        candles=(),
        fills=(),
        state=ChartReadState.UNAVAILABLE,
        message="Chart is unavailable",
    )

    workspace.chart.show_snapshot(snapshot)

    assert workspace.chart._snapshot is snapshot
    assert workspace.setup.isVisible()
    assert workspace.bot_control.isVisible()


def test_workspace_tabs_render_independent_scoped_states(qtbot: QtBot) -> None:
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)
    observed_at = datetime(2026, 8, 2, 1, 2, 4, tzinfo=UTC)
    basket = BasketSnapshot(
        symbol="BTCUSDT",
        market_type="spot",
        entry_count=1,
        entries=basket_entries(
            "66000.0000", quantity="0.00300000", current_price="66321.1200"
        ),
        total_quantity=Decimal("0.00300000"),
        average_entry_price=Decimal("66000.0000"),
        current_price=Decimal("66321.1200"),
        take_profit_price=Decimal("67000.0000"),
        unrealized_pnl=Decimal("0.96336000"),
        entry_gate=BasketEntryGate.OPEN,
        updated_at_utc=observed_at,
    )
    snapshot = replace(
        configured_workspace_snapshot(
            configured_spot_session(), observed_at_utc=observed_at
        ),
        position_basket=ready_position_basket_tab(basket, observed_at_utc=observed_at),
    )

    workspace.show_workspace_snapshot(snapshot)

    assert workspace.position_basket.state_label.text() == ""
    assert workspace.position_basket.table.rowCount() == 1


def test_bot_control_toolbar_is_a_separate_card_above_bot_control(
    qtbot: QtBot,
) -> None:
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)
    workspace.resize(1200, 700)
    workspace.show()

    assert workspace.toolbar_title.text() == "TiewTrade"
    assert workspace.settings_button.text() == "Settings"
    assert workspace.workspace_header.parent() is not workspace.bot_control
    header_rect = _widget_rect_in_viewport(workspace.workspace_header, workspace)
    bot_control_rect = _widget_rect_in_viewport(workspace.bot_control, workspace)
    assert header_rect.bottom() < bot_control_rect.top()
    assert header_rect.left() == bot_control_rect.left()


def test_settings_button_shows_a_placeholder_message(qtbot: QtBot) -> None:
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)

    with patch.object(QMessageBox, "information") as information:
        workspace.settings_button.click()

    information.assert_called_once()


def test_ui_modules_do_not_import_prohibited_adapters() -> None:
    ui_paths = Path("src/tiewtrade/ui").glob("*.py")
    ui_source = "\n".join(path.read_text() for path in ui_paths)
    for prohibited in (
        "tiewtrade.integrations.sqlite",
        "tiewtrade.strategies",
        "binance",
        "tiewtrade.execution",
    ):
        assert prohibited not in ui_source


def test_bot_control_is_docked_beside_the_chart(qtbot: QtBot) -> None:
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)
    workspace.resize(1200, 700)
    workspace.show()


def test_resizing_keeps_the_same_bot_control_and_workspace_state(
    qtbot: QtBot,
) -> None:
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)
    workspace.resize(1200, 700)
    workspace.show()
    session = configured_spot_session()
    bot_instances = (
        workspace.bot_control,
        workspace._bot_pages,
        workspace.setup,
        workspace.overview,
        workspace.unavailable_panel,
    )

    workspace.show_workspace_snapshot(
        configured_workspace_snapshot(
            session,
            observed_at_utc=datetime(2026, 8, 1, 12, tzinfo=UTC),
        )
    )
    workspace.show_configured_session(session)
    workspace.show_tab(WorkspaceTab.BASKET_HISTORY)
    workspace.resize(1199, 700)
    workspace.resize(1200, 700)

    assert (
        workspace.bot_control,
        workspace._bot_pages,
        workspace.setup,
        workspace.overview,
        workspace.unavailable_panel,
    ) == bot_instances
    assert workspace.overview.isVisible()
    assert workspace.tab_pages.currentWidget() is workspace.trade_history
    assert workspace.bot_control.isVisible()


def test_configured_session_shows_the_bot_control_overview(qtbot: QtBot) -> None:
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)
    workspace.resize(1200, 700)
    workspace.show()
    session = configured_spot_session()

    workspace.show_workspace_snapshot(
        configured_workspace_snapshot(
            session,
            observed_at_utc=datetime(2026, 8, 1, 12, tzinfo=UTC),
        )
    )
    workspace.show_configured_session(session)

    assert workspace.overview.isVisible()


def test_trade_history_signal_is_emitted_when_tab_is_opened(qtbot: QtBot) -> None:
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)

    with qtbot.waitSignal(workspace.trade_history_activated):
        workspace.show_tab(WorkspaceTab.BASKET_HISTORY)


def test_both_history_tabs_show_the_same_page_on_its_own_half(
    qtbot: QtBot,
) -> None:
    """One filter bar serves both, so they stay one page under two tabs."""
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)
    workspace.resize(1200, 700)
    workspace.show()

    workspace.show_tab(WorkspaceTab.BASKET_HISTORY)

    assert workspace.tab_pages.currentWidget() is workspace.trade_history
    assert workspace.trade_history.basket_table.isVisible()
    assert not workspace.trade_history.fill_table.isVisible()

    workspace.show_tab(WorkspaceTab.TRADE_HISTORY)

    assert workspace.tab_pages.currentWidget() is workspace.trade_history
    assert workspace.trade_history.fill_table.isVisible()
    assert not workspace.trade_history.basket_table.isVisible()
    # Each half brings its own filter with it.
    assert workspace.trade_history.trade_filters.search_button.isVisible()

    workspace.show_tab(WorkspaceTab.POSITION_BASKET)

    assert workspace.tab_pages.currentWidget() is workspace.position_basket
    assert workspace.current_tab is WorkspaceTab.POSITION_BASKET


def test_opening_a_history_tab_says_which_half_it_wants(qtbot: QtBot) -> None:
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)
    asked: list[HistoryTab] = []
    workspace.trade_history.history_tab_changed.connect(asked.append)

    workspace.show_tab(WorkspaceTab.TRADE_HISTORY)
    workspace.show_tab(WorkspaceTab.BASKET_HISTORY)

    assert asked == [HistoryTab.TRADES, HistoryTab.BASKETS]


def test_unavailable_state_and_busy_control_are_scoped_to_bot_control(
    qtbot: QtBot,
) -> None:
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)
    workspace.resize(1200, 700)
    workspace.show()

    workspace.show_unavailable("Paper Session unavailable")
    workspace.set_bot_busy(True)

    assert workspace.unavailable_message.text() == "Paper Session unavailable"
    assert workspace.unavailable_panel.isVisible()
    assert not workspace.unavailable_retry_button.isEnabled()
    assert not workspace.setup.create_button.isEnabled()


def _widget_rect_in_viewport(widget: QWidget, viewport: QWidget) -> QRect:
    return QRect(widget.mapTo(viewport, QPoint(0, 0)), widget.size())


def test_opening_a_history_tab_announces_it_once(qtbot: QtBot) -> None:
    """A doubled announcement costs a second query and a second table rebuild."""
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)
    activations: list[None] = []
    workspace.trade_history_activated.connect(lambda: activations.append(None))

    workspace.show_tab(WorkspaceTab.BASKET_HISTORY)

    assert activations == [None]

    workspace.show_tab(WorkspaceTab.TRADE_HISTORY)

    assert activations == [None, None]
