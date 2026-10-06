#!/usr/bin/env python3
"""PR-only bounded diagnostics after the cold test rejected real candle data.

No production writes. No per-cycle retries or changed validation criteria.
"""
import json
import time
from direct_kraken import PublicHTTP, finite, BAR, closed_candles


def main():
    api=PublicHTTP('https://api.kraken.com',{'/0/public/OHLC'},budget=3)
    for pair in ('GALAEUR','MEGAEUR','PENDLEEUR'):
        result=api.get('/0/public/OHLC',{'pair':pair,'interval':240})
        data=result.get('data') or {}; keys=[k for k in data if k!='last']
        key=keys[0] if len(keys)==1 else None
        rows=data.get(key,[]); problems=[]
        for index,row in enumerate(rows[:-1]):
            try:
                start=finite(row[0]); values=[finite(x) for x in row[1:6]]
                if start%BAR: problems.append({'index':index,'reason':'MISALIGNED_START','row':row})
                if not values[2]<=min(values[0],values[3])<=max(values[0],values[3])<=values[1]:
                    problems.append({'index':index,'reason':'OHLC_ORDER','row':row})
            except Exception as error:
                problems.append({'index':index,'reason':type(error).__name__,'row':row})
        gaps=[{'index':i,'prior_start':a[0],'next_start':b[0]} for i,(a,b) in enumerate(zip(rows[:-1],rows[1:-1])) if float(b[0])-float(a[0])!=BAR]
        print(json.dumps({'diagnostic_only':True,'pair':pair,'source':result['source'],
            'result_keys':keys,'row_count':len(rows),'last_three_rows':rows[-3:],
            'parser_result':closed_candles(result,key),'invalid_count':len(problems),
            'invalid_examples':problems[:3]+problems[-3:], 'gap_count':len(gaps),
            'last_gap_examples':gaps[-3:],'recent_49_bad_count':sum(x['index']>=len(rows)-50 for x in problems),
            'production_ready':False}),flush=True)
    cg=PublicHTTP('https://api.coingecko.com',{'/api/v3/coins/list'},budget=1)
    coins=cg.get('/api/v3/coins/list',{'include_platform':'true'})
    matches=[r for r in coins.get('data',[]) if isinstance(r,dict) and (str(r.get('symbol','')).lower()=='night' or 'midnight' in str(r.get('name','')).lower())] if isinstance(coins.get('data'),list) else []
    print(json.dumps({'diagnostic_only':True,'night_identity_candidates':matches[:20],'source':coins['source'],
        'identity_approved':False,'production_ready':False}),flush=True)
    cb=PublicHTTP('https://api.exchange.coinbase.com',{'/products/BTC-EUR/ticker'},budget=1)
    record=cb.get('/products/BTC-EUR/ticker')
    print(json.dumps({'diagnostic_only':True,'coinbase_fallback':record,'production_ready':False}),flush=True)

if __name__=='__main__':main()
