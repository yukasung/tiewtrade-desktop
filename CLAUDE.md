# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

TiewTrade is an Internal Alpha trading bot built paper-first. It currently supports
deterministic replay of completed BTCUSDT 5m Paper Spot candles. The replay CLI never
connects to Binance and never sends live orders.

## Required Reading Before Implementing

Read the relevant document(s) before designing or changing code — these are the source
of truth, and if they conflict, stop and surface the conflict rather than guessing:

- `PRODUCT.md` — product scope and explicit non-goals
- `CONTEXT.md` — domain vocabulary and business rules (Trade Mode, Market Type,
  Futures Trading Policy, Session configuration boundaries)
- `ARCHITECTURE.md` — module ownership, interfaces, and dependency rules
- `docs/superpowers/specs/` — rationale behind past architecture decisions
- `PROJECT_PLAN.md` — delivery sequencing

## Commands

Setup:
```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev]"
```

Run the desktop app:
```bash
make run
# equivalent to: PYTHONPATH=src .venv/bin/python -m tiewtrade.desktop_main
```

Deterministic Paper Spot replay (no Binance connection, no live orders):
```bash
PYTHONPATH=src .venv/bin/python -m tiewtrade.paper_replay_main \
  tests/fixtures/btcusdt_5m_tracer.csv \
  --capital-per-entry 250 \
  --max-entries 4
```
Expected stable output: `{"accepted_candles":40,"closed_baskets":1,"current_entries":0,"realized_pnl":"23.679"}`

Full verification (run all of these before reporting work done):
```bash
PYTHONPATH=src .venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests
.venv/bin/python -m ruff format --check src tests
.venv/bin/python -m mypy
git diff --check
```

Run a single test:
```bash
PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/trading/test_some_module.py::test_case
```

Test layout: `tests/unit/<module>` mirrors `src/tiewtrade/<module>`, `tests/acceptance`
holds end-to-end/acceptance flows, `tests/fixtures` holds deterministic market-data
fixtures, `tests/support` holds shared test helpers/fakes.

## Architecture

Feature-first modular monolith. Business rules must not depend on UI, database, or
exchange SDK details. Top-level modules: `application`, `trading`, `execution`,
`market_data`, `integrations`, `strategies`, `replay`, `ui`.

**Shared business rules, split execution.** Paper and Live share one implementation of
session identity, strategy/entry intent, capital allocation, Basket/Entry Pair/Cooldown,
risk limits, and PnL (owned by `trading`).
Execution is the seam that isolates side effects and is *not* shared — Paper and Live
never use the same execution adapter:

| Mode | Market Type | Adapter responsibility |
| --- | --- | --- |
| Paper | Spot/Futures | Simulate fills; no real orders, no trading costs |
| Live | Spot | Send Spot orders after Preflight, idempotency, reconciliation |
| Live | Futures | Manage Futures orders, margin, leverage, funding, reconciliation |

**Configuration boundary.** `SessionConfig` is immutable, shared config holding session
id, preset version, Market Type, Trade Mode, capital, and execution costs. One
installation uses exactly one Binance account and has at most one Active Bot Session;
every Basket/Entry/Order/Fill/PnL/audit record is bound to `session_id`. Market identity
(`symbol`, `timeframe`) comes from configuration, never hardcoded in strategy or
market-data code, and is pinned for the whole session. `timezone` is always `UTC`.
`max_entries` is shared across market types, pinned for the session, and must be an even
number 2–20. RSI parameters, take-profit rule, and Entry Pair live in the Strategy
Preset/product policy, not session config. A Cooldown starts when a Basket completes an
Entry Pair and never lapses on its own — only the "Cancel Cooldown" action lifts it, one
Cooldown at a time. Both Paper markets can lift one; Spot persists the waiver so it
survives a restart, Futures does not (it never takes an open Basket back). `TradeMode.LIVE` must always go
through Preflight and explicit user confirmation — setting `LIVE` is not itself a command
to trade.

**Backfilled candles never open an Entry.** After a disconnect the runtime refetches the
missed candles up to now and delivers them through the same sink call a live candle takes,
so `market_data` reports *whether* a candle was backfilled and the Session enforces the
rule. A backfilled candle still feeds indicators and still closes a Basket (take-profit —
that happened in the market whether the feed saw it or not),
but it may not fill an Entry, and a pending Entry Intent is handed back to the strategy
rather than left to fill on the first live candle off a stale signal. Applies to Paper and
Live alike; see PRODUCT.md 8.1.

**Desktop UI boundary.** `ui` (PySide6) is a thin presentation layer: it converts form
input into application requests and renders durable results. It must not import SQLite,
Strategy, or Execution adapters directly, and persistence work must run on a background
worker off the UI thread. The one narrow exception is ephemeral chart presentation: `ui`
may render an in-memory, read-only, unsaved chart read-model relayed by an
application-owned, generation-bound channel (e.g. in-progress candle) — this fact must
never feed Strategy, risk policy, execution, persistence, replay, or lifecycle decisions,
and must be cleared when the session/runtime generation changes.
`application` owns the `Create Paper Session` and `ClosePaperPosition` use cases (the
latter computes a Paper exit fill from the session's `LatestMarketPrice` — a
thread-safe, freshness-bounded service distinct from the ephemeral chart read-model — and
persists the closed Basket + exit fill in one transaction); UI triggers these through a
background worker and never calls adapters directly.

**Persistence boundary.** `application` owns business Session orchestration and rule
sequencing. `integrations/sqlite` owns transactions, durable mapping, and a
persistence-specific fail-closed coordinator that wraps the application Session: it may
only invoke the Session, persist synchronously, and stop accepting the next candle when
durability isn't confirmed — it must never compute or change a business decision. While
there is only one persistence adapter, use the concrete coordinator directly; only
introduce a generic interface once a second real consumer exists.

**General module rules:** don't create generic interfaces/base classes/registries/factories
before at least two real adapters/consumers exist; put an interface in the module that
consumes it and let integrations implement it; avoid catch-all files like `utils.py`,
`interfaces.py`, `models.py`, `repository.py`.

## Domain Vocabulary (see CONTEXT.md for full detail)

- **Trade Mode**: `PAPER` (simulated, no Binance orders) vs `LIVE` (real execution,
  gated by Preflight + explicit confirmation).
- **Market Type**: `SPOT` vs `FUTURES` (margin/leverage/funding facts).
- **Position Side**: `LONG`/`SHORT` — Paper Futures execution supports both, but the
  RSI Step Grid Preset v1 only ever generates `LONG`.
- **Futures Trading Policy**: immutable per session — 50% Trading Capital, 50%
  Collateral Buffer, integer leverage 1x–5x pinned for the session, One-way Mode, Cross
  Margin, 0.5% deterministic Maintenance Margin Rate (not a claim of matching Binance's
  real mark price or maintenance tiers).

## Workflow Conventions

- Communicate, explain plans, summarize results, and write Linear issues in Thai; use
  English for file/module/class/function/variable names and code comments. Use the
  terminology defined in `CONTEXT.md` — don't invent synonyms.
- Issue tracker is Linear (workspace `chainarong`, team `Chainarong`/`DEV`, project
  `Tiewtrade`); GitHub holds source and PRs but is not the primary issue tracker. Move
  only the issue being worked on to `In Progress`, use one branch per sub-issue, move to
  `Done` only after implementation and verification are actually complete.
- Plan before multi-step changes. Use TDD: failing test → minimal implementation →
  refactor. Don't scaffold empty modules ahead of real behavior.
- Push to GitHub and merges to `main` each require separate, explicit user confirmation.
  Never force-push, delete branches/tags, or rewrite history unless explicitly instructed.
  Don't touch unrelated user work outside the current issue.
- Trading safety: use Paper trading and fake adapters for dev/testing; never send live
  orders or connect a real-money account without explicit instruction; never commit API
  keys/secrets anywhere (code, config, fixtures, logs, SQLite) — live credentials belong
  in the OS keyring; never use Testnet as a substitute for Live safety — use Paper, fake
  transports, and contract tests instead.
- Before reporting a task done: run relevant unit/integration tests, run lint/format
  checks from `pyproject.toml`, run smoke/acceptance tests when application flow is
  affected, and run `git diff --check` on the working tree (or `git diff --check <base>
  HEAD` in CI). Summarize what changed, test results, and remaining risk.
