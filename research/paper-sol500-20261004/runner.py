#!/usr/bin/env python3
"""Operational supervisor only. Calls the unchanged paper engine every 300s.
One active workflow + one queued successor; cron is only a backup starter.
Never edits strategy/state by itself, replays missed ticks, or places real orders.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ID = 'SOL500-FWD-20261004-v1'
PHASH = '394eafafbe6a51a231b989cedd11401e8a4f438c93924ea5194e072bd6174d0a'
ENGINE_SHA = '4461a21a603f73c6615310f0646c262c60ae11f3522c71098a6eb108afa20192'
REPO = 'Dave473374/kraken-market-public'
BRANCH = 'paper-sol500-20261004'
REL = 'research/paper-sol500-20261004'
WORKFLOW = 'paper-sol500-20261004.yml'
REV = 'SUPERVISOR-1.0-20261004'
INTERVAL = 300
END = int(datetime(2026, 10, 11, 7, tzinfo=timezone.utc).timestamp())
HARD_STOP = END + 5400  # Original scheduler shutdown; NOT an extended trading window.


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace('+00:00', 'Z')


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def validate(state, report, previous=None):
    for obj in (state, report):
        if obj.get('id') != ID or obj.get('protocol_hash') != PHASH:
            raise ValueError('Wrong trial/protocol; refusing to initialize or reset')
    if report.get('paper_only') is not True or report.get('real_orders') is not False:
        raise ValueError('Paper-only flags missing or invalid')
    for key in ('last_observed_at', 'equity_eur', 'cash_eur', 'fees_eur', 'final'):
        if report.get(key) != state.get(key):
            raise ValueError('Inconsistent state/report: ' + key)
    fills = state['fills']
    if report['fill_count'] != len(fills):
        raise ValueError('Fill count mismatch')
    for number, fill in enumerate(fills, 1):
        if (fill.get('number') != number or fill.get('id') != f'{ID}-{number}'
                or fill.get('status') != 'MODELLED_PAPER_FILL_NOT_EXECUTED'
                or fill.get('protocol_hash') != PHASH):
            raise ValueError('Unexpected fill identity or non-paper fill')
    if previous:
        for key in ('fills', 'closed_trades', 'gaps', 'entries'):
            if state[key][:len(previous[key])] != previous[key]:
                raise ValueError('Existing history changed: ' + key)
        for key in ('first_active_observed', 'activation_delay_seconds'):
            if previous.get(key) is not None and state.get(key) != previous[key]:
                raise ValueError('Original activation moved')
        for key in ('success_count', 'failure_count'):
            if state[key] < previous[key]:
                raise ValueError('Counter reset: ' + key)
        if (previous.get('last_observed') is not None
                and state.get('last_observed', 0) < previous['last_observed']):
            raise ValueError('Observation time regressed')


def load_ledger(root, previous=None):
    # Deliberately no fresh_state() fallback: missing history is an error.
    state = json.loads((root / 'state.json').read_text())
    report = json.loads((root / 'report.json').read_text())
    validate(state, report, previous)
    return state


def next_due(state, now):
    if state['final'] or now >= HARD_STOP:
        return None
    last = timestamp(state['last_attempt_at']) if state.get('last_attempt_at') else 0
    if last > now + 30:
        raise ValueError('Future last_attempt_at')
    due = max(now, last + INTERVAL)
    # Close at fixed END, not another full polling interval later.
    if now < END:
        due = min(due, END)
    return due if due < HARD_STOP else None


def command(args, cwd, timeout=60, check=True, env=None):
    result = subprocess.run(args, cwd=cwd, timeout=timeout, env=env,
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if check and result.returncode:
        raise RuntimeError('Command failed: ' + args[0] + '\n' + result.stdout[-2000:])
    return result


def sync(ledger):
    if command(['git', 'status', '--porcelain'], ledger).stdout.strip():
        raise RuntimeError('Unpersisted files exist; no further model evaluations')
    command(['git', 'fetch', 'origin', BRANCH], ledger)
    command(['git', 'merge', '--ff-only', 'FETCH_HEAD'], ledger)


def persist(ledger):
    command(['git', 'add', '--', REL], ledger)
    changed = command(['git', 'diff', '--cached', '--quiet'], ledger, check=False)
    if changed.returncode == 0:
        return
    if changed.returncode != 1:
        raise RuntimeError('Unable to inspect staged changes')
    command(['git', 'commit', '-m', 'Record SOL500 supervised observation [skip ci]'], ledger)
    last_error = ''
    for attempt in range(3):
        result = command(['git', 'push', 'origin', 'HEAD:' + BRANCH], ledger, check=False)
        if result.returncode == 0:
            return
        last_error = result.stdout[-1000:]
        time.sleep(2 * (attempt + 1))
    # No force push/rebase/rewind, and no second engine tick with unpublished fills.
    raise RuntimeError('Ledger push failed; preserve local evidence and stop: ' + last_error)


def successor(now, final):
    if final or now >= HARD_STOP:
        return {'status': 'NOT_NEEDED', 'at': iso(now)}
    if os.environ.get('GITHUB_REPOSITORY') != REPO:
        raise ValueError('Unexpected repository; refusing dispatch')
    result = command(['gh', 'api', '--method', 'POST',
                      f'repos/{REPO}/actions/workflows/{WORKFLOW}/dispatches',
                      '-f', 'ref=main'], Path.cwd(), timeout=30, check=False)
    # Do not log credentials. gh reads only the job-scoped GH_TOKEN.
    return {'status': 'ACCEPTED' if result.returncode == 0 else 'FAILED',
            'at': iso(now), 'exit_code': result.returncode,
            'detail': result.stdout[-500:] if result.returncode else ''}


def save_ops(root, state, receipt, started, attempted, phase):
    now = time.time()
    age = now - timestamp(state['last_observed_at']) if state.get('last_observed_at') else None
    ops = {'id': ID, 'supervisor_revision': REV, 'protocol_hash': PHASH,
           'engine_sha256': ENGINE_SHA, 'paper_only': True, 'real_orders': False,
           'phase': phase, 'heartbeat_at': iso(now), 'run_started_at': iso(started),
           'run_id': os.environ.get('GITHUB_RUN_ID'), 'event': os.environ.get('GITHUB_EVENT_NAME'),
           'poll_target_seconds': INTERVAL, 'engine_attempts_this_run': attempted,
           'latest_market_observation': state.get('last_observed_at'),
           'market_age_seconds': age, 'market_data_health': state.get('data_health'),
           'fresh_market_data': age is not None and 0 <= age <= 1200 and state.get('data_health') == 'DATA_OK',
           'successor_dispatch': receipt, 'fixed_trial_end': iso(END),
           'operational_shutdown': iso(HARD_STOP), 'final': state['final'],
           'note': 'Heartbeat is not a market observation. No backfill or strategy change.'}
    path = root / 'operations.json'
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(ops, indent=2, allow_nan=False) + '\n')
    tmp.replace(path)
    print(json.dumps(ops), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ledger', required=True)
    parser.add_argument('--minutes', type=int, default=180)
    args = parser.parse_args()
    if not 6 <= args.minutes <= 240:
        raise ValueError('Operational burst must be between 6 and 240 minutes')
    engine = Path(__file__).with_name('engine.py').resolve()
    if hashlib.sha256(engine.read_bytes()).hexdigest() != ENGINE_SHA:
        raise ValueError('Frozen engine checksum mismatch')
    ledger = Path(args.ledger).resolve()
    root = ledger / REL
    if command(['git', 'branch', '--show-current'], ledger).stdout.strip() != BRANCH:
        raise ValueError('Wrong ledger branch')
    command(['git', 'config', 'user.name', 'paper-simulation[bot]'], ledger)
    command(['git', 'config', 'user.email', 'paper-simulation@users.noreply.github.com'], ledger)
    sync(ledger)
    state = load_ledger(root)
    started = time.time()
    deadline = min(started + args.minutes * 60, HARD_STOP)
    receipt = {'status': 'NOT_REQUESTED'}
    attempted = 0
    previous = state
    while time.time() < deadline:
        sync(ledger)
        state = load_ledger(root, previous)
        due = next_due(state, time.time())
        if due is None:
            break
        if due >= deadline:
            time.sleep(max(0, deadline - time.time()))
            break
        time.sleep(max(0, due - time.time()))
        # Acquire latest persisted history again after sleeping; never overwrite another writer.
        sync(ledger)
        state = load_ledger(root, previous)
        due = next_due(state, time.time())
        if due is None:
            break
        if due > time.time() + 1:
            previous = state
            continue
        clean_env = {k: v for k, v in os.environ.items()
                     if 'TOKEN' not in k.upper() and 'SECRET' not in k.upper()}
        result = command([sys.executable, str(engine), '--state-dir', str(root)],
                         ledger, timeout=120, env=clean_env)
        print(result.stdout, flush=True)
        attempted += 1
        updated = load_ledger(root, state)
        save_ops(root, updated, receipt, started, attempted, 'RUNNING')
        persist(ledger)
        previous = state = updated
        # Queue the next worker early, under the SAME concurrency lock.
        # Pending runs may be replaced by cron, but only one worker can write at once.
        if receipt['status'] != 'ACCEPTED' and not state['final']:
            receipt = successor(time.time(), state['final'])
            save_ops(root, state, receipt, started, attempted, 'RUNNING')
            persist(ledger)
        if state['final']:
            break
    if not state['final'] and time.time() < HARD_STOP and receipt['status'] != 'ACCEPTED':
        receipt = successor(time.time(), False)
    save_ops(root, state, receipt, started, attempted,
             'COMPLETED' if state['final'] else 'HANDOFF' if time.time() < HARD_STOP else 'EXPIRED_INCOMPLETE')
    persist(ledger)


if __name__ == '__main__':
    main()
