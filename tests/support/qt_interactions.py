from datetime import date

from PySide6.QtCore import QDate, QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTableWidget, QTableWidgetItem, QWidget


def click(
    widget: QWidget,
    button: Qt.MouseButton = Qt.MouseButton.LeftButton,
) -> None:
    QTest.mouseClick(widget, button)


def hover(widget: QWidget, x: int, y: int) -> None:
    """Move the pointer over a widget, wherever the tests are running.

    Posting the move for a window server to deliver only works where one is
    watching: run against a real desktop rather than headless and the window
    has to be the active one first, so the widget hears nothing and a test
    about hovering fails for the room it is in. The event is handed straight
    to the widget instead, which is the thing under test either way.
    """
    QApplication.sendEvent(
        widget,
        QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(x, y),
            QPointF(x, y),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )


def table_item(table: QTableWidget, row: int, column: int) -> QTableWidgetItem:
    item = table.item(row, column)
    assert item is not None
    return item


def qdate(value: date) -> QDate:
    return QDate(value.year, value.month, value.day)
