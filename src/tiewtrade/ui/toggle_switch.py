"""A setting that is on or off, drawn as the switch that says so.

Qt has no switch of its own, and a checkbox is the wrong shape for this:
a checkbox answers "is this true", a switch says "this is running". The
LINE setting is the second kind — it turns an outgoing message on — and the
control the trader reaches for should look like something that is left in a
position rather than something ticked off a list.

It is a button underneath, so `isChecked`, `setChecked`, `toggled` and the
space bar all work the way they do on any other checkable control; only the
painting is ours.
"""

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
)
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import QAbstractButton, QWidget

TRACK_WIDTH = 42
TRACK_HEIGHT = 24
KNOB_MARGIN = 3.0
SLIDE_MILLISECONDS = 120

TRACK_ON_COLOR = "#315CF4"
TRACK_OFF_COLOR = "#2B3441"
TRACK_DISABLED_COLOR = "#1B2430"
KNOB_COLOR = "#F1F5F9"
KNOB_DISABLED_COLOR = "#6B7686"


class ToggleSwitch(QAbstractButton):
    """An on/off switch the width of two words and the height of one."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._knob_position = 0.0
        self._slide = QPropertyAnimation(self, b"knob_position", self)
        self._slide.setDuration(SLIDE_MILLISECONDS)
        self._slide.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.toggled.connect(self._slide_to_position)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt's own name
        return QSize(TRACK_WIDTH, TRACK_HEIGHT)

    def get_knob_position(self) -> float:
        """Where the knob is between the two ends: 0.0 off, 1.0 on."""
        return self._knob_position

    def set_knob_position(self, position: float) -> None:
        self._knob_position = position
        self.update()

    knob_position = Property(float, get_knob_position, set_knob_position)

    def setChecked(self, checked: bool) -> None:  # noqa: N802 - Qt's own name
        """Take the position without sliding into it.

        A switch set from stored settings was already in that position when
        the panel opened; animating it there would say the setting had just
        been changed by someone.
        """
        already = self.isChecked()
        super().setChecked(checked)
        if already != checked:
            self._slide.stop()
            self.set_knob_position(1.0 if checked else 0.0)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt's own name
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        track = QRectF(0, 0, TRACK_WIDTH, TRACK_HEIGHT)
        painter.setBrush(QColor(self._track_color()))
        painter.drawRoundedRect(track, TRACK_HEIGHT / 2, TRACK_HEIGHT / 2)

        radius = TRACK_HEIGHT / 2 - KNOB_MARGIN
        travel = TRACK_WIDTH - TRACK_HEIGHT
        centre = QPointF(
            KNOB_MARGIN + radius + travel * self._knob_position,
            TRACK_HEIGHT / 2,
        )
        painter.setBrush(QColor(self._knob_color()))
        painter.drawEllipse(centre, radius, radius)

    def _track_color(self) -> str:
        if not self.isEnabled():
            return TRACK_DISABLED_COLOR
        return TRACK_ON_COLOR if self.isChecked() else TRACK_OFF_COLOR

    def _knob_color(self) -> str:
        return KNOB_COLOR if self.isEnabled() else KNOB_DISABLED_COLOR

    def _slide_to_position(self, checked: bool) -> None:
        self._slide.stop()
        self._slide.setStartValue(self._knob_position)
        self._slide.setEndValue(1.0 if checked else 0.0)
        self._slide.start()
