from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from tiewtrade.application.bot_control import BotControlAction, BotControlSnapshot
from tiewtrade.application.trading_workspace import BotRuntimeState
from tiewtrade.ui.session_overview import SessionOverviewWidget

StopConfirmation = Callable[[Callable[[], None], Callable[[], None]], None]

# The states each button belongs to: the settled one that offers it, and the
# in-flight one that keeps it on screen while the action runs.
_STARTING_STATES = frozenset({BotRuntimeState.CONFIGURED, BotRuntimeState.STARTING})
_STOPPING_STATES = frozenset({BotRuntimeState.RUNNING, BotRuntimeState.STOPPING})


class BotControlWidget(QWidget):
    start_requested = Signal()
    stop_requested = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        stop_confirmation: StopConfirmation | None = None,
    ) -> None:
        super().__init__(parent)
        self._stop_confirmation = stop_confirmation or self._open_stop_confirmation
        self._stop_confirmation_pending = False
        self._stop_confirmation_dialog: QMessageBox | None = None
        self.setObjectName("botControlWidget")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Bot Control")
        self.overview = SessionOverviewWidget()
        self.start_button = QPushButton("Start Bot")
        self.start_button.setObjectName("startBotButton")
        self.stop_button = QPushButton("Stop Bot")
        self.stop_button.setObjectName("stopSessionButton")
        self.stop_button.setProperty("destructive", True)
        self.start_button.clicked.connect(self._start_clicked)
        self.stop_button.clicked.connect(self._stop_clicked)
        self._build_layout()
        self._hide_actions()

    @Slot(object)
    def show_snapshot(self, value: object) -> None:
        if not isinstance(value, BotControlSnapshot):
            return
        self.overview.show_session(value.session)
        self._show_state_content(value)

    def _build_layout(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        root.addWidget(self.overview)

        actions = QHBoxLayout()
        actions.addWidget(self.start_button)
        actions.addWidget(self.stop_button)
        root.addLayout(actions)

    def _show_state_content(self, snapshot: BotControlSnapshot) -> None:
        """Show the one button this state owns, enabled only if it is offered.

        A state in flight carries no actions — BotControlSnapshot rejects any
        it is handed — so the button it owns shows disabled while it settles.
        """
        self._hide_actions()
        if snapshot.state in _STARTING_STATES:
            button, action = self.start_button, BotControlAction.START
        elif snapshot.state in _STOPPING_STATES:
            button, action = self.stop_button, BotControlAction.STOP
        else:
            return
        self._show_action(button, action in snapshot.available_actions)

    def _hide_actions(self) -> None:
        for button in (self.start_button, self.stop_button):
            button.hide()
            button.setDisabled(True)

    @staticmethod
    def _show_action(button: QPushButton, enabled: bool) -> None:
        button.setEnabled(enabled)
        button.show()

    @Slot()
    def _start_clicked(self) -> None:
        self._emit_once(self.start_button, self.start_requested.emit)

    @Slot()
    def _stop_clicked(self) -> None:
        if not self.stop_button.isEnabled() or self._stop_confirmation_pending:
            return
        self._stop_confirmation_pending = True
        self._stop_confirmation(self._confirm_stop, self._cancel_stop)

    def _open_stop_confirmation(
        self,
        confirm: Callable[[], None],
        cancel: Callable[[], None],
    ) -> None:
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Confirm Stop Bot")
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setText("Stop this Paper Bot session?")
        dialog.setInformativeText(
            "New entries will stop. Existing Basket Take Profit stays active."
        )
        stop_button = dialog.addButton(
            "Stop Bot", QMessageBox.ButtonRole.DestructiveRole
        )
        cancel_button = dialog.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(cancel_button)
        dialog.setEscapeButton(cancel_button)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)

        def finish(_result: int) -> None:
            if dialog.clickedButton() is stop_button:
                confirm()
            else:
                cancel()
            dialog.deleteLater()

        dialog.finished.connect(finish)
        self._stop_confirmation_dialog = dialog
        dialog.open()

    def _confirm_stop(self) -> None:
        if not self._stop_confirmation_pending:
            return
        self._stop_confirmation_pending = False
        self._stop_confirmation_dialog = None
        self._emit_once(self.stop_button, self.stop_requested.emit)
        QTimer.singleShot(0, self._restore_confirm_focus)

    def _cancel_stop(self) -> None:
        if not self._stop_confirmation_pending:
            return
        self._stop_confirmation_pending = False
        self._stop_confirmation_dialog = None
        QTimer.singleShot(
            0,
            self._restore_stop_focus,
        )

    def _restore_stop_focus(self) -> None:
        self.window().activateWindow()
        self.stop_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def _restore_confirm_focus(self) -> None:
        # The stop confirmation took focus from a button that is now hidden,
        # so the panel itself takes it back.
        self.window().activateWindow()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def _emit_once(
        self,
        button: QPushButton,
        emit: Callable[[], object],
    ) -> None:
        if not button.isEnabled():
            return
        button.setDisabled(True)
        emit()
