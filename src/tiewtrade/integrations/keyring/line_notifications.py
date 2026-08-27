"""Where the LINE channel access token is kept, which is not this repository.

A channel access token sends messages as the account that owns it, so it is
a credential and belongs where the operating system keeps credentials — the
Keychain on macOS — and never in SQLite, in a log line, in a fixture, or in
a file beside the database. Everything else these settings hold is kept with
it, because splitting them would only make two places to go wrong.

A keyring that will not answer — no backend, or a Keychain the trader closed
the prompt on — is read as "notifications are off". It is the one thing that
can be said honestly without a token, and it costs a message rather than a
Session.
"""

import logging
from dataclasses import dataclass

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

_logger = logging.getLogger(__name__)

SERVICE_NAME = "TiewTrade"
_ENABLED_KEY = "line_notifications_enabled"
_TARGET_KEY = "line_target_id"
_TOKEN_KEY = "line_channel_access_token"
_ENABLED_VALUE = "1"

_UNAVAILABLE_EVENT = "line.notification.settings.unavailable"


class LineNotificationSettingsError(RuntimeError):
    """The settings could not be kept where the trader asked them to be kept."""


@dataclass(frozen=True, slots=True)
class LineNotificationSettings:
    """What the trader asked for, and whether it is enough to send anything."""

    enabled: bool
    target_id: str
    channel_access_token: str

    @property
    def deliverable(self) -> bool:
        """Turned on and complete: a message sent with these would go out.

        Half-filled settings are a form the trader has not finished, not a
        failure to report every time an Entry opens.
        """
        return bool(self.enabled and self.target_id and self.channel_access_token)


def load_line_notification_settings() -> LineNotificationSettings:
    """Read the settings, or read a keyring that will not answer as "off"."""
    try:
        enabled = keyring.get_password(SERVICE_NAME, _ENABLED_KEY) == _ENABLED_VALUE
        target_id = keyring.get_password(SERVICE_NAME, _TARGET_KEY) or ""
        token = keyring.get_password(SERVICE_NAME, _TOKEN_KEY) or ""
    except KeyringError as error:
        _logger.warning("%s reason=%s", _UNAVAILABLE_EVENT, type(error).__name__)
        return LineNotificationSettings(
            enabled=False, target_id="", channel_access_token=""
        )
    return LineNotificationSettings(
        enabled=enabled, target_id=target_id, channel_access_token=token
    )


def save_line_notification_settings(
    *,
    enabled: bool,
    target_id: str,
    channel_access_token: str,
) -> None:
    """Keep the settings, and say so plainly where they could not be kept.

    Every field is taken at its word, the blank ones included: the form these
    come from is filled with what is stored, so a value that arrives empty
    was emptied on purpose rather than never filled in.
    """
    try:
        _put(_ENABLED_KEY, _ENABLED_VALUE if enabled else "")
        _put(_TARGET_KEY, target_id)
        _put(_TOKEN_KEY, channel_access_token)
    except KeyringError as error:
        _logger.warning("%s reason=%s", _UNAVAILABLE_EVENT, type(error).__name__)
        raise LineNotificationSettingsError(
            "LINE notification settings could not be saved to the keyring"
        ) from error


def _put(key: str, value: str) -> None:
    if value:
        keyring.set_password(SERVICE_NAME, key, value)
        return
    try:
        keyring.delete_password(SERVICE_NAME, key)
    except PasswordDeleteError:
        # Nothing was there to take back, which is the state asked for.
        pass
