"""The channel a worker hands chart facts to the Qt thread through.

A candle read off a socket arrives on whatever thread was reading, and a Qt
widget may only be touched from the one that made it, so every fact crosses
here — queued onto the Qt thread by the signals below rather than delivered
where it was found.

Crossing takes time, and a Session can be replaced while a fact is still in
the air. So each fact carries the generation it was published under, and the
reader asks whether that generation is still the one on screen: a candle for
a Session nobody is looking at any more is dropped rather than drawn over the
one that replaced it. Retiring a view is `invalidate`, which makes every fact
already in flight stale without needing to find them.
"""

from threading import Lock

from PySide6.QtCore import QObject, Signal

from tiewtrade.application.chart_data import ChartCandle, ChartFeedState


class ChartFactPublisher:
    def __init__(self, relay: "ChartFactRelay", generation: int) -> None:
        self._relay = relay
        self._generation = generation

    def completed_candle(self, candle: ChartCandle) -> None:
        self._relay.completed_candle_ready.emit(candle, self._generation)

    def in_progress_candle(self, candle: ChartCandle) -> None:
        self._relay.in_progress_candle_ready.emit(candle, self._generation)

    def feed_state(self, state: ChartFeedState) -> None:
        self._relay.feed_state_ready.emit(state, self._generation)


class ChartFactRelay(QObject):
    """Queues generation-bound chart facts onto the Qt thread."""

    completed_candle_ready = Signal(object, int)
    in_progress_candle_ready = Signal(object, int)
    feed_state_ready = Signal(object, int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._lock = Lock()
        self._generation = 0

    def new_generation(self) -> ChartFactPublisher:
        with self._lock:
            self._generation += 1
            return ChartFactPublisher(self, self._generation)

    def invalidate(self) -> None:
        with self._lock:
            self._generation += 1

    def is_current(self, generation: int) -> bool:
        with self._lock:
            return generation == self._generation
