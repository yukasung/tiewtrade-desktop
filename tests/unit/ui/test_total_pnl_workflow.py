import threading
from dataclasses import replace
from decimal import Decimal

from PySide6.QtCore import QThreadPool
from pytestqt.qtbot import QtBot

from tests.support.workspace_snapshots import basket_entries, basket_snapshot
from tiewtrade.application.trading_workspace import BasketSnapshot
from tiewtrade.ui.total_pnl_workflow import TotalPnlWorkflow


def _unmarked_basket() -> BasketSnapshot:
    entries = tuple(
        replace(entry, unrealized_pnl=None)
        for entry in basket_entries("63000", "65000")
    )
    return basket_snapshot(
        entries=entries,
        current_price=None,
        unrealized_pnl=None,
    )


def _workflow(
    read_total_realized_pnl: object = None,
    *,
    read_high_water_mark: object = None,
    record_high_water_mark: object = None,
) -> tuple[TotalPnlWorkflow, list[object]]:
    workflow = TotalPnlWorkflow(
        read_total_realized_pnl=read_total_realized_pnl,  # type: ignore[arg-type]
        read_high_water_mark=read_high_water_mark,  # type: ignore[arg-type]
        record_high_water_mark=record_high_water_mark,  # type: ignore[arg-type]
        thread_pool=QThreadPool(),
    )
    shown: list[object] = []
    workflow.total_changed.connect(shown.append)
    return workflow, shown


def _drawdown_workflow(
    *,
    realized: Decimal,
    high_water_mark: Decimal,
    record: object = None,
) -> tuple[TotalPnlWorkflow, list[object]]:
    workflow = TotalPnlWorkflow(
        read_total_realized_pnl=lambda: realized,
        read_high_water_mark=lambda: high_water_mark,
        record_high_water_mark=record,  # type: ignore[arg-type]
        thread_pool=QThreadPool(),
    )
    shown: list[object] = []
    workflow.drawdown_changed.connect(shown.append)
    return workflow, shown


def test_the_database_is_read_off_the_qt_thread(qtbot: QtBot) -> None:
    read_on: list[int] = []

    def read() -> Decimal:
        read_on.append(threading.get_ident())
        return Decimal("12.5")

    workflow, shown = _workflow(read)

    workflow.start()
    qtbot.waitUntil(lambda: shown == [Decimal("12.5")])
    assert read_on and read_on[0] != threading.get_ident()


def test_an_open_basket_is_added_without_reading_the_database_again(
    qtbot: QtBot,
) -> None:
    reads: list[int] = []

    def read() -> Decimal:
        reads.append(1)
        return Decimal("10")

    workflow, shown = _workflow(read)

    workflow.start()
    qtbot.waitUntil(lambda: shown == [Decimal("10")])
    workflow.set_open_basket(basket_snapshot(unrealized_pnl=Decimal("1000")))

    assert shown[-1] == Decimal("1010")
    assert len(reads) == 1


def test_an_unmarked_basket_reads_as_no_total_yet(qtbot: QtBot) -> None:
    workflow, shown = _workflow(lambda: Decimal("10"))

    workflow.set_open_basket(_unmarked_basket())
    workflow.start()
    qtbot.waitUntil(lambda: len(shown) >= 1)

    assert shown[-1] is None


def test_a_refresh_during_a_read_re_reads_once_rather_than_twice(
    qtbot: QtBot,
) -> None:
    """Two more asks while one is out are still one Basket's worth of news."""
    started = threading.Event()
    release = threading.Event()
    reads: list[int] = []

    def read() -> Decimal:
        reads.append(1)
        started.set()
        release.wait(timeout=2)
        return Decimal(len(reads))

    workflow, _shown = _workflow(read)

    workflow.start()
    assert started.wait(timeout=2)
    workflow.refresh()
    workflow.refresh()
    release.set()
    qtbot.waitUntil(lambda: len(reads) == 2)
    qtbot.wait(50)

    assert len(reads) == 2


def test_a_reading_landing_after_the_close_is_dropped(qtbot: QtBot) -> None:
    release = threading.Event()
    started = threading.Event()

    def read() -> Decimal:
        started.set()
        release.wait(timeout=2)
        return Decimal("10")

    workflow, shown = _workflow(read)

    workflow.start()
    assert started.wait(timeout=2)
    workflow.close()
    release.set()
    qtbot.wait(100)

    assert shown == []


def test_a_read_that_fails_takes_the_total_away_rather_than_leaving_it(
    qtbot: QtBot,
) -> None:
    """A figure that survives a failed read is a figure nothing stands behind."""
    failures: list[int] = []

    def read() -> Decimal:
        if failures:
            raise RuntimeError("database gone")
        failures.append(1)
        return Decimal("10")

    workflow, shown = _workflow(read)

    workflow.start()
    qtbot.waitUntil(lambda: shown == [Decimal("10")])
    workflow.refresh()
    qtbot.waitUntil(lambda: shown[-1] is None)


def test_a_total_that_has_not_changed_is_not_published_again(qtbot: QtBot) -> None:
    workflow, shown = _workflow(lambda: Decimal("10"))

    workflow.start()
    qtbot.waitUntil(lambda: shown == [Decimal("10")])
    workflow.set_open_basket(None)
    workflow.refresh()
    qtbot.wait(50)

    assert shown == [Decimal("10")]


def test_a_window_with_no_reader_stays_silent(qtbot: QtBot) -> None:
    workflow, shown = _workflow(None)

    workflow.start()
    workflow.refresh()
    workflow.set_open_basket(basket_snapshot())
    qtbot.wait(50)

    assert shown == []


def test_a_live_price_moves_the_total_without_reading_the_database_again(
    qtbot: QtBot,
) -> None:
    """The Runtime marks its Basket once a candle; the price moves all along."""
    reads: list[int] = []

    def read() -> Decimal:
        reads.append(1)
        return Decimal("10")

    workflow, shown = _workflow(read)

    workflow.start()
    qtbot.waitUntil(lambda: shown == [Decimal("10")])
    workflow.set_open_basket(basket_snapshot())
    assert shown[-1] == Decimal("1010")

    workflow.set_current_price("BTCUSDT", Decimal("65500"))

    assert shown[-1] == Decimal("1510")
    assert len(reads) == 1


def test_a_price_for_another_pair_does_not_mark_this_basket(qtbot: QtBot) -> None:
    """The card follows whatever the chart is showing; the Basket does not."""
    workflow, shown = _workflow(lambda: Decimal("10"))

    workflow.start()
    qtbot.waitUntil(lambda: shown == [Decimal("10")])
    workflow.set_open_basket(basket_snapshot())
    workflow.set_current_price("ETHUSDT", Decimal("3000"))

    assert shown[-1] == Decimal("1010")


def test_a_price_arriving_before_the_basket_marks_it_when_it_lands(
    qtbot: QtBot,
) -> None:
    workflow, shown = _workflow(lambda: Decimal("10"))

    workflow.start()
    qtbot.waitUntil(lambda: shown == [Decimal("10")])
    workflow.set_current_price("BTCUSDT", Decimal("65500"))
    workflow.set_open_basket(_unmarked_basket())

    assert shown[-1] == Decimal("1510")


def test_a_price_with_no_basket_open_leaves_the_realized_total_alone(
    qtbot: QtBot,
) -> None:
    workflow, shown = _workflow(lambda: Decimal("10"))

    workflow.start()
    qtbot.waitUntil(lambda: shown == [Decimal("10")])
    workflow.set_current_price("BTCUSDT", Decimal("65500"))
    qtbot.wait(50)

    assert shown == [Decimal("10")]


def test_a_cleared_price_leaves_the_basket_reading_as_the_runtime_marked_it(
    qtbot: QtBot,
) -> None:
    """A Session or Pair change takes the price away; the Basket keeps its own."""
    workflow, shown = _workflow(lambda: Decimal("10"))

    workflow.start()
    qtbot.waitUntil(lambda: shown == [Decimal("10")])
    workflow.set_open_basket(basket_snapshot())
    workflow.set_current_price("BTCUSDT", Decimal("65500"))
    assert shown[-1] == Decimal("1510")

    workflow.clear_price()

    assert shown[-1] == Decimal("1010")


def test_with_no_way_to_read_a_peak_drawdown_has_no_answer(qtbot: QtBot) -> None:
    workflow = TotalPnlWorkflow(
        read_total_realized_pnl=lambda: Decimal("10"),
        thread_pool=QThreadPool(),
    )
    drawdown_shown: list[object] = []
    workflow.drawdown_changed.connect(drawdown_shown.append)

    workflow.start()
    qtbot.waitUntil(lambda: len(drawdown_shown) >= 1)

    assert drawdown_shown[-1] is None


def test_standing_at_the_peak_reads_as_no_drawdown(qtbot: QtBot) -> None:
    workflow, shown = _drawdown_workflow(
        realized=Decimal("500"), high_water_mark=Decimal("500")
    )

    workflow.start()

    qtbot.waitUntil(lambda: shown[-1:] == [Decimal("0")])


def test_below_the_peak_reads_as_the_distance_back_to_it(qtbot: QtBot) -> None:
    workflow, shown = _drawdown_workflow(
        realized=Decimal("320"), high_water_mark=Decimal("500")
    )

    workflow.start()

    qtbot.waitUntil(lambda: shown[-1:] == [Decimal("180")])


def test_a_new_peak_is_recorded_and_reads_as_no_drawdown(qtbot: QtBot) -> None:
    recorded: list[Decimal] = []

    def record(value: Decimal) -> Decimal:
        recorded.append(value)
        return value

    workflow, shown = _drawdown_workflow(
        realized=Decimal("650"), high_water_mark=Decimal("500"), record=record
    )

    workflow.start()

    qtbot.waitUntil(lambda: shown[-1:] == [Decimal("0")])
    assert recorded == [Decimal("650")]


def test_standing_below_the_peak_never_asks_to_record_anything(
    qtbot: QtBot,
) -> None:
    """A total that has not beaten the peak is not the Bot's best day yet."""
    recorded: list[Decimal] = []

    def record(value: Decimal) -> Decimal:
        recorded.append(value)
        return value

    workflow, shown = _drawdown_workflow(
        realized=Decimal("320"), high_water_mark=Decimal("500"), record=record
    )

    workflow.start()

    qtbot.waitUntil(lambda: shown[-1:] == [Decimal("180")])
    qtbot.wait(50)
    assert recorded == []


def test_a_live_price_that_sets_a_new_peak_is_recorded_too(qtbot: QtBot) -> None:
    recorded: list[Decimal] = []

    def record(value: Decimal) -> Decimal:
        recorded.append(value)
        return value

    workflow, shown = _drawdown_workflow(
        realized=Decimal("500"), high_water_mark=Decimal("500"), record=record
    )
    workflow.start()
    qtbot.waitUntil(lambda: shown[-1:] == [Decimal("0")])

    basket = basket_snapshot(unrealized_pnl=Decimal("150"))
    workflow.set_open_basket(basket)

    qtbot.waitUntil(lambda: recorded == [Decimal("650")])
    assert shown[-1] == Decimal("0")


def test_a_write_that_never_answers_still_reads_the_value_offered(
    qtbot: QtBot,
) -> None:
    """The screen shows the total's own peak even if the file never confirms it.

    A record that could not be kept costs the record, not the figure a trader
    is watching right now.
    """

    def record(value: Decimal) -> Decimal:
        raise RuntimeError("sqlite failed at /private/tmp with api_key=secret")

    workflow, shown = _drawdown_workflow(
        realized=Decimal("650"), high_water_mark=Decimal("500"), record=record
    )

    workflow.start()

    qtbot.waitUntil(lambda: shown[-1:] == [Decimal("0")])
