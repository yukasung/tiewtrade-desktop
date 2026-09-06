"""What the Bot says out loud while nobody is watching the screen, and what may say it.

The desktop is where a Session is read, and the trader is not always in front
of it. What happens meanwhile falls into four kinds: the Bot took or gave up a
Position, the strategy closed or the trader opened the Entry gate, the market
feed went away or came back, and the Bot stopped for a reason of its own.

Each is a notice of its own rather than one notice with a kind on it, because
they carry different facts and read as different messages. What they share is
the contract below: `application` states what happened, and never learns which
transport carries it.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from tiewtrade.trading.basket import BasketCloseReason


@dataclass(frozen=True, slots=True)
class SessionMarket:
    """Which Session, and which market it was pinned to.

    Every notice names it, and no notice can read it off what happened: a
    Fill knows its price, not the symbol it was quoted in. It is joined on
    where a notice is built, from the configuration the Session was started
    with.
    """

    session_id: UUID
    symbol: str
    timeframe: str


@dataclass(frozen=True, slots=True)
class EntryOpenedNotice:
    """One Entry, as it reads to someone who was not watching it fill."""

    market: SessionMarket
    basket_id: UUID
    entry_number: int
    price: Decimal
    quantity: Decimal
    take_profit_price: Decimal | None
    filled_at: datetime

    @property
    def notional(self) -> Decimal:
        """What the Entry paid, which neither price nor quantity says alone."""
        return self.price * self.quantity


@dataclass(frozen=True, slots=True)
class BasketClosedNotice:
    """A whole Position let go, and what the round came to.

    `realized_pnl` is the Basket's whole life, including Entries sold out of
    it long before this exit — which is the number the trader is waiting on,
    rather than what this last Fill alone happened to be worth.
    """

    market: SessionMarket
    basket_id: UUID
    entry_count: int
    average_entry_price: Decimal
    exit_price: Decimal
    realized_pnl: Decimal
    close_reason: BasketCloseReason
    closed_at: datetime


@dataclass(frozen=True, slots=True)
class CooldownStartedNotice:
    """The Basket completed an Entry Pair, so the gate closed behind it.

    Worth saying because the Bot will take no further Entry until it opens
    again, however far the market moves. A Cooldown that started is one the
    Session gave days to — a Session whose Cooldown is off never starts one —
    so `ends_at` is the day it lifts on, and there is always one to name.
    """

    market: SessionMarket
    basket_id: UUID
    completed_pairs: int
    open_entry_count: int
    started_at: datetime
    ends_at: datetime


@dataclass(frozen=True, slots=True)
class CooldownEndedNotice:
    """The Cooldown ran out of days, so the gate opened with nobody asking.

    Worth saying for exactly that reason: nothing the trader did produced it,
    so nothing else on screen would have told them the Bot may buy again.
    """

    market: SessionMarket
    basket_id: UUID
    completed_pairs: int
    ended_at: datetime


@dataclass(frozen=True, slots=True)
class CooldownCancelledNotice:
    """The trader lifted the Cooldown, so one more Entry may open."""

    market: SessionMarket
    basket_id: UUID
    completed_pairs: int
    cancelled_at: datetime


@dataclass(frozen=True, slots=True)
class MarketFeedLostNotice:
    """The candles stopped arriving, and the Bot is waiting on them.

    The Session is not over: it is holding whatever it held, deciding nothing
    until the feed answers. `reason` is the market-data runtime's own reading
    of why, said in its own words rather than guessed at here.
    """

    market: SessionMarket
    reason: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class MarketFeedRestoredNotice:
    """Candles are arriving again, and the Bot is deciding on them again."""

    market: SessionMarket
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class BotErrorNotice:
    """The Bot stopped and will decide nothing more until it is started again.

    This is the one notice that asks the trader to go and look: everything
    else says what the Bot did, and this says what it can no longer do.
    """

    market: SessionMarket
    reason: str
    observed_at: datetime


type SessionNotice = (
    EntryOpenedNotice
    | BasketClosedNotice
    | CooldownStartedNotice
    | CooldownEndedNotice
    | CooldownCancelledNotice
    | MarketFeedLostNotice
    | MarketFeedRestoredNotice
    | BotErrorNotice
)
"""Anything the Bot may say out loud about a running Session."""


type SessionNotifier = Callable[[SessionNotice], None]
"""Say that something happened.

Called from the candle loop and from the thread a trader's ask arrives on, so
it must return promptly, must never raise, and must be safe to call from
either: a message that could not be sent is not a reason for the Bot to stop
trading, and waiting on a network is not something a Session may be made to do.
"""


class NoticeKind(StrEnum):
    """What a notice is, in the words a durable record keeps it under.

    Written out rather than taken from the class name: a record read back a
    month from now says what happened at the time, and renaming a class is
    not an event in a Session's history.
    """

    ENTRY_OPENED = "entry_opened"
    BASKET_CLOSED = "basket_closed"
    COOLDOWN_STARTED = "cooldown_started"
    COOLDOWN_ENDED = "cooldown_ended"
    COOLDOWN_CANCELLED = "cooldown_cancelled"
    MARKET_FEED_LOST = "market_feed_lost"
    MARKET_FEED_RESTORED = "market_feed_restored"
    BOT_ERROR = "bot_error"


def notice_kind(notice: SessionNotice) -> NoticeKind:
    """Which of the eight this notice is."""
    match notice:
        case EntryOpenedNotice():
            return NoticeKind.ENTRY_OPENED
        case BasketClosedNotice():
            return NoticeKind.BASKET_CLOSED
        case CooldownStartedNotice():
            return NoticeKind.COOLDOWN_STARTED
        case CooldownEndedNotice():
            return NoticeKind.COOLDOWN_ENDED
        case CooldownCancelledNotice():
            return NoticeKind.COOLDOWN_CANCELLED
        case MarketFeedLostNotice():
            return NoticeKind.MARKET_FEED_LOST
        case MarketFeedRestoredNotice():
            return NoticeKind.MARKET_FEED_RESTORED
        case BotErrorNotice():
            return NoticeKind.BOT_ERROR


def notice_moment(notice: SessionNotice) -> datetime:
    """When the thing this notice is about happened.

    Each notice names its own moment after the thing it reports — a Fill is
    filled, a Basket is closed, a feed is observed — so the one question every
    record asks of every notice is answered here instead of eight times over.

    It is never the moment a message was written or sent: a notice queued
    behind a chat service that is down still reports the Fill's own time, and
    a record that drifted to delivery time would date the Session's history by
    how the trader's phone was doing.
    """
    match notice:
        case EntryOpenedNotice():
            return notice.filled_at
        case BasketClosedNotice():
            return notice.closed_at
        case CooldownStartedNotice():
            # The day it lifts on is where it is going, not when it began.
            return notice.started_at
        case CooldownEndedNotice():
            return notice.ended_at
        case CooldownCancelledNotice():
            return notice.cancelled_at
        case MarketFeedLostNotice() | MarketFeedRestoredNotice() | BotErrorNotice():
            return notice.observed_at
