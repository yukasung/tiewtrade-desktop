import pytest
from pytestqt.qtbot import QtBot

from tests.support.paper_session_setup import (
    configured_futures_session,
    configured_spot_session,
)
from tests.support.qt_interactions import click
from tiewtrade.application.paper_session_setup import (
    ConfiguredPaperSession,
    PaperSessionSetupValues,
    setup_values_of,
)
from tiewtrade.trading.futures_policy import FuturesTradingPolicy
from tiewtrade.ui.session_setup import SessionSetupWidget


def test_configuration_controls_have_explicit_accessible_names(qtbot: QtBot) -> None:
    widget = SessionSetupWidget()
    qtbot.addWidget(widget)

    assert widget.market_type.accessibleName() == "Market Type"
    assert widget.symbol.accessibleName() == "Pair"
    assert widget.timeframe.accessibleName() == "Timeframe"
    assert widget.capital_per_entry.accessibleName() == "Amount (USDT)"
    assert widget.max_entries.accessibleName() == "Maximum Entries"
    assert widget.leverage.accessibleName() == "Futures Leverage"
    assert widget.create_button.accessibleName() == "Start Paper Bot"


def test_default_form_builds_paper_spot_values(qtbot: QtBot) -> None:
    widget = SessionSetupWidget()
    qtbot.addWidget(widget)

    values = widget.values()

    assert values.market_type == "spot"
    assert values.symbol == "BTCUSDT"
    assert values.timeframe == "5m"
    assert values.capital_per_entry == ""
    assert values.max_entries == "10"
    assert values.futures_leverage is None
    assert values.preset_version == "rsi-step-grid-v1"
    assert widget.trade_mode_label.text() == "Paper"
    assert widget.market_type.currentData() == "spot"
    assert [
        widget.market_type.itemData(index)
        for index in range(widget.market_type.count())
    ] == ["spot", "futures"]
    assert widget.market_type.isEnabled()
    assert widget.symbol.isEnabled()
    assert widget.strategy.currentText() == "RSI Step Grid v1"
    assert [
        widget.strategy.itemData(index) for index in range(widget.strategy.count())
    ] == ["rsi-step-grid-v1", "rsi-buy-only-v1", "fast-test-v1"]


def test_symbol_combo_has_only_paper_symbols(qtbot: QtBot) -> None:
    widget = SessionSetupWidget()
    qtbot.addWidget(widget)

    symbols = [widget.symbol.itemData(index) for index in range(widget.symbol.count())]

    assert symbols == [
        "BTCUSDT",
        "ETHUSDT",
        "BNBUSDT",
    ]


def test_selected_symbol_is_included_in_the_start_request(qtbot: QtBot) -> None:
    widget = SessionSetupWidget()
    qtbot.addWidget(widget)
    requested: list[PaperSessionSetupValues] = []
    widget.create_requested.connect(requested.append)
    widget.symbol.setCurrentIndex(widget.symbol.findData("ETHUSDT"))

    click(widget.create_button)

    assert requested[0].symbol == "ETHUSDT"


def test_every_setting_is_on_the_form_from_the_start(qtbot: QtBot) -> None:
    """Nothing is folded away — only the Futures row waits its turn."""
    widget = SessionSetupWidget()
    qtbot.addWidget(widget)
    widget.show()

    assert widget.create_button.text() == "Start Paper Bot"
    assert widget.create_button.accessibleName() == "Start Paper Bot"
    assert widget.market_type.isVisible()
    assert widget.symbol.isVisible()
    assert widget.timeframe.isVisible()
    assert widget.capital_per_entry.isVisible()
    assert widget.max_entries.isVisible()
    assert widget.leverage.isVisible() is False
    # Spot asks for no number of its own, so there is no row to hide for it.
    assert not hasattr(widget, "spot_ratio")
    assert not hasattr(widget, "spot_row")


def test_futures_selection_brings_the_leverage_row_in(qtbot: QtBot) -> None:
    widget = SessionSetupWidget()
    qtbot.addWidget(widget)

    widget.market_type.setCurrentIndex(widget.market_type.findData("futures"))

    assert widget.futures_row.isHidden() is False
    assert widget.values().futures_leverage == "1"


def test_futures_leverage_input_uses_domain_bounds(qtbot: QtBot) -> None:
    widget = SessionSetupWidget()
    qtbot.addWidget(widget)

    assert widget.leverage.minimum() == FuturesTradingPolicy.V1_MINIMUM_LEVERAGE
    assert widget.leverage.maximum() == FuturesTradingPolicy.V1_MAXIMUM_LEVERAGE


def test_futures_request_includes_selected_leverage(qtbot: QtBot) -> None:
    widget = SessionSetupWidget()
    qtbot.addWidget(widget)
    widget.market_type.setCurrentIndex(widget.market_type.findData("futures"))

    widget.leverage.setValue(5)

    assert widget.values().futures_leverage == "5"


def test_switching_back_to_spot_removes_futures_input_from_request(
    qtbot: QtBot,
) -> None:
    widget = SessionSetupWidget()
    qtbot.addWidget(widget)
    widget.market_type.setCurrentIndex(widget.market_type.findData("futures"))
    widget.leverage.setValue(5)

    widget.market_type.setCurrentIndex(widget.market_type.findData("spot"))

    values = widget.values()

    assert values.market_type == "spot"
    assert values.futures_leverage is None
    assert widget.futures_row.isHidden()


def test_timeframe_field_offers_every_supported_value(qtbot: QtBot) -> None:
    widget = SessionSetupWidget()
    qtbot.addWidget(widget)

    values = [
        widget.timeframe.itemData(index) for index in range(widget.timeframe.count())
    ]

    assert values == ["1m", "3m", "5m", "15m", "30m", "1h", "4h"]


def test_submit_emits_current_values_once(qtbot: QtBot) -> None:
    widget = SessionSetupWidget()
    qtbot.addWidget(widget)
    widget.capital_per_entry.setText("200000")

    with qtbot.waitSignal(widget.create_requested) as signal:
        click(widget.create_button)

    assert signal.args == [widget.values()]


def test_loading_disables_repeated_submit(qtbot: QtBot) -> None:
    widget = SessionSetupWidget()
    qtbot.addWidget(widget)
    widget.set_loading(True)

    assert widget.create_button.isEnabled() is False
    assert widget.create_button.text() == "Starting…"

    widget.set_loading(False)

    assert widget.create_button.isEnabled() is True
    assert widget.create_button.text() == "Start Paper Bot"


def test_field_error_is_rendered_next_to_its_input(qtbot: QtBot) -> None:
    widget = SessionSetupWidget()
    qtbot.addWidget(widget)

    widget.show_field_error("capital_per_entry", "Amount must be positive")

    assert widget.capital_per_entry_error.text() == ("Amount must be positive")


@pytest.mark.parametrize(
    "session",
    [
        pytest.param(configured_spot_session(), id="spot"),
        pytest.param(configured_futures_session(), id="futures"),
    ],
)
def test_a_form_filled_from_a_session_reads_back_as_that_session(
    qtbot: QtBot, session: ConfiguredPaperSession
) -> None:
    """The comparison "nothing was edited" rests on this being exact.

    A form that came out of a Session must read back as the same values, or
    an untouched field reads as an edit and the window asks a question about
    a change nobody made.
    """
    widget = SessionSetupWidget()
    qtbot.addWidget(widget)

    widget.set_values_from_session(session)

    assert widget.values() == setup_values_of(session)
