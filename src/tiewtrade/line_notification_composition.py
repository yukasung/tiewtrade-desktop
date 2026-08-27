"""Where the LINE notifier is put together, and the only place it is.

The desktop composition root builds Sessions, stores and runtimes, and the
one thing it must not learn on the way is where a credential is kept: a form
the trader fills in and a keyring the machine owns are joined here instead,
so `desktop_main` and `ui` can hand these two functions around without ever
importing a keyring or a transport.

Settings are read when a Runtime starts. Changing them while the Bot runs
therefore takes effect the next time it is started, which is also the only
moment a token can be checked without a message waiting on it.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from tiewtrade.application.session_notice import (
    SessionNotice,
    SessionNotifier,
)
from tiewtrade.integrations.keyring.line_notifications import (
    LineNotificationSettingsError,
    load_line_notification_settings,
    save_line_notification_settings,
)
from tiewtrade.integrations.line.messaging_api import LineNoticeNotifier
from tiewtrade.integrations.line.push_transport import LineMessagingApiPush

__all__ = [
    "LineNotificationSettingsError",
    "line_session_notifier",
    "line_notification_reading",
    "save_line_notification_settings",
]


def line_notification_reading() -> tuple[bool, str, str]:
    """What a settings form is filled with: turned on, sending to whom, with what.

    The token comes back with the rest of it. A screen that offers to change
    a token has to show which token it is changing, and it stays covered on
    the way there — read out of here, never out of a message or a log.
    """
    settings = load_line_notification_settings()
    return (
        settings.enabled,
        settings.target_id,
        settings.channel_access_token,
    )


@asynccontextmanager
async def line_session_notifier() -> AsyncIterator[SessionNotifier]:
    """Open whatever will say what the Bot did, for the life of a Runtime.

    Settings that are off, or half-filled, give a notifier that says nothing:
    a Runtime is composed the same way whether or not the trader wants LINE
    messages, and nothing upstream has to ask which it is.
    """
    settings = load_line_notification_settings()
    if not settings.deliverable:
        yield _say_nothing
        return
    async with LineMessagingApiPush(
        channel_access_token=settings.channel_access_token,
        target_id=settings.target_id,
    ) as push:
        async with LineNoticeNotifier(push) as notifier:
            yield notifier.notify


def _say_nothing(notice: SessionNotice) -> None:
    del notice
