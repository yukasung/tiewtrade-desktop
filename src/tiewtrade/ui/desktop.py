import sys
from collections.abc import Callable

from PySide6.QtWidgets import QApplication

from tiewtrade.application.chart_view_selection import ChartViewSelection
from tiewtrade.application.paper_session_setup import (
    ConfiguredPaperSession,
    PaperSessionSetupValues,
)
from tiewtrade.ui.bot_lifecycle_workflow import LifecycleAction, RuntimeSnapshotRelay
from tiewtrade.ui.chart_workflow import ChartFactRelay, LoadChart, RefreshChart
from tiewtrade.ui.main_window import MainWindow
from tiewtrade.ui.session_workflow import CreateSession, LoadActiveSession
from tiewtrade.ui.theme import DARK_THEME
from tiewtrade.ui.trade_history_workflow import ListBaskets, ListFills


def run_desktop(
    *,
    create_session: CreateSession,
    load_active: LoadActiveSession,
    list_baskets: ListBaskets,
    list_fills: ListFills,
    start_bot: LifecycleAction | None = None,
    stop_bot: LifecycleAction | None = None,
    recover_bot: LifecycleAction | None = None,
    stop_for_replacement: Callable[[ConfiguredPaperSession], None] | None = None,
    validate_session: Callable[[PaperSessionSetupValues], None] | None = None,
    close_position: Callable[[ConfiguredPaperSession], None] | None = None,
    has_open_position: Callable[[ConfiguredPaperSession], bool] | None = None,
    retire_session: Callable[[ConfiguredPaperSession], None] | None = None,
    new_session_available: Callable[[ConfiguredPaperSession], bool] | None = None,
    initialize_bot: LifecycleAction | None = None,
    runtime_snapshots: RuntimeSnapshotRelay | None = None,
    shutdown_runtime: Callable[[], None] | None = None,
    load_chart: LoadChart | None = None,
    refresh_chart: RefreshChart | None = None,
    chart_fact_relay: ChartFactRelay | None = None,
    start_chart_feed: Callable[[ConfiguredPaperSession], None] | None = None,
    stop_chart_feed: Callable[[], None] | None = None,
    select_chart_view: (
        Callable[[ConfiguredPaperSession, ChartViewSelection], None] | None
    ) = None,
) -> int:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    window = MainWindow(
        create_session=create_session,
        load_active=load_active,
        list_baskets=list_baskets,
        list_fills=list_fills,
        start_bot=start_bot,
        stop_bot=stop_bot,
        stop_for_replacement=stop_for_replacement,
        validate_session=validate_session,
        close_position=close_position,
        has_open_position=has_open_position,
        retire_session=retire_session,
        initialize_bot=initialize_bot,
        runtime_snapshots=runtime_snapshots,
        shutdown_runtime=shutdown_runtime,
        load_chart=load_chart,
        refresh_chart=refresh_chart,
        chart_fact_relay=chart_fact_relay,
        start_chart_feed=start_chart_feed,
        stop_chart_feed=stop_chart_feed,
        select_chart_view=select_chart_view,
    )
    window.setStyleSheet(DARK_THEME)
    window.show()
    return app.exec()
