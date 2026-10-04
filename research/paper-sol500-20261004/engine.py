#!/usr/bin/env python3
"""Frozen SOL500 forward paper trial. PUBLIC GET requests; never exchange orders.
A five-minute quote-observation model, NOT tick-accurate or exchange-native stops.
"""
import argparse
import copy
import hashlib
import json
import math
import statistics
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ID = 'SOL500-FWD-20261004-v1'
START = int(datetime(2026, 10, 4, 7, tzinfo=timezone.utc).timestamp())
END = START + 7 * 86400
FEE, SLIP = 0.008, 0.001
PROTOCOL = {
    'id': ID, 'paper_only': True, 'automatic_real_trading': False,
    'start_utc': '2026-10-04T07:00:00Z', 'end_utc': '2026-10-11T07:00:00Z',
    'capital_eur': 500.0, 'pair': 'SOLEUR', 'venue': 'Kraken international spot',
    'fee_each_side': FEE, 'additional_adverse_slippage_each_side': SLIP,
    'fee_assumption': 'Frozen public Tier 1 taker rate; no account-specific discount assumed',
    'signal': 'Closed 1h SMA20>SMA50, close>SMA50, SMA20 rising vs 3h ago; volume>=0.8*prior20 mean; breakout above prior6 highs OR pullback low<=SMA20 and bullish close>SMA20 and previous close',
    'entry_bar_max_age_seconds': 900, 'entry_limit_rolling_24h': 2,
    'entry_spread_max': 0.003, 'risk_budget': 'min(10 EUR,2% net liquidating equity)',
    'max_position_all_in': '80% net liquidating equity; one position',
    'initial_stop_distance': 'max(3%,2.5*ATR14/entry); skip above6%',
    'take_profit': 'Sell half at observed bid >= entry*1.06; rest has no fixed profit ceiling',
    'after_partial': 'Cost-adjusted breakeven stop and 3% trailing from highest OBSERVED bid',
    'trend_exit': 'Two latest closed 1h closes below their SMA20',
    'max_hold_hours': 72, 'cooldown_after_close_hours': 3,
    'three_consecutive_losses_pause_hours': 24,
    'daily_loss_pause': '4% below local-day starting net liquidation equity; close and pause until next local day',
    'experiment_stop': 'Net liquidation equity<=450 EUR triggers full exit and permanent halt',
    'loss_limit_note': 'Triggers, not guaranteed loss ceilings: fills occur at actual later observed quotes',
    'poll_target_seconds': 300, 'fills': 'Walk observed book, then adverse slippage and fees. No invented intrabar or missed-poll executions',
    'end_policy': 'No new entries in last hour; close at first valid observation at/after fixed end; disclose delay',
    'sources': ['https://www.kraken.com/features/fee-schedule', 'https://docs.kraken.com/api-reference/market-data/get-ohlc-data'],
    'excluded': ['tax', 'hosting cost', 'future account-specific fees', 'actual order matching/queue'],
}
PHASH = hashlib.sha256(json.dumps(PROTOCOL, sort_keys=True).encode()).hexdigest()

def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace('+00:00', 'Z')

def fresh_state():
    return {'id': ID, 'protocol_hash': PHASH, 'status': 'ARMED', 'cash_eur': 500.0,
            'position': None, 'fills': [], 'closed_trades': [], 'benchmarks': {},
            'equity_eur': 500.0, 'peak_equity_eur': 500.0, 'max_drawdown_pct': 0.0,
            'fees_eur': 0.0, 'success_count': 0, 'failure_count': 0,
            'consecutive_failures': 0, 'gaps': [], 'halted': False,
            'pause_until': 0, 'loss_streak': 0, 'last_signal_bar': None,
            'last_observed': None, 'first_active_observed': None, 'day': None,
            'day_start_equity': 500.0, 'entries': [], 'final': False}

def get_public(endpoint, **params):
    assert endpoint in {'Time', 'SystemStatus', 'AssetPairs', 'Depth', 'OHLC'}
    url = 'https://api.kraken.com/0/public/' + endpoint
    if params:
        url += '?' + urllib.parse.urlencode(params)
    began = time.time()
    req = urllib.request.Request(url, headers={'User-Agent': 'SOL500-paper-only/1.0', 'Cache-Control': 'no-cache'})
    with urllib.request.urlopen(req, timeout=18) as r:
        data = json.load(r)
        headers = dict(r.headers)
    ended = time.time()
    if data.get('error') or not isinstance(data.get('result'), dict):
        raise ValueError('Public API error: ' + str(data.get('error')))
    return data['result'], {'url': url, 'requested_at': iso(began), 'received_at': iso(ended),
                            'received_unix': ended, 'http_date': headers.get('Date'),
                            'elapsed_seconds': ended-began}

def fetch_snapshot():
    clock, csrc = get_public('Time')
    if abs(time.time()-float(clock['unixtime'])) > 30:
        raise ValueError('Server/local clock disagreement >30 seconds')
    system, ssrc = get_public('SystemStatus')
    meta, msrc = get_public('AssetPairs', pair='SOLEUR', country_code='SI', execution_venue='international')
    m = meta.get('SOLEUR')
    if not m or m.get('wsname') != 'SOL/EUR':
        raise ValueError('Wrong/missing market metadata')
    hour, hsrc = get_public('OHLC', pair='SOLEUR', interval=60)
    book, bsrc = get_public('Depth', pair='SOLEUR', count=100)
    now = time.time()
    rows = hour.get('SOLEUR', [])
    closed = [[float(v) for v in row] for row in rows if float(row[0])+3600 <= now]
    closed = closed[-80:]
    if len(closed) < 54 or any(closed[i][0]-closed[i-1][0] != 3600 for i in range(1, len(closed))):
        raise ValueError('Insufficient or gapped closed hourly bars')
    if now-(closed[-1][0]+3600) > 3900:
        raise ValueError('Stale hourly market data')
    b = book.get('SOLEUR', {})
    asks = [[float(x[0]), float(x[1])] for x in b.get('asks', [])]
    bids = [[float(x[0]), float(x[1])] for x in b.get('bids', [])]
    if not asks or not bids or not 0 < bids[0][0] <= asks[0][0]:
        raise ValueError('Missing/crossed order book')
    if any(not math.isfinite(p) or not math.isfinite(q) or p <= 0 or q <= 0 for p,q in asks+bids):
        raise ValueError('Invalid order book')
    if now-bsrc['received_unix'] > 30 or bsrc['elapsed_seconds'] > 10:
        raise ValueError('Order book transport too slow')
    return {'id': ID, 'observed_unix': now, 'observed_at': iso(now),
            'system_status': system['status'], 'metadata': m, 'asks': asks, 'bids': bids,
            'closed_1h': closed, 'sources': {'clock': csrc, 'system': ssrc, 'metadata': msrc,
                                           'hourly': hsrc, 'book': bsrc}}

def book_price(levels, qty):
    left, total = qty, 0.0
    for p, q in levels:
        take = min(left, q)
        total += p*take
        left -= take
        if left <= 1e-10:
            return total/qty
    raise ValueError('Insufficient observed order book for modelled fill')

def floor_qty(q, dp):
    return math.floor(q * 10**dp) / 10**dp

def liquidating(s, x):
    p = s['position']
    return s['cash_eur'] if not p else s['cash_eur']+p['qty']*book_price(x['bids'],p['qty'])*(1-SLIP)*(1-FEE)

def technical(rows):
    c = [r[4] for r in rows]
    ma20 = statistics.mean(c[-20:])
    ma50 = statistics.mean(c[-50:])
    prior20 = statistics.mean(c[-21:-1])
    slope = ma20 > statistics.mean(c[-23:-3])
    volume = rows[-1][6] >= 0.8*statistics.mean(r[6] for r in rows[-21:-1])
    breakout = c[-1] > max(r[2] for r in rows[-7:-1])
    pullback = rows[-1][3] <= ma20 and c[-1] > ma20 and c[-1] > rows[-1][1] and c[-1] > c[-2]
    tr = [max(rows[i][2]-rows[i][3], abs(rows[i][2]-rows[i-1][4]), abs(rows[i][3]-rows[i-1][4])) for i in range(1,len(rows))]
    return {'buy': c[-1]>ma50 and ma20>ma50 and slope and volume and (breakout or pullback),
            'exit': c[-1]<ma20 and c[-2]<prior20, 'atr14': statistics.mean(tr[-14:]),
            'sma20': ma20, 'sma50': ma50, 'volume_pass': volume, 'breakout': breakout,
            'pullback': pullback, 'close': c[-1], 'bar_end': rows[-1][0]+3600}

def add_fill(s, x, side, q, reason):
    levels = x['asks'] if side=='BUY' else x['bids']
    raw = book_price(levels,q)
    price = raw*(1+SLIP if side=='BUY' else 1-SLIP)
    gross, fee = q*price, q*price*FEE
    f = {'number': len(s['fills'])+1, 'id': f'{ID}-{len(s["fills"])+1}',
         'time': x['observed_at'], 'unix': x['observed_unix'], 'side': side,
         'quantity_sol': q, 'observed_vwap_eur': raw, 'model_price_eur': price,
         'fee_eur': fee, 'gross_eur': gross, 'cash_delta_eur': -gross-fee if side=='BUY' else gross-fee,
         'reason': reason, 'status': 'MODELLED_PAPER_FILL_NOT_EXECUTED',
         'snapshot': x['observed_at'], 'protocol_hash': PHASH}
    s['fills'].append(f)
    s['fees_eur'] += fee
    s['cash_eur'] += f['cash_delta_eur']
    return f

def sell(s,x,q,reason):
    p = s['position']
    q = min(q,p['qty'])
    f = add_fill(s,x,'SELL',q,reason)
    p['proceeds'] += f['cash_delta_eur']
    p['qty'] -= q
    if p['qty'] <= 1e-10:
        pnl = p['proceeds']-p['initial_cost']
        s['closed_trades'].append({'entry': p['opened_at'], 'exit':x['observed_at'], 'pnl_eur':pnl,
                                   'initial_cost_eur':p['initial_cost'], 'reason':reason})
        s['loss_streak'] = s['loss_streak']+1 if pnl < 0 else 0
        s['pause_until'] = max(s['pause_until'], x['observed_unix']+(86400 if s['loss_streak']>=3 else 10800))
        s['position'] = None
    return f

def evaluate(previous,x):
    s = copy.deepcopy(previous)
    if s['id'] != ID or s['protocol_hash'] != PHASH:
        raise ValueError('Protocol/state mismatch; never reset or retune existing trial')
    if s['final']:
        return s
    now = x['observed_unix']
    if s['last_observed'] and now <= s['last_observed']:
        return s
    if s['last_observed'] and now-s['last_observed']>600:
        s['gaps'].append({'from':iso(s['last_observed']), 'to':iso(now), 'seconds':now-s['last_observed'],
                          'note':'No invented orders or stops during gap'})
    s['last_observed'],s['last_observed_at'] = now,x['observed_at']
    s['success_count'] += 1
    s['consecutive_failures'] = 0
    s['data_health'] = 'DATA_OK'
    s['latest_bid'],s['latest_ask'] = x['bids'][0][0],x['asks'][0][0]
    s['status'] = 'ARMED' if now<START else 'ACTIVE'
    if now<START:
        return s
    if not s['first_active_observed']:
        s['first_active_observed'] = now
        s['activation_delay_seconds'] = max(0,now-START)
        for amount in [500.0,400.0]:
            q = floor_qty(amount/(x['asks'][0][0]*(1+SLIP)*(1+FEE)),8)
            for _ in range(3):
                price = book_price(x['asks'],q)*(1+SLIP)
                q = floor_qty(min(q,amount/(price*(1+FEE))),8)
            cost = q*book_price(x['asks'],q)*(1+SLIP)*(1+FEE)
            s['benchmarks'][str(int(amount))] = {'qty':q,'cash':500-cost,'start':x['observed_at'],
                                               'label':'Independent buy-and-hold comparison; not bot funds'}
    equity = liquidating(s,x)
    day = datetime.fromtimestamp(now,ZoneInfo('Europe/Ljubljana')).date().isoformat()
    if s['day'] != day:
        s['day'],s['day_start_equity'] = day,equity
        s['daily_paused'] = False
    if equity<=450:
        s['halted'] = True
    if equity<=s['day_start_equity']*0.96:
        s['daily_paused'] = True
    tech = technical(x['closed_1h'])
    s['latest_signal_inputs'] = tech
    is_new_bar = s['last_signal_bar'] != tech['bar_end']
    had_position = s['position'] is not None
    p = s['position']
    bid = x['bids'][0][0]
    if p:
        reason = None
        if now>=END: reason='FIXED_TRIAL_END'
        elif s['halted']: reason='TOTAL_LOSS_TRIGGER'
        elif s.get('daily_paused'): reason='DAILY_LOSS_TRIGGER'
        elif bid<=p['stop']: reason='OBSERVED_STOP'
        elif now-p['opened_unix']>=72*3600: reason='MAX_72H_HOLD'
        elif is_new_bar and tech['exit']: reason='TWO_CLOSES_BELOW_SMA20'
        if reason:
            sell(s,x,p['qty'],reason)
        elif not p['partial'] and bid>=p['entry']*1.06:
            q = floor_qty(p['qty']/2,int(x['metadata']['lot_decimals']))
            if q>=float(x['metadata']['ordermin']) and p['qty']-q>=float(x['metadata']['ordermin']):
                sell(s,x,q,'HALF_AT_OBSERVED_PLUS_6_PERCENT')
                p = s['position']
                p['partial'] = True
                p['peak_bid'] = bid
                p['stop'] = max(p['stop'],p['entry']*(1+FEE)/((1-FEE)*(1-SLIP)))
        if s['position'] and s['position']['partial']:
            p=s['position']
            p['peak_bid']=max(p['peak_bid'],bid)
            p['stop']=max(p['stop'],p['peak_bid']*0.97)
    if is_new_bar:
        s['last_signal_bar'] = tech['bar_end']
    if (not had_position and not s['position'] and not s['halted'] and not s.get('daily_paused')
        and START<=now<END-3600 and now>=s['pause_until'] and is_new_bar
        and 0<=now-tech['bar_end']<=900 and tech['buy']
        and x['system_status']=='online' and x['metadata']['status']=='online'):
        spread=(x['asks'][0][0]-bid)/((x['asks'][0][0]+bid)/2)
        recent=[v for v in s['entries'] if now-v<86400]
        entry=x['asks'][0][0]*(1+SLIP)
        d=max(0.03,2.5*tech['atr14']/entry)
        if spread<=0.003 and len(recent)<2 and d<=0.06:
            risk=min(10,equity*0.02)
            risk_per_eur=1-(1-d)*(1-SLIP)*(1-FEE)/(1+FEE)
            budget=min(s['cash_eur'],equity*0.8,risk/risk_per_eur)
            q=floor_qty(budget/(entry*(1+FEE)),int(x['metadata']['lot_decimals']))
            for _ in range(3):
                entry=book_price(x['asks'],q)*(1+SLIP)
                q=floor_qty(min(q,budget/(entry*(1+FEE))),int(x['metadata']['lot_decimals']))
            if q>=float(x['metadata']['ordermin']) and q*entry>=float(x['metadata'].get('costmin',0)):
                f=add_fill(s,x,'BUY',q,'FROZEN_TREND_ENTRY')
                entry=f['model_price_eur']
                s['position']={'qty':q,'initial_qty':q,'entry':entry,'stop':entry*(1-d),
                               'distance':d,'partial':False,'peak_bid':bid,'proceeds':0,
                               'initial_cost':-f['cash_delta_eur'],'opened_at':x['observed_at'],
                               'opened_unix':now,'planned_loss_eur':budget*risk_per_eur}
                s['entries'].append(now)
    s['equity_eur']=liquidating(s,x)
    s['peak_equity_eur']=max(s['peak_equity_eur'],s['equity_eur'])
    dd=100*(1-s['equity_eur']/s['peak_equity_eur'])
    s['max_drawdown_pct']=max(s['max_drawdown_pct'],dd)
    for b in s['benchmarks'].values():
        b['net_liquidation_eur']=b['cash']+b['qty']*book_price(x['bids'],b['qty'])*(1-SLIP)*(1-FEE)
    if now>=END:
        s['final']=True
        s['status']='COMPLETED'
        s['final_observed_at']=x['observed_at']
        s['final_delay_seconds']=now-END
    elif s['halted']:
        s['status']='HALTED_LOSS_TRIGGER'
    assert s['cash_eur']>=-1e-7
    return s

def write_json(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    tmp.replace(path)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--state-dir',required=True)
    args=ap.parse_args()
    root=Path(args.state_dir)
    root.mkdir(parents=True,exist_ok=True)
    path=root/'state.json'
    s=json.loads(path.read_text()) if path.exists() else fresh_state()
    if s['protocol_hash']!=PHASH: raise ValueError('Frozen protocol mismatch')
    write_json(root/'protocol.json',PROTOCOL)
    if s['final']: return
    now=time.time()
    try:
        x=fetch_snapshot()
        name=datetime.fromtimestamp(x['observed_unix'],timezone.utc).strftime('%Y%m%dT%H%M%SZ.json')
        write_json(root/'observations'/name,x)
        s=evaluate(s,x)
        s['last_snapshot']='observations/'+name
        s['last_error']=None
    except Exception as exc:
        s['failure_count']+=1
        s['consecutive_failures']+=1
        s['data_health']='DATA_UNAVAILABLE'
        s['last_error']={'time':iso(now),'message':str(exc)[:700]}
        # Existing positions and all prior records remain unchanged. Never assume a stop filled.
    s['engine_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    s['last_attempt_at']=iso(now)
    write_json(path,s)
    report={k:s.get(k) for k in ['id','status','data_health','first_active_observed','last_observed_at',
            'cash_eur','equity_eur','position','fees_eur','max_drawdown_pct','success_count','failure_count',
            'consecutive_failures','gaps','benchmarks','closed_trades','last_error','final','final_delay_seconds']}
    report.update({'generated_at':iso(time.time()),'net_pnl_eur':s['equity_eur']-500,
                   'fill_count':len(s['fills']),'protocol_hash':PHASH,'paper_only':True,
                   'real_orders':False,'sampling':'Observed quotes, nominal 5min; not continuous stops'})
    write_json(root/'report.json',report)
    print(json.dumps(report,indent=2))

if __name__=='__main__': main()
