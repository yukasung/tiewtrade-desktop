import sys
from collections.abc import Callable

from PySide6.QtWidgets import QApplication

from tiewtrade.application.paper_session_setup import PaperSessionSetupValues
from tiewtrade.ui.main_window import (
    NO_BOT,
    NO_CHART,
    BotActions,
    ChartSources,
    MainWindow,
)
from tiewtrade.ui.session_workflow import CreateSession, LoadActiveSession
from tiewtrade.ui.theme import DARK_THEME
from tiewtrade.ui.trade_history_workflow import (
    ListBasketHistory,
    ListTradeFills,
)


def run_desktop(
    *,
    create_session: CreateSession,
    load_active: LoadActiveSession,
    list_basket_history: ListBasketHistory | None = None,
    list_trade_fills: ListTradeFills | None = None,
    validate_session: Callable[[PaperSessionSetupValues], None] | None = None,
    bot: BotActions = NO_BOT,
    chart: ChartSources = NO_CHART,
    clear_database: Callable[[], None] | None = None,
) -> int:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    window = MainWindow(
        create_session=create_session,
        load_active=load_active,
        list_basket_history=list_basket_history,
        list_trade_fills=list_trade_fills,
        validate_session=validate_session,
        bot=bot,
        chart=chart,
        clear_database=clear_database,
    )
    window.setStyleSheet(DARK_THEME)
    window.show()
    return app.exec()
