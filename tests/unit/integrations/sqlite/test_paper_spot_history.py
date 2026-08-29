from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Barrier, Lock, current_thread, main_thread
from uuid import UUID

import pytest

from tiewtrade.application.paper_spot_session import PaperSpotSessionIdentity
from tiewtrade.execution.paper_spot import PaperSpotEntryFill, PaperSpotExitFill
from tiewtrade.integrations.sqlite.database import SQLiteDatabase
from tiewtrade.integrations.sqlite.paper_spot_history import (
    PaperSpotHistoryContext,
    PaperSpotSQLiteHistory,
)
from tiewtrade.integrations.sqlite.trade_history import SQLiteTradeHistory
from tiewtrade.trading.basket import (
    BasketCloseReason,
    ClosedBasket,
    ClosedEntry,
)
from tiewtrade.trading.session_config import MarketType, TradeMode
from tiewtrade.trading.trade_history import (
    BasketResult,
    BasketStatus,
    FillSide,
    FillSource,
    TradeFill,
)

SESSION_ID = UUID("00000000-0000-0000-0000-000000000101")
BASKET_ID = UUID("00000000-0000-0000-0000-000000000102")
OPENED_AT = datetime(2026, 1, 1, tzinfo=UTC)
ENTRY_ATR = Decimal("2.5")
CLOSED_AT = datetime(2026, 1, 2, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> SQLiteTradeHistory:
    database = SQLiteDatabase(tmp_path / "history.sqlite3")
    database.migrate()
    return SQLiteTradeHistory(database)


@pytest.fixture
def history(store: SQLiteTradeHistory) -> PaperSpotSQLiteHistory:
    return PaperSpotSQLiteHistory(
        PaperSpotHistoryContext(
            session_id=SESSION_ID,
            symbol="BTCUSDT",
            timeframe="5m",
            preset_version="rsi-step-grid-v1",
        ),
        store,
    )


class _InterleavingSQLiteTradeHistory(SQLiteTradeHistory):
    def __init__(self, database: SQLiteDatabase, reads: Barrier) -> None:
        super().__init__(database)
        self._reads = reads
        self._remaining_interleaved_reads = 2
        self._read_guard = Lock()

    def get_basket(self, basket_id: UUID) -> BasketResult | None:
        basket = super().get_basket(basket_id)
        with self._read_guard:
            interleave = (
                basket is not None
                and current_thread() is not main_thread()
                and self._remaining_interleaved_reads > 0
            )
            if interleave:
                self._remaining_interleaved_reads -= 1
        if interleave:
            self._reads.wait(timeout=2)
        return basket


def test_history_exposes_session_identity(
    history: PaperSpotSQLiteHistory,
) -> None:
    assert history.session_identity == PaperSpotSessionIdentity(
        session_id=SESSION_ID,
        symbol="BTCUSDT",
        timeframe="5m",
        preset_version="rsi-step-grid-v1",
    )


def entry_fill() -> PaperSpotEntryFill:
    return PaperSpotEntryFill(
        intent_id="intent-1",
        order_id="entry:intent-1",
        fill_id=f"paper:{SESSION_ID}:entry:intent-1:fill",
        price=Decimal("100"),
        quantity=Decimal("2"),
        filled_at=OPENED_AT,
    )


def exit_fill() -> PaperSpotExitFill:
    return PaperSpotExitFill(
        order_id=f"take-profit:{BASKET_ID}",
        fill_id=f"paper:{SESSION_ID}:take-profit:{BASKET_ID}:fill",
        price=Decimal("110"),
        quantity=Decimal("2"),
        filled_at=CLOSED_AT,
    )


def closed_basket() -> ClosedBasket:
    return ClosedBasket(
        basket_id=BASKET_ID,
        entry_count=1,
        average_entry_price=Decimal("100"),
        exit_price=Decimal("110"),
        realized_pnl=Decimal("20"),
        exit_realized_pnl=Decimal("20"),
        closed_at=CLOSED_AT,
    )


def test_entry_fill_creates_exact_open_basket_and_buy_fill(
    history: PaperSpotSQLiteHistory,
    store: SQLiteTradeHistory,
) -> None:
    fill = entry_fill()

    result = history.record_entry(
        basket_id=BASKET_ID,
        entry_number=1,
        fill=fill,
        entry_atr=ENTRY_ATR,
        entry_group_id=1,
        group_take_profit_price=Decimal("106"),
    )

    assert result is True
    assert store.get_basket(BASKET_ID) == BasketResult(
        basket_id=BASKET_ID,
        session_id=SESSION_ID,
        trade_mode=TradeMode.PAPER,
        market_type=MarketType.SPOT,
        symbol="BTCUSDT",
        timeframe="5m",
        strategy_preset_version="rsi-step-grid-v1",
        opened_at_utc=OPENED_AT,
        closed_at_utc=None,
        entry_count=1,
        invested_notional=Decimal("200"),
        realized_pnl=Decimal("0"),
        status=BasketStatus.OPEN,
    )
    assert store.list_fills(BASKET_ID) == (
        TradeFill(
            fill_id=fill.fill_id,
            basket_id=BASKET_ID,
            session_id=SESSION_ID,
            order_id=fill.order_id,
            exchange_trade_id=None,
            side=FillSide.BUY,
            entry_number=1,
            filled_at_utc=OPENED_AT,
            price=Decimal("100"),
            quantity=Decimal("2"),
            notional=Decimal("200"),
            realized_pnl=Decimal("0"),
            source=FillSource.PAPER_EXECUTOR,
            entry_atr=ENTRY_ATR,
            entry_group_id=1,
            group_take_profit_price=Decimal("106"),
        ),
    )


def test_second_entry_replaces_open_aggregate_and_records_another_buy_fill(
    history: PaperSpotSQLiteHistory,
    store: SQLiteTradeHistory,
) -> None:
    history.record_entry(
        basket_id=BASKET_ID,
        entry_number=1,
        fill=entry_fill(),
        entry_atr=ENTRY_ATR,
        entry_group_id=1,
        group_take_profit_price=Decimal("106"),
    )
    second_fill = PaperSpotEntryFill(
        intent_id="intent-2",
        order_id="entry:intent-2",
        fill_id=f"paper:{SESSION_ID}:entry:intent-2:fill",
        price=Decimal("90"),
        quantity=Decimal("1"),
        filled_at=OPENED_AT + timedelta(hours=1),
    )

    history.record_entry(
        basket_id=BASKET_ID,
        entry_number=2,
        fill=second_fill,
        entry_atr=ENTRY_ATR,
        entry_group_id=1,
        group_take_profit_price=Decimal("106"),
    )

    basket = store.get_basket(BASKET_ID)
    fills = store.list_fills(BASKET_ID)
    assert basket is not None
    assert basket.opened_at_utc == OPENED_AT
    assert basket.entry_count == 2
    assert basket.invested_notional == Decimal("290")
    assert basket.realized_pnl == Decimal("0")
    assert [fill.side for fill in fills] == [FillSide.BUY, FillSide.BUY]
    assert fills[1].entry_number == 2
    assert fills[1].notional == Decimal("90")


def test_partial_fill_for_same_spot_order_does_not_increment_entry_count(
    history: PaperSpotSQLiteHistory,
    store: SQLiteTradeHistory,
) -> None:
    first = PaperSpotEntryFill(
        intent_id="intent-1",
        order_id="entry-order-1",
        fill_id="fill-1",
        price=Decimal("100"),
        quantity=Decimal("0.001"),
        filled_at=OPENED_AT,
    )
    partial = PaperSpotEntryFill(
        intent_id="intent-1",
        order_id="entry-order-1",
        fill_id="fill-2",
        price=Decimal("100"),
        quantity=Decimal("0.0005"),
        filled_at=OPENED_AT + timedelta(seconds=1),
    )

    assert history.record_entry(
        basket_id=BASKET_ID,
        entry_number=1,
        fill=first,
        entry_atr=ENTRY_ATR,
        entry_group_id=1,
        group_take_profit_price=Decimal("106"),
    )
    assert history.record_entry(
        basket_id=BASKET_ID,
        entry_number=1,
        fill=partial,
        entry_atr=ENTRY_ATR,
        entry_group_id=1,
        group_take_profit_price=Decimal("106"),
    )

    basket = store.get_basket(BASKET_ID)
    assert basket is not None
    assert basket.entry_count == 1
    assert basket.invested_notional == Decimal("0.1500")
    assert tuple(fill.order_id for fill in store.list_fills(BASKET_ID)) == (
        "entry-order-1",
        "entry-order-1",
    )


def test_concurrent_partial_fills_update_spot_basket_aggregate_atomically(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(tmp_path / "history.sqlite3")
    database.migrate()
    store = _InterleavingSQLiteTradeHistory(database, Barrier(2))
    history = PaperSpotSQLiteHistory(
        PaperSpotHistoryContext(
            session_id=SESSION_ID,
            symbol="BTCUSDT",
            timeframe="5m",
            preset_version="rsi-step-grid-v1",
        ),
        store,
    )
    first = PaperSpotEntryFill(
        intent_id="intent-1",
        order_id="entry-order-1",
        fill_id="fill-1",
        price=Decimal("100"),
        quantity=Decimal("0.001"),
        filled_at=OPENED_AT,
    )
    partials = (
        PaperSpotEntryFill(
            intent_id="intent-1",
            order_id="entry-order-1",
            fill_id="fill-2",
            price=Decimal("100"),
            quantity=Decimal("0.0005"),
            filled_at=OPENED_AT + timedelta(seconds=1),
        ),
        PaperSpotEntryFill(
            intent_id="intent-1",
            order_id="entry-order-1",
            fill_id="fill-3",
            price=Decimal("100"),
            quantity=Decimal("0.00025"),
            filled_at=OPENED_AT + timedelta(seconds=2),
        ),
    )
    assert history.record_entry(
        basket_id=BASKET_ID,
        entry_number=1,
        fill=first,
        entry_atr=ENTRY_ATR,
        entry_group_id=1,
        group_take_profit_price=Decimal("106"),
    )

    def record(fill: PaperSpotEntryFill) -> bool:
        return history.record_entry(
            basket_id=BASKET_ID,
            entry_number=1,
            fill=fill,
            entry_atr=ENTRY_ATR,
            entry_group_id=1,
            group_take_profit_price=Decimal("106"),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(record, partials))

    basket = store.get_basket(BASKET_ID)
    assert results == (True, True)
    assert basket is not None
    assert basket.entry_count == 1
    assert basket.invested_notional == Decimal("0.17500")
    assert tuple(fill.fill_id for fill in store.list_fills(BASKET_ID)) == (
        "fill-1",
        "fill-2",
        "fill-3",
    )


def test_entry_exit_keeps_the_basket_open_and_adds_what_it_realised(
    history: PaperSpotSQLiteHistory,
    store: SQLiteTradeHistory,
) -> None:
    history.record_entry(
        basket_id=BASKET_ID,
        entry_number=1,
        fill=entry_fill(),
        entry_atr=ENTRY_ATR,
        entry_group_id=1,
        group_take_profit_price=Decimal("106"),
    )
    sale = PaperSpotExitFill(
        order_id=f"take-profit:{BASKET_ID}:1",
        fill_id=f"paper:{SESSION_ID}:take-profit:{BASKET_ID}:1:fill",
        price=Decimal("110"),
        quantity=Decimal("2"),
        filled_at=OPENED_AT + timedelta(hours=1),
        entry_number=1,
    )

    recorded = history.record_entry_exit(
        basket_id=BASKET_ID,
        fill=sale,
        closed=ClosedEntry(
            entry_number=1,
            quantity=Decimal("2"),
            entry_price=Decimal("100"),
            exit_price=Decimal("110"),
            realized_pnl=Decimal("20"),
            closed_at=OPENED_AT + timedelta(hours=1),
            group_id=1,
        ),
        closed_basket=None,
    )

    basket = store.get_basket(BASKET_ID)
    fills = store.list_fills(BASKET_ID)
    assert recorded is True
    assert basket is not None
    # The Basket stays open for the Entries that have not sold.
    assert basket.status is BasketStatus.OPEN
    assert basket.closed_at_utc is None
    assert basket.realized_pnl == Decimal("20")
    assert basket.realized_pnl == Decimal("20")
    assert len(fills) == 2
    assert fills[1].closes_entry_number == 1
    assert fills[1].realized_pnl == Decimal("20")


def test_the_last_entry_closes_the_basket_on_the_totals_the_store_kept(
    history: PaperSpotSQLiteHistory,
    store: SQLiteTradeHistory,
) -> None:
    """A Basket taken back counts only what it has sold since it was taken back.

    Its own tally starts empty on every restart, so closing the last Entry
    after one used to write a total and a count that left out every Entry sold
    before it — 20 realised on a Basket that had made 31.34, over two Entries
    reported as one. The store kept both all along.
    """
    for number, price in ((1, "100"), (2, "110")):
        history.record_entry(
            basket_id=BASKET_ID,
            entry_number=number,
            fill=replace(
                entry_fill(),
                order_id=f"entry:{number}",
                fill_id=f"paper:{SESSION_ID}:entry:{number}:fill",
                intent_id=f"intent-{number}",
                price=Decimal(price),
            ),
            entry_atr=ENTRY_ATR,
            entry_group_id=1,
            group_take_profit_price=Decimal("106"),
        )
    history.record_entry_exit(
        basket_id=BASKET_ID,
        fill=replace(
            exit_fill(),
            order_id="close-entry:1",
            fill_id="fill-exit-1",
            entry_number=1,
        ),
        closed=ClosedEntry(
            entry_number=1,
            quantity=Decimal("2"),
            entry_price=Decimal("100"),
            exit_price=Decimal("105.67"),
            realized_pnl=Decimal("11.34"),
            closed_at=OPENED_AT + timedelta(hours=1),
            group_id=1,
        ),
        closed_basket=None,
    )

    # The Session that sells the last Entry has only ever seen that Entry, so
    # the Basket it hands over knows nothing of the 11.34 already realised.
    history.record_entry_exit(
        basket_id=BASKET_ID,
        fill=replace(
            exit_fill(),
            order_id="close-entry:2",
            fill_id="fill-exit-2",
            entry_number=2,
        ),
        closed=ClosedEntry(
            entry_number=2,
            quantity=Decimal("2"),
            entry_price=Decimal("110"),
            exit_price=Decimal("120"),
            realized_pnl=Decimal("20"),
            closed_at=OPENED_AT + timedelta(hours=2),
            group_id=1,
        ),
        closed_basket=ClosedBasket(
            basket_id=BASKET_ID,
            entry_count=1,
            average_entry_price=Decimal("110"),
            exit_price=Decimal("120"),
            realized_pnl=Decimal("20"),
            # The sale was booked by the Entry exit beside this record, so the
            # record itself sold nothing and claims nothing.
            exit_realized_pnl=Decimal("0"),
            closed_at=OPENED_AT + timedelta(hours=2),
            close_reason=BasketCloseReason.TAKE_PROFIT,
        ),
    )

    basket = store.get_basket(BASKET_ID)
    assert basket is not None
    assert basket.status is BasketStatus.CLOSED
    assert basket.realized_pnl == Decimal("31.34")
    assert basket.entry_count == 2


def test_an_exit_fill_and_the_entry_it_closed_must_name_the_same_entry(
    history: PaperSpotSQLiteHistory,
) -> None:
    """A Fill that took the whole Basket has no place on the per-Entry path.

    It names no Entry, so recording it here would write a sale of everything
    as though one Entry had made it — and leave the rest of the Basket open.
    """
    history.record_entry(
        basket_id=BASKET_ID,
        entry_number=1,
        fill=entry_fill(),
        entry_atr=ENTRY_ATR,
        entry_group_id=1,
        group_take_profit_price=Decimal("106"),
    )

    with pytest.raises(ValueError, match="different Entries"):
        history.record_entry_exit(
            basket_id=BASKET_ID,
            fill=exit_fill(),
            closed=ClosedEntry(
                entry_number=1,
                quantity=Decimal("2"),
                entry_price=Decimal("100"),
                exit_price=Decimal("110"),
                realized_pnl=Decimal("20"),
                closed_at=CLOSED_AT,
                group_id=1,
            ),
            closed_basket=None,
        )


def test_close_records_exact_closed_basket_and_sell_fill(
    history: PaperSpotSQLiteHistory,
    store: SQLiteTradeHistory,
) -> None:
    history.record_entry(
        basket_id=BASKET_ID,
        entry_number=1,
        fill=entry_fill(),
        entry_atr=ENTRY_ATR,
        entry_group_id=1,
        group_take_profit_price=Decimal("106"),
    )

    history.record_close(
        basket_id=BASKET_ID,
        fill=exit_fill(),
        closed=closed_basket(),
    )

    basket = store.get_basket(BASKET_ID)
    fills = store.list_fills(BASKET_ID)
    assert basket is not None
    assert basket.status is BasketStatus.CLOSED
    assert basket.opened_at_utc == OPENED_AT
    assert basket.closed_at_utc == CLOSED_AT
    assert basket.invested_notional == Decimal("200")
    assert basket.realized_pnl == Decimal("20")
    assert basket.realized_pnl == Decimal("20")
    assert [fill.side for fill in fills] == [FillSide.BUY, FillSide.SELL]
    assert fills[1] == TradeFill(
        fill_id=exit_fill().fill_id,
        basket_id=BASKET_ID,
        session_id=SESSION_ID,
        order_id=exit_fill().order_id,
        exchange_trade_id=None,
        side=FillSide.SELL,
        entry_number=None,
        filled_at_utc=CLOSED_AT,
        price=Decimal("110"),
        quantity=Decimal("2"),
        notional=Decimal("220"),
        realized_pnl=Decimal("20"),
        source=FillSource.PAPER_EXECUTOR,
    )


def test_mapper_propagates_duplicate_write_result(
    history: PaperSpotSQLiteHistory,
) -> None:
    fill = entry_fill()

    assert (
        history.record_entry(
            basket_id=BASKET_ID,
            entry_number=1,
            fill=fill,
            entry_atr=ENTRY_ATR,
            entry_group_id=1,
            group_take_profit_price=Decimal("106"),
        )
        is True
    )
    assert (
        history.record_entry(
            basket_id=BASKET_ID,
            entry_number=1,
            fill=fill,
            entry_atr=ENTRY_ATR,
            entry_group_id=1,
            group_take_profit_price=Decimal("106"),
        )
        is False
    )
