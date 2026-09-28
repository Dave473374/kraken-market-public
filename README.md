# kraken-market-public

Public static mirror for Crypto Portfolio v2.1 / Kraken Movers KM1.0.

Data path:
Kraken public API -> Cloudflare Worker -> GitHub Action -> static JSON files

Safety:
- no Kraken API keys
- no private Kraken endpoints
- no account balances
- no personal portfolio quantities
- no trading
- no transfers
- no user PC required after setup

Source Worker:
https://kraken-public-test.david-e5e.workers.dev

Expected Worker schema:
KRAKEN_PUBLIC_TRANSPORT_V2.0.2

Published files:
- data/health.json
- data/universe.json
- data/owned-quotes.json
- data/manifest.json
- data/candidates/<PAIR>.json

GitHub Actions runs hourly at minute 1 UTC and may also be run manually.
The mirror is evidence transport only, never a BUY/SELL engine.
