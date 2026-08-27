from __future__ import annotations

import logging

import keyring
import pytest
from keyring.errors import KeyringError, PasswordDeleteError

from tiewtrade.integrations.keyring.line_notifications import (
    LineNotificationSettingsError,
    load_line_notification_settings,
    save_line_notification_settings,
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
    save_line_notification_settings(
        enabled=True, target_id="U123", channel_access_token="token-value"
    )

    settings = load_line_notification_settings()

    assert settings.enabled
    assert settings.target_id == "U123"
    assert settings.channel_access_token == "token-value"
    assert settings.deliverable


def test_nothing_saved_reads_as_notifications_off(fake_keyring: FakeKeyring) -> None:
    settings = load_line_notification_settings()

    assert not settings.enabled
    assert not settings.deliverable


def test_settings_turned_on_without_a_token_deliver_nothing(
    fake_keyring: FakeKeyring,
) -> None:
    save_line_notification_settings(
        enabled=True, target_id="U123", channel_access_token=""
    )

    settings = load_line_notification_settings()

    assert settings.enabled
    assert not settings.deliverable


def test_a_token_the_trader_cleared_is_cleared(fake_keyring: FakeKeyring) -> None:
    """A blank token field is a token taken back, not a field left alone.

    The form is filled from what is stored, so a field that reaches here
    empty was emptied on purpose — the same reading the recipient gets.
    """
    save_line_notification_settings(
        enabled=True, target_id="U123", channel_access_token="token-value"
    )

    save_line_notification_settings(
        enabled=True, target_id="U456", channel_access_token=""
    )

    settings = load_line_notification_settings()
    assert settings.target_id == "U456"
    assert settings.channel_access_token == ""


def test_turning_notifications_off_stops_them_without_forgetting_the_token(
    fake_keyring: FakeKeyring,
) -> None:
    save_line_notification_settings(
        enabled=True, target_id="U123", channel_access_token="token-value"
    )

    save_line_notification_settings(
        enabled=False, target_id="U123", channel_access_token="token-value"
    )

    settings = load_line_notification_settings()
    assert not settings.enabled
    assert not settings.deliverable
    assert settings.channel_access_token == "token-value"


def test_a_target_the_trader_cleared_is_cleared(fake_keyring: FakeKeyring) -> None:
    """The recipient is shown in the form, so an empty one was meant."""
    save_line_notification_settings(
        enabled=True, target_id="U123", channel_access_token="token-value"
    )

    save_line_notification_settings(
        enabled=True, target_id="", channel_access_token="token-value"
    )

    assert load_line_notification_settings().target_id == ""


def test_a_keyring_that_cannot_be_read_reads_as_notifications_off(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _install(monkeypatch, FakeKeyring(error=KeyringError("no backend")))

    with caplog.at_level(logging.WARNING):
        settings = load_line_notification_settings()

    assert not settings.enabled
    assert not settings.deliverable
    assert "line.notification.settings.unavailable" in caplog.text


def test_a_keyring_that_cannot_be_written_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, FakeKeyring(error=KeyringError("no backend")))

    with pytest.raises(LineNotificationSettingsError):
        save_line_notification_settings(
            enabled=True, target_id="U123", channel_access_token="token-value"
        )
