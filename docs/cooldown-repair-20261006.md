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

Eight offline regression tests cover the exact local envelope, missing/unknown
hints, long hints, no requests during cooldown, progressive waiting, recovery and
cross-path rejection bounds. Repository-wide CI remains required. The PR-only
bounded diagnostic reads public relay routes and direct Kraken Time/IO metadata;
it does not publish production data, create orders or run a recurring monitor.
Live recovery and long-duration coverage must be verified separately. Do not
reset existing sampling history or mark this patch as a trading/alpha success.
