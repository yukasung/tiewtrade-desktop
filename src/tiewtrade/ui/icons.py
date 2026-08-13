from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

GEAR_TEETH = 8


def gear_icon(size: int, colour: str, hover_colour: str) -> QIcon:
    """A gear drawn to fill the size asked for, resting and under the pointer.

    The "⚙" character was the obvious way to say settings, but its glyph sits
    small inside its own em box, so asking for a bigger font bought far less
    gear than it promised. Painted here, the size asked for is the size drawn.

    A painted icon is past the reach of the stylesheet that lights the rest of
    a button up on hover, so it carries the lit pixmap too, as the mode Qt
    already asks for while the pointer is over the widget.
    """
    icon = QIcon(_gear_pixmap(size, colour))
    icon.addPixmap(_gear_pixmap(size, hover_colour), QIcon.Mode.Active)
    return icon


def _gear_pixmap(size: int, colour: str) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(QPointF(size / 2, size / 2))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(colour))

    tooth_reach = size / 2
    tooth_width = size * 0.24
    for _ in range(GEAR_TEETH):
        painter.rotate(360 / GEAR_TEETH)
        painter.drawRoundedRect(
            QRectF(-tooth_width / 2, -tooth_reach, tooth_width, tooth_reach * 2),
            tooth_width * 0.3,
            tooth_width * 0.3,
        )
    body = size * 0.36
    painter.drawEllipse(QPointF(0, 0), body, body)

    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    hub = size * 0.16
    painter.drawEllipse(QPointF(0, 0), hub, hub)
    painter.end()
    return pixmap
