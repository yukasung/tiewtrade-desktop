from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon
from pytestqt.qtbot import QtBot

from tiewtrade.ui.icons import gear_icon


def test_gear_is_drawn_at_the_size_asked_for(qtbot: QtBot) -> None:
    icon = gear_icon(22, "#94A3B8", "#F1F5F9")

    assert icon.pixmap(QSize(22, 22)).size() == QSize(22, 22)


def test_gear_lights_up_under_the_pointer(qtbot: QtBot) -> None:
    icon = gear_icon(22, "#94A3B8", "#F1F5F9")

    resting = icon.pixmap(QSize(22, 22), QIcon.Mode.Normal).toImage()
    hovered = icon.pixmap(QSize(22, 22), QIcon.Mode.Active).toImage()

    assert resting != hovered
