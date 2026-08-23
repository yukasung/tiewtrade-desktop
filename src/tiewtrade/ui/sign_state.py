from decimal import Decimal

from PySide6.QtWidgets import QLabel, QWidget

from tiewtrade.ui.money_text import money_text
from tiewtrade.ui.theme import NEGATIVE_COLOR, POSITIVE_COLOR


def sign_colour(value: Decimal) -> str | None:
    """The colour a figure carries, or none at break-even.

    Neither reading is true at zero, so the figure keeps the colour it would
    have had rather than picking a side.
    """
    if value > 0:
        return POSITIVE_COLOR
    if value < 0:
        return NEGATIVE_COLOR
    return None


def paint_by_sign(widget: QWidget, value: Decimal) -> None:
    """Colour a whole widget by the sign of the figure it reads.

    A table cell carries its own brush, but a label is painted by the theme,
    and neither a palette nor a property can be relied on to override it: a
    palette is ignored wherever a stylesheet sets a colour, and a rule keyed
    on a property loses to one keyed on an object name. A stylesheet set on
    the widget itself outranks the application's, whatever the theme says
    about that particular label, and clearing it hands the label back.
    """
    colour = sign_colour(value)
    widget.setStyleSheet("" if colour is None else f"color: {colour};")


def render_realized_total(label: QLabel, value: Decimal | None) -> None:
    """A realised total on a label, or nothing before one has been read.

    Zero is a real break-even result across whatever was totalled, not the
    "hasn't closed yet" case a per-row dash guards against, so it reads as an
    amount rather than a blank.
    """
    if value is None:
        label.clear()
        label.setStyleSheet("")
        return
    label.setText(money_text(value))
    paint_by_sign(label, value)
