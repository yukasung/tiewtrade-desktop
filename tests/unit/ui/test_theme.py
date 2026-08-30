import ast
from pathlib import Path
from types import SimpleNamespace

from _pytest.monkeypatch import MonkeyPatch
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QMessageBox,
    QPushButton,
    QWidget,
)
from pytestqt.qtbot import QtBot

import tiewtrade.ui.theme as theme
from tiewtrade.ui.theme import DARK_THEME, apply_dark_appearance
from tiewtrade.ui.trade_history_page import TradeHistoryPage
from tiewtrade.ui.trading_workspace import TradingWorkspace


def test_dark_theme_uses_approved_workspace_palette() -> None:
    for color in (
        "#0B0E11",
        "#141A22",
        "#1B2430",
        "#2B3441",
        "#F1F5F9",
        "#94A3B8",
        "#0ECB81",
        "#F6465D",
        "#F0B90B",
    ):
        assert color in DARK_THEME

    assert "background: #F4F7FB;" not in DARK_THEME
    assert "background: #FFFFFF;" not in DARK_THEME


def test_dark_theme_defines_focus_and_semantic_states() -> None:
    assert 'QWidget[state="positive"]' in DARK_THEME
    assert 'QWidget[state="negative"]' in DARK_THEME
    assert 'QWidget[state="warning"]' in DARK_THEME
    assert "QPushButton:focus" in DARK_THEME
    assert "QTableWidget:focus" in DARK_THEME
    assert "border: 2px solid #4C7DFF" in DARK_THEME


def test_chart_selector_theme_reserves_text_and_arrow_space() -> None:
    assert "QComboBox#chartSymbol, QComboBox#chartTimeframe" in DARK_THEME
    assert "padding: 0 40px 0 14px;" in DARK_THEME
    assert "QComboBox#chartSymbol QAbstractItemView::item" in DARK_THEME
    assert "padding: 6px 12px;" in DARK_THEME


def test_dark_theme_keeps_confirmation_dialog_text_readable() -> None:
    assert "QMessageBox" in DARK_THEME
    assert "QMessageBox QLabel" in DARK_THEME


def test_dark_theme_styles_scoped_trading_tab_states() -> None:
    for selector in (
        "QLabel#positionBasketState",
        "QLabel#basketState",
        "QLabel#fillState",
    ):
        assert selector in DARK_THEME


def test_dark_theme_covers_current_semantic_object_names_without_dead_navigation() -> (
    None
):
    for selector in (
        "QLabel#eyebrow",
        "QLabel#summaryValue",
        'QPushButton[retryButton="true"]',
    ):
        assert selector in DARK_THEME

    for dead_selector in (
        "QWidget#content",
        "QFrame#navigation",
        "QPushButton#navigationButton",
        "QPushButton#navigationButtonSelected",
        "QPushButton#unavailableRetryButton",
        "QLabel#environmentBadge",
        "QLabel#sectionTitle",
        "QPushButton#advancedButton",
    ):
        assert dead_selector not in DARK_THEME


def test_workspace_and_session_setup_labels_render_readably_on_dark_surfaces(
    qtbot: QtBot,
) -> None:
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)
    workspace.setStyleSheet(DARK_THEME)
    workspace.resize(1200, 700)
    workspace.show()

    form_label = next(
        label
        for label in workspace.setup.findChildren(QLabel)
        if label.text() == "Market Type"
    )

    assert _palette_color(form_label, QPalette.ColorRole.WindowText) == QColor(
        "#F1F5F9"
    )

    workspace.show_unavailable("Session storage is unavailable")

    assert _palette_color(
        workspace.unavailable_message, QPalette.ColorRole.WindowText
    ) == QColor("#FF9CAA")


def test_semantic_status_label_colors_override_generic_dark_label_color(
    qtbot: QtBot,
) -> None:
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)
    workspace.setStyleSheet(DARK_THEME)

    expected_colors = {
        "positive": QColor("#0ECB81"),
        "negative": QColor("#F6465D"),
        "warning": QColor("#F0B90B"),
    }
    for state, expected in expected_colors.items():
        label = QLabel(state, workspace)
        label.setProperty("state", state)
        label.ensurePolished()

        assert _palette_color(label, QPalette.ColorRole.WindowText) == expected


def test_combo_box_popup_view_renders_with_dark_readable_palette(
    qtbot: QtBot,
) -> None:
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)
    workspace.setStyleSheet(DARK_THEME)
    workspace.show()

    combo = workspace.setup.market_type
    combo.showPopup()
    view = combo.view()
    view.ensurePolished()

    assert _palette_color(view, QPalette.ColorRole.Base) == QColor("#1B2430")
    assert _palette_color(view, QPalette.ColorRole.Text) == QColor("#F1F5F9")

    combo.hidePopup()


def test_calendar_popup_renders_with_dark_readable_palette(qtbot: QtBot) -> None:
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    page.setStyleSheet(DARK_THEME)
    page.show()
    calendar = page.basket_filters.from_date.calendarWidget()
    calendar.show()
    calendar_view = calendar.findChild(QAbstractItemView)

    assert calendar_view is not None
    assert _palette_color(calendar, QPalette.ColorRole.Window) == QColor("#141A22")
    assert _palette_color(calendar, QPalette.ColorRole.WindowText) == QColor("#F1F5F9")
    assert _palette_color(calendar_view, QPalette.ColorRole.Base) == QColor("#141A22")
    assert _palette_color(calendar_view, QPalette.ColorRole.Text) == QColor("#F1F5F9")


def test_bot_control_scroll_surfaces_and_scrollbar_render_dark(qtbot: QtBot) -> None:
    workspace = TradingWorkspace()
    qtbot.addWidget(workspace)
    workspace.setStyleSheet(DARK_THEME)
    workspace.setFixedSize(1024, 700)
    workspace.show()
    scroll_bar = workspace.bot_control_scroll.verticalScrollBar()

    assert _palette_color(workspace._bot_pages, QPalette.ColorRole.Window) == QColor(
        "#141A22"
    )
    assert _palette_color(
        workspace.bot_control_scroll.viewport(), QPalette.ColorRole.Window
    ) == QColor("#141A22")
    assert _palette_color(scroll_bar, QPalette.ColorRole.Window) == QColor("#0B0E11")
    assert _palette_color(scroll_bar, QPalette.ColorRole.Base) == QColor("#0B0E11")
    assert (
        max(QColor(color).lightness() for color in _rendered_colors(scroll_bar)) < 128
    )


def test_trade_history_scroll_surfaces_and_scrollbar_render_dark(
    qtbot: QtBot,
) -> None:
    page = TradeHistoryPage()
    qtbot.addWidget(page)
    page.setStyleSheet(DARK_THEME)
    # Short enough that the tables overflow, which is what is scrolled.
    page.setFixedSize(1200, 120)
    page.show()

    scroll_area = page.scroll_area
    scroll_bar = scroll_area.verticalScrollBar()
    qtbot.waitUntil(lambda: scroll_bar.maximum() > 0)
    content = scroll_area.widget()
    assert content is not None

    assert _palette_color(scroll_area, QPalette.ColorRole.Window) == QColor("#141A22")
    assert _palette_color(scroll_area.viewport(), QPalette.ColorRole.Window) == QColor(
        "#141A22"
    )
    assert _palette_color(content, QPalette.ColorRole.Window) == QColor("#141A22")
    assert _palette_color(scroll_bar, QPalette.ColorRole.Window) == QColor("#0B0E11")
    assert _palette_color(scroll_bar, QPalette.ColorRole.Base) == QColor("#0B0E11")
    assert (
        max(QColor(color).lightness() for color in _rendered_colors(scroll_bar)) < 128
    )


def test_unclassified_button_renders_a_visible_dark_theme_surface(
    qtbot: QtBot,
) -> None:
    button = QPushButton("Previous")
    qtbot.addWidget(button)
    button.setStyleSheet(DARK_THEME)
    button.resize(120, 40)
    button.show()

    colors = _rendered_colors(button)
    lightest = max(colors, key=lambda color: QColor(color).lightness())

    assert "#1b2430" in colors
    # The label has to read against that surface, which is what the theme
    # promises — not that some pixel of it lands on exactly #F1F5F9. A glyph
    # is drawn by whatever font the machine has and antialiased against the
    # dark surface behind it, so how close its lightest pixel gets to the
    # full colour is a fact about the font, not about the theme. Asking for
    # the exact colour asked every machine to rasterize text identically,
    # which is why this passed on macOS and failed on Linux for weeks.
    assert QColor(lightest).lightness() > 128, f"lightest pixel was {lightest}"


def _palette_color(widget: QWidget, role: QPalette.ColorRole) -> QColor:
    return widget.palette().color(role)


def _rendered_colors(widget: QWidget) -> set[str]:
    image = widget.grab().toImage()
    return {
        image.pixelColor(x, y).name()
        for x in range(image.width())
        for y in range(image.height())
        if image.pixelColor(x, y).alpha() > 0
    }


def test_a_confirmation_dialog_holds_its_words_off_its_own_edges(
    qtbot: QtBot,
) -> None:
    """These boxes carry paragraphs, and prose against a frame reads as a wall.

    Every irreversible answer in this app is asked for in one of them — sell
    this Entry, lift this Cooldown, close this Position — so they are read
    rather than dismissed, and the reading is what the spacing is for.
    """
    dialog = QMessageBox()
    qtbot.addWidget(dialog)
    dialog.setStyleSheet(DARK_THEME)
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setText("Close this Position at the market price?")
    # Long enough to wrap, which is the shape these boxes are really in.
    dialog.setInformativeText("Market sells it at the price now. " * 6)
    dialog.addButton("Close at Market", QMessageBox.ButtonRole.DestructiveRole)
    dialog.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    dialog.show()

    for name in ("qt_msgbox_label", "qt_msgbox_informativelabel"):
        label = dialog.findChild(QLabel, name)
        assert label is not None, name
        frame, text = label.rect(), label.contentsRect()
        # Off the icon on one side and off the frame on the other.
        assert text.left() - frame.left() >= 8, name
        assert frame.right() - text.right() >= 4, name
        # And clear of whatever the box says next.
        assert frame.bottom() - text.bottom() >= 10, name


def test_a_confirmation_dialog_gives_its_answers_room_without_clipping_them(
    qtbot: QtBot,
) -> None:
    """The answers are named after what they do, so the names have to fit.

    Widening them with min-width does not: Qt then sizes every button from
    that number and forgets the text, and the longest answer in the box came
    out reading "Close at Marke".
    """
    dialog = QMessageBox()
    qtbot.addWidget(dialog)
    dialog.setStyleSheet(DARK_THEME)
    dialog.setText("Close this Position at the market price?")
    dialog.addButton("Start Bot", QMessageBox.ButtonRole.AcceptRole)
    dialog.addButton("Close at Market", QMessageBox.ButtonRole.DestructiveRole)
    dialog.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    dialog.show()

    buttons = [button for button in dialog.findChildren(QPushButton) if button.text()]

    assert len(buttons) == 3
    for button in buttons:
        text_width = button.fontMetrics().horizontalAdvance(button.text())
        assert button.width() >= button.sizeHint().width(), button.text()
        assert button.width() - text_width >= 36, button.text()


def test_every_message_box_is_built_under_a_window_that_carries_the_theme() -> None:
    """The theme reaches a box through its parent, so a box needs one.

    Only the main window is given the stylesheet, and Qt hands it down the
    object tree. A box built with no parent is outside that tree: it comes up
    in the desktop's own light colours, with none of the room these boxes are
    given to be read in, and nothing in a rendering test would catch it
    because such a box is never the one a test builds.
    """
    offenders: list[str] = []
    paths = tuple(Path("src/tiewtrade").rglob("*.py"))

    assert paths
    for path in paths:
        for node in _themed_by_a_window(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            where = f"{path}:{node.lineno}"
            if _is_message_box_name(node.func) and _parentless(node.args, 0):
                offenders.append(f"{where} QMessageBox()")
            # QMessageBox.warning(None, ...) and its siblings take the parent
            # first too, and skip the constructor entirely.
            if (
                isinstance(node.func, ast.Attribute)
                and _is_message_box_name(node.func.value)
                and node.func.attr
                in ("information", "warning", "critical", "question", "about")
                and _parentless(node.args, 0)
            ):
                offenders.append(f"{where} QMessageBox.{node.func.attr}()")

    assert offenders == []


# The one box built before a main window exists, and so the one that cannot
# inherit the theme from a parent. It sets the stylesheet on itself instead,
# which is the same fact arrived at the only way left open to it.
_THEMED_WITHOUT_A_PARENT = ("report_installation_in_use",)


def _themed_by_a_window(tree: ast.AST) -> list[ast.AST]:
    """Every node except those inside a function that themes its own box."""
    exempt = {
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name in _THEMED_WITHOUT_A_PARENT
    }
    inside = {node for parent in exempt for node in ast.walk(parent)}
    return [node for node in ast.walk(tree) if node not in inside]


def _is_message_box_name(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) and node.id == "QMessageBox"


def _parentless(args: list[ast.expr], index: int) -> bool:
    """Whether the parent argument is missing or handed a literal None."""
    if len(args) <= index:
        return True
    parent = args[index]
    return isinstance(parent, ast.Constant) and parent.value is None


def test_the_app_asks_the_platform_for_dark_controls(
    monkeypatch: MonkeyPatch,
) -> None:
    """The native controls have to be told which window they are drawn into.

    Asked of a stand-in rather than of the real application, because whether
    a platform honours the request is the platform's business — the offscreen
    one used by these tests reports back Unknown either way.
    """
    asked: list[Qt.ColorScheme] = []

    class Hints:
        def setColorScheme(self, scheme: Qt.ColorScheme) -> None:  # noqa: N802
            asked.append(scheme)

    monkeypatch.setattr(theme, "QGuiApplication", SimpleNamespace(styleHints=Hints))

    apply_dark_appearance()

    assert asked == [Qt.ColorScheme.Dark]
