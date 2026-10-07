#!/usr/bin/env python3
"""Bounded single-writer sessions and same-source hourly archive.

Publication repair: immutable commits, bounded retries and exact remote-ref
verification. No exchange requests, strategy or account state are changed.
"""
import argparse
import copy
import io
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import subprocess
import tarfile
import tempfile
import time
import urllib.request
from datetime import datetime, timezone

REPO = 'Dave473374/kraken-market-public'
WORKFLOW = 'collect-core.yml'
ROOT = Path(__file__).resolve().parents[1]
PUBLICATION_REVISION = '1.6-verified-publication'
RETRY_DELAYS = (0, 15, 45, 90)


def git(*args, input=None, env=None):
    return subprocess.check_output(['git', *args], cwd=ROOT, timeout=45,
        input=input, env=env, stderr=subprocess.STDOUT).decode().strip()


def utc():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def transient_git(error):
    if isinstance(error, subprocess.TimeoutExpired):
        return True
    text = (error.output or b'') if isinstance(error, subprocess.CalledProcessError) else b''
    if isinstance(text, bytes):
        text = text.decode('utf-8', errors='replace')
    text = text.lower()
    # Permission/lease/ruleset failures are deliberately not retried.
    return any(word in text for word in ('internal server error', 'bad gateway',
        'service unavailable', 'gateway timeout', 'error: 500', 'error: 502',
        'error: 503', 'error: 504', 'connection reset', 'connection timed out',
        'operation timed out', 'could not resolve host', 'could not resolve hostname',
        'failed to connect', 'remote end hung up', 'unexpected disconnect'))


def fetch_core_ref():
    for attempt, delay in enumerate(RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            return git('fetch', 'origin', '+refs/heads/core-live:refs/remotes/origin/core-live')
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            if not transient_git(error) or attempt == len(RETRY_DELAYS) - 1:
                raise


def snapshot_from_git():
    fetch_core_ref()
    sha = git('rev-parse', 'origin/core-live')
    raw = subprocess.check_output(['git', 'archive', sha, 'data/core'], cwd=ROOT, timeout=90)
    if len(raw) > 32 * 1024 * 1024:
        raise ValueError('Oversized public snapshot')
    files = {}
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for member in archive.getmembers():
            path = Path(member.name)
            if member.isdir():
                continue
            if not member.isfile() or '..' in path.parts or path.is_absolute() or path.parts[:2] != ('data', 'core') or path.suffix != '.json':
                raise ValueError('Unexpected public snapshot member')
            files[path.relative_to('data/core').as_posix()] = archive.extractfile(member).read()
    if 'manifest.json' not in files:
        raise ValueError('Snapshot has no manifest')
    return sha, files


def write_snapshot(dest, files):
    if dest.exists():
        shutil.rmtree(dest)
    for name, content in files.items():
        path = dest / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def local_snapshot():
    dest = ROOT / 'data/core'
    return {p.relative_to(dest).as_posix(): p.read_bytes() for p in dest.rglob('*.json')}


class PublicationUnavailable(subprocess.CalledProcessError):
    """A bounded transient failure; retain the immutable attempted commit."""
    def __init__(self, sha, expected_sha):
        super().__init__(1, ['git', 'push', 'origin', sha + ':core-live'])
        self.sha = sha
        self.expected_sha = expected_sha


def publication_receipt(**fields):
    # Local recovery evidence, uploaded only by the existing job on failure.
    # Not a market timestamp, health recovery, trade decision or public signal.
    dest = ROOT / '.caw-publication'
    dest.mkdir(exist_ok=True)
    row = {'publication_revision': PUBLICATION_REVISION, 'observed_at': utc(), **fields}
    with (dest / 'attempts.jsonl').open('a') as stream:
        stream.write(json.dumps(row) + '\n')
    print(json.dumps(row), flush=True)


def remote_core_sha():
    value = git('ls-remote', '--refs', 'origin', 'refs/heads/core-live').split()
    if len(value) != 2 or value[1] != 'refs/heads/core-live' or not re.fullmatch('[0-9a-f]{40}', value[0]):
        raise RuntimeError('Unknown core-live remote identity; refusing overwrite')
    return value[0]


def push_verified(sha, expected_sha):
    """Retry the SAME commit and lease; never adopt an unknown remote writer."""
    for attempt, delay in enumerate(RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        push_error = None
        try:
            git('push', '--force-with-lease=refs/heads/core-live:' + expected_sha,
                'origin', sha + ':refs/heads/core-live')
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            push_error = error
        try:
            actual = remote_core_sha()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as read_error:
            if not transient_git(read_error):
                raise
            actual = None
        publication_receipt(attempt=attempt + 1, attempted_sha=sha,
            expected_sha=expected_sha, remote_sha=actual,
            status='REMOTE_REF_VERIFIED' if actual == sha else 'PUBLICATION_UNCONFIRMED')
        # Covers a committed push whose acknowledgment was lost.
        if actual == sha:
            return sha
        if actual is not None and actual != expected_sha:
            raise subprocess.CalledProcessError(1, ['git', 'push', 'origin', 'core-live'],
                output=b'Concurrent core-live writer detected; exact lease preserved')
        if push_error is not None and not transient_git(push_error):
            raise push_error
    raise PublicationUnavailable(sha, expected_sha)


def publish_core(files, expected_sha):
    if 'manifest.json' not in files or sum(map(len, files.values())) > 32 * 1024 * 1024:
        raise ValueError('Invalid public snapshot size or missing manifest')
    for name, content in files.items():
        path = PurePosixPath(name)
        if not isinstance(content, bytes) or path.is_absolute() or '..' in path.parts or str(path) != name or not re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.json', name):
            raise ValueError('Unexpected public snapshot path or content')
    if not re.fullmatch('[0-9a-f]{40}', expected_sha):
        raise ValueError('Exact expected core-live SHA required')
    write_snapshot(ROOT / 'data/core', files)
    # Separate index: no checkout/rm/clean of main, code, other projects or caches.
    # Retain the preexisting rolling/orphan core-live design and exact lease.
    with tempfile.TemporaryDirectory(prefix='caw-publication-index-') as tmp:
        env = {**os.environ, 'GIT_INDEX_FILE': str(Path(tmp) / 'index')}
        git('read-tree', '--empty', env=env)
        entries = {'data/core/' + name: data for name, data in files.items()}
        entries['README.md'] = b'# Kraken fast core public evidence\n\nOne rolling snapshot. No account data or trade authorization.\nPin the commit and recompute all source ages. See sampling-report.json for observed gaps.\n'
        for name, data in sorted(entries.items()):
            blob = git('hash-object', '-w', '--stdin', input=data)
            git('update-index', '--add', '--cacheinfo', '100644', blob, name, env=env)
        tree = git('write-tree', env=env)
        sha = git('commit-tree', tree, '-m', 'Publish Kraken fast core snapshot')
    publication_receipt(status='IMMUTABLE_COMMIT_PREPARED', attempted_sha=sha,
        expected_sha=expected_sha, snapshot_id=json.loads(files['manifest.json']).get('snapshot_id'),
        source_generated_at=json.loads(files['manifest.json']).get('generated_at'))
    return push_verified(sha, expected_sha)


def publish_with_recovery(files, expected_sha, deadline):
    """Pause collection while retrying an unchanged pending snapshot, bounded by session."""
    try:
        return publish_core(files, expected_sha)
    except PublicationUnavailable as error:
        pending = error
    while time.monotonic() + 60 < deadline:
        time.sleep(60)
        try:
            return push_verified(pending.sha, pending.expected_sha)
        except PublicationUnavailable:
            pass
    raise pending


def api(path, body=None):
    # Fixed repository only. No arbitrary URLs, private exchange APIs or new secrets.
    url = 'https://api.github.com/repos/' + REPO + '/' + path
    request = urllib.request.Request(url, method='POST' if body is not None else 'GET',
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
                 'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json',
                 'X-GitHub-Api-Version': '2022-11-28'})
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read(2 * 1024 * 1024)
        return response.status, json.loads(raw) if raw else {}


def handoff(remaining, elapsed, published, current_id, request=api):
    """At most one dispatch, bounded chain, no cancel/re-enable or tight retry loop."""
    receipt = {'requested_at': utc(), 'parent_run_id': str(current_id),
               'remaining_handoffs': remaining, 'successor_started': False}
    if remaining <= 0:
        return {**receipt, 'status': 'HANDOFF_HORIZON_REACHED_USE_REGULAR_SCHEDULE'}
    if elapsed < 600 or published < 2:
        return {**receipt, 'status': 'EARLY_FAILURE_NO_RECURSIVE_DISPATCH'}
    _, workflow = request('actions/workflows/' + WORKFLOW)
    if workflow.get('state') != 'active':
        return {**receipt, 'status': 'WORKFLOW_DISABLED_NO_DISPATCH'}
    _, runs = request('actions/workflows/' + WORKFLOW + '/runs?branch=main&per_page=100')
    if not isinstance(runs.get('workflow_runs'), list):
        raise RuntimeError('Unknown workflow queue state; do not dispatch blindly')
    for run in runs['workflow_runs']:
        if str(run.get('id')) != str(current_id) and run.get('head_branch') == 'main' and run.get('status') in ('queued', 'pending', 'waiting', 'requested', 'in_progress'):
            return {**receipt, 'status': 'SUCCESSOR_ALREADY_QUEUED', 'successor_run_id': run['id']}
    status, _ = request('actions/workflows/' + WORKFLOW + '/dispatches',
                        {'ref': 'main', 'inputs': {'handoff_remaining': str(remaining - 1), 'session_minutes': '240'}})
    if status != 204:
        raise RuntimeError('Successor request not acknowledged')
    return {**receipt, 'status': 'SUCCESSOR_DISPATCH_ACCEPTED_NOT_YET_STARTED'}


def collect_session(minutes, remaining):
    from evidence_guard import obj
    if not 10 <= minutes <= 240 or not 0 <= remaining <= 7:
        raise ValueError('Invalid bounded session parameters')
    if os.environ.get('GITHUB_REPOSITORY') != REPO or os.environ.get('GITHUB_REF') != 'refs/heads/main':
        raise RuntimeError('Production writer may run only in the exact repository/main')
    expected_sha, files = snapshot_from_git()
    write_snapshot(ROOT / 'data/core', files)
    os.environ['CAW_CODE_SHA'] = git('rev-parse', 'HEAD')
    git('config', 'user.name', 'github-actions[bot]')
    git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
    started, published = time.monotonic(), 0
    deadline = started + minutes * 60
    try:
        while time.monotonic() < deadline:
            cycle_start = time.monotonic()
            # Missing receipt after a crash must never republish the old cycle.
            (ROOT / 'data/core/manifest.json').unlink(missing_ok=True)
            result = subprocess.run([sys.executable, '-B', 'scripts/collect_direct.py'], cwd=ROOT, timeout=540)
            if result.returncode not in (0, 1):
                raise RuntimeError('Collector crashed; no stale republish or recursive restart')
            new_files = local_snapshot()
            manifest = obj(json.loads(new_files['manifest.json']))
            if manifest.get('operational_revision') != '1.4-single-owner' or manifest.get('run_id') != os.environ['GITHUB_RUN_ID']:
                raise RuntimeError('Unrecognized collector output')
            expected_sha = publish_with_recovery(new_files, expected_sha, deadline)
            files = new_files
            published += 1
            print(json.dumps({'published_sha': expected_sha, 'cycle_status': manifest['status'], 'modules': manifest.get('modules')}), flush=True)
            remaining_time = deadline - time.monotonic()
            if remaining_time > 0:
                time.sleep(min(remaining_time, max(15, 300 - (time.monotonic() - cycle_start))))
    except PublicationUnavailable:
        # Do not let a late publication outage silently bypass the existing
        # guarded handoff. No recursive dispatch after an early crash.
        receipt = handoff(remaining, time.monotonic() - started, published, os.environ['GITHUB_RUN_ID'])
        publication_receipt(status='PUBLICATION_EXHAUSTED', handoff=receipt)
        raise
    receipt = handoff(remaining, time.monotonic() - started, published, os.environ['GITHUB_RUN_ID'])
    files['handoff.json'] = (json.dumps(receipt, indent=2) + '\n').encode()
    manifest = json.loads(files['manifest.json'])
    manifest['file_sha256'] = {name: hashlib.sha256(data).hexdigest() for name, data in files.items() if name != 'manifest.json'}
    files['manifest.json'] = (json.dumps(manifest, indent=2) + '\n').encode()
    # Does not change evidence timestamps or claim the successor already started.
    publish_core(files, expected_sha)
    print(json.dumps(receipt), flush=True)


def archive_manifest(files, sha, now=None):
    from collect import CFG, EXPECTED
    from evidence_guard import candidate_status, fresh, obj
    from quote_guard import coverage
    from collect_core import history_ok
    now = now or datetime.now(timezone.utc)
    original = json.loads(files['manifest.json'])
    if original.get('schema') != 'KRAKEN_FAST_CORE_MIRROR_V1':
        raise ValueError('Unrecognized core snapshot')
    logical = CFG['quotes_pairs']
    aliases = CFG.get('pair_aliases')
    quotes = json.loads(files['owned-quotes.json'])
    good = coverage({'ok': True, 'body': quotes}, logical, EXPECTED, aliases, now)['research_usable']
    for pair in logical:
        candidate = json.loads(files.get('candidates/' + pair + '.json', b'{}'))
        good = good and candidate_status({'ok': True, 'body': candidate}, pair, EXPECTED, aliases, now)['fresh_quote_evidence'] and history_ok({'ok': True, 'body': candidate}, now)
    manifest = copy.deepcopy(original)
    manifest.update(schema='KRAKEN_MARKET_PUBLIC_MIRROR_V1', mirror_revision='1.2-core-health',
                    archive_revision='1.4-same-source-no-extra-requests', source_core_sha=sha,
                    archived_at=now.isoformat(), source_manifest_status=original.get('status'),
                    status='OK' if good and original.get('status') == 'OK' else 'PARTIAL',
                    is_independent_freshness_fallback=False)
    manifest['summary']['core_ok'] = manifest['status'] == 'OK'
    manifest['source_core_file_sha256'] = manifest.get('file_sha256', {})
    manifest['file_sha256'] = {name: digest for name, digest in manifest.get('file_sha256', {}).items()
        if name in ('health.json', 'owned-quotes.json', 'universe.json', 'sampling-report.json', 'independent-quotes.json') or name.startswith('candidates/')}
    return manifest


def archive_main():
    sha, files = snapshot_from_git()
    manifest = archive_manifest(files, sha)
    dest = ROOT / 'data'
    for name in ('health.json', 'owned-quotes.json', 'universe.json', 'sampling-report.json', 'independent-quotes.json'):
        if name in files:
            (dest / name).write_bytes(files[name])
    candidates = {n.removeprefix('candidates/'): b for n, b in files.items() if n.startswith('candidates/')}
    write_snapshot(dest / 'candidates', candidates)
    (dest / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    git('config', 'user.name', 'github-actions[bot]')
    git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
    git('add', 'data/manifest.json', 'data/health.json', 'data/owned-quotes.json', 'data/universe.json', 'data/candidates')
    if (dest / 'sampling-report.json').exists():
        git('add', 'data/sampling-report.json')
    if (dest / 'independent-quotes.json').exists():
        git('add', 'data/independent-quotes.json')
    if subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=ROOT).returncode == 0:
        return
    git('commit', '-m', 'Archive exact Kraken core evidence without new market requests')
    for attempt in range(3):
        try:
            git('push', 'origin', 'HEAD:main')
            return
        except subprocess.CalledProcessError:
            if attempt == 2:
                raise
            git('pull', '--rebase', 'origin', 'main')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['core', 'archive'])
    args = parser.parse_args()
    if args.mode == 'archive':
        archive_main()
    else:
        collect_session(int(os.environ.get('CAW_SESSION_MINUTES', '240')),
                        int(os.environ.get('CAW_HANDOFF_REMAINING', '7')))
