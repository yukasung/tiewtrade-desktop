"""What a row of the Notification tab says, read off a durable record.

A notice is kept as a chat message meant for a phone — several lines, blank
lines between blocks — and a table row is one line. The row shows what the
message opens with rather than the whole of it, the way a phone's own
notification list shows a message's first line and no more.

Delivery is read the same way whichever channel it came from: named,
outcome, and the status a refusal was answered with, so a trader who never
got a message can tell a bad minute from a withdrawn token without opening a
log.
"""

from tiewtrade.application.session_notices import (
    SessionNoticeDelivery,
    SessionNoticeHistoryItem,
)

_CHANNEL_NAMES = {"line": "LINE", "telegram": "Telegram"}

_OUTCOME_TEXT = {
    "delivered": "Delivered",
    "rejected": "Rejected",
    "retryable": "Not answered",
}


def notice_headline(item: SessionNoticeHistoryItem) -> str:
    """What the message opens with, which is what a phone shows at a glance."""
    return item.message.split("\n", 1)[0]


def notice_delivery_text(item: SessionNoticeHistoryItem) -> str:
    """How every channel that was set up took this notice, read together.

    No deliveries at all is not a channel gone quiet — it is a trader who had
    not set one up when this happened, which reads as its own plain sentence
    rather than an empty cell asking to be worked out.
    """
    if not item.deliveries:
        return "No chat channel set up"
    return " · ".join(_delivery_text(delivery) for delivery in item.deliveries)


def _delivery_text(delivery: SessionNoticeDelivery) -> str:
    channel = _CHANNEL_NAMES.get(delivery.channel, delivery.channel)
    outcome = _OUTCOME_TEXT.get(delivery.outcome, delivery.outcome)
    # The status is worth a trader's attention only on a refusal: it says
    # which setting is wrong. A delivered 200 tells them nothing a delivered
    # message has not already said.
    if delivery.outcome == "rejected" and delivery.status is not None:
        return f"{channel}: {outcome} ({delivery.status})"
    return f"{channel}: {outcome}"
