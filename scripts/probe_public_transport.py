#!/usr/bin/env python3
"""One bounded read-only eligibility diagnostic; no publication/trade/schedule.

The relay recovery smoke already completed in CI run 37417483118: BNB/MEGA/NIGHT
DATA_OK, one relay-local 429 recovered after 2.05s; direct IOEUR was Unknown pair.
This follow-up does NOT repeat those candidate calls. It checks alternate quotes
in one bulk public SI/international AssetPairs response, without account access.
"""
import json
from public_transport import PublicGetter


def main():
    direct=PublicGetter('https://api.kraken.com',timeout=15)
    path='/0/public/AssetPairs?country_code=SI&aclass_base=currency&execution_venue=international'
    r=direct.get(path)
    b=r.get('body',{})
    result=b.get('result')
    valid=r.get('ok') is True and b.get('error')==[] and isinstance(result,dict) and bool(result)
    selected=[]
    if valid:
        for key,meta in result.items():
            if isinstance(meta,dict) and (meta.get('base')=='IO' or meta.get('altname') in ('IOEUR','IOUSD','IOUSDC')):
                selected.append({'pair_key':key, **{k:meta.get(k) for k in ('altname','wsname','base','quote','status','ordermin','costmin')}})
    print(json.dumps({'diagnostic_only':True,'source_url':r.get('url'),
        'retrieved_at':r.get('retrieved_at'),'complete_metadata_response':valid,
        'returned_pair_count':len(result) if isinstance(result,dict) else None,
        'io_pairs':selected,'error':r.get('error'),'api_error':b.get('error'),
        'http_response_headers':r.get('http_response_headers'),
        'account_eligibility':'NOT_CHECKED','production_ready':False,'trade_signal':None}),flush=True)


if __name__=='__main__':main()
