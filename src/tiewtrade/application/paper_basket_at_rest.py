"""Acting on a Basket the Bot is not holding.

A stopped Bot leaves its Basket in the database, and the trader can still see
it, still sell one Entry of it, and still lift the Cooldown on it. Every one of
those composes the Session exactly as the Runtime composes it and asks it the
same question, so a sale made with the Bot stopped cannot book differently from
the same sale made while it runs — which is the whole reason this is composition
rather than a second set of rules written against the tables.

Reading the Basket back is the other half. A Basket nobody holds is a Position
nobody watches: its take profit goes unchecked and its Entries go uncounted, so
one that cannot be put back faithfully says so rather than being traded beside.
"""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from tiewtrade.application.open_basket import OpenBasketEntry, OpenBasketState
from tiewtrade.application.paper_session_setup import ConfiguredPaperSession
from tiewtrade.application.paper_spot_session import (
    PaperSpotSession,
    PaperSpotSessionSnapshot,
)
from tiewtrade.application.session_persistence import EntryClosingPersistence
from tiewtrade.integrations.sqlite.paper_spot_history import (
    PaperSpotHistoryContext,
    PaperSpotSQLiteHistory,
)
from tiewtrade.integrations.sqlite.persistent_paper_spot_session import (
    create_persistent_paper_spot_session,
)
from tiewtrade.integrations.sqlite.trade_history import SQLiteTradeHistory
from tiewtrade.strategies.protocols import StrategyPreset
from tiewtrade.trading.basket import SettledEntries
from tiewtrade.trading.session_config import MarketType
from tiewtrade.trading.symbol_rules import SymbolRules
from tiewtrade.trading.trade_history import TradeFill


class PaperPositionRestoreError(RuntimeError):
    """An open Basket cannot be put back the way it was left."""


def _session_at_rest(
    session: ConfiguredPaperSession,
    *,
    trade_history: SQLiteTradeHistory,
    symbol_rules: SymbolRules,
    preset: StrategyPreset,
    refusal: str,
) -> EntryClosingPersistence[PaperSpotSessionSnapshot] | None:
    """The Session holding this Basket, composed the way the Runtime composes it.

    None is the answer where there is no Basket to act on at all. Only Spot
    keeps one past its Runtime — Futures never takes a Basket back — so asking
    this of a Futures Session is asking for something that cannot exist, which
    is a mistake in the caller rather than an answer.
    """
    if session.config.market_type is not MarketType.SPOT:
        raise ValueError(refusal)
    open_basket = read_open_basket_state(trade_history, session.config.session_id)
    if open_basket is None:
        return None
    application = PaperSpotSession(
        session.config,
        session.market_data,
        symbol_rules,
        preset,
        open_basket=open_basket,
    )
    history = PaperSpotSQLiteHistory(
        PaperSpotHistoryContext(
            session_id=session.config.session_id,
            symbol=session.market_data.symbol,
            timeframe=session.market_data.timeframe,
            preset_version=session.config.preset_version,
        ),
        trade_history,
    )
    return create_persistent_paper_spot_session(application, history)


def close_one_entry_at_rest(
    session: ConfiguredPaperSession,
    entry_number: int,
    *,
    trade_history: SQLiteTradeHistory,
    symbol_rules: SymbolRules,
    preset: StrategyPreset,
    price: Decimal,
    observed_at: datetime,
) -> bool:
    """Sell one Entry of a Basket no Runtime is holding.

    False is the answer where there was nothing to sell: no open Basket, or no
    Entry by that number.
    """
    persistent = _session_at_rest(
        session,
        trade_history=trade_history,
        symbol_rules=symbol_rules,
        preset=preset,
        refusal="only a Paper Spot Basket can let one Entry go",
    )
    if persistent is None:
        return False
    return persistent.close_entry(
        entry_number, price=price, observed_at=observed_at
    ).session.accepted


def waive_cooldown_at_rest(
    session: ConfiguredPaperSession,
    *,
    trade_history: SQLiteTradeHistory,
    symbol_rules: SymbolRules,
    preset: StrategyPreset,
    observed_at: datetime,
) -> bool:
    """Lift a Cooldown holding a Basket no Runtime is holding.

    False is the answer where there was nothing to waive: no open Basket, or a
    gate that is not a Cooldown right now.
    """
    persistent = _session_at_rest(
        session,
        trade_history=trade_history,
        symbol_rules=symbol_rules,
        preset=preset,
        refusal="only a Paper Spot Basket can have its Cooldown waived",
    )
    if persistent is None:
        return False
    return persistent.waive_cooldown(observed_at).session.accepted


def read_open_basket_state(
    trade_history: SQLiteTradeHistory,
    session_id: UUID,
) -> OpenBasketState | None:
    """Read back the Basket a previous Runtime left open, if there is one.

    A Basket nobody holds is a Position nobody watches: its take profit goes
    unchecked and its Entries go uncounted. Rather than trade beside one, a
    Basket that cannot be put back says so.
    """
    basket = trade_history.find_open_basket(session_id)
    if basket is None:
        return None
    fills = trade_history.list_fills(basket.basket_id)
    # An Entry that was sold at its own target is not part of the position
    # left open, however the Basket it belonged to still is.
    sold = {
        fill.closes_entry_number
        for fill in fills
        if fill.closes_entry_number is not None
    }
    entries: list[OpenBasketEntry] = []
    settled = SettledEntries(
        count=len(sold),
        realized_pnl=sum(
            (
                fill.realized_pnl
                for fill in fills
                if fill.closes_entry_number is not None
            ),
            Decimal("0"),
        ),
        quantity=sum(
            (
                fill.quantity
                for fill in fills
                if fill.entry_number is not None and fill.entry_number in sold
            ),
            Decimal("0"),
        ),
        notional=sum(
            (
                fill.price * fill.quantity
                for fill in fills
                if fill.entry_number is not None and fill.entry_number in sold
            ),
            Decimal("0"),
        ),
    )
    for fill in fills:
        if fill.entry_number is None or fill.entry_number in sold:
            continue
        if fill.entry_atr is None:
            raise PaperPositionRestoreError(
                "open Basket has an Entry recorded without its ATR"
            )
        entries.append(
            OpenBasketEntry(
                entry_number=fill.entry_number,
                price=fill.price,
                quantity=fill.quantity,
                filled_at=fill.filled_at_utc,
                atr=fill.entry_atr,
                # A Fill written before Groups were kept names none, and the
                # Basket is told to work its Groups out from the Entries again.
                group_id=fill.entry_group_id or 1,
            )
        )
    if not entries:
        raise PaperPositionRestoreError("open Basket has no Entry Fills")
    return OpenBasketState(
        basket_id=basket.basket_id,
        entries=tuple(entries),
        closed_basket_count=trade_history.count_closed_baskets(session_id),
        group_take_profit_prices=_group_take_profit_prices(
            fills, open_groups={entry.group_id for entry in entries}
        ),
        settled=settled,
        waived_pair=trade_history.find_cooldown_waiver(basket.basket_id),
    )


def _group_take_profit_prices(
    fills: Sequence[TradeFill],
    *,
    open_groups: set[int],
) -> dict[int, Decimal]:
    """Where each still-open Group's target had been ratcheted to.

    Every Fill that moved a Group's target recorded where it moved it to, and
    the Fills come back in the order they happened, so the last figure a
    Group's Fills wrote is where its target stands. Reading the last rather
    than the nearest keeps this free of which way "closer" lies — a Long
    ratchets its target down and a Short ratchets it up, and the Fill that
    wrote last had already settled that.

    Nothing is returned unless every open Group has a figure. A Basket
    recorded before Groups were kept has none at all, and one measured from
    part of its history would be worse than measuring all of it again.
    """
    measured: dict[int, Decimal] = {}
    for fill in fills:
        group_id = fill.entry_group_id
        if group_id is None or group_id not in open_groups:
            continue
        if fill.group_take_profit_price is None:
            continue
        measured[group_id] = fill.group_take_profit_price
    if set(measured) != open_groups:
        return {}
    return measured
