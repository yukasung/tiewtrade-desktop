"""A trader is told on their phone what the Bot just did, over both channels.

Everything between a candle and a chat service is exercised here for real:
the Session that opens the Entry, the reading that turns it into a notice,
the wording, the queue, the one retry, and the request each channel builds.
The only thing faked is the socket itself and the keyring the settings are
read out of — no token in this file is a token anywhere.

What the unit tests around this prove one seam at a time, this proves joined
up, because the failure worth catching is at a join: a message that reads
well and is never sent, or is sent to the channel the other one's token
belongs to.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from threading import Event
from typing import Any, TypeVar

import aiohttp
import pytest
from _pytest.monkeypatch import MonkeyPatch

import tiewtrade.notification_composition as composition
from tests.support.paper_runtime_fakes import (
    ImmediateScheduler,
    PaperRuntimeCandleSource,
)
from tests.support.paper_session_setup import configured_spot_session
from tiewtrade.application.paper_runtime import PaperRuntimeController
from tiewtrade.application.paper_session_setup import ConfiguredPaperSession
from tiewtrade.application.trading_workspace import BotRuntimeState
from tiewtrade.integrations.binance.public_endpoints import BinancePublicEndpoints
from tiewtrade.integrations.keyring.line_notifications import LineNotificationSettings
from tiewtrade.integrations.keyring.telegram_notifications import (
    TelegramNotificationSettings,
)
from tiewtrade.integrations.line.push_transport import PUSH_URL as LINE_PUSH_URL
from tiewtrade.integrations.sqlite.active_paper_sessions import (
    SQLiteActivePaperSessions,
)
from tiewtrade.integrations.sqlite.database import SQLiteDatabase
from tiewtrade.integrations.sqlite.paper_runtime_lifecycle import (
    SQLitePaperRuntimeLifecycle,
)
from tiewtrade.integrations.sqlite.session_notices import (
    KeptNotice,
    SQLiteSessionNotices,
)
from tiewtrade.integrations.sqlite.trade_history import SQLiteTradeHistory
from tiewtrade.trading.symbol_rules import SymbolRules

LINE_TOKEN = "line-token-value"
LINE_TARGET = "U1234567890"
TELEGRAM_TOKEN = "1234:telegram-token-value"
TELEGRAM_CHAT = "-1009876"
_T = TypeVar("_T")


class ChatService:
    """One chat service's socket, answering whatever the test scripted.

    Statuses are answered in turn and the last one stands, so a service that
    is down for one request and back for the next is written as `[500, 200]`.
    """

    def __init__(self, *statuses: int) -> None:
        self._statuses = list(statuses) or [200]
        self.calls: list[dict[str, Any]] = []
        self.received = Event()

    def post(self, url: str, **kwargs: Any) -> ChatResponse:
        self.calls.append({"url": url, **kwargs})
        self.received.set()
        status = self._statuses[0]
        if len(self._statuses) > 1:
            self._statuses.pop(0)
        return ChatResponse(status)

    async def close(self) -> None:
        return None

    @property
    def messages(self) -> list[str]:
        return [_message_of(call) for call in self.calls]

    def wait_for_a_message(self) -> None:
        assert self.received.wait(5), "no message reached the chat service"


class RoutedSockets:
    """The one socket factory both channels reach for, routed by address.

    Which channel opens its socket first is a race between two workers, so
    nothing here is handed out in order: a request is given to the service
    whose own address it names.
    """

    def __init__(self, line: ChatService, telegram: ChatService) -> None:
        self._line = line
        self._telegram = telegram

    def post(self, url: str, **kwargs: Any) -> ChatResponse:
        service = self._line if url == LINE_PUSH_URL else self._telegram
        return service.post(url, **kwargs)

    async def close(self) -> None:
        return None


class ChatResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    async def __aenter__(self) -> ChatResponse:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


@pytest.fixture
def chat_services(monkeypatch: MonkeyPatch) -> Callable[..., dict[str, ChatService]]:
    """Set both channels up in the keyring, and hand back their sockets.

    The settings are read through the same composition the desktop uses, so
    what a trader filled in on the Settings page is what decides which
    channels open — the test only stands in for the keyring behind it.
    """

    def set_up(
        *,
        line: ChatService | None = None,
        telegram: ChatService | None = None,
    ) -> dict[str, ChatService]:
        services = {
            "line": line if line is not None else ChatService(),
            "telegram": telegram if telegram is not None else ChatService(),
        }
        monkeypatch.setattr(
            composition,
            "load_line_notification_settings",
            lambda: LineNotificationSettings(
                enabled=True,
                target_id=LINE_TARGET,
                channel_access_token=LINE_TOKEN,
            ),
        )
        monkeypatch.setattr(
            composition,
            "load_telegram_notification_settings",
            lambda: TelegramNotificationSettings(
                enabled=True,
                chat_id=TELEGRAM_CHAT,
                bot_token=TELEGRAM_TOKEN,
            ),
        )
        monkeypatch.setattr(
            aiohttp,
            "ClientSession",
            lambda: RoutedSockets(services["line"], services["telegram"]),
        )
        return services

    return set_up


def test_an_entry_the_bot_opened_reaches_both_chat_services(
    tmp_path: Path,
    chat_services: Callable[..., dict[str, ChatService]],
) -> None:
    """One thing happened, and every phone the trader set up hears about it."""
    services = chat_services()

    _run_until_a_message(tmp_path, services)

    for service in services.values():
        assert service.messages, "a channel that was set up was never sent anything"
        opened = service.messages[0]
        assert "Entry #1 opened" in opened
        assert "BTCUSDT" in opened
        assert "Take profit at" in opened


def test_each_channel_is_addressed_the_way_its_own_service_expects(
    tmp_path: Path,
    chat_services: Callable[..., dict[str, ChatService]],
) -> None:
    """The two services take a message differently, and a token differently.

    LINE names the bot in a header and Telegram in the path. Getting that
    wrong sends a live token to the wrong service, which is why it is worth
    pinning where the whole thing is joined up rather than only per channel.
    """
    services = chat_services()

    _run_until_a_message(tmp_path, services)

    line = services["line"].calls[0]
    assert line["url"] == LINE_PUSH_URL
    assert line["headers"]["Authorization"] == f"Bearer {LINE_TOKEN}"
    assert line["json"]["to"] == LINE_TARGET
    assert LINE_TOKEN not in line["url"]

    telegram = services["telegram"].calls[0]
    assert TELEGRAM_TOKEN in telegram["url"]
    assert telegram["json"]["chat_id"] == TELEGRAM_CHAT
    assert TELEGRAM_TOKEN not in str(telegram["json"])
    assert TELEGRAM_TOKEN not in str(telegram.get("headers", {}))
    # Neither channel's credential goes anywhere near the other's request.
    assert LINE_TOKEN not in str(telegram)
    assert TELEGRAM_TOKEN not in str(line)


def test_a_service_having_a_bad_minute_is_asked_once_more(
    tmp_path: Path,
    chat_services: Callable[..., dict[str, ChatService]],
) -> None:
    """A 500 costs the delivery a couple of seconds, not the message."""
    stumbling = ChatService(500, 200)
    services = chat_services(line=stumbling)

    _run_until_a_message(tmp_path, services, wait_for=stumbling, calls=2)

    assert len(stumbling.calls) >= 2
    first, retried = stumbling.calls[0], stumbling.calls[1]
    # The same message, not a fresh reading of a Basket that has since moved.
    assert _message_of(retried) == _message_of(first)


def test_a_refused_message_is_not_retried_and_does_not_stop_the_bot(
    tmp_path: Path,
    chat_services: Callable[..., dict[str, ChatService]],
) -> None:
    """A withdrawn token costs the message. The trading is not its business.

    The Entry is in the database and on the Position tab either way — a chat
    service refusing to carry the news must not reach back into the Session
    that made it, nor into the other channel, which is unaffected.
    """
    refusing = ChatService(403)
    services = chat_services(line=refusing)
    telegram = services["telegram"]

    session, database = _run_until_a_message(tmp_path, services, wait_for=telegram)

    assert len(refusing.calls) == 1
    assert telegram.messages, "one channel refusing must not silence the other"
    basket = SQLiteTradeHistory(database).find_open_basket(session.config.session_id)
    assert basket is not None, "the Entry the trader was told about is in the database"
    assert basket.entry_count == 1


def _database_path(tmp_path: Path) -> Path:
    return tmp_path / "acceptance.sqlite3"


def _run_until_a_message(
    tmp_path: Path,
    services: dict[str, ChatService],
    *,
    wait_for: ChatService | None = None,
    calls: int = 1,
) -> tuple[ConfiguredPaperSession, SQLiteDatabase]:
    """Start a Paper Spot Bot, let it trade, and stop it once it has spoken."""
    session = configured_spot_session()
    database = SQLiteDatabase(_database_path(tmp_path))
    database.migrate()
    assert SQLiteActivePaperSessions(database).create(session).created
    watched = wait_for if wait_for is not None else services["line"]
    controller = PaperRuntimeController(
        lifecycle=SQLitePaperRuntimeLifecycle(database),
        trade_history=SQLiteTradeHistory(database),
        symbol_rules=SymbolRules(
            symbol="BTCUSDT",
            tick_size=Decimal("0.1"),
            step_size=Decimal("0.001"),
            min_notional=Decimal("5"),
        ),
        source_factory=_source_factory(),
        scheduler=ImmediateScheduler(),
        snapshot_callback=lambda _snapshot: None,
        # The journal the desktop composes, on the database the Session's own
        # Fills are written to.
        notifier_factory=lambda: composition.session_notifier(
            journal=SQLiteSessionNotices(database)
        ),
    )
    result = controller.start(session)
    assert result.workspace.header is not None
    assert result.workspace.header.runtime_state is BotRuntimeState.RUNNING
    try:
        watched.wait_for_a_message()
        _wait_for_calls(watched, calls)
    finally:
        # Stopping drains what is queued, so every message the Bot raised is
        # pushed before the sockets close.
        controller.stop(session)
    return session, database


def _source_factory() -> Callable[[BinancePublicEndpoints], PaperRuntimeCandleSource]:
    source = PaperRuntimeCandleSource()
    return lambda _endpoints: source


def _wait_for_calls(service: ChatService, calls: int) -> None:
    deadline = Event()
    for _ in range(500):
        if len(service.calls) >= calls:
            return
        deadline.wait(0.02)


def _message_of(call: dict[str, Any]) -> str:
    payload = call["json"]
    if "messages" in payload:
        return str(payload["messages"][0]["text"])
    return str(payload["text"])


def test_what_the_bot_said_outlives_the_process_that_said_it(
    tmp_path: Path,
    chat_services: Callable[..., dict[str, ChatService]],
) -> None:
    """The message reached a phone; the record of it reaches the next run.

    A chat message is gone as soon as it is read, and the log is not a record
    a trader can be asked to keep. What was said, and whether each service
    took it, is read back out of the database the Session traded into.
    """
    services = chat_services()

    session, database = _run_until_a_message(tmp_path, services)
    _wait_for_calls(services["telegram"], 1)

    kept = _notices_after_reopening(_database_path(tmp_path), session)
    assert kept, "the Bot said something and kept no record of it"
    entry = kept[0]
    assert entry.notice.message == services["line"].messages[0]
    assert entry.notice.session_id == session.config.session_id
    assert sorted(delivery.channel for delivery in entry.deliveries) == [
        "line",
        "telegram",
    ]
    assert {delivery.outcome for delivery in entry.deliveries} == {"delivered"}


def test_a_refusal_is_kept_beside_the_message_it_refused(
    tmp_path: Path,
    chat_services: Callable[..., dict[str, ChatService]],
) -> None:
    """Which service did not take it, and what it answered with.

    This is the record's whole reason for being: a trader who never got a
    message can find out here whether it was sent and refused, and by which
    of their two chat services.
    """
    refusing = ChatService(403)
    services = chat_services(line=refusing)

    session, database = _run_until_a_message(
        tmp_path, services, wait_for=services["telegram"]
    )
    _wait_for_calls(refusing, 1)

    kept = _notices_after_reopening(_database_path(tmp_path), session)
    assert kept
    settled = {
        delivery.channel: (delivery.outcome, delivery.status)
        for delivery in kept[0].deliveries
    }
    assert settled["line"] == ("rejected", 403)
    assert settled["telegram"][0] == "delivered"


def _notices_after_reopening(
    database_path: Path, session: ConfiguredPaperSession
) -> tuple[KeptNotice, ...]:
    """Read the records the way the next run of the application would."""
    return SQLiteSessionNotices(SQLiteDatabase(database_path)).list_notices(
        session.config.session_id
    )
