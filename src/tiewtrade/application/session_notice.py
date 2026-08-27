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

    Worth saying because a Cooldown does not lapse: until the trader lifts it
    the Bot will take no further Entry, however far the market moves.
    """

    market: SessionMarket
    basket_id: UUID
    completed_pairs: int
    open_entry_count: int
    started_at: datetime


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
