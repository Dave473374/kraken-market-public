import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
import engine as e
import runner as r


def ledger():
    s = e.fresh_state()
    s['last_attempt_at'] = e.iso(e.START + 60)
    s['last_observed_at'] = e.iso(e.START + 61)
    s['last_observed'] = e.START + 61
    p = {k: s.get(k) for k in ('id', 'protocol_hash', 'last_observed_at', 'cash_eur',
                              'equity_eur', 'fees_eur', 'final')}
    p.update(paper_only=True, real_orders=False, fill_count=0)
    return s, p


class SupervisorTests(unittest.TestCase):
    def test_frozen_engine_bytes(self):
        self.assertEqual(hashlib.sha256(Path(e.__file__).read_bytes()).hexdigest(), r.ENGINE_SHA)
        self.assertEqual(e.PHASH, r.PHASH)
        self.assertEqual(e.END, r.END)

    def test_valid_ledger(self):
        r.validate(*ledger())

    def test_missing_state_does_not_reset(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileNotFoundError):
                r.load_ledger(Path(d))
            self.assertFalse((Path(d) / 'state.json').exists())

    def test_protocol_mismatch_rejected(self):
        s, p = ledger(); s['protocol_hash'] = 'bad'
        with self.assertRaises(ValueError): r.validate(s, p)

    def test_live_order_flag_rejected(self):
        s, p = ledger(); p['real_orders'] = True
        with self.assertRaises(ValueError): r.validate(s, p)

    def test_missing_paper_flag_rejected(self):
        s, p = ledger(); del p['paper_only']
        with self.assertRaises(ValueError): r.validate(s, p)

    def test_inconsistent_report_rejected(self):
        s, p = ledger(); p['equity_eur'] = 501
        with self.assertRaises(ValueError): r.validate(s, p)

    def test_no_history_rewrite(self):
        s, p = ledger(); old = copy.deepcopy(s)
        old['gaps'] = [{'from': 'original'}]
        with self.assertRaises(ValueError): r.validate(s, p, old)

    def test_no_counter_reset(self):
        s, p = ledger(); old = copy.deepcopy(s); old['success_count'] = 10
        with self.assertRaises(ValueError): r.validate(s, p, old)

    def test_no_activation_reset(self):
        s, p = ledger(); old = copy.deepcopy(s); old['first_active_observed'] = e.START
        with self.assertRaises(ValueError): r.validate(s, p, old)

    def test_no_observation_regression(self):
        s, p = ledger(); old = copy.deepcopy(s); old['last_observed'] += 1
        with self.assertRaises(ValueError): r.validate(s, p, old)

    def test_no_repeated_sample_on_handoff(self):
        s, _ = ledger()
        self.assertEqual(r.next_due(s, e.START + 100), e.START + 360)

    def test_failed_attempt_still_waits_five_minutes(self):
        s, _ = ledger(); s['last_attempt_at'] = e.iso(e.START + 600)
        self.assertEqual(r.next_due(s, e.START + 700), e.START + 900)

    def test_no_missed_tick_replay(self):
        s, _ = ledger()
        self.assertEqual(r.next_due(s, e.START + 10000), e.START + 10000)

    def test_future_time_rejected(self):
        s, _ = ledger()
        with self.assertRaises(ValueError): r.next_due(s, e.START)

    def test_exact_end_gets_priority(self):
        s, _ = ledger(); s['last_attempt_at'] = e.iso(e.END - 10)
        self.assertEqual(r.next_due(s, e.END - 9), e.END)

    def test_no_more_ticks_after_final(self):
        s, _ = ledger(); s['final'] = True
        self.assertIsNone(r.next_due(s, e.END))

    def test_hard_shutdown(self):
        s, _ = ledger()
        self.assertIsNone(r.next_due(s, r.HARD_STOP))

    def test_no_dispatch_when_finished(self):
        with patch.object(r, 'command') as command:
            self.assertEqual(r.successor(e.END, True)['status'], 'NOT_NEEDED')
            command.assert_not_called()

    def test_no_dispatch_after_shutdown(self):
        with patch.object(r, 'command') as command:
            self.assertEqual(r.successor(r.HARD_STOP, False)['status'], 'NOT_NEEDED')
            command.assert_not_called()

    def test_dispatch_only_own_workflow(self):
        with patch.dict(r.os.environ, {'GITHUB_REPOSITORY': r.REPO}):
            with patch.object(r, 'command', return_value=Mock(returncode=0, stdout='')) as c:
                self.assertEqual(r.successor(e.START, False)['status'], 'ACCEPTED')
                args = c.call_args.args[0]
                self.assertIn('POST', args)
                self.assertIn(f'repos/{r.REPO}/actions/workflows/{r.WORKFLOW}/dispatches', args)
                self.assertEqual(args[-1], 'ref=main')

    def test_no_dispatch_for_different_project(self):
        with patch.dict(r.os.environ, {'GITHUB_REPOSITORY': 'other/project'}):
            with self.assertRaises(ValueError): r.successor(e.START, False)

    def test_stale_heartbeat_is_not_recovery(self):
        s, _ = ledger(); s['data_health'] = 'DATA_OK'
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with patch.object(r.time, 'time', return_value=e.START + 3600):
                r.save_ops(root, s, {'status': 'ACCEPTED'}, e.START, 1, 'RUNNING')
            ops = json.loads((root / 'operations.json').read_text())
            self.assertFalse(ops['fresh_market_data'])
            self.assertFalse((root / 'state.json').exists())


if __name__ == '__main__': unittest.main(verbosity=2)
