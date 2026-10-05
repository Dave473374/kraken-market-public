"""Synthetic isolated regression tests; no real network, delivery, or financial state."""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
from datetime import timedelta
from email.message import Message
from unittest.mock import patch
import collect_core as core
from collector_runtime import CycleTransport, retained_seeds, review_seed_pairs, sampling_report
from evidence_guard import candidate_status, universe_status
from run_public_collector import handoff, archive_manifest
from test_owned_coverage import NOW, STAMP, PRIORITY, candidate, quote, envelope, universe


class GuardRegressions(unittest.TestCase):
    def test_generic_guard_rejects_stale(self):
        self.assertFalse(core.candidate_usable(candidate('MEGAEUR'), 'MEGAEUR', NOW + timedelta(seconds=601)))
    def test_expected_pair_is_mandatory(self):
        self.assertFalse(core.candidate_usable(candidate('MEGAEUR'), now=NOW))
        self.assertFalse(core.candidate_usable(candidate('MEGAEUR'), 'NIGHTEUR', NOW))
    def test_malformed_nested_values_do_not_crash(self):
        for key in ('pair_metadata', 'timestamped_spread', 'depth'):
            for value in ([], ['not an object'], 'oops', 3, True):
                r = candidate('MEGAEUR'); r['body'][key] = value
                self.assertFalse(core.candidate_usable(r, 'MEGAEUR', NOW))
    def test_malformed_ticker_uses_shared_guard(self):
        row = quote('MEGAEUR'); row['ticker'] = ['malformed']
        self.assertFalse(core.owned_quote_coverage(envelope({'rows':[row]}), ['MEGAEUR'], NOW)['research_usable'])
    def test_same_provider_conflict_rejected(self):
        r = candidate('MEGAEUR'); r['body']['depth'].update(best_bid=2, best_ask=2.001)
        self.assertFalse(core.candidate_usable(r, 'MEGAEUR', NOW))
    def test_liquid_and_spec_ceiling_not_conflated(self):
        r = candidate('MEGAEUR'); r['body']['depth'].update(best_bid=1.015, best_ask=1.016)
        s = core.owned_candidate_status(r, 'MEGAEUR', NOW)
        self.assertTrue(s['fresh_quote_evidence'])
        self.assertFalse(s['liquid_1pct_check_passed'])
        self.assertIsNone(s['trade_signal'])
    def test_empty_coverage_configuration_rejected(self):
        with patch.dict(core.CFG, {'quotes_pairs': []}):
            with self.assertRaises(ValueError): core.configured_pairs()
    def test_valid_partial_ticker_universe_is_discovery_only(self):
        s = universe_status(universe([]), core.EXPECTED, NOW)
        self.assertTrue(s['research_usable']); self.assertFalse(s['executable_evidence'])
    def test_stale_universe_rejected(self):
        self.assertFalse(universe_status(universe([]), core.EXPECTED, NOW+timedelta(seconds=601))['research_usable'])
    def test_wrong_clock_and_unfinished_candles_rejected(self):
        r = candidate('MEGAEUR')
        self.assertTrue(core.history_ok(r, NOW))
        self.assertFalse(core.history_ok(r, NOW+timedelta(hours=4)))
        r['body']['closed_4h']['unfinished_final_bar_excluded'] = False
        self.assertFalse(core.history_ok(r, NOW))


class TransportRecovery(unittest.TestCase):
    def test_cooldown_is_restored_between_processes(self):
        with patch('collector_runtime.time.time', return_value=1000), patch('collector_runtime.time.monotonic', return_value=20):
            c = CycleTransport('https://public.test', {'cooldown_until_epoch':1120})
            self.assertEqual(c.cooldown_until,140)
            self.assertAlmostEqual(c.state()['cooldown_until_epoch'],1120)
    def test_corrupt_cooldown_cannot_become_zero(self):
        for value in ('bad', float('nan'), -1, True):
            with self.assertRaises(ValueError): CycleTransport('https://public.test', {'cooldown_until_epoch':value})
    def test_large_retry_after_is_not_bypassed(self):
        with patch('collector_runtime.time.time', return_value=1000), patch('collector_runtime.time.monotonic', return_value=20), patch('public_transport.urllib.request.urlopen') as net:
            c=CycleTransport('https://public.test', {'cooldown_until_epoch':2000})
            result, attempts = c.get_bounded('/health')
            self.assertFalse(result['ok']); self.assertEqual(attempts,0); net.assert_not_called()
    def test_429_wait_then_resume_without_burst(self):
        clock=[1000.0]
        class Response:
            status=200; headers={'Content-Type':'application/json'}
            def read(self,n): return b'{"generated_at":"unchanged"}'
            def __enter__(self): return self
            def __exit__(self,*args): return False
        headers=Message(); headers['Retry-After']='60'
        error=urllib.error.HTTPError('https://public.test/health',429,'limit',headers,io.BytesIO(b'{"status":"LOCAL_COOLDOWN"}'))
        with patch('collector_runtime.time.time', side_effect=lambda:clock[0]), patch('collector_runtime.time.monotonic', side_effect=lambda:clock[0]), patch('collector_runtime.time.sleep', side_effect=lambda s:clock.__setitem__(0,clock[0]+s)), patch('public_transport.urllib.request.urlopen', side_effect=[error,Response()]) as net:
            c=CycleTransport('https://public.test');result,attempts=c.get_bounded('/health')
        self.assertTrue(result['ok']);self.assertEqual(attempts,2);self.assertEqual(net.call_count,2)
        self.assertGreaterEqual(c.recovery_wait_seconds,60)
        self.assertEqual(result['body']['generated_at'],'unchanged')
    def test_exhausted_cycle_budget_no_network(self):
        c=CycleTransport('https://public.test');c.deadline=0
        with patch('public_transport.urllib.request.urlopen') as net:
            self.assertEqual(c.get_bounded('/health')[1],0);net.assert_not_called()


class CandidateRetention(unittest.TestCase):
    def test_retention_max48h_and20_assets(self):
        old=[{'pair':'OLDEUR','last_seen_at':(NOW-timedelta(hours=49)).isoformat()}]
        rows=retained_seeds(old,[{'pair_key':f'A{i}EUR','asset':f'A{i}'} for i in range(30)],NOW)
        self.assertEqual(len(rows),20);self.assertNotIn('OLDEUR',[r['pair'] for r in rows])
    def test_duplicate_asset_and_exclusions(self):
        rows=retained_seeds([], [{'pair_key':'AAAUSD','asset':'AAA'},{'pair_key':'AAAEUR','asset':'AAA'},{'pair_key':'BBBEUR','asset':'BBB'}], NOW)
        self.assertEqual(len(rows),2)
        self.assertEqual(review_seed_pairs(rows,{'AAAUSD'},3),['BBBEUR'])
    def test_rotation_and_no_buy_authorization(self):
        rows=retained_seeds([], [{'pair_key':f'A{i}EUR','asset':f'A{i}'} for i in range(5)],NOW)
        first=review_seed_pairs(rows,set(),3)
        for row in rows:
            if row['pair'] in first: row['last_reviewed_at']=STAMP
        second=review_seed_pairs(rows,set(),3)
        self.assertTrue({'A3EUR','A4EUR'}.issubset(second))
        self.assertNotIn('trade_signal', rows[0])


class HandoffTests(unittest.TestCase):
    def test_short_failure_does_not_self_dispatch(self):
        with patch('run_public_collector.api') as api:
            r=handoff(7,1,0,1,request=api);api.assert_not_called()
        self.assertEqual(r['status'],'EARLY_FAILURE_NO_RECURSIVE_DISPATCH')
    def test_horizon_stops_recursion(self):
        with patch('run_public_collector.api') as api:
            handoff(0,1000,2,1,request=api);api.assert_not_called()
    def test_disabled_workflow_not_reenabled(self):
        with patch('run_public_collector.api',return_value=(200,{'state':'disabled_manually'})) as api:
            r=handoff(7,1000,2,1,request=api)
        self.assertEqual(r['status'],'WORKFLOW_DISABLED_NO_DISPATCH');self.assertEqual(api.call_count,1)
    def test_queued_successor_not_duplicated(self):
        with patch('run_public_collector.api', side_effect=[(200,{'state':'active'}),(200,{'workflow_runs':[{'id':2,'status':'pending','head_branch':'main'}]})]) as api:
            r=handoff(7,1000,2,1,request=api)
        self.assertEqual(r['successor_run_id'],2);self.assertEqual(api.call_count,2)
    def test_one_bounded_successor_not_claimed_running(self):
        with patch('run_public_collector.api', side_effect=[(200,{'state':'active'}),(200,{'workflow_runs':[{'id':1,'status':'in_progress','head_branch':'main'}]}),(204,{})]) as api:
            r=handoff(7,1000,2,1,request=api)
        self.assertEqual(api.call_count,3);self.assertFalse(r['successor_started'])
        self.assertEqual(api.call_args.args[1]['inputs']['handoff_remaining'],'6')
    def test_failed_dispatch_does_not_loop(self):
        with patch('run_public_collector.api', side_effect=[(200,{'state':'active'}),(200,{'workflow_runs':[]}),(500,{})]) as api:
            with self.assertRaises(RuntimeError): handoff(7,1000,2,1,request=api)
        self.assertEqual(api.call_count,3)


class ArchiveAndSoak(unittest.TestCase):
    def fixture(self):
        m={'schema':core.CORE_SCHEMA,'generated_at':STAMP,'status':'OK','summary':{'core_ok':True}}
        return {'manifest.json':json.dumps(m).encode(),'owned-quotes.json':json.dumps(envelope({'rows':[quote(p) for p in PRIORITY]})['body']).encode(),
                **{'candidates/'+p+'.json':json.dumps(candidate(p)['body']).encode() for p in PRIORITY}}
    def test_archive_does_not_refresh_source_time(self):
        with patch.dict(core.CFG, {'quotes_pairs':PRIORITY}):
            m=archive_manifest(self.fixture(),'exact-sha',NOW)
        self.assertEqual(m['generated_at'],STAMP);self.assertEqual(m['source_core_sha'],'exact-sha')
        self.assertEqual(m['status'],'OK');self.assertFalse(m['is_independent_freshness_fallback'])
    def test_archive_recomputes_expired_quote_evidence(self):
        with patch.dict(core.CFG, {'quotes_pairs':PRIORITY}):
            m=archive_manifest(self.fixture(),'exact-sha',NOW+timedelta(seconds=601))
        self.assertEqual(m['status'],'PARTIAL');self.assertEqual(m['generated_at'],STAMP)
    def test_no_24h_claim_from_one_good_sample(self):
        r=sampling_report([{'generated_at':STAMP,'owned_complete':True,'run_id':'one'}])
        self.assertEqual(r['status'],'NOT_YET_24H');self.assertFalse(r['production_ready'])
    def test_gaps_and_multiple_runs_not_silently_erased(self):
        r=sampling_report([{'generated_at':STAMP,'owned_complete':True,'run_id':'one'},
             {'generated_at':(NOW+timedelta(hours=25)).isoformat(),'owned_complete':False,'run_id':'two'}])
        self.assertEqual(r['gaps_over_600s'],1);self.assertEqual(r['owned_complete_sample_pct'],50)
        self.assertFalse(r['continuous_uptime_proven']);self.assertEqual(len(r['distinct_run_ids']),2)
    def test_only_one_workflow_fetches_market(self):
        root=Path(__file__).resolve().parents[1]
        core_text=(root/'.github/workflows/collect-core.yml').read_text()
        m1_text=(root/'.github/workflows/collect.yml').read_text()
        self.assertIn('run_public_collector.py core', core_text)
        self.assertIn('run_public_collector.py archive',m1_text)
        self.assertNotIn('python scripts/collect.py',m1_text)
        self.assertNotIn('workflow_run:',core_text)


if __name__=='__main__':
    unittest.main()
