from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Lock
from uuid import UUID


class LatestMarketPrice:
    """Thread-safe public market-price fact with an explicit freshness bound."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._clock = clock
        self._lock = Lock()
        self._session_id: UUID | None = None
        self._price: Decimal | None = None
        self._received_at: datetime | None = None

    def reset(self) -> None:
        with self._lock:
            self._session_id = None
            self._price = None
            self._received_at = None

    def observe(self, session_id: UUID, price: Decimal) -> None:
        if not price.is_finite() or price <= 0:
            raise ValueError("price must be finite and positive")
        with self._lock:
            self._session_id = session_id
            self._price = price
            self._received_at = self._clock()

    def get(self, session_id: UUID, *, max_age: timedelta) -> Decimal:
        if max_age <= timedelta(0):
            raise ValueError("max_age must be positive")
        observed_at = self._clock()
        with self._lock:
            current_session_id = self._session_id
            price = self._price
            received_at = self._received_at
        if (
            current_session_id != session_id
            or price is None
            or received_at is None
            or observed_at < received_at
            or observed_at - received_at > max_age
        ):
            raise RuntimeError("A fresh market price is unavailable")
        return price
