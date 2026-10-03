# Crypto v2.3 SHADOW — frozen prospective research v1

This is **research, not production v2.2**. It submits no exchange orders, modifies no
account, sends no trading alerts, and cannot promote itself. Production prompt,
ledger, DCA, spend/intents, health history and signal thresholds are not inputs or
outputs. All money in this dataset is explicitly synthetic (initial EUR1000 per
comparison book, EUR20 per cohort), NOT a public copy of a user's actual holdings.

## First milestone / scope

A separate hourly GitHub workflow collects existing FAST CORE/M1 evidence first,
then uses bounded public GET requests to the existing Worker only for missing or
stale detail. It covers every public `quotes_pairs` configuration entry plus the
research watch list, all outstanding virtual positions, and at most three extra
discovery candidates. The public universe's ranked seeds are discovery, not a
complete fundamental review of every listed asset. Retention is 48h/max20 seeds.
BTC/ETH/SOL and concentrated DOGE/LTC/meme research seeds are observation-only in
this first study: it does not redesign DCA or simulate additional meme exposure.

Frozen entry hypotheses: breakout/retest, trend pullback, consolidation breakout.
These are explicit test hypotheses, not academically proven optimal parameters.
Every new decision is pending until a **later fresh observation**; entries never
use a preceding candle close as a fictional fill. Four exit paths share each entry:
structure + 2R; half at 1.5R + trailing; trailing only; 24h no-progress time exit.
A hold-until-30-days path is the common-entry benchmark. All paths have a 30-day
observed exit ceiling. A cohort occupies a slot until all paths close, deliberately
keeping the opportunity set identical. This can limit sample acquisition speed.
Core indicators: closed 4h rows only, SMA20/SMA50, arithmetic mean of 14 true ranges
(not Wilder ATR), closed24h and six-bar volume versus seven prior disjoint blocks.
The engine uses the actual exported rows (often 60), not `closed_bars_received` as
proof that 720 bars were saved. It is prospective; a multi-year backtest is NOT done.

## Execution/cost caveats

Observed bid/ask and source timestamps are retained. Exits happen only at a valid
observed book, not at an ideal stop/target crossed inside an unseen candle. Missing,
future, stale, malformed and gapped evidence is UNKNOWN, not a winning trade.
Order-book last-update time is not mislabelled as snapshot time. Timestamped spread
and book retrieval are both checked. Aliases and FX are explicit; EUR is preferred.

LOW/BASE/STRESS are **assumed** per-leg add-ons of 1.25/1.75/2.50 percent to the
observed book, covering hypothetical retail costs for sensitivity analysis.
They are NOT actual Convert fees, calibrated spreads, guaranteed bounds, or an
independent cross-check. Real Convert previews, minima, eligibility and fills are
unobserved. A pair's turnover is a conservative lower bound on aggregate turnover;
missing aggregate data is never invented. Research positions do not authorize
production BUY/SELL, regardless of modeled performance.

## State and interpretation

Code: `research/v23_shadow/`; output **only** on branch `shadow-v23-live`, under
`research/v23/`. State, source observations, events and run coverage are preserved.
Git commits publish them atomically. Corrupt/missing prior state or changed frozen
protocol/implementation fails closed; there is no implicit reset or force push.
No file under production `data/`, `config/` or `scripts/` is a research output.
Read `report.json` there for health and modeled books, `state.json` for lifecycle,
and monthly `observations/`, `events/`, `runs/` JSONL for audit. Scheduled starts can
be delayed: actual gaps are recorded. On HTTP429 the extra public requests stop.
The engine never retries aggressively or adds a high-frequency collector.

Counts distinguish independent entry cohorts from repeated exit/cost paths.
Even cohorts can be correlated. Thirty fully closed paired cohorts are a review
checkpoint only, not proof or a promotion threshold. Observed drawdown can miss
intraperiod extremes. Stale open-position marks make current equity UNKNOWN.
**Comparison with actual v2.2 is NOT available** until its genuine dated signals
and executions are supplied through a separate privacy-preserving process. The
engine does not reconstruct or invent those signals from public prices. Strategy
superiority, optimal sizing, and reproducible net profit are not established.

## Tests and public references

Run `python -m unittest discover -s research/v23_shadow -p 'test_*.py' -v`.
Tests are synthetic fixtures, never study observations.

- https://docs.kraken.com/api/docs/rest-api/get-ohlc-data/ — current candle and 720-entry limit.
- https://www.kraken.com/features/fee-schedule — Convert has fees plus variable spread;
  a published fee is not evidence of a specific user's charged fee.
- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule
  — scheduled Actions are not a guaranteed clock.
