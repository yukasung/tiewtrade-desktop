from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidget
from pytestqt.qtbot import QtBot

from tiewtrade.ui.table_empty_state import TableEmptyStateOverlay


def test_empty_state_overlay_centers_message_in_table_viewport(qtbot: QtBot) -> None:
    table = QTableWidget(0, 2)
    table.setHorizontalHeaderLabels(["First", "Second"])
    table.resize(640, 360)
    qtbot.addWidget(table)
    table.show()

    overlay = TableEmptyStateOverlay(table)
    overlay.show_message("No rows")

    qtbot.waitUntil(overlay.isVisible)

    assert overlay.text() == "No rows"
    assert overlay.geometry().center() == table.viewport().rect().center()
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
