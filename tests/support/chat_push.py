"""A chat service's socket, faked to whatever a test needs it to answer.

Both channels reach their service the same way, over the same shared
transport, so one fake serves every test of it: what a test varies is the
status that comes back or the error the socket raises, and what it reads is
the call that went out.
"""

from __future__ import annotations

from typing import Any


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class FakeSession:
    """Answers every post the same way, and keeps what it was sent."""

    def __init__(self, status: int = 200, error: BaseException | None = None) -> None:
        self._status = status
        self._error = error
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if self._error is not None:
            raise self._error
        return FakeResponse(self._status)

    async def close(self) -> None:
        self.closed = True
