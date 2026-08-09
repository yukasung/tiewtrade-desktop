from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QLabel, QTableWidget


class TableEmptyStateOverlay(QLabel):
    """A non-interactive empty-state label centered in a table viewport."""

    def __init__(self, table: QTableWidget) -> None:
        super().__init__(table.viewport())
        self._viewport = table.viewport()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setProperty("stateMessage", True)
        self.setVisible(False)
        self._viewport.installEventFilter(self)

    def show_message(self, message: str) -> None:
        self.setText(message)
        self._resize_to_viewport()
        self.setVisible(True)

    def clear_message(self) -> None:
        self.clear()
        self.setVisible(False)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._viewport and event.type() in {
            QEvent.Type.Resize,
            QEvent.Type.Show,
        }:
            self._resize_to_viewport()
        return super().eventFilter(watched, event)

    def _resize_to_viewport(self) -> None:
        self.setGeometry(self._viewport.rect())
