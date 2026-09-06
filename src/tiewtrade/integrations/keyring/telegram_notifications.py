"""Where the Telegram bot token is kept, which is not this repository.

A bot token sends messages as the bot that owns it, and anyone holding one can
send as that bot until it is revoked — so it is a credential and belongs where
the operating system keeps credentials, never in SQLite, in a log line, in a
fixture, or in a file beside the database. The chat id is kept with it for the
same reason the LINE recipient is kept with its token: splitting them would
only make two places to go wrong.

Kept apart from the LINE settings rather than in one record with them: a
trader may run either channel, both, or neither, and one of them being unset
must never read as the other being unset too.

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
_ENABLED_KEY = "telegram_notifications_enabled"
_CHAT_KEY = "telegram_chat_id"
_TOKEN_KEY = "telegram_bot_token"
_ENABLED_VALUE = "1"

_UNAVAILABLE_EVENT = "telegram.notification.settings.unavailable"


class TelegramNotificationSettingsError(RuntimeError):
    """The settings could not be kept where the trader asked them to be kept."""


@dataclass(frozen=True, slots=True)
class TelegramNotificationSettings:
    """What the trader asked for, and whether it is enough to send anything."""

    enabled: bool
    chat_id: str
    bot_token: str

    @property
    def deliverable(self) -> bool:
        """Turned on and complete: a message sent with these would go out.

        Half-filled settings are a form the trader has not finished, not a
        failure to report every time an Entry opens.
        """
        return bool(self.enabled and self.chat_id and self.bot_token)


def load_telegram_notification_settings() -> TelegramNotificationSettings:
    """Read the settings, or read a keyring that will not answer as "off"."""
    try:
        enabled = keyring.get_password(SERVICE_NAME, _ENABLED_KEY) == _ENABLED_VALUE
        chat_id = keyring.get_password(SERVICE_NAME, _CHAT_KEY) or ""
        token = keyring.get_password(SERVICE_NAME, _TOKEN_KEY) or ""
    except KeyringError as error:
        _logger.warning("%s reason=%s", _UNAVAILABLE_EVENT, type(error).__name__)
        return TelegramNotificationSettings(enabled=False, chat_id="", bot_token="")
    return TelegramNotificationSettings(
        enabled=enabled, chat_id=chat_id, bot_token=token
    )


def save_telegram_notification_settings(
    *,
    enabled: bool,
    chat_id: str,
    bot_token: str,
) -> None:
    """Keep the settings, and say so plainly where they could not be kept.

    Every field is taken at its word, the blank ones included: the form these
    come from is filled with what is stored, so a value that arrives empty
    was emptied on purpose rather than never filled in.
    """
    try:
        _put(_ENABLED_KEY, _ENABLED_VALUE if enabled else "")
        _put(_CHAT_KEY, chat_id)
        _put(_TOKEN_KEY, bot_token)
    except KeyringError as error:
        _logger.warning("%s reason=%s", _UNAVAILABLE_EVENT, type(error).__name__)
        raise TelegramNotificationSettingsError(
            "Telegram notification settings could not be saved to the keyring"
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
