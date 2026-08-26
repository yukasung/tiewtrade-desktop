"""Whether the prices on the chart are current, and how the screen learns it.

Two facts make one reading. The feed reports what its own connection is doing,
and that report can be right while the chart still stands still — a socket
that believes it is live but has gone quiet looks exactly like a market that
has. So prices arriving are counted as a fact in their own right, and a chart
that has heard none for long enough says so whatever the feed claims.

This is the fault as the reader meets it. What is behind it — a dead socket, a
machine that slept, a stream that went quiet — is the feed's business to report
and to recover from; the screen's business is not to keep drawing as though
nothing had changed.
"""

import logging
from collections.abc import Callable
from datetime import datetime

from tiewtrade.application.chart_data import ChartFeedState
from tiewtrade.application.chart_view_selection import ChartViewSelection

_LOGGER = logging.getLogger("tiewtrade.ui.chart_workflow")

# How long a chart of a Pair that trades every second may go without a price
# before the screen says so. Long enough that ordinary jitter passes, short
# enough that a reader is still looking at the screen when it answers.
PRICE_SILENCE_SECONDS = 15.0
# Asking is cheap — one subtraction — and asking often is what keeps the
# answer within the window above rather than a check period behind it.
PRICE_SILENCE_CHECK_MILLISECONDS = 5000


class ChartFeedReporter:
    """One reading of the two facts, and a say when that reading changes.

    `announce` is told the new reading and the one it replaced, because
    coming back from a silence is a different event to falling into one and
    the chart has work to do on only one of them.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime],
        view: Callable[[], ChartViewSelection | None],
        announce: Callable[[ChartFeedState, ChartFeedState | None], None],
    ) -> None:
        self._clock = clock
        self._view = view
        self._announce = announce
        self._reported: ChartFeedState | None = None
        self._announced: ChartFeedState | None = None
        self._prices_silent = False
        self._last_fact_at: datetime | None = None

    def restart(self) -> None:
        """Begin watching a fresh chart, which has been silent for no time yet."""
        self._prices_silent = False
        self._last_fact_at = self._clock()

    def feed_reported(self, state: ChartFeedState) -> None:
        """Take what the chart's own connection says it is doing."""
        if state is self._reported:
            return
        self._reported = state
        self._publish()

    def price_arrived(self) -> None:
        """Count a price as having reached the chart, breaking any silence."""
        now = self._clock()
        last_fact_at = self._last_fact_at
        self._last_fact_at = now
        if not self._prices_silent:
            return
        self._prices_silent = False
        self._log(
            "chart.prices.resumed",
            0.0 if last_fact_at is None else (now - last_fact_at).total_seconds(),
        )
        self._publish()

    def check_silence(self) -> None:
        """Ask whether prices are still arriving, and say so when they stop.

        Nothing to answer for until a chart has been started: silence is
        measured from the last price, and a chart that has watched no market
        has neither a price nor a wait to compare one against.
        """
        last_fact_at = self._last_fact_at
        if last_fact_at is None:
            return
        silent_seconds = (self._clock() - last_fact_at).total_seconds()
        if self._prices_silent or silent_seconds < PRICE_SILENCE_SECONDS:
            return
        self._prices_silent = True
        self._log("chart.prices.silent", silent_seconds)
        self._publish()

    def shown_state(self) -> ChartFeedState:
        """The reading the two facts come to together.

        A feed that has stopped is the worse of the two and stays on top of a
        silence, which is what a stopped feed causes anyway. Prices coming in
        with nothing said against them is what live means, whether or not
        anything is reporting the feed at all.
        """
        if self._reported is ChartFeedState.STOPPED:
            return ChartFeedState.STOPPED
        if self._prices_silent or self._reported is ChartFeedState.RECONNECTING:
            return ChartFeedState.RECONNECTING
        return ChartFeedState.LIVE

    def _publish(self) -> None:
        state = self.shown_state()
        if state is self._announced:
            return
        previous = self._announced
        self._announced = state
        self._announce(state, previous)

    def _log(self, event_name: str, silent_seconds: float) -> None:
        selection = self._view()
        _LOGGER.info(
            event_name,
            extra={
                "event_name": event_name,
                "silent_seconds": round(silent_seconds, 1),
                "symbol": None if selection is None else selection.symbol,
                "timeframe": None if selection is None else selection.timeframe,
            },
        )
