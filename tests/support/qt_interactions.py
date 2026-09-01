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


def press(widget: QWidget, x: int, y: int) -> None:
    """Put the left button down on a widget, headless or not (see `hover`)."""
    _send_mouse(
        widget,
        QEvent.Type.MouseButtonPress,
        x,
        y,
        button=Qt.MouseButton.LeftButton,
        buttons=Qt.MouseButton.LeftButton,
    )


def drag_to(widget: QWidget, x: int, y: int) -> None:
    """Move with the left button held, as a drag in progress."""
    _send_mouse(
        widget,
        QEvent.Type.MouseMove,
        x,
        y,
        button=Qt.MouseButton.NoButton,
        buttons=Qt.MouseButton.LeftButton,
    )


def release(widget: QWidget, x: int, y: int) -> None:
    _send_mouse(
        widget,
        QEvent.Type.MouseButtonRelease,
        x,
        y,
        button=Qt.MouseButton.LeftButton,
        buttons=Qt.MouseButton.NoButton,
    )


def _send_mouse(
    widget: QWidget,
    event_type: QEvent.Type,
    x: int,
    y: int,
    *,
    button: Qt.MouseButton,
    buttons: Qt.MouseButton,
) -> None:
    QApplication.sendEvent(
        widget,
        QMouseEvent(
            event_type,
            QPointF(x, y),
            QPointF(x, y),
            button,
            buttons,
            Qt.KeyboardModifier.NoModifier,
        ),
    )


def table_item(table: QTableWidget, row: int, column: int) -> QTableWidgetItem:
    item = table.item(row, column)
    assert item is not None
    return item


def qdate(value: date) -> QDate:
    return QDate(value.year, value.month, value.day)
