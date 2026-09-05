import signal
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import FrameType
from typing import cast

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from tiewtrade.application.paper_session_setup import PaperSessionSetupValues
from tiewtrade.ui.instance_activation import InstanceActivation
from tiewtrade.ui.main_window import (
    NO_BOT,
    NO_CHART,
    BotActions,
    ChartSources,
    MainWindow,
)
from tiewtrade.ui.session_workflow import CreateSession, LoadActiveSession
from tiewtrade.ui.settings_workflow import (
    ReadBotTitle,
    ReadLineNotifications,
    SaveBotTitle,
    SaveLineNotifications,
)
from tiewtrade.ui.theme import DARK_THEME, apply_dark_appearance
from tiewtrade.ui.total_pnl_workflow import ReadTotalRealizedPnl
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
    read_total_realized_pnl: ReadTotalRealizedPnl | None = None,
    validate_session: Callable[[PaperSessionSetupValues], None] | None = None,
    bot: BotActions = NO_BOT,
    chart: ChartSources = NO_CHART,
    clear_database: Callable[[], None] | None = None,
    save_line_notifications: SaveLineNotifications | None = None,
    read_line_notifications: ReadLineNotifications | None = None,
    save_bot_title: SaveBotTitle | None = None,
    read_bot_title: ReadBotTitle | None = None,
    activation_name: str | None = None,
) -> int:
    app = the_qt_application()
    apply_dark_appearance()

    window = MainWindow(
        create_session=create_session,
        load_active=load_active,
        list_basket_history=list_basket_history,
        list_trade_fills=list_trade_fills,
        read_total_realized_pnl=read_total_realized_pnl,
        validate_session=validate_session,
        bot=bot,
        chart=chart,
        clear_database=clear_database,
        save_line_notifications=save_line_notifications,
        read_line_notifications=read_line_notifications,
        save_bot_title=save_bot_title,
        read_bot_title=read_bot_title,
    )
    window.setStyleSheet(DARK_THEME)
    window.show()
    with (
        _closing_on_a_signal(window),
        _answering_a_second_launch(window, activation_name),
    ):
        return app.exec()


def the_qt_application() -> QApplication:
    """The one `QApplication` this process gets, built if it does not exist.

    Qt allows exactly one per process, and there is more than one way into
    this module now — a window opening, a refusal to open, a second launch
    handing over. Each of them needs an application object and none of them
    may be the one that builds a second.
    """
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return cast(QApplication, app)


@contextmanager
def _answering_a_second_launch(
    window: MainWindow,
    activation_name: str | None,
) -> Iterator[None]:
    """Listen for a second launch asking for this window, while it is open.

    Only while: the channel is taken down before `exec` returns, so the name
    is free for the next launch rather than held by a process on its way out.

    A channel that cannot be opened is not a reason to refuse the window. It
    costs the trader a convenience, not their Bot — and the installation lock,
    which is what actually keeps the second Bot from running, has already been
    taken by the time this is asked.
    """
    if activation_name is None:
        yield
        return
    activation = InstanceActivation(activation_name)
    activation.listen(window.present)
    try:
        yield
    finally:
        activation.close()


# Often enough that Ctrl+C feels immediate, seldom enough to cost nothing.
_SIGNAL_POLL_MS = 200
_CLOSING_SIGNALS = (signal.SIGINT, signal.SIGTERM)


@contextmanager
def _closing_on_a_signal(window: MainWindow) -> Iterator[None]:
    """Turn Ctrl+C and a shutdown request into the close the window knows.

    Killed where it stands, the app leaves an open WebSocket, a market-data
    runtime stopped out of order, and a lifecycle marker still saying the Bot
    is running. `closeEvent` already stops the Runtime, waits for the workers
    and marks the Session stopped, so the whole of this is asking for it.

    SIGKILL is deliberately absent: no process may handle it. That is the
    death the installation lock answers instead.
    """

    def close_the_window(_received: int, _frame: FrameType | None) -> None:
        window.close()

    try:
        previous = [
            (received, signal.signal(received, close_the_window))
            for received in _CLOSING_SIGNALS
        ]
    except ValueError:
        # Handlers belong to the main thread. Somewhere else, the app still
        # opens; it just closes the one way it always could.
        yield
        return
    # Python runs a handler between its own bytecodes, and `exec` spends its
    # time inside Qt's C++ loop. A timer that does nothing hands control back
    # often enough for the handler to be reached at all.
    awake = QTimer()
    awake.timeout.connect(lambda: None)
    awake.start(_SIGNAL_POLL_MS)
    try:
        yield
    finally:
        awake.stop()
        for received, handler in previous:
            signal.signal(received, handler)


def report_installation_in_use(message: str) -> None:
    """Say why the app will not open, to a trader who has no terminal.

    A bundled desktop app writes its reasons to a log nobody is watching, and
    this one has to land before any window exists — so it brings up the only
    thing available that early: a dialog of its own.
    """
    the_qt_application()
    apply_dark_appearance()
    dialog = QMessageBox()
    # The theme normally reaches a box through the main window it is built
    # under. There is no window here and there never will be in this run, so
    # the box carries the stylesheet itself.
    dialog.setStyleSheet(DARK_THEME)
    dialog.setWindowTitle("TiewTrade")
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setText(message)
    dialog.exec()
