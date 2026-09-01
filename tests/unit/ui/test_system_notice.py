"""What the system says about a connection, without a window to say it in."""

from pytestqt.qtbot import QtBot

from tests.support.connection_notice import RecordingConnectionNotice
from tiewtrade.application.chart_data import ChartFeedState
from tiewtrade.ui.system_notice import (
    CHART_FEED_STOPPED_NOTICE,
    SystemNoticeWorkflow,
)


def _workflow(
    notice: RecordingConnectionNotice,
    *,
    delay_ms: int,
) -> tuple[SystemNoticeWorkflow, list[str]]:
    """The workflow, and every line it has put on the system notice."""
    said: list[str] = []
    workflow = SystemNoticeWorkflow(
        connection_notice=notice,
        connection_notice_delay_ms=delay_ms,
    )
    workflow.notice_changed.connect(said.append)
    return workflow, said


def test_a_stopped_chart_feed_is_said_on_the_system_notice() -> None:
    workflow, said = _workflow(RecordingConnectionNotice(), delay_ms=60_000)

    workflow.feed_state(ChartFeedState.STOPPED)

    assert said == [CHART_FEED_STOPPED_NOTICE]


def test_a_feed_that_comes_back_takes_the_line_back_with_it() -> None:
    workflow, said = _workflow(RecordingConnectionNotice(), delay_ms=60_000)
    workflow.feed_state(ChartFeedState.STOPPED)

    workflow.feed_state(ChartFeedState.LIVE)

    assert said[-1] == ""


def test_a_feed_that_is_reconnecting_is_not_on_the_line_at_all() -> None:
    """Waiting on a connection is told in a dialog, not on this line.

    The line carries the faults the reader has to act on, and a connection
    the runtime is still retrying is not yet one of them.
    """
    workflow, said = _workflow(RecordingConnectionNotice(), delay_ms=60_000)

    workflow.feed_state(ChartFeedState.RECONNECTING)

    assert said == [""]


def test_a_connection_that_comes_back_quickly_is_never_announced(
    qtbot: QtBot,
) -> None:
    """The runtime retries forever, so most drops are a blink.

    Announcing every one of them would put a dialog over the chart each time
    a phone hotspot wavered, and the trader would learn to dismiss it.
    """
    notice = RecordingConnectionNotice()
    workflow, _said = _workflow(notice, delay_ms=60_000)

    workflow.feed_state(ChartFeedState.RECONNECTING)
    workflow.feed_state(ChartFeedState.LIVE)

    qtbot.waitUntil(lambda: not workflow._timer.isActive())
    assert notice.shown == 0


def test_a_connection_still_away_after_the_wait_is_announced(qtbot: QtBot) -> None:
    notice = RecordingConnectionNotice()
    workflow, _said = _workflow(notice, delay_ms=0)

    workflow.feed_state(ChartFeedState.RECONNECTING)

    qtbot.waitUntil(lambda: notice.shown == 1)


def test_more_reconnect_news_neither_delays_nor_repeats_the_announcement(
    qtbot: QtBot,
) -> None:
    """Restarting the countdown on every update would defer it forever.

    A drop keeps producing snapshots the whole time it lasts, so a timer
    that started again on each one would never reach the end of its wait.
    """
    notice = RecordingConnectionNotice()
    workflow, _said = _workflow(notice, delay_ms=60_000)
    workflow.feed_state(ChartFeedState.RECONNECTING)
    qtbot.waitUntil(workflow._timer.isActive)
    remaining = workflow._timer.remainingTime()
    qtbot.wait(20)

    workflow.feed_state(ChartFeedState.RECONNECTING)
    workflow.feed_state(ChartFeedState.RECONNECTING)

    assert workflow._timer.remainingTime() < remaining
    assert notice.shown == 0


def test_the_announcement_is_taken_back_when_prices_are_current_again(
    qtbot: QtBot,
) -> None:
    notice = RecordingConnectionNotice()
    workflow, _said = _workflow(notice, delay_ms=0)
    workflow.feed_state(ChartFeedState.RECONNECTING)
    qtbot.waitUntil(lambda: notice.shown == 1)

    workflow.feed_state(ChartFeedState.LIVE)

    qtbot.waitUntil(lambda: notice.hidden == 1)


def test_closing_takes_the_announcement_down_and_stops_the_wait(
    qtbot: QtBot,
) -> None:
    notice = RecordingConnectionNotice()
    workflow, _said = _workflow(notice, delay_ms=60_000)
    workflow.feed_state(ChartFeedState.RECONNECTING)
    qtbot.waitUntil(workflow._timer.isActive)

    workflow.close()

    assert not workflow._timer.isActive()
    assert notice.hidden == 1
