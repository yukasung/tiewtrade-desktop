from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

import tiewtrade.notification_composition as composition
from tiewtrade.application.session_notice import EntryOpenedNotice, SessionMarket
from tiewtrade.integrations.keyring.line_notifications import LineNotificationSettings
from tiewtrade.integrations.keyring.telegram_notifications import (
    TelegramNotificationSettings,
)
from tiewtrade.integrations.notifications.notice_queue import PushOutcome

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

    async def push(self, message: str) -> PushOutcome:
        self.messages.append(message)
        return PushOutcome.DELIVERED

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


@pytest.fixture
def telegram_push(monkeypatch: pytest.MonkeyPatch) -> Iterator[RecordingPush]:
    recording = RecordingPush()

    def transport(*, bot_token: str, chat_id: str) -> RecordingPush:
        recording.opened_with = {"bot_token": bot_token, "chat_id": chat_id}
        return recording

    monkeypatch.setattr(composition, "TelegramBotApiPush", transport)
    yield recording


def _settings(**overrides: object) -> LineNotificationSettings:
    values: dict[str, object] = {
        "enabled": True,
        "target_id": "U123",
        "channel_access_token": "token-value",
    }
    values.update(overrides)
    return LineNotificationSettings(**values)  # type: ignore[arg-type]


def _telegram_settings(**overrides: object) -> TelegramNotificationSettings:
    values: dict[str, object] = {
        "enabled": True,
        "chat_id": "-1001234",
        "bot_token": "123:token-value",
    }
    values.update(overrides)
    return TelegramNotificationSettings(**values)  # type: ignore[arg-type]


_NOTHING_STORED = TelegramNotificationSettings(enabled=False, chat_id="", bot_token="")
_NO_LINE = LineNotificationSettings(
    enabled=False, target_id="", channel_access_token=""
)


def _notify_once(
    monkeypatch: pytest.MonkeyPatch,
    settings: object = _NO_LINE,
    telegram: object = _NOTHING_STORED,
) -> None:
    monkeypatch.setattr(
        composition, "load_line_notification_settings", lambda: settings
    )
    monkeypatch.setattr(
        composition, "load_telegram_notification_settings", lambda: telegram
    )

    async def run() -> None:
        async with composition.session_notifier() as notify:
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


def test_the_telegram_reading_is_the_telegram_settings_themselves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        composition,
        "load_telegram_notification_settings",
        lambda: _telegram_settings(),
    )

    assert composition.telegram_notification_reading() == (
        True,
        "-1001234",
        "123:token-value",
    )


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


def test_telegram_settings_that_are_ready_push_the_notice(
    monkeypatch: pytest.MonkeyPatch,
    telegram_push: RecordingPush,
) -> None:
    _notify_once(monkeypatch, telegram=_telegram_settings())

    assert telegram_push.opened_with == {
        "bot_token": "123:token-value",
        "chat_id": "-1001234",
    }
    assert len(telegram_push.messages) == 1
    assert "Entry #1 opened" in telegram_push.messages[0]
    assert telegram_push.closed


def test_both_channels_set_up_both_get_the_same_notice(
    monkeypatch: pytest.MonkeyPatch,
    push: RecordingPush,
    telegram_push: RecordingPush,
) -> None:
    """Two channels are two phones, not a choice between one and the other.

    A trader who set both up did so to be told either way — a notice reaching
    only whichever channel is read first would be a message quietly lost.
    """
    _notify_once(monkeypatch, _settings(), _telegram_settings())

    assert push.messages == telegram_push.messages
    assert len(push.messages) == 1


def test_one_channel_being_off_leaves_the_other_sending(
    monkeypatch: pytest.MonkeyPatch,
    push: RecordingPush,
    telegram_push: RecordingPush,
) -> None:
    _notify_once(monkeypatch, _settings(enabled=False), _telegram_settings())

    assert push.messages == []
    assert len(telegram_push.messages) == 1


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


@pytest.mark.parametrize(
    "telegram",
    [
        _telegram_settings(enabled=False),
        _telegram_settings(chat_id=""),
        _telegram_settings(bot_token=""),
    ],
)
def test_telegram_settings_that_are_off_or_half_filled_say_nothing_at_all(
    monkeypatch: pytest.MonkeyPatch,
    telegram_push: RecordingPush,
    telegram: TelegramNotificationSettings,
) -> None:
    _notify_once(monkeypatch, telegram=telegram)

    assert telegram_push.messages == []
    assert telegram_push.opened_with == {}
