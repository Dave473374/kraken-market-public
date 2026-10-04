# SOL500 operational repair — 2026-10-04

This changes execution scheduling only. The forward experiment remains `SOL500-FWD-20261004-v1`, with the original EUR500 paper capital and fixed 2026-10-04 07:00 UTC–2026-10-11 07:00 UTC trial window. No real exchange orders, credentials or account access are added.

## Frozen invariants

- `engine.py` remains byte-for-byte unchanged: SHA256 `4461a21a603f73c6615310f0646c262c60ae11f3522c71098a6eb108afa20192`.
- Protocol SHA256 remains `394eafafbe6a51a231b989cedd11401e8a4f438c93924ea5194e072bd6174d0a`.
- Original start/end, fee assumptions, signals, sizing, stop rules, existing fills, original activation delay and recorded gaps are retained. Missing intervals are never reconstructed as trades.
- Ledger remains on branch `paper-sol500-20261004`, under `research/paper-sol500-20261004/`. Existing monitoring paths `state.json` and `report.json` are unchanged.
- No Crypto Alpha Watch, real portfolio, DCA, NiceHash or VMM files or policies are modified.

## Why the execution method changed

The original short workflow performed one observation and relied on a new GitHub schedule event for the next one. Persisted observations showed multi-hour gaps despite a nominal five-minute cron. This evidence establishes missed operational coverage, not the precise GitHub infrastructure cause. GitHub documents that scheduled runs may be delayed or dropped.

The existing workflow now runs an operational supervisor. It calls the unchanged engine every 300 seconds inside a three-hour worker and commits each observation promptly. A six-minute bootstrap on a configuration push exercises the transition to the normal worker without changing sampling frequency.

After the first persisted iteration, the worker requests one successor via the same workflow's `workflow_dispatch` endpoint. The original single-writer concurrency group keeps the successor pending until the active worker exits. New scheduled triggers may replace the pending successor, but do not cancel the active worker. The twice-hourly cron is a backup starter, not the source of the five-minute cadence. No parallel paper engines, external cron subscription or new exchange key is needed.

## Persistence and failure handling

The supervisor refuses to initialize an empty ledger. It validates existing ID, hash, paper-only flags, state/report consistency, fill identity, preserved history prefixes, original activation and monotonic counters before proceeding. Strategy bytes are verified before every worker starts.

Git updates are fast-forward-only; there is no force push or ledger reset. Push failure is retried at most three times. If persistence still fails, no next model evaluation is allowed in that worker. The workflow preserves small recovery artifacts rather than silently forgetting unpublished state. A prepared successor or the fallback schedule can then restart from the published ledger.

Market-fetch failure is still handled by the original engine and recorded as such. The supervisor does not manufacture a quote, repeat a missed signal, assume a stop filled, or classify a green workflow as healthy market coverage.

`operations.json` is separate operational evidence: worker/run ID, heartbeat, target cadence, actual last market observation and successor dispatch receipt. Recompute age from the timestamp at review time. An old `fresh_market_data=true` flag or a recent heartbeat is NOT proof of current market freshness.

## End and cancellation

The strategy still blocks entries in the final hour and closes on the first valid observation at/after the fixed end. The supervisor prioritizes an observation at that end instead of sleeping through another whole polling interval. Actual close time and delay remain determined by the original engine.

After finalization, no successor is requested and only this trial's workflow is disabled. The original operational shutdown at 2026-10-11 08:30 UTC is retained as an outer retry cutoff. Failure to obtain a closing observation before that cutoff must be reported as incomplete, never fabricated as a completed trade or an extended experiment.

To stop operation manually, disable this workflow and cancel its active/pending runs. Do not disable unrelated workflows or reset the ledger.

## Validation

Local checks passed before/alongside deployment: 14 original strategy tests, 23 supervisor unit tests and four integration tests (41 total). Integration tests use temporary local Git repositories, synthetic market inputs and a fake clock; they have no connection to the actual paper ledger or an exchange. They verify two-worker cadence, state preservation, market failure behavior and stopping on publication failure. The workflow runs the test suite before each worker starts.

Operational recovery must additionally be established from actual persisted market observations and a verified handoff. A single newer but already stale observation is insufficient. Record at least three distinct fresh observations approximately 300 seconds apart and check that the successor worker actually starts. Unit-test success is not evidence of profitable trading.

## Remaining limitations and cost scope

This remains a finite paper test on hosted infrastructure, not high-availability or tick-accurate execution. GitHub downtime, runner availability, network/API failure and handoff delay can still interrupt it. Existing hourly health monitoring remains the independent notification layer. Old gaps remain part of the seven-day result and reduce its evidential value.

Only a standard Ubuntu runner on this existing public repository is used. The job explicitly refuses to run on a private repository or another repository; no paid larger runner is selected. GitHub documents free standard-runner minutes for public repositories. Failure artifacts have seven-day retention and contain only the small state/report/operations files.

Official references:
- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows
- https://docs.github.com/en/actions/how-tos/troubleshoot-workflows
- https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency
- https://docs.github.com/en/actions/reference/limits
- https://docs.github.com/en/billing/concepts/product-billing/github-actions
