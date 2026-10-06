#!/usr/bin/env python3
"""One bounded public read-only diagnostic. No production publication or state.

Only called from this repair PR's read-only CI. Not a scheduled collector,
not a trading test, and never evidence for a user BUY/SELL alert.
"""
import json
import time
from collector_runtime import CycleTransport, iso
from public_transport import PublicGetter


def main():
    relay=CycleTransport('https://kraken-public-test.david-e5e.workers.dev',timeout=12)
    relay.deadline=time.monotonic()+150
    relay.wait_budget=40
    paths=['/health', '/quotes.json?pairs=BNBEUR,MEGAEUR,NIGHTEUR',
           '/candidate.json?pair=BNBEUR&notional_eur=60',
           '/candidate.json?pair=MEGAEUR&notional_eur=60',
           '/candidate.json?pair=NIGHTEUR&notional_eur=60',
           '/candidate.json?pair=IOEUR&notional_eur=60']
    for path in paths:
        r,attempts=relay.get_bounded(path)
        b=r.get('body',{})
        print(json.dumps({'diagnostic_only':True,'retrieved_at':r.get('retrieved_at'),
            'source_url':r.get('url'),'attempts':attempts,'transport_ok':r.get('ok'),
            'source_health':b.get('data_health'),'source_generated_at':b.get('generated_at'),
            'error':r.get('error'),'pair_error':b.get('pair_error'),
            'blocking_reasons':b.get('blocking_data_reasons'),
            'local_cooldown_seconds':r.get('local_cooldown_seconds'),
            'rate_limit_kind':r.get('rate_limit_kind'),
            'http_response_headers':r.get('http_response_headers'),
            'detail':r.get('detail'), 'calls_made':relay.requests_made}),flush=True)
    # Availability/identity diagnostics, not alternate attempts to bypass an
    # exchange throttle. No Kraken private route, credential, order or transfer.
    direct=PublicGetter('https://api.kraken.com',timeout=12)
    for path in ('/0/public/Time','/0/public/AssetPairs?pair=IOEUR&country_code=SI&aclass_base=currency&execution_venue=international'):
        r=direct.get(path);b=r.get('body',{})
        print(json.dumps({'diagnostic_only':True,'source_url':r.get('url'),
            'retrieved_at':r.get('retrieved_at'),'transport_ok':r.get('ok'),
            'error':r.get('error'),'api_error':b.get('error'),
            'server_unixtime':b.get('result',{}).get('unixtime'),
            'pair_keys':[k for k in b.get('result',{}) if k not in ('unixtime','rfc1123')],
            'http_response_headers':r.get('http_response_headers')}),flush=True)
    print(json.dumps({'diagnostic_finished_at':iso(),'http_429_count':relay.http_429_count,
                     'recovery_wait_seconds':relay.recovery_wait_seconds,
                     'production_ready':False,'trade_signal':None}),flush=True)


if __name__=='__main__':main()
