# Predeployment observations (not market signals)

First full cold test: CI run 37435379631 / job 112175787143.
125 offline tests passed, but the public cold test correctly FAILED before any
production switch. 48 direct Kraken requests completed with zero rate-limit
errors in about 65 seconds. Mandatory candle validation rejected GALA/MEGA/PENDLE.

The targeted follow-up, run 37435892871 / job 112177477173, fetched the exact three
OHLC responses and identified explicit Kraken no-trade rows: flat positive OHLC,
VWAP=0, volume=0 and count=0. There were no missing candle timestamps. The parser
now accepts precisely that reported shape without inventing bars/volumes and
still rejects zero VWAP with nonzero trading, gaps, NaN, future/missing data.

The same follow-up returned CoinGecko coin identities:
- midnight: unrelated Polygon NIGHT, contract 0xd33fd95fc17bc808b35e98458e078330f35dbfa3.
- midnight-3: Cardano NIGHT, asset 0691b2fecca1ac4f53cb6dfb00b7013e561d1f34403b957cbb5af1fa4e49474854.
The project's official tokenomics whitepaper and CoinGecko midnight-3 refer to
the latter Cardano asset. Runtime now verifies exact ID+symbol+Cardano asset for
NIGHT rather than using the ambiguous name. The wrong first-probe reference was
NEVER a production quote or action. Any fresh independent reference is separately
compared to Kraken and retains explicit 1%/2% mismatch gates; passing comparison
never authorizes a trade, a retail quote, or a claim of alpha.

Identity sources read 2026-10-06:
https://www.coingecko.com/en/coins/midnight
https://www.coingecko.com/en/coins/midnight-3
https://midnight.network/night
https://45047878.fs1.hubspotusercontent-na1.net/hubfs/45047878/Midnight-Tokenomics-And-Incentives-Whitepaper.pdf

The bounded Coinbase BTC-EUR fallback diagnostic also returned a fresh successful
public ticker at 2026-10-06T08:24:37.663301Z. This verifies accessibility, not phone
delivery or uninterrupted operation. Production switch remains contingent on a
subsequent successful full cold/warm test and repository regression CI.
