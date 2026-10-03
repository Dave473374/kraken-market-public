"""Frozen, prospective paper research. Pure functions; no exchange/order I/O."""
from __future__ import annotations
import copy
import hashlib
import json
import math
import statistics
from datetime import datetime, timezone

SCHEMA = 'CRYPTO_V23_SHADOW_STATE_V1'
WORKER = 'KRAKEN_PUBLIC_TRANSPORT_V2.0.2'
STEP = 14400
BLOCKED_BASES = {'EUR','USD','USDC','USDT','DAI','USDG','CHF','GBP','CAD','AUD','JPY','PAXG','WBTC','WETH','STETH'}


def num(value):
    if isinstance(value, bool):
        raise ValueError('BOOLEAN_NUMBER')
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('NONFINITE_NUMBER')
    return result


def ts(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('NAIVE_TIME')
    return stamp.timestamp()


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def canon(pair):
    p = str(pair or '').upper().replace('/', '').strip()
    return {'XDGEUR':'DOGEEUR'}.get(p, p)


def fresh(value, now, limit=600):
    try:
        return 0 <= now-ts(value) <= limit
    except (TypeError, ValueError, AttributeError, OverflowError):
        return False


def source_ok(source, now, limit=600):
    return isinstance(source, dict) and source.get('ok') is True and fresh(source.get('retrieved_at'), now, limit)


def read_observation(body, pair, now, protocol):
    """Strictly validate actual rows; counts/status labels alone are insufficient."""
    try:
        if not isinstance(body, dict) or body.get('schema') != WORKER:
            raise ValueError('SCHEMA')
        if any(body.get(k) is not False for k in ('contains_account_data','automatic_trading','private_exchange_api')):
            raise ValueError('UNSAFE_SOURCE')
        if body.get('data_health') != 'DATA_OK' or body.get('pair_error') or body.get('blocking_data_reasons'):
            raise ValueError('PARTIAL_SOURCE')
        meta, spread, depth, hist, fx = [body[k] for k in ('pair_metadata','timestamped_spread','depth','closed_4h','fx')]
        if canon(meta.get('altname') or meta.get('pair_key')) != canon(pair):
            raise ValueError('PAIR_MISMATCH')
        if meta.get('status') != 'online' or meta.get('country_filter') != 'SI' or meta.get('execution_venue') != 'international':
            raise ValueError('METADATA_ELIGIBILITY')
        if meta.get('aclass_base') != 'currency' or meta.get('quote_display') not in ('EUR','USD','USDC'):
            raise ValueError('ASSET_CLASS')
        asset = meta.get('base_display') or meta.get('asset')
        if not isinstance(asset, str) or asset in BLOCKED_BASES or asset.endswith(('.d','3L','3S','UP','DOWN')):
            raise ValueError('EXCLUDED_BASE')
        limit = protocol['quote_max_age_seconds']
        if not fresh(body.get('generated_at'), now, limit):
            raise ValueError('STALE_ENVELOPE')
        for module in (spread, depth, hist):
            if module.get('quality') != 'VERIFIED' or not source_ok(module.get('source'), now, limit):
                raise ValueError('STALE_OR_MISSING_SOURCE')
        if not source_ok(body.get('metadata_source'), now, limit) or not fresh(spread.get('observed_at'), now, limit):
            raise ValueError('STALE_METADATA_OR_SPREAD')
        bid, ask = num(depth['best_bid']), num(depth['best_ask'])
        sb, sa = num(spread['bid']), num(spread['ask'])
        if not (0 < bid <= ask and 0 < sb <= sa):
            raise ValueError('INVALID_BOOK')
        if abs((bid+ask)/(sb+sa)-1)*100 > 2:
            raise ValueError('INTERNAL_PRICE_CONFLICT')
        if fx.get('quality') != 'VERIFIED':
            raise ValueError('FX_MISSING')
        if meta['quote_display'] == 'EUR':
            if fx.get('conversion') != 'IDENTITY':
                raise ValueError('FX_NOT_IDENTITY')
            fx_bid = fx_ask = 1.0
        else:
            if not source_ok(fx.get('source'), now, limit) or not fresh(fx.get('observed_at'), now, limit):
                raise ValueError('FX_STALE')
            fx_bid, fx_ask = num(fx['eur_per_quote_bid']), num(fx['eur_per_quote_ask'])
            if not 0 < fx_bid <= fx_ask:
                raise ValueError('FX_INVALID')
        if hist.get('interval_minutes') != 240 or hist.get('unfinished_final_bar_excluded') is not True or hist.get('history_has_gaps') is not False:
            raise ValueError('HISTORY_FLAGS')
        rows = hist['recent_closed_rows']
        if len(rows) < 51:
            raise ValueError('INSUFFICIENT_EXPORTED_BARS')
        rows = [[num(x) for x in row[:8]] for row in rows]
        for i, row in enumerate(rows):
            if len(row) != 8 or row[0] % STEP or row[0]+STEP > now:
                raise ValueError('UNFINISHED_OR_MALFORMED_BAR')
            if not (0 < row[3] <= min(row[1], row[4]) <= max(row[1], row[4]) <= row[2]) or row[6] < 0:
                raise ValueError('INVALID_OHLCV')
            if i and row[0]-rows[i-1][0] != STEP:
                raise ValueError('GAPPED_OR_DUPLICATE_BARS')
        end = rows[-1][0]+STEP
        if end != math.floor(now/STEP)*STEP or ts(hist['latest_closed_bar_end']) != end:
            raise ValueError('LATEST_CLOSED_BAR_MISSING')
        prior = [sum(r[6] for r in rows[-6*(i+2):-6*(i+1)]) for i in range(7)]
        median = statistics.median(prior)
        volume_ratio = sum(r[6] for r in rows[-6:])/median if median > 0 else None
        atr = statistics.mean(max(rows[i][2]-rows[i][3], abs(rows[i][2]-rows[i-1][4]), abs(rows[i][3]-rows[i-1][4])) for i in range(len(rows)-14,len(rows)))
        if atr <= 0:
            raise ValueError('ZERO_ATR')
        sides = depth['depth_each_side']
        depth_eur = {s: num(sides[s]['notional_quote_lower_bound'])*fx_bid for s in ('bids','asks')}
        if any(v < 0 for v in depth_eur.values()):
            raise ValueError('INVALID_DEPTH')
        ticker = body['ticker']
        if not source_ok(ticker.get('source'), now, limit):
            raise ValueError('STALE_TURNOVER')
        turnover = num(ticker['turnover_24h_quote'])*fx_bid
        out = {'pair':canon(pair), 'asset':asset, 'quote':meta['quote_display'],
               'bid':bid, 'ask':ask, 'bid_eur':bid*fx_bid, 'ask_eur':ask*fx_ask,
               'observed_at':spread['observed_at'], 'retrieved_at':depth['source']['retrieved_at'],
               'source_url':depth['source']['source_url'], 'source_hash':digest(body),
               'evidence_oldest_at':iso(min([ts(spread['observed_at']), ts(body['metadata_source']['retrieved_at']),
                   ts(spread['source']['retrieved_at']), ts(depth['source']['retrieved_at']),
                   ts(hist['source']['retrieved_at']), ts(ticker['source']['retrieved_at'])]
                   + ([] if meta['quote_display']=='EUR' else [ts(fx['observed_at']),ts(fx['source']['retrieved_at'])]))),
               'rows':rows, 'bar_end':end, 'atr':atr, 'volume_ratio':volume_ratio,
               'closed24_pct':(rows[-1][4]/rows[-7][4]-1)*100,
               'spread_pct':(ask-bid)/((ask+bid)/2)*100,
               'depth_eur':depth_eur, 'pair_turnover_eur':turnover,
               'entry_exclusion':body.get('new_discovery_exclusion'),
               'account_specific_eligibility':'NOT_CHECKED', 'convert_quote':'NOT_OBSERVED',
               'independent_crosscheck':'NOT_COLLECTED_RESEARCH_ONLY'}
        return out, None
    except (KeyError, ValueError, TypeError, AttributeError, IndexError, ZeroDivisionError, OverflowError) as exc:
        return None, str(exc)[:160]


def setup(obs, protocol):
    rows, atr = obs['rows'], obs['atr']
    close = rows[-1][4]
    sma20 = statistics.mean(r[4] for r in rows[-20:])
    sma50 = statistics.mean(r[4] for r in rows[-50:])
    if obs['asset'] in protocol['observe_only_assets'] or obs['entry_exclusion']:
        return None
    if not close > sma20 > sma50 or obs['closed24_pct'] >= protocol['max_new_24h_gain_pct']:
        return None
    if obs['ask'] > min(close+0.5*atr,close*1.02):
        return None
    level = max(r[2] for r in rows[-22:-2])
    name, stop = None, None
    if rows[-2][4] > level and level-0.5*atr <= rows[-1][3] <= level+0.3*atr and close >= level:
        name, stop = 'BREAKOUT_RETEST', min(r[3] for r in rows[-2:])-0.25*atr
    elif rows[-2][3] <= sma20+0.25*atr and rows[-2][3] > sma50 and close > rows[-2][2]:
        name, stop = 'TREND_PULLBACK', min(r[3] for r in rows[-3:])-0.25*atr
    else:
        ceiling, floor = max(r[2] for r in rows[-7:-1]), min(r[3] for r in rows[-7:-1])
        if ceiling-floor <= 2*atr and close > ceiling and rows[-1][2]-rows[-1][3] <= 2*atr:
            name, stop = 'CONSOLIDATION_BREAKOUT', floor-0.25*atr
    if not name or stop <= 0:
        return None
    risk_pct = (obs['ask']-stop)/obs['ask']*100
    if not protocol['min_stop_distance_pct'] <= risk_pct <= protocol['max_stop_distance_pct']:
        return None
    # Common cost-aware gate for all exit variants; no fitted cost estimate.
    base_roundtrip = 2*protocol['cost_scenarios_per_side']['BASE']*100+obs['spread_pct']
    if 2*risk_pct < protocol['min_target_room_over_base_cost_multiple']*base_roundtrip:
        return None
    return {'family':name, 'stop':stop, 'max_entry':min(close+0.5*atr,close*1.02),
            'bar_end':obs['bar_end'], 'atr':atr}


def liquidity_pass(obs, protocol):
    # Pair turnover is a lower bound on aggregate turnover. This conservative
    # rule never fabricates totals from inaccessible pairs or stale FX.
    return (obs['spread_pct'] <= protocol['max_spread_pct']
            and obs['pair_turnover_eur'] >= max(protocol['min_pair_turnover_eur'], protocol['min_aggregate_turnover_eur'])
            and min(obs['depth_eur'].values()) >= protocol['depth_multiple']*protocol['synthetic_entry_budget_eur'])


def initial_state(protocol, now):
    return {'schema':SCHEMA, 'protocol_hash':digest(protocol), 'started_at':iso(now),
            'last_run_at':None, 'run_count':0, 'pending':{}, 'cohorts':{}, 'last_sources':{},
            'seen_setups':[], 'last_entry':{}, 'retained_candidates':{}, 'discovery_cursor':0,
            'equity':{}, 'gap_count':0, 'events_total':0, 'latest_marks':{}}


def validate_state(state, protocol):
    if state.get('schema') != SCHEMA or state.get('protocol_hash') != digest(protocol):
        raise ValueError('STATE_OR_FROZEN_PROTOCOL_MISMATCH_NO_RESET')
    if any(k not in state for k in ('pending','cohorts','last_sources','seen_setups','last_entry','equity')):
        raise ValueError('INCOMPLETE_STATE_NO_RESET')


def path_value(cohort, path, cost, mark=None):
    budget, entry = cohort['budget_eur'], cohort['entry_ask_eur']
    proceeds = sum(budget*x['fraction']*x['bid_eur']/entry*(1-cost)/(1+cost) for x in path['exits'])
    if path['remaining'] > 0:
        if mark is None:
            return None
        proceeds += budget*path['remaining']*mark/entry*(1-cost)/(1+cost)
    return proceeds-budget


def free_cash(state, protocol, variant, cost):
    cash = protocol['synthetic_initial_cash_eur']
    for cohort in state['cohorts'].values():
        cash -= cohort['budget_eur']
        cash += sum(cohort['budget_eur']*x['fraction']*x['bid_eur']/cohort['entry_ask_eur']*(1-cost)/(1+cost) for x in cohort['paths'][variant]['exits'])
    return cash


def advance(state, observations, protocol, now):
    validate_state(state, protocol)
    state = copy.deepcopy(state)
    if state['last_run_at'] and now <= ts(state['last_run_at']):
        raise ValueError('NON_MONOTONIC_RUN_TIME')
    events = []
    def event(kind, **data):
        state['events_total'] += 1
        events.append({'sequence':state['events_total'], 'event_id':f"{protocol['experiment_id']}:{state['events_total']}",
                       'at':iso(now), 'kind':kind, 'simulated':True, 'actual_execution':False, **data})
    if state['last_run_at'] and now-ts(state['last_run_at']) > protocol['observation_gap_seconds']:
        state['gap_count'] += 1
        event('OBSERVATION_GAP', seconds=now-ts(state['last_run_at']))
    valid = {}
    for pair, obs in observations.items():
        if not all(fresh(obs.get(k), now, protocol['quote_max_age_seconds']) for k in ('retrieved_at','observed_at','evidence_oldest_at')):
            continue
        source_time = ts(obs['retrieved_at'])
        if source_time <= state['last_sources'].get(pair,0):
            continue
        state['last_sources'][pair] = source_time
        state['latest_marks'][pair] = {'bid_eur':obs['bid_eur'], 'retrieved_at':obs['retrieved_at']}
        valid[pair] = obs
    # Existing virtual positions first. No retrospective fills at intrabar highs/lows.
    for cid, cohort in state['cohorts'].items():
        obs = valid.get(cohort['pair'])
        if obs is None or ts(obs['retrieved_at']) <= ts(cohort['entered_at']):
            continue
        for variant, path in cohort['paths'].items():
            if path['remaining'] <= 0:
                continue
            bid, entry, risk = obs['bid'], cohort['entry_ask'], cohort['initial_risk']
            elapsed = now-ts(cohort['entered_at'])
            new_close = obs['bar_end'] > ts(cohort['entered_at']) and obs['bar_end'] > path['last_bar_end']
            stop = cohort['setup']['stop'] if variant in ('STRUCTURE_2R','TIME_24H') else path['trail_stop']
            reason, fraction = None, path['remaining']
            if variant != 'HOLD_30D' and (bid <= stop or (new_close and obs['rows'][-1][4] <= stop)):
                reason = 'OBSERVED_INVALIDATION'
            elif elapsed >= 30*86400:
                reason = 'MAX_30D_OBSERVED_EXIT'
            elif variant in ('STRUCTURE_2R','TIME_24H') and bid >= entry+2*risk:
                reason = 'OBSERVED_2R'
            elif variant == 'HALF_1_5R_TRAIL' and not path['partial_done'] and bid >= entry+1.5*risk:
                reason, fraction = 'OBSERVED_HALF_1_5R', 0.5
            elif variant == 'TIME_24H' and elapsed >= 86400 and bid-entry < 0.5*risk:
                reason = 'TIME_STOP_NO_PROGRESS'
            if reason:
                path['exits'].append({'at':iso(now),'fraction':fraction,'bid_eur':obs['bid_eur'],
                                      'reason':reason,'source_hash':obs['source_hash'], 'quote_retrieved_at':obs['retrieved_at']})
                path['remaining'] = max(0, path['remaining']-fraction)
                path['partial_done'] = True
                event('VIRTUAL_EXIT', cohort_id=cid, pair=cohort['pair'], variant=variant,
                      fraction=fraction, reason=reason, bid_eur=obs['bid_eur'])
            # Update only AFTER exit evaluation, avoiding an assumed intrabar order.
            path['highest_observed'] = max(path['highest_observed'],bid)
            if variant in ('HALF_1_5R_TRAIL','TRAIL_ONLY') and (variant == 'TRAIL_ONLY' or path['partial_done']):
                multiple = 2.0 if variant == 'HALF_1_5R_TRAIL' else 2.5
                path['trail_stop'] = max(path['trail_stop'], path['highest_observed']-multiple*obs['atr'])
            path['last_bar_end'] = max(path['last_bar_end'],obs['bar_end'])
    # A decision becomes a virtual entry only at a later, independently collected observation.
    for cid, pending in list(state['pending'].items()):
        if now-ts(pending['decision_at']) > protocol['pending_expiry_seconds']:
            event('PENDING_EXPIRED',cohort_id=cid,pair=pending['pair'])
            del state['pending'][cid]
            continue
        obs = valid.get(pending['pair'])
        if obs is None or ts(obs['retrieved_at']) <= ts(pending['decision_at']):
            continue
        if not liquidity_pass(obs,protocol):
            continue
        stop, ask = pending['setup']['stop'], obs['ask']
        risk_pct = (ask-stop)/ask*100
        if not stop < ask <= pending['setup']['max_entry'] or not protocol['min_stop_distance_pct'] <= risk_pct <= protocol['max_stop_distance_pct']:
            event('PENDING_INVALIDATED',cohort_id=cid,pair=pending['pair'])
            del state['pending'][cid]
            continue
        budget = protocol['synthetic_entry_budget_eur']
        enough = all(free_cash(state,protocol,v,c) >= budget for v in protocol['exit_variants'] for c in protocol['cost_scenarios_per_side'].values())
        if not enough:
            continue
        cohort = {**pending,'entered_at':iso(now),'entry_ask':ask,'entry_ask_eur':obs['ask_eur'],
                  'entry_source_hash':obs['source_hash'],'entry_quote_retrieved_at':obs['retrieved_at'],
                  'initial_risk':ask-stop,'budget_eur':budget,'paths':{}}
        for variant in protocol['exit_variants']:
            cohort['paths'][variant] = {'remaining':1.0,'partial_done':False,'exits':[],
                                       'highest_observed':obs['bid'],'trail_stop':stop,'last_bar_end':obs['bar_end']}
        state['cohorts'][cid] = cohort
        state['last_entry'][cohort['pair']] = now
        del state['pending'][cid]
        event('VIRTUAL_ENTRY',cohort_id=cid,pair=cohort['pair'],family=pending['setup']['family'],
              ask_eur=obs['ask_eur'],budget_eur=budget)
    open_cohorts = [c for c in state['cohorts'].values() if any(p['remaining'] > 0 for p in c['paths'].values())]
    occupied = {c['pair'] for c in open_cohorts} | {p['pair'] for p in state['pending'].values()}
    created = 0
    for pair, obs in valid.items():
        if created >= protocol['max_new_setups_per_run'] or len(open_cohorts)+len(state['pending']) >= protocol['max_open_cohorts']:
            break
        if pair in occupied or now-state['last_entry'].get(pair,0) < protocol['reentry_cooldown_seconds'] or not liquidity_pass(obs,protocol):
            continue
        idea = setup(obs,protocol)
        if idea is None:
            continue
        cid = digest([protocol['experiment_id'],pair,idea['bar_end'],idea['family']])[:24]
        if cid in state['seen_setups']:
            continue
        state['seen_setups'].append(cid)
        state['pending'][cid] = {'pair':pair,'asset':obs['asset'],'decision_at':iso(now),'setup':idea,
                                 'decision_source_hash':obs['source_hash'],'decision_quote_retrieved_at':obs['retrieved_at']}
        event('PENDING_ENTRY',cohort_id=cid,pair=pair,family=idea['family'])
        occupied.add(pair); created += 1
    state['last_run_at'], state['run_count'] = iso(now), state['run_count']+1
    return state, events


def report(state, protocol, now, coverage):
    books = {}
    for variant in protocol['exit_variants']:
        books[variant] = {}
        for name,cost in protocol['cost_scenarios_per_side'].items():
            closed, marked, complete, drag = [], [], True, 0.0
            for c in state['cohorts'].values():
                p = c['paths'][variant]
                mark = state['latest_marks'].get(c['pair'],{})
                price = mark.get('bid_eur') if fresh(mark.get('retrieved_at'),now,protocol['quote_max_age_seconds']) else None
                value = path_value(c,p,cost,price)
                if value is None:
                    complete = False
                else:
                    marked.append(value)
                    drag += path_value(c,p,0,price)-value
                if p['remaining'] == 0:
                    closed.append(path_value(c,p,cost))
            gains, losses = sum(x for x in closed if x>0), -sum(x for x in closed if x<0)
            equity = protocol['synthetic_initial_cash_eur']+sum(marked) if complete else None
            key = variant+':'+name
            history = state['equity'].setdefault(key,{'peak':protocol['synthetic_initial_cash_eur'],'max_drawdown_pct':0.0})
            if equity is not None:
                history['peak'] = max(history['peak'],equity)
                history['max_drawdown_pct'] = max(history['max_drawdown_pct'],100*(history['peak']-equity)/history['peak'])
            books[variant][name] = {'closed_cohorts':len(closed),'net_pnl_closed_cohorts_eur_estimate':sum(closed),
                'mean_closed_pnl_eur_estimate':statistics.mean(closed) if closed else None,
                'profit_factor_estimate':gains/losses if losses>0 else None,
                'win_rate_pct':100*sum(x>0 for x in closed)/len(closed) if closed else None,
                'equity_eur_estimate':equity,'marks_complete':complete,
                'cost_drag_eur_estimate':drag if complete else None,
                'observed_max_drawdown_pct_lower_bound':history['max_drawdown_pct'],
                'drawdown_caveat':'Hourly observations can miss intraperiod drawdowns.'}
    paired = sum(all(p['remaining']==0 for p in c['paths'].values()) for c in state['cohorts'].values())
    return {'schema':'CRYPTO_V23_SHADOW_REPORT_V1','role':'RESEARCH_ONLY','generated_at':iso(now),
            'started_at':state['started_at'],'run_count':state['run_count'],'protocol_hash':state['protocol_hash'],
            'automatic_trading':False,'production_changes_allowed':False,'auto_promotion':False,
            'realized_user_pnl':None,'production_v22_comparison':'UNAVAILABLE_NO_ACTUAL_SIGNAL_HISTORY_IMPORTED',
            'convert_execution':'UNOBSERVED_COST_SENSITIVITY_ONLY','independent_crosscheck':'NOT_COLLECTED',
            'market_coverage':coverage,'pending_setups':len(state['pending']),'unique_entry_cohorts':len(state['cohorts']),
            'fully_closed_paired_cohorts':paired,'observation_gaps':state['gap_count'],
            'evidence_stage':'REVIEW_CHECKPOINT_NOT_PROOF' if paired>=protocol['paired_review_checkpoint'] else 'COLLECTING',
            'variant_cost_paths_are_not_independent_trades':True,'books':books,
            'limitations':['No actual trades or account balances in this dataset.',
                'Synthetic portfolios cover watched assets and sampled public discovery; not the user portfolio.',
                'No retroactive fills or guaranteed stops. Exits use the next observed valid book.',
                'Unobserved Convert fees, spread, eligibility and minima prevent production validation.',
                'Results are conditional on the sampled universe and correlated market observations.',
                'A review checkpoint never automatically promotes a strategy.']}
