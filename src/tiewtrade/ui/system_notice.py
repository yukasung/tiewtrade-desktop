"""What the system has to say for itself, on the one line it says it on.

Two faults reach the trader from opposite directions and mean the same thing
to them: the Bot has stopped, or the chart's feed has. Read apart they were
read twice — a Bot waiting on data it could not reach looked like a Bot that
was trading, and a Bot that stopped while the reader was elsewhere left
nothing behind once its dialog was closed. Held together here, there is one
line, worst first, and one rule for what goes on it.

A connection being retried is told in a dialog instead, which a reader looking
anywhere else on the screen still meets. That telling waits: the runtime
retries for as long as it takes, climbing from a second to a thirty-second
ceiling, so a drop shorter than the wait is one it was always going to ride
out on its own. Only the crossings start and stop the wait — a drop keeps
producing snapshots for as long as it lasts, and a countdown restarted by each
of them would never reach the end of it.
"""

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from tiewtrade.application.bot_control import BotControlSnapshot
from tiewtrade.application.chart_data import ChartFeedState
from tiewtrade.application.trading_workspace import BotRuntimeState, DataFreshness
from tiewtrade.ui.connection_notice import ConnectionNotice

# What the one system notice says, worst first. It carries only faults the
# reader has to act on.
CHART_FEED_STOPPED_NOTICE = "Chart feed stopped — prices are not current"

# How long a connection has to stay away before the trader is taken away from
# what they were doing.
CONNECTION_NOTICE_DELAY_MILLISECONDS = 30_000


class SystemNoticeWorkflow(QObject):
    """Holds both faults, says which is worth telling, and raises the notice."""

    #: The line as it now reads, empty where there is nothing to say.
    notice_changed = Signal(str)

    def __init__(
        self,
        *,
        connection_notice: ConnectionNotice,
        connection_notice_delay_ms: int = CONNECTION_NOTICE_DELAY_MILLISECONDS,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._connection_notice = connection_notice
        self._bot_stopped_reason = ""
        self._bot_reconnecting = False
        self._connection_lost = False
        self._chart_feed_state: ChartFeedState | None = None
        # A drop worth telling is one that lasts, so the telling waits.
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(connection_notice_delay_ms)
        self._timer.timeout.connect(self._announce_connection_lost)

    @property
    def chart_feed_state(self) -> ChartFeedState | None:
        """What the chart's feed was last heard to be doing."""
        return self._chart_feed_state

    @property
    def reconnecting(self) -> bool:
        """Whether the Bot is reaching for data it has not got back yet."""
        return self._bot_reconnecting

    def bot_snapshot(
        self,
        snapshot: BotControlSnapshot,
        previous_state: BotRuntimeState | None,
    ) -> None:
        """Take what the Bot has to say, which outranks what the chart does."""
        header = snapshot.workspace.header
        if snapshot.state is BotRuntimeState.RUNNING:
            self._bot_stopped_reason = ""
            self._bot_reconnecting = (
                header is not None and header.data_freshness is DataFreshness.STALE
            )
        elif (
            snapshot.state is BotRuntimeState.BLOCKED
            and previous_state is BotRuntimeState.RUNNING
        ):
            self._bot_stopped_reason = snapshot.blocked_reason or ""
            self._bot_reconnecting = False
        else:
            self._bot_stopped_reason = ""
            self._bot_reconnecting = False
        self._publish()

    def feed_state(self, state: ChartFeedState) -> None:
        """Take what the chart's feed is doing, which the Bot can speak over."""
        self._chart_feed_state = state
        self._publish()

    @Slot()
    def close(self) -> None:
        """Stop waiting on a return, and take back anything already said."""
        self._timer.stop()
        self._connection_notice.hide()

    def _publish(self) -> None:
        self.notice_changed.emit(self._notice_text())
        self._update_connection_notice()

    def _notice_text(self) -> str:
        if self._bot_stopped_reason:
            return self._bot_stopped_reason
        if self._chart_feed_state is ChartFeedState.STOPPED:
            return CHART_FEED_STOPPED_NOTICE
        return ""

    def _update_connection_notice(self) -> None:
        """Raise the notice on a drop that lasts, drop it on the return."""
        lost = not self._bot_stopped_reason and (
            self._bot_reconnecting
            or self._chart_feed_state is ChartFeedState.RECONNECTING
        )
        if lost == self._connection_lost:
            return
        self._connection_lost = lost
        if lost:
            self._timer.start()
            return
        self._timer.stop()
        self._connection_notice.hide()

    @Slot()
    def _announce_connection_lost(self) -> None:
        self._connection_notice.show()
