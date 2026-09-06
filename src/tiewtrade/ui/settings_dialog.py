"""The modal window `SettingsPanel` sits in, apart from the sidebar it used to share.

Docked inside the Bot Control card, the Settings form pushed Save Settings off
the bottom of a column sized for Start/Stop and a handful of readings, not for
two notification channels. A dialog answers to its own size instead of the
sidebar's: wide enough for the LINE and Telegram cards side by side on a
desktop screen, capped so it never grows past the screen itself, and free to
scroll its own form without the sidebar scrolling under it.
"""

from PySide6.QtCore import QPoint
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout, QWidget

from tiewtrade.ui.settings_panel import SettingsPanel

# Roomy enough for the LINE and Telegram cards to breathe side by side on a
# desktop screen, without reading as a window trying to be the whole app.
_PREFERRED_WIDTH = 720
_PREFERRED_HEIGHT = 640
_MINIMUM_WIDTH = 420
_MINIMUM_HEIGHT = 420
# However roomy the screen is, the dialog stops short of covering it — a
# trader should still see the app it belongs to around the edges.
_MAX_SCREEN_FRACTION = 0.9


class SettingsDialog(QDialog):
    """A centred window around `SettingsPanel`, sized to the screen it opens on."""

    def __init__(self, panel: SettingsPanel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsDialog")
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.panel = panel
        panel.close_requested.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(panel)

        self.resize(_PREFERRED_WIDTH, _PREFERRED_HEIGHT)
        self.setMinimumSize(_MINIMUM_WIDTH, _MINIMUM_HEIGHT)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._clamp_to_screen()
        self._centre_on_parent()

    def _clamp_to_screen(self) -> None:
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        max_width = int(available.width() * _MAX_SCREEN_FRACTION)
        max_height = int(available.height() * _MAX_SCREEN_FRACTION)
        width = max(_MINIMUM_WIDTH, min(_PREFERRED_WIDTH, max_width))
        height = max(_MINIMUM_HEIGHT, min(_PREFERRED_HEIGHT, max_height))
        self.resize(width, height)

    def _centre_on_parent(self) -> None:
        anchor = self.parentWidget()
        anchor_window = anchor.window() if anchor is not None else None
        centre = (
            anchor_window.frameGeometry().center()
            if anchor_window is not None and anchor_window.isVisible()
            else self._screen_centre()
        )
        if centre is None:
            return
        geometry = self.frameGeometry()
        geometry.moveCenter(centre)
        self.move(geometry.topLeft())

    def _screen_centre(self) -> QPoint | None:
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return None
        return screen.availableGeometry().center()
