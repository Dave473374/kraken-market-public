#!/usr/bin/env python3
"""Single public-evidence owner: all configured positions, watchlist, <=3 Movers.

No portfolio sizes, cash, user confirmations, orders or decision state are stored.
"""
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
import urllib.parse

from collect import CFG, DATA, EXPECTED, BASE, atomic_write, request_pair, normalize_pair
from quote_guard import coverage
from evidence_guard import fresh as fresh_time, book as valid_book, obj, candidate_status, universe_status
from collector_runtime import CycleTransport, retained_seeds, review_seed_pairs, sampling_report

CORE = DATA / 'core'
CORE_CANDIDATES = CORE / 'candidates'
CORE_SCHEMA = 'KRAKEN_FAST_CORE_MIRROR_V1'
REVISION = '1.0-fast-core'
OPERATIONAL_REVISION = '1.4-single-owner'
CLIENT = None


def utcnow():
    return datetime.now(timezone.utc)


def now_iso():
    return utcnow().isoformat().replace('+00:00', 'Z')


def read_json(path, default):
    if not path.exists():
        return default
    # Corrupt persisted state must not silently become a new empty history.
    return json.loads(path.read_text(encoding='utf-8'))


def retry_get(path, delays=()):
    if CLIENT is None:
        raise RuntimeError('Collector transport not initialized')
    return CLIENT.get_bounded(path)


def candidate_get_fast(path):
    return retry_get(path)


def save_core(filename, result):
    result = obj(result)
    body = result.get('body') if result.get('ok') is True else {
        'mirror_status': 'SOURCE_FETCH_FAILED', 'mirror_generated_at': now_iso(),
        'source_url': result.get('url'), 'error': result.get('error'),
        'detail': result.get('detail'), 'retry_after_seconds': result.get('retry_after_seconds'),
        'http_request_made': result.get('http_request_made', True)}
    atomic_write(CORE / filename, body)


def owned_quote_coverage(result, logical_pairs, now=None):
    return coverage(result, logical_pairs, EXPECTED, CFG.get('pair_aliases'), now=now)


def owned_candidate_status(result, logical_pair, now=None):
    return candidate_status(result, logical_pair, EXPECTED, CFG.get('pair_aliases'), now)


def candidate_usable(result, logical_pair=None, now=None):
    return owned_candidate_status(result, logical_pair, now)['fresh_quote_evidence']


def configured_pairs():
    logical = list(dict.fromkeys(normalize_pair(p) for p in CFG['quotes_pairs']))
    priority = list(dict.fromkeys(normalize_pair(p) for p in CFG.get('fast_owned_pairs', [])))
    watch = list(dict.fromkeys(normalize_pair(p) for p in CFG.get('watch_pairs', [])))
    if not logical or any(p not in logical for p in priority):
        raise ValueError('Empty owned coverage or unexpected priority identity')
    for pair in logical + watch:
        if not re.fullmatch(r'[A-Z0-9]{3,24}', pair):
            raise ValueError('Invalid configured public pair')
    if len(logical) > 30 or len(watch) > 3:
        raise ValueError('Collection request budget exceeded by configuration')
    return logical, priority, [p for p in watch if p not in logical]


def selected_pairs(universe):
    logical, priority, watch = configured_pairs()
    owned = priority + [p for p in logical if p not in priority]
    seeds = obj(obj(universe).get('body')).get('candidates', [])
    rows = retained_seeds([], seeds if isinstance(seeds, list) else [], utcnow())
    discovery = review_seed_pairs(rows, set(owned + watch), int(CFG.get('discovery_deep_review_limit', 3)))
    return ([(p, 'OWNED_PRIORITY') for p in owned] + [(p, 'WATCHLIST') for p in watch]
            + [(p, 'DISCOVERY_ONLY') for p in discovery])


def history_ok(result, now):
    block = obj(obj(obj(result).get('body')).get('closed_4h'))
    source = obj(block.get('source'))
    # Closed-candle calendar freshness is separate from live book freshness.
    try:
        end = datetime.fromisoformat(block['latest_closed_bar_end'].replace('Z', '+00:00'))
        current_boundary = now.replace(hour=(now.hour // 4) * 4, minute=0, second=0, microsecond=0)
        return (block.get('quality') == 'VERIFIED' and block.get('unfinished_final_bar_excluded') is True
                and block.get('history_has_gaps') is False and end == current_boundary
                and source.get('ok') is True and fresh_time(source.get('retrieved_at'), now, max_age=4 * 3600)
                and datetime.fromisoformat(source['retrieved_at'].replace('Z', '+00:00')) >= end)
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def main():
    global CLIENT
    CORE.mkdir(parents=True, exist_ok=True)
    previous = read_json(CORE / 'collection-state.json', {})
    history = read_json(CORE / 'sampling-history.json', [])
    if not isinstance(previous, dict) or not isinstance(history, list):
        raise ValueError('Invalid operational history; refusing to reset')
    CLIENT = CycleTransport(BASE, previous.get('transport'), int(CFG.get('request_timeout_seconds', 30)))
    if CORE_CANDIDATES.exists():
        shutil.rmtree(CORE_CANDIDATES)
    CORE_CANDIDATES.mkdir(parents=True, exist_ok=True)
    started = now_iso()
    logical, priority, watch = configured_pairs()
    results, roles, attempts_log = {}, {}, []
    health, attempts = retry_get('/health')
    save_core('health.json', health)
    requested = [request_pair(p) for p in logical]
    quotes, attempts = retry_get('/quotes.json?pairs=' + urllib.parse.quote(','.join(requested), safe=','))
    save_core('owned-quotes.json', quotes)
    # Oldest successful evidence first within owned. Newly missing positions get
    # priority next cycle instead of starving behind the same initial three.
    last_success = obj(previous.get('last_success'))
    default_order = priority + [p for p in logical if p not in priority]
    owned = sorted(default_order, key=lambda p: (last_success.get(p, ''), default_order.index(p)))
    notional = float(CFG.get('synthetic_notional_eur', 60))
    if not (0 < notional <= 1000):
        raise ValueError('Invalid synthetic diagnostic notional')

    def collect_one(pair, role):
        result, attempts = candidate_get_fast('/candidate.json?pair=' + urllib.parse.quote(request_pair(pair))
            + '&notional_eur=' + str(notional))
        results[pair], roles[pair] = result, role
        save_core('candidates/' + pair + '.json', result)
        attempts_log.append({'logical_pair': pair, 'requested_pair': request_pair(pair),
                             'role': role, 'attempts': attempts, 'http_request_made': attempts > 0})

    for pair in owned:
        collect_one(pair, 'OWNED_PRIORITY')
    for pair in watch:
        collect_one(pair, 'WATCHLIST')
    universe, attempts = retry_get('/universe.json')
    save_core('universe.json', universe)
    u_status = universe_status(universe, EXPECTED, utcnow())
    new_seeds = obj(obj(universe).get('body')).get('candidates', []) if u_status['research_usable'] else []
    seeds = retained_seeds(previous.get('candidate_history', []), new_seeds, utcnow())
    for pair in review_seed_pairs(seeds, set(logical + watch), int(CFG.get('discovery_deep_review_limit', 3))):
        collect_one(pair, 'DISCOVERY_ONLY')
        for seed in seeds:
            if seed['pair'] == pair:
                seed['last_reviewed_at'] = now_iso()

    now = utcnow()
    statuses = {p: owned_candidate_status(r, p, now) for p, r in results.items()}
    for p, s in statuses.items():
        s['closed_4h_current'] = history_ok(results[p], now)
        if s['fresh_quote_evidence']:
            last_success[p] = now.isoformat()
    q_status = owned_quote_coverage(quotes, logical, now)
    u_status = universe_status(universe, EXPECTED, now)
    owned_book_ok = bool(logical) and all(statuses[p]['fresh_quote_evidence'] for p in logical)
    owned_history_ok = bool(logical) and all(statuses[p]['closed_4h_current'] for p in logical)
    owned_ok = q_status['research_usable'] and owned_book_ok and owned_history_ok
    discovery = [p for p, role in roles.items() if role == 'DISCOVERY_ONLY']
    modules = {
        'owned_quotes': 'DATA_OK' if q_status['research_usable'] else 'PARTIAL_DATA',
        'owned_books': 'DATA_OK' if owned_book_ok else 'PARTIAL_DATA',
        'owned_closed_history': 'DATA_OK' if owned_history_ok else 'PARTIAL_DATA',
        'watchlist': 'DATA_OK' if all(statuses[p]['fresh_quote_evidence'] and statuses[p]['closed_4h_current'] for p in watch) else 'PARTIAL_DATA',
        'discovery': 'DATA_OK' if u_status['research_usable'] and all(statuses[p]['fresh_quote_evidence'] for p in discovery) else 'PARTIAL_DATA',
        'strategy_decisions': 'NOT_VALIDATED_BY_COLLECTOR', 'delivery': 'NOT_VALIDATED_BY_COLLECTOR'}
    generated = now_iso()
    run_id = os.environ.get('GITHUB_RUN_ID')
    code_sha = os.environ.get('CAW_CODE_SHA') or os.environ.get('GITHUB_SHA')
    snapshot_id = hashlib.sha256((generated + str(run_id)).encode()).hexdigest()[:24]
    manifest = {
        'schema': CORE_SCHEMA, 'revision': REVISION, 'coverage_revision': '1.2-all-owned-and-watch',
        'operational_revision': OPERATIONAL_REVISION, 'transport_revision': '1.4-bounded-recovery',
        'expected_worker_schema': EXPECTED, 'generated_at': generated,
        'collection_started_at': started, 'snapshot_id': snapshot_id, 'run_id': run_id, 'code_sha': code_sha,
        'status': 'OK' if owned_ok else 'PARTIAL', 'modules': modules,
        'files': {'health.json': {'ok': health.get('ok') is True,
                    'schema_ok': obj(health.get('body')).get('schema') == EXPECTED},
                  'owned-quotes.json': {'ok': quotes.get('ok') is True,
                    'logical_pairs': logical, 'requested_pairs': requested, **q_status},
                  'universe.json': {'ok': universe.get('ok') is True, **u_status}},
        'candidate_pairs_attempted': attempts_log,
        'candidate_pairs_ok': [p for p, s in statuses.items() if s['fresh_quote_evidence']],
        'candidate_pairs_partial_or_failed': [{'logical_pair': p, 'role': roles[p], **s}
            for p, s in statuses.items() if not s['fresh_quote_evidence']],
        'owned_priority_status': {p: statuses[p] for p in logical},
        'candidate_statuses': statuses,
        'summary': {'core_ok': owned_ok, 'owned_quotes_research_usable': q_status['research_usable'],
            'owned_quotes_executable_evidence': False, 'owned_priority_complete': owned_book_ok,
            'owned_priority_count': len(logical), 'owned_history_complete': owned_history_ok,
            'universe_ok': u_status['research_usable'], 'candidate_ok_count': sum(s['fresh_quote_evidence'] for s in statuses.values()),
            'candidate_partial_or_failed_count': sum(not s['fresh_quote_evidence'] for s in statuses.values())},
        'notes': ['Collection only, not a BUY/SELL or retail Convert quote.',
                  'Recompute source ages at use; root OK means owned evidence at publication only.',
                  'Watchlist/discovery health is separate; inspect modules, not only root status.',
                  'All routes remain one Kraken provider; independent cross-check is still required.',
                  'Synthetic 60 EUR/20x diagnostics never override actual-notional 10x/15x rules.']}
    record = {'generated_at': generated, 'collection_started_at': started,
              'snapshot_id': snapshot_id, 'run_id': run_id, 'code_sha': code_sha,
              'owned_complete': owned_ok, 'modules': modules,
              'requests_made': CLIENT.requests_made, 'recovery_wait_seconds': round(CLIENT.recovery_wait_seconds, 3)}
    history.append(record)
    # Bounded public operational history; never reset advisory/financial records.
    cutoff = now.timestamp() - 7 * 86400
    history = [r for r in history if datetime.fromisoformat(r['generated_at'].replace('Z', '+00:00')).timestamp() >= cutoff][-4096:]
    atomic_write(CORE / 'sampling-history.json', history)
    atomic_write(CORE / 'sampling-report.json', sampling_report(history))
    atomic_write(CORE / 'collection-state.json', {'schema': 'CAW_PUBLIC_COLLECTION_STATE_V1',
        'transport': CLIENT.state(), 'last_success': last_success, 'candidate_history': seeds,
        'contains_account_data': False, 'trade_signal': None})
    # Hashes bind all evidence files to this publication. Consumer pins branch SHA.
    manifest['file_sha256'] = {p.relative_to(CORE).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(CORE.rglob('*.json')) if p.name != 'manifest.json'}
    atomic_write(CORE / 'manifest.json', manifest)
    print(json.dumps(record, ensure_ascii=False))
    return 0 if owned_ok else 1


if __name__ == '__main__':
    sys.exit(main())
