# Respect provider rate limits returned inside HTTP200

After PR4, production core snapshot f871b0ab44de37faab753c83ab9d0ad56421687f
(2026-10-06T05:26:37Z) restored 15/17 detailed owned candidates and HYPE/SHIB.
LTC and AAVE remained PARTIAL because the shared BTC OHLC source returned
EGeneral:Too many requests inside an otherwise successful relay HTTP200.
The exact source records are in candidates/LTCEUR.json and AAVEEUR.json.
Discovery later hit the bounded six-429 ceiling; it was not relabeled healthy.

This narrow follow-up adds recognition of explicit Kraken public source-rate
errors within V2.0.2 responses and applies a conservative >=60s provider cooldown,
or longer Retry-After if supplied. The original response body and source times
remain unchanged. No additional retries, alternate IP, exchange credentials,
public-to-private API switch, or data-gate relaxation is introduced.
The single collector also increases its post-response quiet gap from 1.25 to
3 seconds to reduce repeated local throttles and upstream request pressure.
This is a conservative operational choice, not a claim about an exact Kraken
or Worker quota. Existing time/wait/rejection bounds and histories remain.

Seven isolated tests cover nested rate errors, unknown pairs not being rate
limits, provenance, longer hints, cooldown despite HTTP200, conservative pacing
and malformed payloads. Repository-wide CI and actual later publications must
be checked separately. No reset of financial/advisory state, sampling history,
strategy, schedule, health streak or notification settings.
