"""The Settings panel: what this installation keeps, and what it can throw away.

Everything here runs off the UI thread. Reading or writing the LINE settings
touches wherever this machine keeps them, which can put a prompt of the
operating system's own in front of the trader and wait on an answer — not
something a window may do while it is being drawn. Clearing the database is a
write across every table there is.

So all three go through a `GuardedAction`, and all three answer the same way:
the panel says "busy" while one is in flight, because it has no line of its
own to put a message on the way the Position tab has.
"""

from collections.abc import Callable

from PySide6.QtCore import QObject, QThreadPool, Signal, Slot
from PySide6.QtWidgets import QMessageBox

from tiewtrade.application.trading_workspace import BotRuntimeState
from tiewtrade.ui.guarded_action import GuardedAction
from tiewtrade.ui.settings_dialogs import ask_about_clearing_database
from tiewtrade.ui.settings_panel import LineNotificationsReading, SettingsPanel

type SaveLineNotifications = Callable[[bool, str, str], None]
"""Keep these LINE settings, wherever this installation keeps them."""

type ReadLineNotifications = Callable[[], LineNotificationsReading]
"""Read back the LINE settings — never the token, only that there is one."""

SETTINGS_SAVED_TEXT = (
    "Settings saved.\n\n"
    "They are kept outside the database, so clearing the database leaves "
    "them where they are.\n\n"
    "A Runtime reads them when it starts. A Bot that is already running keeps "
    "the settings it started with — stop it and start it again for these to "
    "take effect."
)
"""Said only once the settings are actually stored, never before.

Nothing else on this page says a save landed. The button goes back to reading
"Save Settings" whether the settings were kept or never left the form, and the
token field empties and refills either way — so a save that worked and a save
that quietly did nothing look identical without this.

Worded for the button rather than for what is behind it today. The button says
"Save Settings" and this page is built to hold the values still to come — a
Binance API key among them — so an acknowledgement naming LINE would have to
be rewritten by whoever adds the second setting, and would be wrong for the
saves in between.

The two things it goes on to say are the two a trader is most likely to have
wrong, and both are facts about the page rather than about any one setting:

That the database does not hold them, because "Clear All Database" sits
directly below this button, and a trader who reads these as database rows would
expect that button to take them. Only that it is elsewhere, never where: this
page hands its values to a function and is not told what is behind it, and
tests/acceptance/test_desktop_session_setup.py holds `ui` to not knowing.

When they start being used, because they are read as a Runtime starts (see
line_notification_composition) — so a trader who saves while the Bot runs and
watches for a change would be watching a Bot that is still using what it
started with.
"""

# The database may be cleared whenever the Bot is not actually trading or
# moving between states. Stopped, Blocked, and a freshly Configured Session
# all resolve through the same editable Setup form, whose only way onward
# retires whatever Session was there and creates a fresh one — so a cleared
# database never leaves that form pointed at a row that just went.
_CLEAR_DATABASE_BLOCKED_STATES = frozenset(
    {
        BotRuntimeState.STARTING,
        BotRuntimeState.RUNNING,
        BotRuntimeState.STOPPING,
    }
)


class SettingsWorkflow(QObject):
    """Runs what the Settings panel asks for, and says when the database went."""

    #: Every durable row this installation had has just been wiped.
    database_cleared = Signal()

    def __init__(
        self,
        *,
        panel: SettingsPanel,
        clear_database: Callable[[], None] | None = None,
        save_line_notifications: SaveLineNotifications | None = None,
        read_line_notifications: ReadLineNotifications | None = None,
        thread_pool: QThreadPool | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._panel = panel
        self._clear_database = clear_database
        self._save_line_notifications = save_line_notifications
        self._read_line_notifications = read_line_notifications
        pool = thread_pool or QThreadPool.globalInstance()
        self._bot_state: BotRuntimeState | None = None
        busy = self._say_busy
        self._wipe_database = GuardedAction(
            available=busy,
            report_failure=lambda: QMessageBox.warning(
                panel,
                "Settings",
                "Could not clear the database. Try again.",
            ),
            failure_log="Could not clear the database",
            start=pool.start,
        )
        self._keep_line_settings = GuardedAction(
            available=busy,
            report_failure=lambda: QMessageBox.warning(
                panel,
                "Settings",
                "Could not save the LINE settings. "
                "This machine may not be letting TiewTrade store them.",
            ),
            failure_log="Could not save the LINE notification settings",
            start=pool.start,
        )
        # Reading is its own action rather than a second use of the one that
        # saves: what is saved is read back straight afterwards, and an action
        # already running is an action that refuses to start again.
        self._show_line_settings = GuardedAction(
            available=busy,
            report_failure=panel.say_line_notifications_unreadable,
            failure_log="Could not read the LINE notification settings",
            start=pool.start,
        )
        panel.clear_database_requested.connect(self._clear_database_requested)
        panel.line_notifications_save_requested.connect(self._save_requested)

    def _say_busy(self, available: bool) -> None:
        self._panel.set_busy(not available)

    def set_bot_state(self, state: BotRuntimeState | None) -> None:
        """Take what the Bot is doing, which is what a wipe has to wait for."""
        self._bot_state = state

    @Slot()
    def opened(self) -> None:
        """Fill the LINE form from what is stored, now the panel is up.

        The form opens as it is and fills in when the reading lands, because
        the reading is not something the window can wait on.
        """
        read = self._read_line_notifications
        if read is None or self._show_line_settings.is_running:
            return
        self._show_line_settings.run(read, succeeded=self._reading_landed)

    @Slot(object)
    def _reading_landed(self, reading: object) -> None:
        if not isinstance(reading, tuple):
            return
        self._panel.show_line_notifications(reading)

    @Slot(bool, str, str)
    def _save_requested(
        self,
        enabled: bool,
        target_id: str,
        access_token: str,
    ) -> None:
        save = self._save_line_notifications
        if save is None or self._keep_line_settings.is_running:
            return
        self._keep_line_settings.run(
            lambda: save(enabled, target_id, access_token),
            succeeded=self._settings_kept,
        )

    @Slot(object)
    def _settings_kept(self, _result: object) -> None:
        """Read the form back from what was actually stored, and say it landed.

        What the trader typed and what is now stored are two things, and the
        second is the one the Bot will use. Reading it back is also what
        empties the token field once its value is somewhere safer.

        The reading is asked for before the box goes up rather than after,
        because the box is modal and holds this thread inside it. Asked first,
        the read runs while the trader is looking at the box and the form is
        already filled in behind it when they dismiss it.

        This is reached only from the success of the save itself, so it can
        never tell a trader their token was kept when it was not — the failure
        has its own message and does not come through here.
        """
        self.opened()
        QMessageBox.information(self._panel, "Settings", SETTINGS_SAVED_TEXT)

    @Slot()
    def _clear_database_requested(self) -> None:
        clear_database = self._clear_database
        if clear_database is None or self._wipe_database.is_running:
            return
        if self._bot_state in _CLEAR_DATABASE_BLOCKED_STATES:
            QMessageBox.warning(
                self._panel,
                "Settings",
                "Stop the bot before clearing the database.",
            )
            return
        if not ask_about_clearing_database(self._panel):
            return
        self._wipe_database.run(clear_database, succeeded=self._wiped)

    @Slot(object)
    def _wiped(self, _result: object) -> None:
        self.database_cleared.emit()
