from __future__ import annotations

import logging

import keyring
import pytest
from keyring.errors import KeyringError, PasswordDeleteError

from tiewtrade.integrations.keyring.line_notifications import (
    load_line_notification_settings,
    save_line_notification_settings,
)
from tiewtrade.integrations.keyring.telegram_notifications import (
    TelegramNotificationSettingsError,
    load_telegram_notification_settings,
    save_telegram_notification_settings,
)


class FakeKeyring:
    """The little of a keyring these settings use, without a real one."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.stored: dict[tuple[str, str], str] = {}
        self._error = error

    def get_password(self, service: str, username: str) -> str | None:
        if self._error is not None:
            raise self._error
        return self.stored.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        if self._error is not None:
            raise self._error
        self.stored[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if self._error is not None:
            raise self._error
        if (service, username) not in self.stored:
            raise PasswordDeleteError("not stored")
        del self.stored[(service, username)]


@pytest.fixture
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> FakeKeyring:
    return _install(monkeypatch, FakeKeyring())


def _install(monkeypatch: pytest.MonkeyPatch, fake: FakeKeyring) -> FakeKeyring:
    monkeypatch.setattr(keyring, "get_password", fake.get_password)
    monkeypatch.setattr(keyring, "set_password", fake.set_password)
    monkeypatch.setattr(keyring, "delete_password", fake.delete_password)
    return fake


def test_settings_come_back_the_way_they_were_saved(fake_keyring: FakeKeyring) -> None:
    save_telegram_notification_settings(
        enabled=True, chat_id="-1001234", bot_token="123:token-value"
    )

    settings = load_telegram_notification_settings()

    assert settings.enabled
    assert settings.chat_id == "-1001234"
    assert settings.bot_token == "123:token-value"
    assert settings.deliverable


def test_nothing_saved_reads_as_notifications_off(fake_keyring: FakeKeyring) -> None:
    settings = load_telegram_notification_settings()

    assert not settings.enabled
    assert not settings.deliverable


def test_settings_turned_on_without_a_token_deliver_nothing(
    fake_keyring: FakeKeyring,
) -> None:
    save_telegram_notification_settings(enabled=True, chat_id="-1001234", bot_token="")

    settings = load_telegram_notification_settings()

    assert settings.enabled
    assert not settings.deliverable


def test_a_token_the_trader_cleared_is_cleared(fake_keyring: FakeKeyring) -> None:
    """A blank token field is a token taken back, not a field left alone."""
    save_telegram_notification_settings(
        enabled=True, chat_id="-1001234", bot_token="123:token-value"
    )

    save_telegram_notification_settings(enabled=True, chat_id="-1005678", bot_token="")

    settings = load_telegram_notification_settings()
    assert settings.chat_id == "-1005678"
    assert settings.bot_token == ""


def test_turning_notifications_off_stops_them_without_forgetting_the_token(
    fake_keyring: FakeKeyring,
) -> None:
    save_telegram_notification_settings(
        enabled=True, chat_id="-1001234", bot_token="123:token-value"
    )

    save_telegram_notification_settings(
        enabled=False, chat_id="-1001234", bot_token="123:token-value"
    )

    settings = load_telegram_notification_settings()
    assert not settings.enabled
    assert not settings.deliverable
    assert settings.bot_token == "123:token-value"


def test_a_chat_the_trader_cleared_is_cleared(fake_keyring: FakeKeyring) -> None:
    save_telegram_notification_settings(
        enabled=True, chat_id="-1001234", bot_token="123:token-value"
    )

    save_telegram_notification_settings(
        enabled=True, chat_id="", bot_token="123:token-value"
    )

    assert load_telegram_notification_settings().chat_id == ""


def test_the_two_channels_are_kept_apart(fake_keyring: FakeKeyring) -> None:
    """One channel set up must never read as the other being set up.

    Both live in the same keyring under the same service name, and a trader
    who runs only Telegram would be told LINE was ready to send if these
    shared a record.
    """
    save_telegram_notification_settings(
        enabled=True, chat_id="-1001234", bot_token="123:token-value"
    )

    assert not load_line_notification_settings().deliverable

    save_line_notification_settings(
        enabled=True, target_id="U123", channel_access_token="line-token"
    )

    telegram = load_telegram_notification_settings()
    assert telegram.deliverable
    assert telegram.bot_token == "123:token-value"


def test_a_keyring_that_cannot_be_read_reads_as_notifications_off(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _install(monkeypatch, FakeKeyring(error=KeyringError("no backend")))

    with caplog.at_level(logging.WARNING):
        settings = load_telegram_notification_settings()

    assert not settings.enabled
    assert not settings.deliverable
    assert "telegram.notification.settings.unavailable" in caplog.text


def test_a_keyring_that_cannot_be_written_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, FakeKeyring(error=KeyringError("no backend")))

    with pytest.raises(TelegramNotificationSettingsError):
        save_telegram_notification_settings(
            enabled=True, chat_id="-1001234", bot_token="123:token-value"
        )
