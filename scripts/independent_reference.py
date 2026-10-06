"""Independent-provider price references only; never substitute Kraken execution.

CoinGecko identity is checked against /coins/list before querying by exact ID.
Coinbase fallback is deliberately limited to established BTC/ETH/SOL EUR pairs.
Other missing assets remain UNKNOWN, not automatically aliased to another token.
"""
import json
import time
from direct_kraken import PublicHTTP, finite, fresh, ts, stamp

IDS = {'BTC':('bitcoin','btc'), 'ETH':('ethereum','eth'), 'SOL':('solana','sol'),
       'DOGE':('dogecoin','doge'), 'LTC':('litecoin','ltc'), 'BNB':('binancecoin','bnb'),
       'LINK':('chainlink','link'), 'SUI':('sui','sui'), 'AAVE':('aave','aave'),
       'ONDO':('ondo-finance','ondo'), 'INJ':('injective-protocol','inj'), 'PENDLE':('pendle','pendle'),
       'RENDER':('render-token','render'), 'TAO':('bittensor','tao'), 'GALA':('gala','gala'),
       'MEGA':('megaeth','mega'), 'NIGHT':('midnight-3','night'), 'HYPE':('hyperliquid','hype'),
       'SHIB':('shiba-inu','shib')}


# Official Midnight tokenomics whitepaper and CoinGecko midnight-3 refer to
# this Cardano asset. CoinGecko id midnight is an unrelated Polygon meme.
NIGHT_CARDANO='0691b2fecca1ac4f53cb6dfb00b7013e561d1f34403b957cbb5af1fa4e49474854'

def compare_reference(reference, book_mid_quote, eur_per_quote, asset):
    result={'quality':'MISSING','action_approved':False,'independent_price_only':True,
            'liquid_1pct_pass':False,'speculative_2pct_pass':False}
    try:
        if (reference.get('quality')!='VERIFIED_PROVIDER_REFERENCE' or reference.get('asset_symbol')!=asset
                or reference.get('quote')!='EUR' or not fresh(reference.get('source',{}),900)
                or not 0<=time.time()-finite(reference.get('observed_unix'))<=900): return result
        price=finite(reference['price']);mid=finite(book_mid_quote)*finite(eur_per_quote)
        mismatch=100*abs(mid-price)/min(mid,price)
        return {**result,'quality':'CONFLICT' if mismatch>2 else 'COMPARABLE_REFERENCE',
                'provider':reference['provider'],'kraken_mid_eur':mid,'reference_eur':price,
                'mismatch_pct':mismatch,'liquid_1pct_pass':mismatch<=1,
                'speculative_2pct_pass':mismatch<=2,
                'note':'Not a trade approval; identity, current source times and all other gates remain required.'}
    except (KeyError,TypeError,ValueError):return result


class IndependentReferences:
    def __init__(self, cache):
        self.cache=cache; self.references={};self.attempted=False
        self.cg=PublicHTTP('https://api.coingecko.com',{'/api/v3/coins/list','/api/v3/simple/price'},budget=2,timeout=10)
        self.cb=PublicHTTP('https://api.exchange.coinbase.com',{
            '/products/BTC-EUR/ticker','/products/ETH-EUR/ticker','/products/SOL-EUR/ticker'},budget=3,timeout=10)
        self.diagnostics=[]
    def _load(self):
        self.attempted=True
        identity=self.cache.get('independent:coingecko:identities')
        if not identity or not fresh(identity.get('source',{}),86400):
            raw=self.cg.get('/api/v3/coins/list',{'include_platform':'true'})
            found={}
            if raw['source']['ok'] and isinstance(raw['data'],list):
                for base,(coin_id,symbol) in IDS.items():
                    hits=[r for r in raw['data'] if isinstance(r,dict) and r.get('id')==coin_id and r.get('symbol','').lower()==symbol]
                    if len(hits)==1 and (base!='NIGHT' or hits[0].get('platforms',{}).get('cardano')==NIGHT_CARDANO):found[base]=hits[0]
            identity={'source':raw['source'],'data':found}
            if found:self.cache['independent:coingecko:identities']=identity
        mapped={base:r for base,r in identity.get('data',{}).items()
                if base in IDS and isinstance(r,dict) and r.get('id')==IDS[base][0]
                and str(r.get('symbol','')).lower()==IDS[base][1]
                and (base!='NIGHT' or r.get('platforms',{}).get('cardano')==NIGHT_CARDANO)}
        if mapped:
            prices=self.cg.get('/api/v3/simple/price',{'ids':','.join(sorted(r['id'] for r in mapped.values())),
                  'vs_currencies':'eur','include_last_updated_at':'true'})
            self.diagnostics.append(prices['source'])
            for base,meta in mapped.items():
                row=(prices['data'] or {}).get(meta['id'],{}) if isinstance(prices.get('data'),dict) else {}
                try:
                    price=finite(row['eur']);updated=finite(row['last_updated_at'])
                    if not prices['source']['ok'] or not 0<=time.time()-updated<=900:continue
                    self.references[base]={'quality':'VERIFIED_PROVIDER_REFERENCE','provider':'CoinGecko','asset_id':meta['id'],
                        'identity_name':meta.get('name'),'asset_symbol':base,'identity_source':identity['source'],'identity_platforms':meta.get('platforms',{}),'quote':'EUR','price':price,
                        'observed_unix':updated,'source':prices['source'],'is_kraken_executable':False,
                        'action_approved':False,'note':'Aggregator price, not an independent Kraken order book.'}
                except (KeyError,TypeError,ValueError):continue
        else:self.diagnostics.append(identity.get('source',{}))
    def get(self,base):
        if not self.attempted:self._load()
        if base not in self.references and base in ('BTC','ETH','SOL'):
            row=self.cb.get('/products/'+base+'-EUR/ticker');raw=row.get('data')
            self.diagnostics.append(row['source'])
            try:
                bid,ask=finite(raw['bid']),finite(raw['ask']);when=ts(raw['time'])
                if not row['source']['ok'] or bid>ask or not 0<=time.time()-when<=900:raise ValueError('Stale/missing crosscheck')
                self.references[base]={'quality':'VERIFIED_PROVIDER_REFERENCE','provider':'Coinbase Exchange',
                    'pair':base+'-EUR','asset_symbol':base,'quote':'EUR','price':(bid+ask)/2,'bid':bid,'ask':ask,
                    'observed_unix':when,'time_basis':'API_LAST_TRADE_TIME_CHECKED_PLUS_CURRENT_BBO_ACQUISITION',
                    'source':row['source'],'is_kraken_executable':False,'action_approved':False}
            except (ValueError,TypeError,KeyError):
                self.references[base]={'quality':'MISSING','provider':'Coinbase Exchange','source':row['source']}
        return self.references.get(base,{'quality':'MISSING','reason':'NO_VERIFIED_FRESH_REFERENCE_FOR_EXACT_ASSET',
                                        'is_kraken_executable':False,'action_approved':False})
    def report(self):
        return {'schema':'CAW_INDEPENDENT_REFERENCES_V1','generated_at':stamp(),'references':self.references,
                'diagnostics':self.diagnostics,'is_kraken_executable':False,'automatic_trading':False}
