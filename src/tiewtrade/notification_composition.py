"""Where the notifiers are put together, and the only place they are.

The desktop composition root builds Sessions, stores and runtimes, and the
one thing it must not learn on the way is where a credential is kept: the
forms a trader fills in and the keyring the machine owns are joined here
instead, so `desktop_main` and `ui` can hand these functions around without
ever importing a keyring or a transport.

A Bot has two chat services it can speak into, and they are not a choice
between: a trader who has set up both gets both, because a notice is one
thing that happened and each channel is a phone they may be looking at. A
channel that is off, or half filled in, is simply not opened — the other one
is unaffected, and a Runtime is composed the same way whichever of the four
combinations the trader has left the settings in.

Settings are read when a Runtime starts. Changing them while the Bot runs
therefore takes effect the next time it is started, which is also the only
moment a token can be checked without a message waiting on it.
"""

from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager

from tiewtrade.application.session_notice import (
    SessionNotice,
    SessionNotifier,
)
from tiewtrade.integrations.keyring.line_notifications import (
    LineNotificationSettingsError,
    load_line_notification_settings,
    save_line_notification_settings,
)
from tiewtrade.integrations.keyring.telegram_notifications import (
    TelegramNotificationSettingsError,
    load_telegram_notification_settings,
    save_telegram_notification_settings,
)
from tiewtrade.integrations.line.push_transport import LineMessagingApiPush
from tiewtrade.integrations.notifications.notice_queue import (
    NoticeNotifier,
    NoticePush,
)
from tiewtrade.integrations.telegram.push_transport import TelegramBotApiPush

__all__ = [
    "LineNotificationSettingsError",
    "TelegramNotificationSettingsError",
    "line_notification_reading",
    "save_line_notification_settings",
    "save_telegram_notification_settings",
    "session_notifier",
    "telegram_notification_reading",
]

LINE_CHANNEL = "line"
TELEGRAM_CHANNEL = "telegram"
"""What the log calls each channel, and the only name that distinguishes them."""


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


def telegram_notification_reading() -> tuple[bool, str, str]:
    """The Telegram half of the same form, read back the same way."""
    settings = load_telegram_notification_settings()
    return (settings.enabled, settings.chat_id, settings.bot_token)


@asynccontextmanager
async def session_notifier() -> AsyncIterator[SessionNotifier]:
    """Open whatever will say what the Bot did, for the life of a Runtime.

    Settings that are off, or half-filled, give a notifier that says nothing:
    nothing upstream has to ask which channels a trader wanted, or whether
    they wanted any.
    """
    async with AsyncExitStack() as stack:
        notify_each: list[SessionNotifier] = []
        line = load_line_notification_settings()
        if line.deliverable:
            notify_each.append(
                await _open_channel(
                    stack,
                    LineMessagingApiPush(
                        channel_access_token=line.channel_access_token,
                        target_id=line.target_id,
                    ),
                    channel=LINE_CHANNEL,
                )
            )
        telegram = load_telegram_notification_settings()
        if telegram.deliverable:
            notify_each.append(
                await _open_channel(
                    stack,
                    TelegramBotApiPush(
                        bot_token=telegram.bot_token,
                        chat_id=telegram.chat_id,
                    ),
                    channel=TELEGRAM_CHANNEL,
                )
            )
        yield _say_it_everywhere(notify_each)


async def _open_channel(
    stack: AsyncExitStack,
    push: AbstractAsyncContextManager[NoticePush],
    *,
    channel: str,
) -> SessionNotifier:
    """One transport and the queue in front of it, held open by the caller.

    Both go on the same stack, so leaving the Runtime drains what is queued
    before the socket it would be pushed over is closed.
    """
    opened = await stack.enter_async_context(push)
    notifier = await stack.enter_async_context(NoticeNotifier(opened, channel=channel))
    return notifier.notify


def _say_it_everywhere(notify_each: Sequence[SessionNotifier]) -> SessionNotifier:
    """One notice to every channel that is set up, in the order they are read.

    Each of these queues the notice and returns, so the second channel is
    never waiting on the first one's network — and none of them raises, so
    a channel cannot cost another one its message.
    """
    if not notify_each:
        return _say_nothing
    channels = tuple(notify_each)

    def say_it(notice: SessionNotice) -> None:
        for notify in channels:
            notify(notice)

    return say_it


def _say_nothing(notice: SessionNotice) -> None:
    del notice
