# Direct public data path, 2026-10-06

User requested an alternative after repeated Worker throttles. The existing
runner and public snapshot publication remain in place; the upstream adapter is
replaced, not duplicated. No trade, private API, account key, financial state,
strategy threshold, task schedule or other project is changed by these files.

## Structural change

The primary adapter calls public api.kraken.com directly from the existing GitHub
runner; no Worker is called. One batch AssetPairs response (SI/international) and
one all-market Ticker response serve every pair. Each pair's closed 4h OHLC data
is reused until the next closed bar is required, preserving source times. The
BTC comparison uses that same cached BTC history, not one repeated BTC request
per candidate. Each snapshot gets a fresh L2 book for every selected pair.

The public request pace is no faster than one request per 1.25 seconds after the
preceding response. Kraken documents one request per second or less as within
public rate limits; Trades/OHLC limits are per IP and pair, others per IP.
https://support.kraken.com/articles/206548367-what-are-the-api-rate-limits-

HTTP 429 and explicit Kraken API limit errors stop that provider until its
cooldown expires. We do not switch IP/Worker to circumvent a Kraken limit. One
5xx retry is bounded; no uncontrolled retries. Clock and HTTP Date/Age checks,
source times, correct pair keys and closed-candle continuity remain required.

## What a different provider can and cannot replace

CoinGecko exact IDs are first matched to its public coin list; only timestamped
EUR references within 15 minutes are accepted. Coinbase Exchange BTC/ETH/SOL EUR
is the bounded fallback for those three established pairs if CoinGecko is absent.
Other missing assets remain UNKNOWN. A third party cannot replace Kraken's own
execution book, eligibility or Convert quote. A paid/keyed endpoint is never
silently used. The same provider snapshot is reused, not fetched per asset.
https://docs.coingecko.com/reference/simple-price
https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-ticker

## Compatibility, truthful timestamps and interpretation

Producer=CAW_DIRECT_KRAKEN_V1 and transport_mode=DIRECT_KRAKEN_NO_WORKER identify
the new source. KRAKEN_PUBLIC_TRANSPORT_V2.0.2 is retained only as the existing
wire format; it must NOT be interpreted as evidence that the Worker produced it.
Depth-derived spread records explicitly use local acquisition time, not a claimed
exchange change/event timestamp. Spread and depth here are the SAME source.
HTTP Date/Age and server clock are checked; all ages must be recomputed at use.
Last-update timestamps on individual book levels are never snapshot timestamps.

Closed24h uses six completed 4h candles with the prior boundary close; volume
uses six completed candles against seven preceding non-overlapping blocks. The
unfinished final Kraken OHLC row is excluded. No missing bars/volumes are made up.
https://docs.kraken.com/api-reference/market-data/get-ohlc-data
https://docs.kraken.com/api-reference/market-data/get-order-book

Daily candles are optional/not collected by this adapter. BTC comparison is
optional; missing BTC context is not bullish or a new veto on otherwise complete
owned evidence. Mandatory own-pair metadata, ticker, closed history, clock, FX
and book still fail closed. Existing actual-notional depth and risk gates apply.
Aggregate liquidity is separately summed over eligible EUR/USD/USDC pairs, with
missing conversions marked as a lower bound. It is not the single-pair volume.

## State, scope and validation

The public direct-cache.json contains original metadata/OHLC and provider identity
records only. It is bounded and survives the existing runner handoff. Private
ledger and intents are never put there. Previous sampling/candidate history is
preserved. Reports additionally group actual samples by transport_revision, so
hours collected on an older transport cannot certify this transport's 24h test.

26 isolated offline tests passed locally; full repository CI and the PR-only
cold/warm public test are still required before deployment. The public test writes
only to a temporary directory, never production. Passing it is not 24h uptime,
strategy alpha, or proof of notification delivery. A 100% availability guarantee
is not made. Revert the reviewed runner change to restore the old known-imperfect
Worker path without resetting any financial state or other project.
