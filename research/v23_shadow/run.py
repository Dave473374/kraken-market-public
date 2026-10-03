"""Bounded public-data collection + isolated shadow publication files only."""
from __future__ import annotations
import argparse
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from engine import WORKER, advance, canon, digest, fresh, initial_state, iso, read_observation, report, validate_state

ROOT = Path(__file__).resolve().parents[2]
BASE = 'https://kraken-public-test.david-e5e.workers.dev'
PAIR = re.compile(r'^[A-Z0-9]{3,32}$')
MAX_BYTES = 2_000_000


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('REDIRECT_NOT_ALLOWED')


class PublicReader:
    def __init__(self, protocol):
        self.protocol, self.calls, self.rate_limited = protocol, 0, False
        self.opener = urllib.request.build_opener(NoRedirect())

    def get(self, path):
        u = urllib.parse.urlsplit(BASE+path)
        if u.scheme != 'https' or u.netloc != urllib.parse.urlsplit(BASE).netloc or u.path not in ('/candidate.json','/universe.json'):
            return {'read_error':'ENDPOINT_NOT_ALLOWED'}
        if self.rate_limited or self.calls >= self.protocol['max_public_requests_per_run']:
            return {'read_error':'PUBLIC_REQUEST_BUDGET_OR_429_STOP'}
        self.calls += 1
        try:
            req = urllib.request.Request(BASE+path, method='GET', headers={'Accept':'application/json','User-Agent':'kraken-v23-shadow-research/1.0'})
            with self.opener.open(req,timeout=self.protocol['public_request_timeout_seconds']) as resp:
                raw = resp.read(MAX_BYTES+1)
                if resp.status != 200 or len(raw)>MAX_BYTES or 'json' not in resp.headers.get('Content-Type','').lower():
                    raise ValueError('INVALID_PUBLIC_RESPONSE')
            body = json.loads(raw)
            if not isinstance(body,dict):
                raise ValueError('NON_OBJECT')
            return body
        except urllib.error.HTTPError as exc:
            self.rate_limited = exc.code == 429
            return {'read_error':f'HTTP_{exc.code}'}
        except Exception as exc:
            return {'read_error':type(exc).__name__}
        finally:
            time.sleep(self.protocol['public_request_pause_seconds'])


def read_file(path):
    try:
        if path.stat().st_size > MAX_BYTES:
            return {'read_error':'FILE_TOO_LARGE'}
        body = json.loads(path.read_text())
        return body if isinstance(body,dict) else {'read_error':'NON_OBJECT'}
    except (OSError, ValueError):
        return {'read_error':'MISSING_OR_INVALID_FILE'}


def good_universe(body, now):
    return (body.get('schema') == WORKER and body.get('contains_account_data') is False
            and fresh(body.get('generated_at'),now) and isinstance(body.get('candidates'),list))


def plan_pairs(state, config, universe, protocol, now):
    watched = list(dict.fromkeys(canon(p) for p in config['quotes_pairs']+protocol['watch_pairs']))
    retained = {p:v for p,v in state.get('retained_candidates',{}).items()
                if 0 <= now-v['seen_unix'] <= protocol['candidate_retention_hours']*3600}
    if good_universe(universe,now):
        from engine import ts
        for row in universe['candidates']:
            pair = canon(row.get('altname') or row.get('pair_key'))
            if PAIR.fullmatch(pair) and row.get('status') == 'online' and row.get('country_filter') == 'SI':
                retained[pair] = {'seen_unix':ts(universe['generated_at'])}
    retained = dict(sorted(retained.items(),key=lambda kv:kv[1]['seen_unix'],reverse=True)[:protocol['candidate_retention_count']])
    state['retained_candidates'] = retained
    active = [c['pair'] for c in state['cohorts'].values() if any(p['remaining']>0 for p in c['paths'].values())]
    active += [c['pair'] for c in state['pending'].values()]
    base = list(dict.fromkeys(watched+active))
    others = [p for p in retained if p not in base]
    cursor = state.get('discovery_cursor',0)
    rotated = others[cursor%len(others):]+others[:cursor%len(others)] if others else []
    discovery = rotated[:protocol['discovery_limit']]
    state['discovery_cursor'] = cursor+len(discovery)
    return base+discovery, watched, discovery


def write_json(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+'\n')
    os.replace(tmp,path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--main-data',type=Path,required=True)
    ap.add_argument('--core-data',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True)
    args = ap.parse_args()
    out = args.out.resolve()
    if out.parts[-2:] != ('research','v23') or out == ROOT/'research'/'v23':
        raise ValueError('OUTPUT_MUST_BE_ISOLATED_RESEARCH_V23_CHECKOUT')
    protocol = json.loads((Path(__file__).parent/'protocol.json').read_text())
    if protocol['role'] != 'RESEARCH_ONLY' or any(protocol[k] is not False for k in ('automatic_trading','production_changes_allowed','auto_promotion')):
        raise ValueError('SAFETY_FLAGS')
    config = json.loads((ROOT/'config'/'assets.json').read_text())
    state_path = out/'state.json'
    if state_path.exists():
        state = json.loads(state_path.read_text())  # Corruption must fail, never reset.
        validate_state(state,protocol)
    else:
        if out.exists() and any(out.iterdir()):
            raise ValueError('MISSING_STATE_WITH_PRIOR_OUTPUT_NO_RESET')
        state = initial_state(protocol,time.time())
    code_hash = digest({p.name:p.read_text() for p in (Path(__file__),Path(__file__).with_name('engine.py'))})
    if state.get('implementation_hash',code_hash) != code_hash:
        raise ValueError('FROZEN_IMPLEMENTATION_CHANGED_NO_RESET')
    state['implementation_hash'] = code_hash
    reader = PublicReader(protocol)
    now = time.time()
    universe = read_file(args.core_data/'universe.json')
    if not good_universe(universe,now):
        universe = read_file(args.main_data/'universe.json')
    if not good_universe(universe,now):
        universe = reader.get('/universe.json')
    pairs, watched, discovered = plan_pairs(state,config,universe,protocol,time.time())
    collected, failures, archived = {}, {}, []
    for pair in pairs:
        if not PAIR.fullmatch(pair):
            failures[pair] = 'UNSAFE_PAIR'
            continue
        obs, error, transport = None, None, None
        for folder,name in ((args.core_data,'FAST_CORE'),(args.main_data,'M1')):
            body = read_file(folder/'candidates'/f'{pair}.json')
            obs,error = read_observation(body,pair,time.time(),protocol)
            if obs:
                transport = name
                break
        if obs is None:
            requested = config.get('pair_aliases',{}).get(pair,pair)
            body = reader.get('/candidate.json?'+urllib.parse.urlencode({'pair':requested,'notional_eur':protocol['synthetic_entry_budget_eur']}))
            obs,error = read_observation(body,pair,time.time(),protocol)
            transport = 'PUBLIC_WORKER_FALLBACK'
        if obs:
            collected[pair] = obs
            archived.append({'read_at':iso(time.time()),'transport':transport,'observation':obs})
        else:
            failures[pair] = body.get('read_error') or error
    now = time.time()
    # Revalidate the earliest observations after bounded collection completes.
    valid = {}
    for pair,obs in collected.items():
        if all(fresh(obs.get(k),now,protocol['quote_max_age_seconds']) for k in ('retrieved_at','observed_at','evidence_oldest_at')):
            valid[pair] = obs
        else:
            failures[pair] = 'STALE_AT_PUBLICATION'
    state,events = advance(state,valid,protocol,now)
    coverage = {'data_health':'DATA_OK' if len(valid)==len(pairs) else 'PARTIAL_DATA',
                'selected_pairs':pairs,'watched_pairs':watched,'discovery_pairs':discovered,
                'valid_pairs':list(valid),'unavailable':failures,'public_requests':reader.calls,
                'public_429_stop':reader.rate_limited,
                'universe_fresh':good_universe(universe,now),
                'scope':'All configured watched pairs plus open shadow positions plus at most 3 discovery candidates; not an exhaustive deep scan.'}
    result = report(state,protocol,now,coverage)
    result['implementation_hash'] = code_hash
    result['git_run_id'] = os.environ.get('GITHUB_RUN_ID')
    result['source_code_commit'] = os.environ.get('GITHUB_SHA')
    result['production_mutations_by_this_job'] = False
    # Git publication makes this set atomic remotely; failed jobs do not publish.
    month = iso(now)[:7]
    out.mkdir(parents=True,exist_ok=True)
    for name,records in [('observations',archived),('events',events),('runs',[{'at':iso(now),'coverage':coverage,'events':len(events),'unique_cohorts':len(state['cohorts'])}])]:
        path = out/name/(month+'.jsonl'); path.parent.mkdir(exist_ok=True)
        with path.open('a') as fh:
            for record in records:
                fh.write(json.dumps(record,sort_keys=True,allow_nan=False,separators=(',',':'))+'\n')
    write_json(out/'state.json',state)
    write_json(out/'report.json',result)
    write_json(out/'protocol-frozen.json',protocol)
    print(json.dumps({'role':'RESEARCH_ONLY','data_health':coverage['data_health'],
                      'valid_pairs':len(valid),'selected_pairs':len(pairs),'events':len(events),
                      'pending':len(state['pending']),'cohorts':len(state['cohorts']),
                      'production_changed':False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
