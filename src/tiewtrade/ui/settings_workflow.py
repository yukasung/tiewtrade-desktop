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

type SaveBotTitle = Callable[[str], None]
"""Keep what this installation calls itself, wherever it is kept."""

type ReadBotTitle = Callable[[], str]
"""Read back what this installation calls itself, or "" for nobody's."""

SETTINGS_SAVED_TEXT = "Settings saved successfully."
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

    #: What this installation is now called, for whoever puts it on screen.
    bot_title_changed = Signal(str)

    def __init__(
        self,
        *,
        panel: SettingsPanel,
        clear_database: Callable[[], None] | None = None,
        save_line_notifications: SaveLineNotifications | None = None,
        read_line_notifications: ReadLineNotifications | None = None,
        save_bot_title: SaveBotTitle | None = None,
        read_bot_title: ReadBotTitle | None = None,
        thread_pool: QThreadPool | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._panel = panel
        self._clear_database = clear_database
        self._save_line_notifications = save_line_notifications
        self._read_line_notifications = read_line_notifications
        self._save_bot_title = save_bot_title
        self._read_bot_title = read_bot_title
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
        self._keep_settings = GuardedAction(
            available=busy,
            report_failure=lambda: QMessageBox.warning(
                panel,
                "Settings",
                "Could not save the settings. "
                "This machine may not be letting TiewTrade store them.",
            ),
            failure_log="Could not save the settings",
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
        # The Title is read at launch, before anything is on the Settings page
        # and long before anyone opens it, because the toolbar says it from
        # the first frame. A read that fails leaves the toolbar naming the
        # application alone, which is what an untitled installation shows
        # anyway — so there is nothing to put in front of the trader about it.
        self._show_bot_title = GuardedAction(
            available=busy,
            report_failure=lambda: None,
            failure_log="Could not read the bot title",
            start=pool.start,
        )
        panel.clear_database_requested.connect(self._clear_database_requested)
        panel.settings_save_requested.connect(self._save_requested)

    def _say_busy(self, available: bool) -> None:
        self._panel.set_busy(not available)

    def set_bot_state(self, state: BotRuntimeState | None) -> None:
        """Take what the Bot is doing, which is what a wipe has to wait for."""
        self._bot_state = state

    @Slot()
    def refresh_bot_title(self) -> None:
        """Read what this installation is called, for the form and the toolbar.

        Asked for at launch as well as on the Settings page: the toolbar wears
        the Title whether or not anyone has been near the form.
        """
        read = self._read_bot_title
        if read is None or self._show_bot_title.is_running:
            return
        self._show_bot_title.run(read, succeeded=self._bot_title_landed)

    @Slot(object)
    def _bot_title_landed(self, title: object) -> None:
        if not isinstance(title, str):
            return
        self._panel.show_bot_title(title)
        self.bot_title_changed.emit(title)

    @Slot()
    def opened(self) -> None:
        """Fill the form from what is stored, now the panel is up.

        The form opens as it is and fills in when the reading lands, because
        the reading is not something the window can wait on.
        """
        self.refresh_bot_title()
        read = self._read_line_notifications
        if read is None or self._show_line_settings.is_running:
            return
        self._show_line_settings.run(read, succeeded=self._reading_landed)

    @Slot(object)
    def _reading_landed(self, reading: object) -> None:
        if not isinstance(reading, tuple):
            return
        self._panel.show_line_notifications(reading)

    @Slot(str, bool, str, str)
    def _save_requested(
        self,
        bot_title: str,
        enabled: bool,
        target_id: str,
        access_token: str,
    ) -> None:
        """Keep everything the page holds, under the one button that asks.

        Both values are kept in one action rather than one each, because the
        page has one button and one busy state: two actions would answer it
        twice, and a trader would be told twice that their settings landed.

        The LINE settings go first. They are the half kept somewhere this
        application does not own, and the half a machine can refuse to keep,
        so a save that fails is a save that has touched nothing yet.
        """
        save = self._save_line_notifications
        if save is None or self._keep_settings.is_running:
            return
        save_title = self._save_bot_title
        self._keep_settings.run(
            lambda: self._keep_everything(
                lambda: save(enabled, target_id, access_token),
                save_title,
                bot_title,
            ),
            succeeded=self._settings_kept,
        )

    @staticmethod
    def _keep_everything(
        keep_line_settings: Callable[[], None],
        save_bot_title: SaveBotTitle | None,
        bot_title: str,
    ) -> None:
        keep_line_settings()
        if save_bot_title is not None:
            save_bot_title(bot_title)

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
