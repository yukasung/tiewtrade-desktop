# DEV-141 Realized Drawdown Design

## Goal

Close the Paper Trading Complete Gate gap in `PRODUCT.md §15.1` by showing a
deterministic, session-scoped Drawdown fact in the Trading Workspace. This is
monitoring information only: it must not alter entry, exit, capital allocation,
or stop behavior.

## Decision

Drawdown means the decline from the highest realized-equity point to the current
realized-equity point for one Session. Realized equity is the cumulative
`net_realized_pnl` of closed Baskets, evaluated in the existing deterministic
history order. Every monetary value remains an exact `Decimal`.

The read model has no Drawdown fact until the Session has at least one closed
Basket. The UI renders that condition as `Drawdown: Not available`; it does not
misrepresent unavailable history as zero Drawdown.

## Alternatives considered

### Mark-to-market Drawdown

Including unrealized PnL would make the fact depend on runtime pricing,
freshness, and recovery timing. It is not required to satisfy the Paper Gate
and would expand this work into a new market-data policy.

### Capital-baseline Drawdown

Measuring against initial capital needs a cross-market baseline definition for
Spot and Futures. That definition is not currently a durable Workspace fact and
is unnecessary when closed Basket results already provide a deterministic equity
curve.

## Architecture

`application/trading_workspace.py` owns a small immutable `DrawdownSnapshot`.
It validates exact `Decimal` fields and represents unavailable data explicitly.
A focused application helper derives it from normalized closed Basket history;
the helper neither reads SQLite nor imports execution/runtime adapters.

`TradingWorkspaceSnapshot` carries the optional Drawdown fact with the same
generation-safe application-to-UI publication path already used by header,
orders, and position/Basket facts. A stale or failed refresh therefore keeps the
last published immutable snapshot rather than letting an older callback replace
it.

`ui/trading_workspace.py` receives and formats the supplied fact in the
persistent header. It has no business calculation: it renders an exact decimal
and stable accessible text. A missing fact is rendered as unavailable.

## Calculation

For closed Basket results in deterministic history order:

1. Start realized equity and peak realized equity at `Decimal("0")`.
2. Add each Basket's `net_realized_pnl` to realized equity.
3. Update the peak when realized equity exceeds it.
4. Drawdown equals `peak_realized_equity - realized_equity`.

The resulting amount is non-negative. It is a monetary Drawdown amount in
USDT, not a percentage. This avoids float conversion and remains valid when the
equity curve has not become positive.

## Error handling and scope

The helper accepts only closed Basket history results with `Decimal`
`net_realized_pnl`; invalid values fail at the application boundary. It never
persists Drawdown separately, sends an order, changes a risk policy, imports a
private endpoint, or reads credentials.

This work intentionally does not introduce Maximum Drawdown forced close. Per
`PRODUCT.md §10`, Drawdown is a monitoring fact only.

## Verification

Tests will prove deterministic exact-Decimal calculation, unavailable history,
snapshot validation/publication, semantic UI rendering, accessible text, and
stale-callback protection. The full Python, Ruff, format, Mypy, docs, and diff
quality gates must pass before re-running the Paper Trading Complete Gate audit.
