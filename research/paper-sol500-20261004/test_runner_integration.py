"""Local-only integration tests: real temporary Git, fake market and clock.
No calls to exchanges/GitHub and no writes to the actual paper ledger.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import engine as e
import runner as r
from test_engine import snap


class Clock:
    def __init__(self, now): self.now = now
    def time(self): return self.now
    def sleep(self, seconds): self.now += max(0, seconds)


def report(state):
    out = {k: state.get(k) for k in ('id', 'protocol_hash', 'last_observed_at',
            'cash_eur', 'equity_eur', 'fees_eur', 'final')}
    out.update(paper_only=True, real_orders=False, fill_count=len(state['fills']))
    return out


class OperationalIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        parent = Path(self.tmp.name)
        self.git = parent / 'ledger'
        self.remote = parent / 'remote.git'
        self.git.mkdir()
        self.root = self.git / r.REL
        self.root.mkdir(parents=True)
        def git(*args, cwd=self.git):
            return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True)
        git('init', '--bare', str(self.remote), cwd=parent)
        git('init', '-b', r.BRANCH)
        git('config', 'user.name', 'test')
        git('config', 'user.email', 'test@example.invalid')
        git('remote', 'add', 'origin', str(self.remote))
        self.clock = Clock(e.START + 7205)
        initial = e.evaluate(e.fresh_state(), snap(self.clock.now - 10000))
        initial['last_attempt_at'] = e.iso(self.clock.now - 10000)
        self.write_state(initial)
        git('add', '.')
        git('commit', '-m', 'seed')
        git('push', '-u', 'origin', r.BRANCH)
        self.times = []
        self.dispatches = []
        self.failed_market_attempts = 0
        self.fail_push = False
        self.real_command = r.command

    def write_state(self, state):
        (self.root / 'state.json').write_text(json.dumps(state))
        (self.root / 'report.json').write_text(json.dumps(report(state)))

    def fake_command(self, args, cwd, **kwargs):
        if args[0] == sys.executable:
            state = json.loads((self.root / 'state.json').read_text())
            self.times.append(self.clock.now)
            if self.failed_market_attempts:
                self.failed_market_attempts -= 1
                state['failure_count'] += 1
                state['data_health'] = 'DATA_UNAVAILABLE'
            else:
                state = e.evaluate(state, snap(self.clock.now))
            state['last_attempt_at'] = e.iso(self.clock.now)
            self.write_state(state)
            return subprocess.CompletedProcess(args, 0, 'FAKE LOCAL MARKET ONLY')
        if args[0] == 'gh':
            self.dispatches.append(args)
            return subprocess.CompletedProcess(args, 0, '')
        if args[:2] == ['git', 'push'] and self.fail_push:
            return subprocess.CompletedProcess(args, 1, 'local injected push failure')
        return self.real_command(args, cwd, **kwargs)

    def execute(self):
        with patch.object(sys, 'argv', ['runner', '--ledger', str(self.git), '--minutes', '6']), \
             patch.object(r.time, 'time', self.clock.time), \
             patch.object(r.time, 'sleep', self.clock.sleep), \
             patch.object(r, 'command', self.fake_command), \
             patch.dict(r.os.environ, {'GITHUB_REPOSITORY': r.REPO}):
            r.main()

    def test_full_burst_and_persisted_handoff(self):
        start = self.clock.now
        self.execute()
        self.assertEqual(self.times, [start, start + 300])
        self.assertEqual(len(self.dispatches), 1)
        self.assertEqual(r.load_ledger(self.root)['last_observed'], start + 300)
        ops = json.loads((self.root / 'operations.json').read_text())
        self.assertEqual(ops['phase'], 'HANDOFF')
        self.assertEqual(ops['successor_dispatch']['status'], 'ACCEPTED')
        self.assertFalse(self.real_command(['git', 'status', '--porcelain'], self.git).stdout.strip())

    def test_two_workers_keep_five_minute_cadence(self):
        start = self.clock.now
        self.execute()
        before = r.load_ledger(self.root)
        self.execute()
        after = r.load_ledger(self.root, before)
        self.assertEqual(self.times, [start, start + 300, start + 600])
        self.assertEqual(len(self.dispatches), 2)
        self.assertEqual(after['first_active_observed'], before['first_active_observed'])
        self.assertEqual(after['activation_delay_seconds'], before['activation_delay_seconds'])

    def test_market_failure_does_not_create_or_replay_fill(self):
        start = self.clock.now
        self.failed_market_attempts = 1
        self.execute()
        state = r.load_ledger(self.root)
        self.assertEqual(self.times, [start, start + 300])
        self.assertEqual(state['failure_count'], 1)
        self.assertTrue(all(fill['unix'] != start for fill in state['fills']))

    def test_push_failure_stops_before_another_engine_tick(self):
        self.fail_push = True
        with self.assertRaises(RuntimeError): self.execute()
        self.assertEqual(len(self.times), 1)
        self.assertEqual(self.dispatches, [])


if __name__ == '__main__': unittest.main(verbosity=2)
