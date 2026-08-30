from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

import tiewtrade.line_notification_composition as composition
from tiewtrade.application.session_notice import EntryOpenedNotice, SessionMarket
from tiewtrade.integrations.keyring.line_notifications import LineNotificationSettings
from tiewtrade.integrations.line.messaging_api import LinePushOutcome

NOTICE = EntryOpenedNotice(
    market=SessionMarket(session_id=uuid4(), symbol="BTCUSDT", timeframe="5m"),
    basket_id=uuid4(),
    entry_number=1,
    price=Decimal("64120.50"),
    quantity=Decimal("0.0039"),
    take_profit_price=None,
    filled_at=datetime(2026, 8, 26, 10, 5, tzinfo=UTC),
)


class RecordingPush:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.opened_with: dict[str, str] = {}
        self.closed = False

    async def push(self, message: str) -> LinePushOutcome:
        self.messages.append(message)
        return LinePushOutcome.DELIVERED

    async def __aenter__(self) -> RecordingPush:
        return self

    async def __aexit__(self, *args: object) -> None:
        self.closed = True


@pytest.fixture
def push(monkeypatch: pytest.MonkeyPatch) -> Iterator[RecordingPush]:
    recording = RecordingPush()

    def transport(*, channel_access_token: str, target_id: str) -> RecordingPush:
        recording.opened_with = {
            "channel_access_token": channel_access_token,
            "target_id": target_id,
        }
        return recording

    monkeypatch.setattr(composition, "LineMessagingApiPush", transport)
    yield recording


def _settings(**overrides: object) -> LineNotificationSettings:
    values: dict[str, object] = {
        "enabled": True,
        "target_id": "U123",
        "channel_access_token": "token-value",
    }
    values.update(overrides)
    return LineNotificationSettings(**values)  # type: ignore[arg-type]


def _notify_once(monkeypatch: pytest.MonkeyPatch, settings: object) -> None:
    monkeypatch.setattr(
        composition, "load_line_notification_settings", lambda: settings
    )

    async def run() -> None:
        async with composition.line_session_notifier() as notify:
            notify(NOTICE)

    asyncio.run(run())


def test_the_reading_a_form_gets_is_the_settings_themselves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The form is filled from what is stored, token included.

    A screen that offers to change a token has to show which one it is
    changing, the same way it shows the recipient beside it.
    """
    monkeypatch.setattr(
        composition, "load_line_notification_settings", lambda: _settings()
    )

    assert composition.line_notification_reading() == (True, "U123", "token-value")


def test_settings_that_are_ready_push_the_notice(
    monkeypatch: pytest.MonkeyPatch,
    push: RecordingPush,
) -> None:
    _notify_once(monkeypatch, _settings())

    assert push.opened_with == {
        "channel_access_token": "token-value",
        "target_id": "U123",
    }
    assert len(push.messages) == 1
    assert "Entry #1 opened" in push.messages[0]
    assert push.closed


@pytest.mark.parametrize(
    "settings",
    [
        _settings(enabled=False),
        _settings(target_id=""),
        _settings(channel_access_token=""),
    ],
)
def test_settings_that_are_off_or_half_filled_say_nothing_at_all(
    monkeypatch: pytest.MonkeyPatch,
    push: RecordingPush,
    settings: LineNotificationSettings,
) -> None:
    _notify_once(monkeypatch, settings)

    assert push.messages == []
    assert push.opened_with == {}
