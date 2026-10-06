"""Direct public Kraken evidence adapter. No Worker requests or exchange credentials.

The old JSON wire schema is retained for the existing consumer, but producer and
all source URLs identify this adapter. A second provider is reference-only.
"""
import copy
import json
import math
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

SCHEMA = 'KRAKEN_PUBLIC_TRANSPORT_V2.0.2'
PRODUCER = 'CAW_DIRECT_KRAKEN_V1'
BAR = 14400
FIAT_STABLE = {'EUR','USD','USDC','USDT','GBP','CHF','CAD','AUD','JPY','DAI','PYUSD','EURC','USDE','USDG','USDD','TUSD','FDUSD'}
DISPLAY = {'XXBT':'BTC','XBT':'BTC','XDG':'DOGE','XXDG':'DOGE','XETH':'ETH','XLTC':'LTC','ZEUR':'EUR','ZUSD':'USD','ZGBP':'GBP','ZCAD':'CAD','ZJPY':'JPY','ZCHF':'CHF'}


def now(): return datetime.now(timezone.utc)
def stamp(): return now().isoformat().replace('+00:00','Z')
def ts(value): return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
def finite(value, zero=False):
    if isinstance(value, bool): raise ValueError('Boolean number')
    number = float(value)
    if not math.isfinite(number) or number < 0 or (number == 0 and not zero): raise ValueError('Invalid number')
    return number

def fresh(source, age=600, at=None):
    try:
        s = source['retrieved_at']; parsed = datetime.fromisoformat(s.replace('Z','+00:00'))
        return source.get('ok') is True and parsed.tzinfo is not None and 0 <= (at or time.time()) - parsed.timestamp() <= age
    except (ValueError, TypeError, KeyError, AttributeError): return False


def wait_hint(headers, minimum=60):
    try:
        hint = float(headers.get('Retry-After'))
    except (ValueError, TypeError):
        try: hint = parsedate_to_datetime(headers.get('Retry-After')).timestamp() - time.time()
        except (ValueError, TypeError, AttributeError, OverflowError): hint = minimum
    return max(minimum, hint) if math.isfinite(hint) else minimum


class PublicHTTP:
    """Allowlisted GET transport. Per-host cooldown; <=1 request/1.25s after reply.

    One transient 5xx retry is permitted; a throttle is NOT retried via a different
    IP, host or Worker. Direct Kraken is the sole primary, not throttle evasion.
    """
    def __init__(self, origin, allowed, previous=None, budget=80, timeout=15):
        self.origin, self.allowed = origin, set(allowed)
        self.last = None; self.count = 0; self.rate_errors = 0
        self.deadline = time.monotonic() + 440; self.budget = budget; self.timeout = timeout
        self.until = finite((previous or {}).get('cooldown_until_epoch',0), zero=True)

    def get(self, path, params=None):
        if path not in self.allowed: raise ValueError('Non-allowlisted public route')
        query = urllib.parse.urlencode(params or {})
        url = self.origin + path + ('?' + query if query else '')
        source = {'provider': urllib.parse.urlsplit(self.origin).hostname, 'source_url':url,
                  'method':'GET','ok':False,'request_started_at':stamp(), 'retrieved_at':stamp(),
                  'market_observed_at':None,'from_cache':False}
        for attempt in range(2):
            if time.time() < self.until or self.count >= self.budget or time.monotonic()+self.timeout+2 >= self.deadline:
                source.update(status='COOLDOWN_OR_BUDGET',retrieved_at=stamp(),http_request_made=False)
                return {'data':None,'source':source}
            delay = 0 if self.last is None else max(0,1.25-(time.monotonic()-self.last))
            if delay: time.sleep(delay)
            source['request_started_at'] = stamp(); self.count += 1
            req=urllib.request.Request(url,headers={'Accept':'application/json','User-Agent':'CAW-public-evidence/1.5'},method='GET')
            try:
                with urllib.request.urlopen(req,timeout=self.timeout) as response:
                    data=response.read(16*1024*1024+1)
                    if len(data)>16*1024*1024: raise ValueError('Response too large')
                    if 'json' not in response.headers.get('Content-Type','').lower(): raise ValueError('Non-JSON response')
                    body=json.loads(data)
                    source.update(http_status=response.status, http_metadata={k:response.headers.get(k) for k in ('Date','Age','Cache-Control','Retry-After') if response.headers.get(k) is not None})
                source.update(retrieved_at=stamp(),http_request_made=True)
                headers=source.get('http_metadata',{})
                if self.origin == 'https://api.kraken.com':
                    date=parsedate_to_datetime(headers.get('Date'))
                    if date.tzinfo is None or not -2 <= time.time()-date.timestamp() <= 600 or float(headers.get('Age',0))>600:
                        source.update(status='STALE_HTTP_RESPONSE');return {'data':None,'source':source}
                if self.origin == 'https://api.kraken.com':
                    if not isinstance(body,dict) or not isinstance(body.get('error'),list) or not isinstance(body.get('result',{}),dict): raise ValueError('Bad Kraken envelope')
                    if body['error']:
                        source.update(status='KRAKEN_API_ERROR',errors=body['error'])
                        if any('Too many requests' in e or 'Rate limit' in e or 'Throttled' in e for e in body['error'] if isinstance(e,str)):
                            self.until=time.time()+wait_hint(source.get('http_metadata',{})); self.rate_errors+=1
                        return {'data':None,'source':source}
                    body=body.get('result')
                    if not isinstance(body,dict): raise ValueError('Missing Kraken result')
                source.update(ok=True,status='PUBLIC_JSON_ENVELOPE_VALID')
                return {'data':body,'source':source}
            except urllib.error.HTTPError as error:
                source.update(http_status=error.code,status='HTTP_'+str(error.code),retrieved_at=stamp(),http_request_made=True)
                if error.code in (418,429):
                    self.until=time.time()+wait_hint(error.headers or {}); self.rate_errors+=1
                if error.code in (500,502,503,504) and attempt==0:
                    time.sleep(2); continue
                return {'data':None,'source':source}
            except Exception as error:
                source.update(status=type(error).__name__,retrieved_at=stamp(),http_request_made=True)
                return {'data':None,'source':source}
            finally: self.last=time.monotonic()
        return {'data':None,'source':source}


def closed_candles(result, key, at=None):
    """49 contiguous closed 4h rows cover 24h return and 7 prior volume blocks."""
    at = at or time.time(); boundary=int(at//BAR)*BAR
    bad={'quality':'MISSING','source':result.get('source',{}),'reason':'INCOMPLETE_OR_NONCOMPARABLE_CANDLES'}
    try:
        rows=result['data'][key]
        if not isinstance(rows,list) or len(rows)<50 or not fresh(result['source'],4*3600,at): return bad
        # Kraken always includes an unfinished final bar, never use it as closed.
        rows=rows[:-1]
        normalized=[]
        for row in rows:
            if not isinstance(row,list) or len(row)!=8: return bad
            start=finite(row[0]); vals=[finite(x) for x in row[1:6]]
            volume=finite(row[6],zero=True); trades=finite(row[7],zero=True)
            if start%BAR or start+BAR>boundary or not vals[2]<=min(vals[0],vals[3])<=max(vals[0],vals[3])<=vals[1]: return bad
            normalized.append([int(start),*vals,volume,trades])
        tail=normalized[-49:]
        if len(tail)<49 or tail[-1][0]+BAR!=boundary or any(b[0]-a[0]!=BAR for a,b in zip(tail,tail[1:])): return bad
        if ts(result['source']['retrieved_at'])<boundary: return bad
        volumes=[sum(r[6] for r in tail[-6*(i+2):-6*(i+1)]) for i in range(7)]
        current=sum(r[6] for r in tail[-6:]); median=statistics.median(volumes)
        def returns(n):
            return {'quality':'VERIFIED','start_unix':boundary-n*BAR,'end_unix':boundary,
                    'start_at':datetime.fromtimestamp(boundary-n*BAR,timezone.utc).isoformat(),
                    'end_at':datetime.fromtimestamp(boundary,timezone.utc).isoformat(),
                    'from_close':tail[-n-1][4],'to_close':tail[-1][4],
                    'change_pct':100*(tail[-1][4]/tail[-n-1][4]-1)}
        return {'quality':'VERIFIED','source':result['source'],'unfinished_final_bar_excluded':True,
                'history_has_gaps':False,'latest_closed_bar_start':datetime.fromtimestamp(boundary-BAR,timezone.utc).isoformat(),
                'latest_closed_bar_end':datetime.fromtimestamp(boundary,timezone.utc).isoformat(),
                'closed_24h':returns(6),'closed_48h':returns(12),
                'volume_research':{'quality':'VERIFIED' if median>0 else 'MISSING',
                    'last_24h_volume_base':current,'prior_7_block_volumes_base':volumes,
                    'median_prior_7_blocks_base':median,'ratio':current/median if median>0 else None},
                'columns':['start_unix','open','high','low','close','vwap','volume_base','trades'],
                'recent_closed_rows':normalized[-96:]}
    except (KeyError,ValueError,TypeError,IndexError,OverflowError): return bad


def depth_evidence(result,key,at=None):
    at=at or time.time()
    bad={'quality':'MISSING','source':result.get('source',{})}
    try:
        if not fresh(result['source'],600,at): return bad
        raw=result['data'][key]; sides={}
        for side in ('bids','asks'):
            rows=raw[side]
            if not isinstance(rows,list) or not rows: return bad
            sides[side]=[[finite(r[0]),finite(r[1]),finite(r[2],zero=True)] for r in rows]
            if any(r[2]>at for r in sides[side]): return bad
            prices=[r[0] for r in sides[side]]
            if prices!=sorted(set(prices),reverse=side=='bids'): return bad
        bid,ask=sides['bids'][0][0],sides['asks'][0][0]
        if bid>ask: return bad
        mid=(bid+ask)/2
        bands={}
        for side in sides:
            rows=sides[side]
            within=[r for r in rows if (r[0]>=mid*.995 if side=='bids' else r[0]<=mid*1.005)]
            fully=len(rows)<100 or (rows[-1][0]<=mid*.995 if side=='bids' else rows[-1][0]>=mid*1.005)
            bands[side]={'notional_quote_lower_bound':sum(p*q for p,q,t in within),'band_fully_observed':fully}
        return {'quality':'VERIFIED','source':result['source'],'best_bid':bid,'best_ask':ask,'mid':mid,
                'spread_pct':100*(ask-bid)/mid,'band_pct':.5,'depth_each_side':bands,
                'returned_levels':{k:len(v) for k,v in sides.items()},'levels':sides,
                'book_level_timestamps_note':'LEVEL_LAST_UPDATE_TIMES_ARE_NOT_SNAPSHOT_TIME',
                'snapshot_observed_at':result['source']['retrieved_at'],
                'snapshot_time_basis':'LOCAL_ACQUISITION_TIME_NOT_EXCHANGE_EVENT_TIMESTAMP'}
    except (KeyError,TypeError,ValueError,IndexError): return bad


class DirectKrakenTransport:
    """Drop-in route adapter for the existing collector. Its cache is public only."""
    def __init__(self,base=None,previous=None,timeout=15,cache_path=None):
        self.cache_path=Path(cache_path) if cache_path else None
        self.cache=json.loads(self.cache_path.read_text()) if self.cache_path and self.cache_path.exists() else {}
        if not isinstance(self.cache,dict): raise ValueError('Corrupt direct public cache')
        self.http=PublicHTTP('https://api.kraken.com',{'/0/public/Time','/0/public/AssetPairs','/0/public/Ticker','/0/public/Depth','/0/public/OHLC'},previous,timeout=min(timeout,15))
        self.memory={}; self.recovery_wait_seconds=0.0
        from independent_reference import IndependentReferences
        self.independent=IndependentReferences(self.cache)
    @property
    def requests_made(self): return self.http.count
    @property
    def http_429_count(self): return self.http.rate_errors
    def state(self):
        return {'cooldown_until_epoch':self.http.until,'provider':'api.kraken.com','transport_mode':PRODUCER}
    def save(self):
        if self.cache_path:
            # Bounded cache with original source records. No account/advisory state.
            cutoff=time.time()-86400
            self.cache={k:v for k,v in self.cache.items() if fresh(v.get('source',{}),86400)}
            if len(self.cache)>96:
                self.cache=dict(sorted(self.cache.items(),key=lambda x:x[1]['source']['retrieved_at'],reverse=True)[:96])
            self.cache_path.parent.mkdir(parents=True,exist_ok=True)
            tmp=self.cache_path.with_suffix('.tmp');tmp.write_text(json.dumps(self.cache,allow_nan=False));tmp.replace(self.cache_path)
            ref=self.cache_path.parent/'independent-quotes.json'
            tmp=ref.with_suffix('.tmp');tmp.write_text(json.dumps(self.independent.report(),allow_nan=False));tmp.replace(ref)
    def _api(self,path,params=None,ttl=0,candle_key=None):
        key=path+'?'+urllib.parse.urlencode(params or {})
        if key in self.memory: return self.memory[key]
        old=self.cache.get(key)
        if old and fresh(old.get('source',{}),ttl) and (not candle_key or closed_candles(old,candle_key).get('quality')=='VERIFIED'):
            result=copy.deepcopy(old); result['source']['from_cache']=True
        else:
            result=self.http.get(path,params)
            if result['source'].get('ok') and ttl: self.cache[key]=copy.deepcopy(result)
        self.memory[key]=result; return result
    def _meta(self):
        return self._api('/0/public/AssetPairs',{'country_code':'SI','aclass_base':'currency','execution_venue':'international'},ttl=550)
    def _ticks(self): return self._api('/0/public/Ticker')
    def _resolve(self,pair):
        data=self._meta()['data'] or {}
        matches=[(k,m) for k,m in data.items() if isinstance(m,dict) and all(isinstance(m.get(x),str) for x in ('altname','base','quote')) and pair in (k,m.get('altname'),str(m.get('wsname','')).replace('/',''))]
        return matches[0] if len(matches)==1 else (None,None)
    def _ticker(self,key):
        result=self._ticks();raw=(result['data'] or {}).get(key,{})
        try:
            bid,ask,last=finite(raw['b'][0]),finite(raw['a'][0]),finite(raw['c'][0])
            volume,vwap=finite(raw['v'][1],zero=True),finite(raw['p'][1],zero=True)
            if bid>ask or not fresh(result['source']): raise ValueError('Stale/bad ticker')
            return {'quality':'APPROXIMATE','bid':bid,'ask':ask,'last':last,'mid':(bid+ask)/2,
                    'source':result['source'],'volume_24h_base':volume,'vwap_24h_quote':vwap,
                    'turnover_24h_quote':volume*vwap,'change_since_midnight_utc_pct':100*(last/finite(raw['o'])-1),
                    'rolling_24h_price_change_pct':None,'timestamp_note':'TICKER_HAS_NO_EXCHANGE_QUOTE_TIMESTAMP'}
        except (KeyError,TypeError,ValueError,IndexError): return {'quality':'MISSING','source':result['source']}
    def _fx(self,quote):
        quote=DISPLAY.get(quote,quote)
        if quote=='EUR': return 1.0
        requested={'USD':('EURUSD',True),'USDC':('USDCEUR',False)}.get(quote)
        if not requested: return None
        key,meta=self._resolve(requested[0]);tick=self._ticker(key)
        if tick.get('quality')=='MISSING': return None
        return 1/tick['mid'] if requested[1] else tick['mid']
    def _pairmeta(self,key,meta):
        base=DISPLAY.get(meta.get('base'),meta.get('base'));quote=DISPLAY.get(meta.get('quote'),meta.get('quote'))
        return {'pair_key':key,'altname':meta.get('altname'),'wsname':meta.get('wsname'),'asset':base,
                'base_code':meta.get('base'),'quote_code':meta.get('quote'),'base_display':base,'quote_display':quote,
                'aclass_base':meta.get('aclass_base'),'aclass_quote':meta.get('aclass_quote'),'status':meta.get('status'),
                'execution_venue':'international','country_filter':'SI','account_specific_eligibility':'NOT_CHECKED',
                **{k:meta.get(k) for k in ('pair_decimals','lot_decimals','cost_decimals')},
                'ordermin_base':meta.get('ordermin'),'costmin_quote':meta.get('costmin'),
                'tick_size_quote':meta.get('tick_size'),'fee_tier_and_interface_fees':'UNKNOWN'}
    def _candle(self,key,meta):
        return closed_candles(self._api('/0/public/OHLC',{'pair':meta['altname'],'interval':240},ttl=14399,candle_key=key),key)
    def _aggregate(self,base):
        total=0;parts=[];missing=[]
        for key,meta in (self._meta()['data'] or {}).items():
            if not isinstance(meta,dict) or meta.get('base')!=base or meta.get('aclass_base')!='currency': continue
            if DISPLAY.get(meta.get('quote'),meta.get('quote')) not in ('EUR','USD','USDC'): continue
            tick=self._ticker(key);fx=self._fx(meta.get('quote'))
            if tick.get('quality')=='MISSING' or fx is None: missing.append(key);continue
            value=tick['turnover_24h_quote']*fx;total+=value;parts.append({'pair':key,'turnover_eur':value})
        return {'quality':'APPROXIMATE' if parts and not missing else 'LOWER_BOUND' if parts else 'MISSING',
                'turnover_24h_eur':total if parts else None,'included_pairs':parts,'missing_pairs':missing,
                'scope':'SI_INTERNATIONAL_SPOT_EUR_USD_USDC_ONLY_NOT_GLOBAL_EXCHANGE_VOLUME',
                'source':self._ticks()['source']}
    def _candidate(self,pair):
        key,meta=self._resolve(pair)
        if key is None:
            # An absent bulk metadata result is not synthesized as a per-pair API error.
            # One exact API lookup lets the existing guard distinguish ineligibility.
            exact=self._api('/0/public/AssetPairs',{'pair':pair,'country_code':'SI','aclass_base':'currency','execution_venue':'international'})
            return {'data_health':'DATA_UNAVAILABLE','stage':'AssetPairs','source':exact['source'],
                    'pair_error':'PAIR_UNRESOLVED','blocking_data_reasons':['PAIR_NOT_RESOLVED_IN_SI_METADATA']}
        meta_out=self._pairmeta(key,meta);ticker=self._ticker(key)
        candles=self._candle(key,meta)
        # One BTC 4h retrieval per boundary, reused across every candidate/cycle.
        bk,bm=self._resolve('XBTEUR')
        btc=self._candle(bk,bm) if bk and bm else {'quality':'MISSING'}
        raw_depth=self._api('/0/public/Depth',{'pair':meta['altname'],'count':100})
        depth=depth_evidence(raw_depth,key)
        spread={'quality':'MISSING','source':raw_depth['source']}
        if depth['quality']=='VERIFIED':
            spread={'quality':'VERIFIED','bid':depth['best_bid'],'ask':depth['best_ask'],'mid':depth['mid'],
                    'spread_pct':depth['spread_pct'],'observed_at':depth['snapshot_observed_at'],
                    'meaning':'DIRECT_L2_SNAPSHOT_ACQUIRED_AT_LOCAL_TIME_NOT_EXCHANGE_CHANGE_TIMESTAMP',
                    'same_source_as_depth':True,'source':raw_depth['source']}
        fx=self._fx(meta.get('quote'));reasons=[]
        if meta.get('status')!='online' or meta.get('aclass_base')!='currency': reasons.append('PAIR_NOT_ONLINE_CURRENCY_SPOT')
        if not fresh(self._meta()['source']): reasons.append('METADATA_MISSING_OR_STALE')
        if ticker.get('quality')=='MISSING': reasons.append('TICKER_MISSING')
        if depth['quality']!='VERIFIED': reasons.append('DEPTH_MISSING')
        if candles.get('quality')!='VERIFIED': reasons.append('CLOSED_CANDLES_MISSING')
        if fx is None: reasons.append('FX_MISSING')
        if not self._clock_ok(): reasons.append('CLOCK_UNVERIFIED')
        mismatch=None
        if ticker.get('mid') and depth.get('mid'):
            mismatch=100*abs(ticker['mid']-depth['mid'])/min(ticker['mid'],depth['mid'])
            if mismatch>2: reasons.append('SAME_PROVIDER_PRICE_MISMATCH_GT_2PCT')
        return {'data_health':'PARTIAL_DATA' if reasons else 'DATA_OK','pair_metadata':meta_out,
                'metadata_source':self._meta()['source'],'ticker':ticker,'timestamped_spread':spread,'depth':depth,
                'closed_4h':candles,'closed_daily':{'quality':'NOT_COLLECTED_OPTIONAL'},
                'btc_same_period_reference':{'quality':btc.get('quality'),'source':btc.get('source'),
                    'closed_24h':btc.get('closed_24h'),'optional_only':True},
                'aggregate_turnover':self._aggregate(meta['base']),
                'independent_reference':self.independent.get(meta_out['base_display']),
                'fx':{'quality':'VERIFIED' if meta_out['quote_display']=='EUR' else 'APPROXIMATE' if fx else 'MISSING',
                      'eur_per_quote_mid':fx,'quote_currency':meta_out['quote_display'],
                      'source':None if meta_out['quote_display']=='EUR' else self._ticks()['source']},
                'same_provider_ticker_depth_mismatch_pct':mismatch,'blocking_data_reasons':reasons,
                'not_checked':['independent provider confirmation','fundamentals/security','private portfolio/risk caps','Convert price/fees','actual fill'],
                'test_notional_is_recommendation':False}
    def _clock_ok(self):
        clock=self._api('/0/public/Time')
        try: return fresh(clock['source']) and abs(finite(clock['data']['unixtime'])-ts(clock['source']['retrieved_at']))<30
        except (KeyError,ValueError,TypeError): return False
    def _universe(self):
        assets={}
        for key,meta in (self._meta()['data'] or {}).items():
            if not isinstance(meta,dict) or meta.get('aclass_base')!='currency' or meta.get('status')!='online': continue
            base=DISPLAY.get(meta.get('base'),meta.get('base'));quote=DISPLAY.get(meta.get('quote'),meta.get('quote'))
            if not isinstance(base,str) or base in FIAT_STABLE or quote not in ('EUR','USD','USDC') or not base.isalnum(): continue
            tick=self._ticker(key);fx=self._fx(quote)
            if tick.get('quality')=='MISSING' or fx is None: continue
            row={'pair_key':key,'altname':meta.get('altname'),'asset':base,'quote':quote,
                 'turnover_24h_eur':tick['turnover_24h_quote']*fx,
                 'change_since_midnight_utc_pct':tick['change_since_midnight_utc_pct']}
            old=assets.get(base)
            if old is None or (row['quote']=='EUR',row['turnover_24h_eur'])>(old['quote']=='EUR',old['turnover_24h_eur']): assets[base]=row
        rows=list(assets.values());gains=sorted([r for r in rows if r['change_since_midnight_utc_pct']>0],key=lambda r:r['change_since_midnight_utc_pct'],reverse=True)[:10]
        losses=sorted([r for r in rows if r['change_since_midnight_utc_pct']<0],key=lambda r:r['change_since_midnight_utc_pct'])[:10]
        return {'data_health':'PARTIAL_DATA','candidates':gains+losses,'discovery_only':True,
                'change_window':'SINCE_MIDNIGHT_UTC_NOT_CLOSED_24H',
                'source_times':{'metadata':self._meta()['source'],'ticker':self._ticks()['source']}}
    def get_bounded(self,path):
        start=self.http.count;parsed=urllib.parse.urlsplit(path);query=urllib.parse.parse_qs(parsed.query)
        if parsed.scheme or parsed.netloc: raise ValueError('Only logical public routes')
        if parsed.path=='/health':
            ok=self._clock_ok();body={'data_health':'DATA_OK' if ok else 'DATA_UNAVAILABLE','clock_source':self._api('/0/public/Time')['source']}
        elif parsed.path=='/quotes.json':
            pairs=query.get('pairs',[''])[0].split(',');rows=[];unresolved=[]
            for p in pairs:
                k,m=self._resolve(p)
                if k is None: unresolved.append(p);continue
                rows.append({'altname':m['altname'],'pair_key':k,'ticker':self._ticker(k)})
            body={'data_health':'PARTIAL_DATA','rows':rows,'unresolved':unresolved}
        elif parsed.path=='/candidate.json': body=self._candidate(query.get('pair',[''])[0])
        elif parsed.path=='/universe.json': body=self._universe()
        else: raise ValueError('Unsupported collector route')
        body.update(schema=SCHEMA,producer=PRODUCER,role='PUBLIC_READ_ONLY_EVIDENCE_TRANSPORT',
            transport_mode='DIRECT_KRAKEN_NO_WORKER',schema_note='LEGACY_WIRE_COMPATIBILITY_NOT_WORKER_IDENTITY',
            generated_at=stamp(),contains_account_data=False,automatic_trading=False,private_exchange_api=False,
            trade_signal=None,actionability='NEVER_AUTHORIZED_BY_DATA_ADAPTER')
        return {'ok':True,'body':body,'retrieved_at':stamp(),'http_request_made':self.http.count>start},self.http.count-start
