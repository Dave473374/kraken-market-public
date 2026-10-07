#!/usr/bin/env python3
"""SOL500 operational repair: retry publication, recover real records, verify handoff.
Never evaluates a trading signal itself. Calls the byte-frozen engine via runner.py.
Only this trial's public ledger and job-scoped GitHub permissions are used.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import runner as core

REV = 'SUPERVISOR-1.2-20261007'
DELAYS = (0, 3, 10, 20, 40, 60, 90, 120, 120, 120, 120)
NETWORK_BUDGET = 600
TRANSPORT = []
FINANCIAL_KEYS = ('cash_eur', 'equity_eur', 'position', 'fills', 'closed_trades',
                  'entries', 'fees_eur', 'pause_until', 'loss_streak', 'halted')


def record(kind, **data):
    out = {'kind': kind, 'at': core.iso(core.time.time()), **data}
    TRANSPORT.append(out)
    del TRANSPORT[:-12]
    print(json.dumps(out), flush=True)


def network(args, cwd, timeout=45, budget=NETWORK_BUDGET):
    """Bounded retry of transport errors. Never retry a rejected history change."""
    started = core.time.time()
    last = ''
    for attempt, delay in enumerate(DELAYS, 1):
        if attempt > 1:
            remaining = min(budget - (core.time.time() - started),
                            core.HARD_STOP - core.time.time())
            if remaining <= 0:
                break
            core.time.sleep(min(delay, remaining))
            if core.time.time() - started >= budget:
                break
        try:
            result = core.command(args, cwd, timeout=min(timeout, budget), check=False)
            if result.returncode == 0:
                if attempt > 1:
                    record('TRANSPORT_RECOVERED', command=args[0:2], attempts=attempt,
                           elapsed_seconds=core.time.time() - started)
                return result
            last = result.stdout[-1500:]
        except (subprocess.TimeoutExpired, OSError) as exc:
            last = type(exc).__name__  # Do not print environments or credentials.
        if any(v in last.lower() for v in ('non-fast-forward', 'fetch first',
               'permission denied', 'authentication failed', 'protected branch')):
            raise ValueError('Publication/history/access conflict; no force push: ' + last)
        record('TRANSPORT_RETRY', command=args[0:2], attempt=attempt,
               detail=last[-500:])
    raise RuntimeError('Transport unavailable after bounded retries: ' + last)


def sync(ledger):
    if core.command(['git', 'status', '--porcelain'], ledger).stdout.strip():
        raise RuntimeError('Unpublished files exist; no further model evaluation')
    network(['git', 'fetch', 'origin', core.BRANCH], ledger)
    core.command(['git', 'merge', '--ff-only', 'FETCH_HEAD'], ledger)
    # An unpushed local commit must not be mistaken for a clean, published ledger.
    ahead = core.command(['git', 'rev-list', '--count', 'FETCH_HEAD..HEAD'], ledger).stdout.strip()
    if int(ahead):
        network(['git', 'push', 'origin', 'HEAD:' + core.BRANCH], ledger)


def persist(ledger):
    core.command(['git', 'add', '--', core.REL], ledger)
    names = core.command(['git', 'diff', '--cached', '--name-only'], ledger).stdout.splitlines()
    if any(not name.startswith(core.REL + '/') for name in names):
        raise ValueError('Refusing to publish files outside this paper trial')
    changed = core.command(['git', 'diff', '--cached', '--quiet'], ledger, check=False)
    if changed.returncode == 1:
        core.command(['git', 'commit', '-m', 'Record SOL500 durable observation [skip ci]'], ledger)
    elif changed.returncode != 0:
        raise RuntimeError('Unable to inspect staged ledger')
    # ALWAYS flush HEAD, even after a previous ambiguous/failed push with no staged diff.
    # No rebase/reset/force push; the same commit is retried before any next engine tick.
    network(['git', 'push', 'origin', 'HEAD:' + core.BRANCH], ledger)


def github_json(path, budget=NETWORK_BUDGET):
    if os.environ.get('GITHUB_REPOSITORY') != core.REPO:
        raise ValueError('Unexpected repository')
    result = network(['gh', 'api', 'repos/' + core.REPO + '/' + path], Path.cwd(), budget=budget)
    return json.loads(result.stdout)


def ensure_successor(now, final):
    """Re-check actual queue, not a once-accepted dispatch that may be cancelled."""
    if final or now >= core.HARD_STOP:
        return {'status': 'NOT_NEEDED', 'at': core.iso(now)}
    if os.environ.get('GITHUB_REPOSITORY') != core.REPO:
        raise ValueError('Unexpected repository')
    current = str(os.environ.get('GITHUB_RUN_ID', ''))
    path = 'actions/workflows/' + core.WORKFLOW + '/runs?per_page=30'
    runs = github_json(path, budget=60).get('workflow_runs', [])
    for run in runs:
        if (str(run.get('id')) != current and run.get('head_branch') == 'main'
                and run.get('path', '').split('@')[0] == '.github/workflows/' + core.WORKFLOW
                and run.get('status') in ('queued', 'pending', 'in_progress', 'waiting', 'requested')):
            return {'status': 'ACCEPTED', 'verification': 'EXISTING_RUN_OBSERVED',
                    'run_id': run['id'], 'run_status': run['status'], 'at': core.iso(now)}
    # The unchanged concurrency group ensures at most one active writer, even after an
    # ambiguous dispatch response. The next worker reads persisted state after taking lock.
    network(['gh', 'api', '--method', 'POST',
             f'repos/{core.REPO}/actions/workflows/{core.WORKFLOW}/dispatches',
             '-f', 'ref=main'], Path.cwd(), timeout=30, budget=60)
    return {'status': 'ACCEPTED', 'verification': 'DISPATCH_ACCEPTED_NOT_START_CONFIRMED',
            'at': core.iso(core.time.time())}


def safe_snapshot_path(root, relative):
    rel = Path(relative)
    if len(rel.parts) != 2 or rel.parts[0] != 'observations' or rel.suffix != '.json':
        raise ValueError('Unexpected snapshot path')
    path = root / rel
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Unsafe snapshot path')
    return path


def restore_candidate(ledger, candidate_root, run_id, artifact_id):
    """Restore original bytes only. An incomplete legacy no-trade record is evidence,
    not a fabricated market observation. Missing evidence for new fills blocks trading.
    """
    root = ledger / core.REL
    old = core.load_ledger(root)
    new = json.loads((candidate_root / 'state.json').read_text())
    report = json.loads((candidate_root / 'report.json').read_text())
    core.validate(new, report)
    if new.get('engine_sha256') != core.ENGINE_SHA:
        raise ValueError('Recovery artifact engine checksum mismatch')
    if core.timestamp(new['last_attempt_at']) <= core.timestamp(old['last_attempt_at']):
        if new['fills'] != old['fills'][:len(new['fills'])]:
            raise ValueError('Older recovery artifact has conflicting trade history')
        evidence = root / 'recovery' / str(run_id)
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / 'receipt.json').write_text(json.dumps({
            'id': core.ID, 'protocol_hash': core.PHASH, 'source_run': int(run_id),
            'artifact_id': int(artifact_id), 'status': 'ALREADY_COVERED',
            'original_observed_at': new.get('last_observed_at'),
            'recovery_checked_at': core.iso(core.time.time()),
            'paper_only': True, 'real_orders': False}, indent=2) + '\n')
        persist(ledger)
        return 'ALREADY_COVERED'
    core.validate(new, report, old)
    evidence = root / 'recovery' / str(run_id)
    evidence.mkdir(parents=True, exist_ok=True)
    for name in ('state.json', 'report.json', 'operations.json'):
        source = candidate_root / name
        if source.exists():
            target = evidence / name
            if target.exists() and target.read_bytes() != source.read_bytes():
                raise ValueError('Recovery evidence would be overwritten')
            target.write_bytes(source.read_bytes())
    relative = new.get('last_snapshot', '')
    saved = safe_snapshot_path(candidate_root, relative)
    existing = safe_snapshot_path(root, relative)
    if not saved.is_file() and not existing.is_file():
        if any(new.get(k) != old.get(k) for k in FINANCIAL_KEYS):
            raise ValueError('Unpublished financial change lacks its market snapshot; STOP')
        status = 'INCOMPLETE_LEGACY_NO_TRADE_CHANGE_EVIDENCE_ONLY'
    else:
        snap_path = saved if saved.is_file() else existing
        snap = json.loads(snap_path.read_text())
        if (snap.get('id') != core.ID or snap.get('observed_at') != new['last_observed_at']
                or abs(float(snap.get('observed_unix', 0)) - new['last_observed']) > 0.001):
            raise ValueError('Recovery observation does not match original state')
        # Every genuinely new fill needs its original, retained snapshot.
        for fill in new['fills'][len(old['fills']):]:
            stamp = core.datetime.fromtimestamp(fill['unix'], core.timezone.utc).strftime('%Y%m%dT%H%M%SZ.json')
            choices = [safe_snapshot_path(candidate_root, 'observations/' + stamp),
                       safe_snapshot_path(root, 'observations/' + stamp)]
            choices = [p for p in choices if p.is_file()]
            if not choices:
                raise ValueError('New recovery fill has no source observation')
            fs = json.loads(choices[0].read_text())
            if fs.get('id') != core.ID or fs.get('observed_at') != fill['time']:
                raise ValueError('Recovery fill/snapshot identity mismatch')
        for source in (candidate_root / 'observations').glob('*.json'):
            dest = safe_snapshot_path(root, 'observations/' + source.name)
            if dest.exists() and dest.read_bytes() != source.read_bytes():
                raise ValueError('Existing observation conflict')
        for source in (candidate_root / 'observations').glob('*.json'):
            dest = safe_snapshot_path(root, 'observations/' + source.name)
            dest.parent.mkdir(exist_ok=True)
            if not dest.exists():
                dest.write_bytes(source.read_bytes())
        for name in ('state.json', 'report.json'):
            (root / name).write_bytes((candidate_root / name).read_bytes())
        status = 'ORIGINAL_UNPUBLISHED_RECORD_RESTORED'
    receipt = {'id': core.ID, 'protocol_hash': core.PHASH, 'source_run': int(run_id),
               'artifact_id': int(artifact_id), 'status': status,
               'original_observed_at': new.get('last_observed_at'),
               'recovery_checked_at': core.iso(core.time.time()),
               'paper_only': True, 'real_orders': False,
               'note': 'Original artifact bytes only; never a retrospective model execution.'}
    (evidence / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    persist(ledger)
    record('RECOVERY', **receipt)
    return status


def recover(ledger):
    runs = github_json('actions/workflows/' + core.WORKFLOW + '/runs?status=failure&per_page=10').get('workflow_runs', [])
    for run in reversed(runs):
        if (run.get('head_branch') != 'main' or
            run.get('path', '').split('@')[0] != '.github/workflows/' + core.WORKFLOW):
            continue
        rid = int(run['id'])
        if str(rid) == str(os.environ.get('GITHUB_RUN_ID')):
            continue
        # Re-read archived evidence only once; no persistent state is reset.
        receipt = ledger / core.REL / 'recovery' / str(rid) / 'receipt.json'
        if receipt.exists():
            continue
        artifacts = github_json(f'actions/runs/{rid}/artifacts?per_page=100').get('artifacts', [])
        for art in artifacts:
            if not art.get('name', '').startswith(f'sol500-recovery-{rid}-') or art.get('expired'):
                continue
            with tempfile.TemporaryDirectory(prefix='sol500-recovery-') as d:
                network(['gh', 'run', 'download', str(rid), '--repo', core.REPO,
                         '--name', art['name'], '--dir', d], Path.cwd(), timeout=90)
                roots = [p.parent for p in Path(d).rglob('state.json')
                         if (p.parent / 'report.json').is_file() and 'recovery' not in p.relative_to(d).parts]
                if len(roots) != 1:
                    raise ValueError('Ambiguous recovery artifact; refusing to guess ledger')
                restore_candidate(ledger, roots[0], rid, int(art['id']))


def main():
    # Same CLI and engine. Swaps ONLY operational transport/handoff functions.
    args = core.argparse.ArgumentParser()
    args.add_argument('--ledger', required=True)
    args.add_argument('--minutes', type=int, default=180)
    parsed = args.parse_args()
    if not 6 <= parsed.minutes <= 240:
        raise ValueError('Invalid operational burst')
    ledger = Path(parsed.ledger).resolve()
    root = ledger / core.REL
    engine = Path(core.__file__).with_name('engine.py')
    if hashlib.sha256(engine.read_bytes()).hexdigest() != core.ENGINE_SHA:
        raise ValueError('Frozen engine mismatch; no restart')
    if os.environ.get('GITHUB_REPOSITORY') != core.REPO:
        raise ValueError('Wrong repository')
    if core.command(['git', 'branch', '--show-current'], ledger).stdout.strip() != core.BRANCH:
        raise ValueError('Wrong ledger branch')
    core.command(['git', 'config', 'user.name', 'paper-simulation[bot]'], ledger)
    core.command(['git', 'config', 'user.email', 'paper-simulation@users.noreply.github.com'], ledger)
    originals = core.sync, core.persist, core.successor, core.REV
    core.sync, core.persist, core.successor, core.REV = sync, persist, ensure_successor, REV
    safety_failure = False
    failure = None
    starting = None
    try:
        sync(ledger)
        starting = core.load_ledger(root)
        if not starting['final'] and core.time.time() < core.HARD_STOP:
            recover(ledger)
        core.main()
    except (ValueError, FileNotFoundError) as exc:
        safety_failure = True
        failure = exc
        record('SAFETY_HALT', reason=str(exc)[-1000:])
        raise
    except Exception as exc:
        failure = exc
        record('OPERATIONAL_FAILURE', reason=str(exc)[-1000:])
        raise
    finally:
        # Runs while the same concurrency lock is held. The next worker cannot start
        # before upload-artifact and job completion. Do not trust an old ACCEPTED receipt.
        try:
            if not safety_failure:
                try:
                    state = core.load_ledger(root, starting)
                    handoff = ensure_successor(core.time.time(), state['final'])
                    record('FINAL_HANDOFF_CHECK', receipt=handoff)
                    status = {'id': core.ID, 'protocol_hash': core.PHASH,
                              'supervisor_revision': REV, 'paper_only': True, 'real_orders': False,
                              'checked_at': core.iso(core.time.time()), 'handoff': handoff,
                              'transport_events': TRANSPORT[-12:],
                              'failure': None if failure is None else str(failure)[-1000:]}
                    (root / 'reliability.json').write_text(json.dumps(status, indent=2) + '\n')
                    # Best-effort final flush; the workflow archives the ENTIRE original
                    # trial data on failure, including unpublished observation snapshots.
                    if failure is None:
                        persist(ledger)
                except Exception as exc:
                    record('HANDOFF_OR_FINAL_FLUSH_FAILED', reason=str(exc)[-1000:])
                    if failure is None:
                        raise
        finally:
            core.sync, core.persist, core.successor, core.REV = originals


if __name__ == '__main__':
    main()
