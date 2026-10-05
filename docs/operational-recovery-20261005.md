# Crypto Alpha Watch: operational recovery 1.4

## Scope and baseline

User-approved follow-up to the 2026-10-05 read-only audit. Baseline main commit:
`b99091dedd63032f5c05306d11b99de8305e334c`. No real orders, account APIs, exchange
credentials, capital, strategy thresholds or private advisory state are changed.
SOL paper trial, shadow v2.3, NiceHash and VMM are not modified.

## One upstream owner, not two competing collectors

The existing `collect-core.yml` now runs the sole upstream owner. It reads all
configured owned pairs, the three configured watch pairs, then at most three
additional public discovery candidates. The existing `collect.yml` archives an
immutable `core-live` snapshot into existing `main/data` paths; it makes **zero**
Worker/Kraken calls. Its `archived_at` does not refresh any source timestamp.
This is explicitly a same-source archive, **not an independent freshness fallback**.
The legacy `collect.py` retains support functions and is not run by either workflow.

This removes duplicate upstream work from the two CAW collectors. It does not
claim to coordinate unrelated projects or Cloudflare isolates. The deployed
Worker V2.0.2 source is not changed; unexplained Worker limitations remain observable.

## Bounded collection, cooldown, and fair coverage

The existing public GET transport and 1.25-second post-response spacing remain.
The single-owner wrapper persists server cooldown across its processes and
successors, waits only within a 120-second per-cycle wait budget, and permits at
most one retry per path. Its cycle budget is 480 seconds. It never retries before
the recorded cooldown or converts missing evidence into a market rejection.
Owned pairs with older successful evidence get priority on the next cycle.

A target 300-second start-to-start collection interval includes collection time;
it is not a freshness guarantee. Delays and incomplete modules remain recorded.
Watchlist and discovery failures do not erase valid owned evidence.

## Continuation guardrails

Sessions are 10-240 minutes, below a 270-minute job limit. A reviewed main push
starts a ten-minute deployment session to exercise handoff; normal sessions and
successors use four hours. The original cron remains a backup.

Scheduled and dispatched successors queue in the same concurrency group rather
than cancelling their active parent. Only a reviewed code push replaces active
old code. Publication uses an explicit force-with-lease on the existing rolling
`core-live` branch, never a forced main update. The exact main code SHA is recorded.

After a normal session of at least ten minutes and two published snapshots, the
runner checks that its existing workflow is active and whether a successor is
already queued. It requests at most one successor if needed. A chain is bounded
by seven remaining handoffs (normally up to 32 hours including its root session;
a ten-minute deployment root plus seven four-hour successors is up to 28h10m).
There is no rapid recursive retry after a crash, no automatic re-enable, and no
cancel/dispatch action against another project. Regular scheduled roots can
continue normal monitoring after a chain ends. A scheduler outage beyond the
bounded chain remains a limitation; this is not an always-on hosting guarantee.

`handoff.json` distinguishes an acknowledged dispatch from an actually started
successor. `sampling-history.json` run IDs and publication gaps are needed to
verify the transition. GitHub's short-lived job token requires contents/write for
existing snapshot publication and actions/write for this one bounded handoff.
No new user secret or exchange credential is needed.

## Evidence semantics

Shared quote integrity validation replaces the divergent core duplicate. Candidate
validation always has an expected pair, recalculates source ages, handles malformed
nested data, and exposes same-provider quote disagreement. A mismatch above 2%
is unusable for any tier; 1-2% does not pass the liquid 1% action rule. This validator
is **not** an independent price provider and **never** authorizes BUY/SELL.

Closed-history calendar freshness is separate from ten-minute book freshness.
Data collectors never alter the user's 10x/15x actual-notional depth requirements;
the existing synthetic 60 EUR / 20x diagnostic does not set a trading budget.
Retail Convert fees, minimums, eligibility and quote execution remain unverified
without the appropriate user-provided preview. No 2% retail tolerance is added.

The root `OK` means complete configured owned evidence **at publication only**.
Always inspect `modules`, `candidate_statuses`, individual source times, and
closed-history status. Strategy and notification-delivery status remain explicitly
NOT_VALIDATED_BY_COLLECTOR. Consumers pin a commit SHA and may verify file hashes.

## Public operational history, not private portfolio history

Collection cooldown, last successful public observations, up to 20 deduplicated
48-hour discovery seeds and seven days / at most 4096 sampling records persist
on the existing rolling snapshot. No balances, quantities, execution confirmations,
private intents or private run-decision records are published. Previously absent
operational metrics start with their actual first observation; historical samples
are not invented. A corrupt existing state fails visibly instead of silently reset.

`sampling-report.json` reports observed sample quality and gaps. It cannot prove
continuous exchange uptime, trading alpha or phone delivery. At 24 hours it is
READY_FOR_HUMAN_REVIEW, never automatically production-ready or a trading trigger.
The existing hourly advisor must keep its own private ledger/dedupe and decision
reasons through its explicitly authorized state-only persistence path.

## Validation and rollback

New/expanded tests cover wrong pairs, stale/future/invalid data, shared guards,
closed-candle boundaries, complete owned/watch coverage, separate discovery health,
429 recovery and preserved cooldown, retention and fair selection, bounded handoff,
no duplicate queued successor, disabled workflow, archive freshness, and actual
local-Git publication/lease conflicts while preserving main and other-project files.
All existing public-transport and quote-guard regressions must pass in repository CI.

A passing test or one live publication is insufficient. Require at least 24 hours
of measured samples, multiple real run IDs, review of ten-minute freshness gaps,
owned and watch coverage, and documented limitations. Notification generation and
receipt require separate evidence, not collector status or a phone-settings guess.

Rollback: revert this operational PR after checking newer changes, preserving
private advisor state and all existing confirmed executions. This restores the old
known-imperfect transport, not a proven healthy system. Neither rollback nor
publication resets strategy, DCA or financial history.
