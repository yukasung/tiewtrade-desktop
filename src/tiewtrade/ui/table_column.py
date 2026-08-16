from collections.abc import Callable
from dataclasses import dataclass, field

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QTableWidgetItem

from tiewtrade.ui.blank_text import BLANK

LEFT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
CENTER = Qt.AlignmentFlag.AlignCenter
RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter


def _no_colour(row: object) -> str | None:
    del row
    return None


@dataclass(frozen=True, slots=True)
class Column[Row]:
    """A column carries its own heading, alignment and reading of a row.

    Stating the three together is what keeps them in step: a column moved or
    added cannot leave its values under a different heading, and no cell is
    dressed by counting its position in the row.

    A column reads the record itself rather than an already-rendered string,
    so a colour is decided by the figure it belongs to. Deciding it from the
    text instead would make the wording of a label load-bearing.
    """

    header: str
    alignment: Qt.AlignmentFlag
    text: Callable[[Row], str]
    colour: Callable[[Row], str | None] = field(default=_no_colour)


def headers[Row](columns: tuple[Column[Row], ...]) -> tuple[str, ...]:
    return tuple(column.header for column in columns)


def table_item[Row](column: Column[Row], row: Row) -> QTableWidgetItem:
    """One cell, read and dressed by the column that owns it.

    A cell with no value to show is the one exception to the column's
    alignment: a dash is not a figure, so hanging it on the right edge the
    figures share would have it read as the smallest number in the column.
    Centred, it reads as the absence it is.
    """
    text = column.text(row)
    item = QTableWidgetItem(text)
    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
    item.setTextAlignment(CENTER if text == BLANK else column.alignment)
    colour = column.colour(row)
    if colour is not None:
        item.setForeground(QBrush(QColor(colour)))
    return item
