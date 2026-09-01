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
) -> tuple[TotalPnlWorkflow, list[object]]:
    workflow = TotalPnlWorkflow(
        read_total_realized_pnl=read_total_realized_pnl,  # type: ignore[arg-type]
        thread_pool=QThreadPool(),
    )
    shown: list[object] = []
    workflow.total_changed.connect(shown.append)
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
