# Observed local cooldown interpretation repair — 2026-10-06

Production baseline: main `54c075064901219e41116bdbc6dafd5ecc039a37`.
Observed core snapshot: `beeaee5cad43a38ccfae80efbc5bebdd32b02033`,
generated 2026-10-06T05:05:06Z, one successful detailed candidate.
The GALAEUR fetch received HTTP429 with the exact V2.0.2 relay LOCAL_COOLDOWN
body and Retry-After=1. Existing code changed every such response to at least
60 seconds, exhausting the cycle's 120-second wait budget after repeated errors.
This is a confirmed client-side inefficiency, not proof of the root Worker defect.

This patch honors the recognized relay-local hint with progressive 2/4/8/...s
quiet periods, never before Retry-After or the existing 1.25s post-response gap.
Unknown/upstream errors and missing hints retain the conservative >=60s behavior.
At most one retry per path and six total HTTP429 responses per cycle are allowed;
existing time/wait bounds and cross-process cooldown persistence remain.
Public response headers are allowlisted for diagnostics. No cookies, request
headers, account data, exchange credentials, trading rules or financial state.

## Actual bounded diagnostic observations

CI run 37417483118 / job 112119205429 ran the full 84-test suite successfully.
Its separate public diagnostic returned BNB, MEGA and NIGHT DATA_OK, including
an actual MEGA relay-local HTTP429 recovered after 2.05 seconds; one 429 total.
This proves the specific recovery attempt, not production uptime or strategy.

Direct IOEUR SI/international AssetPairs returned EQuery:Unknown asset pair.
Follow-up CI run 37417666141 / job 112119778816 made one bulk SI/international
metadata request at 2026-10-06T05:16:01.923862Z: error=[], 1292 pairs, no matching
base=IO or IOEUR/IOUSD/IOUSDC identities. It does not check a private account.

A fresh, exact requested-pair AssetPairs rejection is now separately recorded
in watchlist_availability. CHECKED_WITH_UNAVAILABLE_PAIRS means the eligibility
check completed while that pair has NO usable price; it is not DATA_OK quote
evidence, a claim of exchange outage, an automatic alternative pair, or a
permanent whole-asset exclusion. A stale error, wrong pair/region/provider,
429 or other API error remains UNKNOWN/PARTIAL. Owned-risk completeness is
unchanged: missing owned quotes/history still prevents owned root OK.

Sixteen new offline regression tests cover local cooldown and eligibility
classification. Live and long-duration recovery must be verified separately.
No existing sampling history, financial ledger or task health is reset.
