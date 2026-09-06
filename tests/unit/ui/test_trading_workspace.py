from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from tests.support.paper_session_setup import configured_spot_session
from tests.support.workspace_snapshots import (
    basket_entries,
    basket_snapshot,
    basket_targets,
)
from tiewtrade.application.chart_data import ChartRange, ChartReadState, ChartSnapshot
from tiewtrade.application.trading_workspace import (
    BasketEntryGate,
    BasketSnapshot,
    configured_workspace_snapshot,
    ready_position_basket_tab,
)
from tiewtrade.ui.candlestick_chart import CandlestickChartWidget
from tiewtrade.ui.trade_history_workflow import HistoryTab
from tiewtrade.ui.trading_workspace import (
    BOT_CONTROL_WIDTH,
    TradingWorkspace,
    WorkspaceTab,
)


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
        "Notifications",
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
        position_side="long",
        entry_count=1,
        entries=basket_entries(
            "66000.0000", quantity="0.00300000", current_price="66321.1200"
        ),
        total_quantity=Decimal("0.00300000"),
        average_entry_price=Decimal("66000.0000"),
        current_price=Decimal("66321.1200"),
        take_profit_price=Decimal("67000.0000"),
        targets=basket_targets(
            basket_entries(
                "66000.0000", quantity="0.00300000", current_price="66321.1200"
            )
        ),
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


def test_connection_notice_card_sits_below_the_topbar_and_starts_hidden(
    qtbot: QtBot,
) -> None:
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)
    workspace.resize(1200, 700)
    workspace.show()

    assert not workspace.connection_notice_card.isVisible()

    workspace.connection_notice_card.show()

    header_rect = _widget_rect_in_viewport(workspace.workspace_header, workspace)
    notice_rect = _widget_rect_in_viewport(workspace.connection_notice_card, workspace)
    bot_control_rect = _widget_rect_in_viewport(workspace.bot_control, workspace)
    assert header_rect.bottom() < notice_rect.top()
    assert notice_rect.bottom() < bot_control_rect.top()
    assert notice_rect.left() == header_rect.left()


def test_live_price_card_sits_between_the_notice_and_the_bot_control(
    qtbot: QtBot,
) -> None:
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)
    workspace.resize(1200, 700)
    workspace.show()
    workspace.connection_notice_card.show()

    qtbot.waitUntil(lambda: workspace.live_price_card.isVisible())

    notice_rect = _widget_rect_in_viewport(workspace.connection_notice_card, workspace)
    price_rect = _widget_rect_in_viewport(workspace.live_price_card, workspace)
    bot_control_rect = _widget_rect_in_viewport(workspace.bot_control, workspace)
    assert notice_rect.bottom() < price_rect.top()
    assert price_rect.bottom() < bot_control_rect.top()
    assert price_rect.left() == bot_control_rect.left()
    assert price_rect.width() == BOT_CONTROL_WIDTH


def test_settings_button_emits_settings_requested(qtbot: QtBot) -> None:
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)

    with qtbot.waitSignal(workspace.settings_requested, timeout=1000):
        workspace.settings_button.click()


def test_show_settings_opens_the_dialog_over_the_sidebar(qtbot: QtBot) -> None:
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)
    workspace.show()
    assert workspace.setup.isVisible()

    workspace.show_settings()

    assert workspace.is_showing_settings
    assert workspace.settings_dialog.isVisible()
    assert workspace.settings_panel.isVisible()
    assert workspace.settings_panel.clear_database_button.isEnabled()
    # The dialog is a modal window over the sidebar, not a page replacing it —
    # the sidebar keeps whatever it was showing underneath.
    assert workspace.setup.isVisible()


def test_hide_settings_closes_the_dialog(qtbot: QtBot) -> None:
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)
    workspace.show()

    workspace.show_settings()
    workspace.hide_settings()

    assert not workspace.is_showing_settings
    assert workspace.setup.isVisible()


def test_hide_settings_leaves_the_bot_control_page_shown_underneath(
    qtbot: QtBot,
) -> None:
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)
    workspace.show()
    workspace.show_configured_session(configured_spot_session())
    assert workspace.bot_control_widget.isVisible()

    workspace.show_settings()
    assert workspace.is_showing_settings

    workspace.hide_settings()

    assert not workspace.is_showing_settings
    assert workspace.bot_control_widget.isVisible()


def test_cancel_button_hides_settings(qtbot: QtBot) -> None:
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)
    workspace.show()

    workspace.show_settings()
    workspace.settings_panel.cancel_button.click()

    assert not workspace.is_showing_settings
    assert workspace.setup.isVisible()


def test_a_state_update_while_settings_is_open_does_not_close_it(
    qtbot: QtBot,
) -> None:
    """A running Bot's own snapshots must not sweep Settings off screen."""
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)
    workspace.show()
    workspace.show_configured_session(configured_spot_session())
    workspace.show_settings()

    workspace.show_configured_session(configured_spot_session())

    assert workspace.is_showing_settings
    assert workspace.settings_panel.isVisible()

    workspace.hide_settings()

    assert workspace.bot_control_widget.isVisible()


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


def test_cooldown_notice_card_sits_in_the_right_column_and_starts_hidden(
    qtbot: QtBot,
) -> None:
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)
    workspace.resize(1200, 700)
    workspace.show()

    assert not workspace.cooldown_notice_card.isVisible()

    workspace.cooldown_notice_card.show()

    header_rect = _widget_rect_in_viewport(workspace.workspace_header, workspace)
    notice_rect = _widget_rect_in_viewport(workspace.cooldown_notice_card, workspace)
    price_rect = _widget_rect_in_viewport(workspace.live_price_card, workspace)
    assert header_rect.bottom() < notice_rect.top()
    assert notice_rect.bottom() < price_rect.top()
    assert notice_rect.left() == header_rect.left()
    assert notice_rect.width() == BOT_CONTROL_WIDTH


def test_a_cooling_basket_raises_the_notice_beside_the_bot_control(
    qtbot: QtBot,
) -> None:
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)
    workspace.resize(1200, 700)
    workspace.show()
    observed_at = datetime(2026, 8, 2, 1, 2, 4, tzinfo=UTC)
    cooling = replace(
        configured_workspace_snapshot(
            configured_spot_session(), observed_at_utc=observed_at
        ),
        position_basket=ready_position_basket_tab(
            basket_snapshot(
                entry_gate=BasketEntryGate.COOLDOWN,
                cooldown_ends_at_utc=datetime(2026, 8, 31, 12, tzinfo=UTC),
            ),
            observed_at_utc=observed_at,
        ),
    )

    workspace.show_workspace_snapshot(cooling)

    assert workspace.cooldown_notice_card.isVisible()
    assert "Cooldown until 2026-08-31" in workspace.cooldown_notice_card.label.text()

    workspace.show_workspace_snapshot(
        replace(
            cooling,
            position_basket=ready_position_basket_tab(
                basket_snapshot(entry_gate=BasketEntryGate.OPEN),
                observed_at_utc=observed_at,
            ),
        )
    )

    assert not workspace.cooldown_notice_card.isVisible()


def test_going_back_to_setup_takes_the_cooldown_notice_away(qtbot: QtBot) -> None:
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)
    workspace.show()
    workspace.cooldown_notice_card.show()

    workspace.show_setup()

    assert not workspace.cooldown_notice_card.isVisible()


def test_bot_control_scrolls_without_showing_a_scrollbar(qtbot: QtBot) -> None:
    """The side column is narrow, so a bar eats width the readings need.

    The panel still scrolls — by wheel, and whenever the form asks for a
    field to be brought into view — it just never paints a track for it.
    """
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)
    workspace.resize(1200, 400)
    workspace.show()

    scroll = workspace.bot_control_scroll
    assert scroll.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert not scroll.verticalScrollBar().isVisible()
    assert scroll.viewport().width() == scroll.width()


def test_bot_control_page_is_not_stretched_by_the_taller_setup_form(
    qtbot: QtBot,
) -> None:
    """A short window still shows the whole Bot Control page, button and all.

    The pages share one stack, and the setup form is the tallest of them.
    If the stack asked for that height on every page, Bot Control would be
    stretched past the viewport and its button would fall out of sight.
    """
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)
    workspace.resize(1200, 560)
    workspace.show()
    session = configured_spot_session()

    workspace.show_configured_session(session)

    viewport = workspace.bot_control_scroll.viewport()
    button = workspace.bot_control_widget.start_button
    assert button.isVisible()
    bottom = button.mapTo(viewport, QPoint(0, button.height())).y()
    assert bottom <= viewport.height()


def test_the_toolbar_always_names_the_application(qtbot: QtBot) -> None:
    """The Bot Title changes the OS window title, never this toolbar."""
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)

    assert workspace.toolbar_title.text() == "TiewTrade"


def test_the_notifications_tab_shows_its_page_and_announces_it_opened(
    qtbot: QtBot,
) -> None:
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)
    workspace.show()

    with qtbot.waitSignal(workspace.notifications_activated, timeout=1000):
        workspace.show_tab(WorkspaceTab.NOTIFICATIONS)

    assert workspace.tab_pages.currentWidget() is workspace.notifications
    assert workspace.current_tab is WorkspaceTab.NOTIFICATIONS


def test_leaving_the_notifications_tab_announces_it_closed(qtbot: QtBot) -> None:
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)
    workspace.show()
    workspace.show_tab(WorkspaceTab.NOTIFICATIONS)

    with qtbot.waitSignal(workspace.notifications_deactivated, timeout=1000):
        workspace.show_tab(WorkspaceTab.POSITION_BASKET)
