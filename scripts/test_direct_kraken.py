"""Synthetic, network-free tests for direct evidence and cache semantics."""
import copy
import json
import tempfile
import time
import unittest
import urllib.error
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
import direct_kraken as d
from independent_reference import IndependentReferences

AT=1791276000.0
BOUND=int(AT//d.BAR)*d.BAR
DATE=datetime.fromtimestamp(AT,timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')
STAMP=datetime.fromtimestamp(AT,timezone.utc).isoformat()

def source(ok=True):return {'ok':ok,'retrieved_at':STAMP,'http_metadata':{'Date':DATE}}
def ohlc(key='XXBTZEUR'):
    rows=[[BOUND-(49-i)*d.BAR,100,102,99,101,100,10,1] for i in range(50)]
    return {'data':{key:rows,'last':BOUND},'source':source()}
def dep(key='XXBTZEUR'):
    return {'data':{key:{'bids':[['100','3',AT-1],['99','4',AT-2]],'asks':[['100.1','3',AT-1],['102','4',AT-2]]}},'source':source()}
def meta():
    return {'XXBTZEUR':{'altname':'XBTEUR','wsname':'XBT/EUR','base':'XXBT','quote':'ZEUR','aclass_base':'currency','status':'online','ordermin':'0.0001','costmin':'0.45'},
            'XXDGZEUR':{'altname':'XDGEUR','wsname':'XDG/EUR','base':'XXDG','quote':'ZEUR','aclass_base':'currency','status':'online'},
            'XXBTZUSD':{'altname':'XBTUSD','wsname':'XBT/USD','base':'XXBT','quote':'ZUSD','aclass_base':'currency','status':'online'},
            'ZEURZUSD':{'altname':'EURUSD','wsname':'EUR/USD','base':'ZEUR','quote':'ZUSD','aclass_base':'currency','status':'online'}}
def tick(price):return {'a':[str(price*1.001),'1','1'],'b':[str(price),'1','1'],'c':[str(price),'1'],'v':['3','10'],'p':[str(price),str(price)],'o':str(price*.99)}

class FakeHTTP:
    def __init__(self):self.count=0;self.rate_errors=0;self.until=0;self.calls=[]
    def get(self,path,params=None):
        self.count+=1;self.calls.append((path,params))
        if path.endswith('Time'):data={'unixtime':AT}
        elif path.endswith('AssetPairs'):data=meta()
        elif path.endswith('Ticker'):data={k:tick(1.1 if k=='ZEURZUSD' else 110 if k=='XXBTZUSD' else 100) for k in meta()}
        elif path.endswith('OHLC'):
            key=next(k for k,m in meta().items() if m['altname']==params['pair']);return ohlc(key)
        elif path.endswith('Depth'):
            key=next(k for k,m in meta().items() if m['altname']==params['pair']);return dep(key)
        else:raise AssertionError(path)
        return {'data':data,'source':source()}

class CandleTests(unittest.TestCase):
    def test_excludes_running_bar_and_nonoverlap_volume(self):
        r=d.closed_candles(ohlc(),'XXBTZEUR',AT)
        self.assertEqual(r['quality'],'VERIFIED');self.assertEqual(len(r['recent_closed_rows']),49)
        self.assertEqual(r['volume_research']['ratio'],1);self.assertEqual(r['volume_research']['prior_7_block_volumes_base'],[60]*7)
        self.assertEqual(r['closed_24h']['start_unix'],BOUND-86400)
        self.assertEqual(r['closed_48h']['start_unix'],BOUND-172800)
    def test_never_use_running_bar_for_return(self):
        r=ohlc();r['data']['XXBTZEUR'][-1][4]=999999
        self.assertEqual(d.closed_candles(r,'XXBTZEUR',AT)['closed_24h']['change_pct'],0)
    def test_gap_or_wrong_pair_fail(self):
        r=ohlc();r['data']['XXBTZEUR'].pop(-10)
        self.assertEqual(d.closed_candles(r,'XXBTZEUR',AT)['quality'],'MISSING')
        self.assertEqual(d.closed_candles(ohlc(),'WRONG',AT)['quality'],'MISSING')
    def test_boundary_requires_new_actual_closed_bar(self):
        self.assertEqual(d.closed_candles(ohlc(),'XXBTZEUR',BOUND+d.BAR+1)['quality'],'MISSING')
    def test_future_or_stale_provenance_fails(self):
        self.assertEqual(d.closed_candles(ohlc(),'XXBTZEUR',AT-1)['quality'],'MISSING')
    def test_zero_median_not_bullish_volume(self):
        r=ohlc()
        for row in r['data']['XXBTZEUR']:row[6]=0
        x=d.closed_candles(r,'XXBTZEUR',AT)
        self.assertIsNone(x['volume_research']['ratio']);self.assertEqual(x['volume_research']['quality'],'MISSING')
    def test_bad_prices_nan_bool_and_duplicate_rejected(self):
        for value in ('nan',True,-1):
            r=ohlc();r['data']['XXBTZEUR'][-3][4]=value
            self.assertEqual(d.closed_candles(r,'XXBTZEUR',AT)['quality'],'MISSING')
        r=ohlc();r['data']['XXBTZEUR'][-3][0]=r['data']['XXBTZEUR'][-4][0]
        self.assertEqual(d.closed_candles(r,'XXBTZEUR',AT)['quality'],'MISSING')

class DepthTests(unittest.TestCase):
    def test_lower_bound_in_half_percent_band(self):
        x=d.depth_evidence(dep(),'XXBTZEUR',AT)
        self.assertEqual(x['quality'],'VERIFIED');self.assertAlmostEqual(x['depth_each_side']['bids']['notional_quote_lower_bound'],300)
        self.assertEqual(x['snapshot_observed_at'],STAMP)
    def test_bad_order_crossed_book_wrong_pair_future_time_fail(self):
        for change in ('order','cross','future'):
            r=dep()
            if change=='order':r['data']['XXBTZEUR']['bids'].reverse()
            if change=='cross':r['data']['XXBTZEUR']['asks'][0][0]='90'
            if change=='future':r['data']['XXBTZEUR']['asks'][0][2]=AT+1
            self.assertEqual(d.depth_evidence(r,'XXBTZEUR',AT)['quality'],'MISSING')
        self.assertEqual(d.depth_evidence(dep(),'WRONG',AT)['quality'],'MISSING')
    def test_stale_snapshot_never_refreshed_by_level_time(self):
        self.assertEqual(d.depth_evidence(dep(),'XXBTZEUR',AT+601)['quality'],'MISSING')

class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.clock=patch('direct_kraken.time.time',return_value=AT);self.clock.start();self.addCleanup(self.clock.stop)
        self.adapter=d.DirectKrakenTransport();self.adapter.http=FakeHTTP()
        self.adapter.independent.get=lambda base:{'quality':'MISSING'}
    def test_source_identity_explicit_and_no_trade(self):
        r,n=self.adapter.get_bounded('/candidate.json?pair=XBTEUR&notional_eur=60')
        self.assertEqual(r['body']['producer'],d.PRODUCER)
        self.assertEqual(r['body']['data_health'],'DATA_OK');self.assertIsNone(r['body']['trade_signal'])
        self.assertEqual(r['body']['independent_reference']['quality'],'MISSING')
    def test_one_btc_history_and_batch_ticker_shared_across_pairs(self):
        for pair in ('XBTEUR','XDGEUR'):self.adapter.get_bounded('/candidate.json?pair='+pair)
        calls=self.adapter.http.calls
        self.assertEqual(sum(p.endswith('Ticker') for p,q in calls),1)
        self.assertEqual(sum(p.endswith('AssetPairs') for p,q in calls),1)
        self.assertEqual(sum(p.endswith('OHLC') and q['pair']=='XBTEUR' for p,q in calls),1)
    def test_persisted_candles_reused_with_original_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.adapter.cache_path=Path(tmp)/'direct-cache.json'
            self.adapter.get_bounded('/candidate.json?pair=XBTEUR');self.adapter.save()
            other=d.DirectKrakenTransport(cache_path=self.adapter.cache_path);other.http=FakeHTTP()
            other.independent.get=lambda base:{'quality':'MISSING'}
            r,n=other.get_bounded('/candidate.json?pair=XBTEUR')
            self.assertEqual(sum(p.endswith('OHLC') for p,q in other.http.calls),0)
            self.assertEqual(r['body']['closed_4h']['source']['retrieved_at'],STAMP)
    def test_aggregate_is_not_single_pair_turnover(self):
        agg=self.adapter._aggregate('XXBT')
        self.assertEqual(len(agg['included_pairs']),2)
        self.assertAlmostEqual(agg['turnover_24h_eur'],2000/1.0005,delta=2)
    def test_no_stable_base_discovery(self):
        r,n=self.adapter.get_bounded('/universe.json')
        self.assertNotIn('EUR',[row['asset'] for row in r['body']['candidates']])
    def test_private_and_arbitrary_urls_not_supported(self):
        for path in ('/0/private/Balance','https://evil.test/health','/account'):
            with self.assertRaises(ValueError):self.adapter.get_bounded(path)
    def test_btc_optional_failure_does_not_invent_missing_alt_price(self):
        r,n=self.adapter.get_bounded('/candidate.json?pair=XDGEUR')
        self.assertTrue(r['body']['btc_same_period_reference']['optional_only'])
    def test_corrupt_cache_raises_not_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'bad';p.write_text('[]')
            with self.assertRaises(ValueError):d.DirectKrakenTransport(cache_path=p)

class Response:
    status=200;headers={'Content-Type':'application/json','Date':DATE}
    def __init__(self,body):self.body=json.dumps(body).encode()
    def read(self,n):return self.body[:n]
    def __enter__(self):return self
    def __exit__(self,*args):return False

class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.clock=patch('direct_kraken.time.time',return_value=AT);self.clock.start();self.addCleanup(self.clock.stop)
    def test_nested_direct_kraken_api_limit_blocks_subsequent_call(self):
        h=d.PublicHTTP('https://api.kraken.com',{'/0/public/Ticker'})
        with patch('direct_kraken.urllib.request.urlopen',return_value=Response({'error':['EGeneral:Too many requests']})) as net:
            first=h.get('/0/public/Ticker');second=h.get('/0/public/Ticker')
        self.assertFalse(first['source']['ok']);self.assertFalse(second['source']['http_request_made']);self.assertEqual(net.call_count,1)
    def test_unknown_asset_is_not_rate_limit(self):
        h=d.PublicHTTP('https://api.kraken.com',{'/0/public/AssetPairs'})
        with patch('direct_kraken.urllib.request.urlopen',return_value=Response({'error':['EQuery:Unknown asset pair']})):
            r=h.get('/0/public/AssetPairs')
        self.assertFalse(r['source']['ok']);self.assertEqual(h.rate_errors,0)
    def test_5xx_has_only_one_retry(self):
        h=d.PublicHTTP('https://api.kraken.com',{'/0/public/Time'})
        e=urllib.error.HTTPError('https://api.kraken.com/0/public/Time',503,'failure',{},None)
        with patch('direct_kraken.urllib.request.urlopen',side_effect=e) as net,patch('direct_kraken.time.sleep'):
            r=h.get('/0/public/Time')
        self.assertEqual(net.call_count,2);self.assertFalse(r['source']['ok'])
    def test_stale_http_content_not_relabelled_fresh(self):
        h=d.PublicHTTP('https://api.kraken.com',{'/0/public/Time'})
        response=Response({'error':[],'result':{'unixtime':AT}})
        response.headers={**response.headers,'Age':'3600'}
        with patch('direct_kraken.urllib.request.urlopen',return_value=response):
            result=h.get('/0/public/Time')
        self.assertFalse(result['source']['ok']);self.assertEqual(result['source']['status'],'STALE_HTTP_RESPONSE')
    def test_only_allowed_gets(self):
        h=d.PublicHTTP('https://api.kraken.com',{'/0/public/Time'})
        with self.assertRaises(ValueError):h.get('/0/private/AddOrder')

class IndependentTests(unittest.TestCase):
    def test_mismatched_id_symbol_does_not_create_reference(self):
        x=IndependentReferences({})
        x.cg.get=lambda *args,**kw:{'data':[{'id':'megaeth','symbol':'wrong'}],'source':source()}
        self.assertEqual(x.get('MEGA')['quality'],'MISSING')
    def test_provider_outage_never_becomes_kraken_quote(self):
        x=IndependentReferences({});x.cg.get=lambda *a,**k:{'data':None,'source':source(False)}
        x.cb.get=lambda *a,**k:{'data':None,'source':source(False)}
        self.assertEqual(x.get('BTC')['quality'],'MISSING')
    def test_cg_timestamp_and_identity_required(self):
        x=IndependentReferences({})
        def api(path,params):
            if path.endswith('list'):return {'data':[{'id':'megaeth','symbol':'mega','name':'MegaETH'}],'source':source()}
            return {'data':{'megaeth':{'eur':.04,'last_updated_at':AT-1800}},'source':source()}
        x.cg.get=api
        with patch('direct_kraken.time.time',return_value=AT):self.assertEqual(x.get('MEGA')['quality'],'MISSING')

if __name__=='__main__':unittest.main()
