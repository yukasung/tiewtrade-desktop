from dataclasses import replace

import pytest
from PySide6.QtGui import QValidator
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
from tiewtrade.trading.cooldown_policy import CooldownPolicy
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
    assert widget.strategy.currentText() == "RSI Step Grid"
    assert [
        widget.strategy.itemData(index) for index in range(widget.strategy.count())
    ] == ["rsi-step-grid-v1", "fast-test-v1"]


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


def _validator_state(validator: QValidator, text: str) -> QValidator.State:
    result = validator.validate(text, len(text))
    assert isinstance(result, tuple)
    state = result[0]
    assert isinstance(state, QValidator.State)
    return state


def test_amount_field_accepts_only_a_number(qtbot: QtBot) -> None:
    widget = SessionSetupWidget()
    qtbot.addWidget(widget)

    validator = widget.capital_per_entry.validator()
    assert validator is not None
    assert _validator_state(validator, "250.75") is QValidator.State.Acceptable
    assert _validator_state(validator, "2 5 0") is QValidator.State.Invalid
    assert _validator_state(validator, "abc") is QValidator.State.Invalid
    assert _validator_state(validator, "-250") is QValidator.State.Invalid
    # A field being filled in is not yet a field that is wrong: an empty
    # Amount is still what the form reports as a missing Amount.
    assert _validator_state(validator, "") is not QValidator.State.Invalid


def test_the_form_opens_on_the_longest_cooldown(qtbot: QtBot) -> None:
    """The safest of the choices, so an untouched form is never the loosest."""
    widget = SessionSetupWidget()
    qtbot.addWidget(widget)

    assert widget.values().cooldown_days == "30"
    assert widget.cooldown.currentText() == "30"
    assert widget.cooldown.accessibleName() == "Cooldown (days)"


def test_the_form_offers_off_and_every_day_it_supports(qtbot: QtBot) -> None:
    widget = SessionSetupWidget()
    qtbot.addWidget(widget)

    labels = [widget.cooldown.itemText(i) for i in range(widget.cooldown.count())]

    # The unit is on the label, so the list is thirty plain numbers.
    assert labels[:4] == ["Off", "1", "2", "3"]
    assert labels[-1] == "30"
    assert len(labels) == CooldownPolicy.MAXIMUM_DAYS + 1


def test_choosing_off_states_no_days_at_all(qtbot: QtBot) -> None:
    widget = SessionSetupWidget()
    qtbot.addWidget(widget)

    widget.cooldown.setCurrentIndex(widget.cooldown.findText("Off"))

    assert widget.values().cooldown_days is None


def test_choosing_a_number_of_days_states_that_number(qtbot: QtBot) -> None:
    widget = SessionSetupWidget()
    qtbot.addWidget(widget)

    widget.cooldown.setCurrentIndex(widget.cooldown.findText("1"))

    assert widget.values().cooldown_days == "1"


@pytest.mark.parametrize(
    ("cooldown", "expected"),
    [
        (CooldownPolicy.off(), "Off"),
        (CooldownPolicy.for_days(1), "1"),
        (CooldownPolicy.for_days(3), "3"),
    ],
)
def test_a_session_reads_back_onto_the_form(
    qtbot: QtBot, cooldown: CooldownPolicy, expected: str
) -> None:
    widget = SessionSetupWidget()
    qtbot.addWidget(widget)
    session = _session_with_cooldown(cooldown)

    widget.set_values_from_session(session)

    assert widget.cooldown.currentText() == expected
    assert widget.values() == setup_values_of(session)


def test_a_cooldown_error_reaches_the_field_that_asked(qtbot: QtBot) -> None:
    widget = SessionSetupWidget()
    qtbot.addWidget(widget)

    widget.show_field_error("cooldown_days", "days must be between 1 and 30")

    revealed = widget.reveal_validation_field("cooldown_days")

    assert revealed is not None
    assert revealed[0] is widget.cooldown
    assert revealed[1].text() == "days must be between 1 and 30"


def _session_with_cooldown(cooldown: CooldownPolicy) -> ConfiguredPaperSession:
    session = configured_spot_session()
    return replace(
        session,
        config=replace(
            session.config,
            entry_policy=replace(session.config.entry_policy, cooldown=cooldown),
        ),
    )
