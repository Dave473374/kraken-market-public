import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import engine as e
import run as runner

P = json.loads(Path(__file__).with_name('protocol.json').read_text())
NOW = 1791007200.0  # 2026-10-03 06:00 UTC, fixed test fixture only.


def body(now=NOW, pair='LINKEUR', ask=112.1, bid=112):
    end = int(now//e.STEP)*e.STEP
    rows = []
    for i in range(60):
        price = 100+i*.2
        rows.append([end-(60-i)*e.STEP,price,price+1,price-1,price+.1,price,100,10])
    source = {'ok':True,'retrieved_at':e.iso(now-2),'source_url':'https://api.kraken.com/0/public/Depth?pair='+pair}
    return {'schema':e.WORKER,'contains_account_data':False,'private_exchange_api':False,
        'automatic_trading':False,'data_health':'DATA_OK','generated_at':e.iso(now-1),
        'pair_metadata':{'altname':pair,'base_display':pair[:-3],'aclass_base':'currency','status':'online',
                         'quote_display':'EUR','country_filter':'SI','execution_venue':'international'},
        'metadata_source':copy.deepcopy(source),
        'timestamped_spread':{'quality':'VERIFIED','bid':bid,'ask':ask,'observed_at':e.iso(now-3),'source':copy.deepcopy(source)},
        'depth':{'quality':'VERIFIED','best_bid':bid,'best_ask':ask,'source':copy.deepcopy(source),
                 'depth_each_side':{'bids':{'notional_quote_lower_bound':10000},'asks':{'notional_quote_lower_bound':10000}}},
        'fx':{'quality':'VERIFIED','conversion':'IDENTITY'},
        'closed_4h':{'quality':'VERIFIED','source':copy.deepcopy(source),'interval_minutes':240,
                     'unfinished_final_bar_excluded':True,'history_has_gaps':False,
                     'latest_closed_bar_end':e.iso(end),'recent_closed_rows':rows},
        'ticker':{'source':copy.deepcopy(source),'turnover_24h_quote':100000}}


def observation(now=NOW, pair='LINKEUR', ask=112.1, bid=112):
    obs,err = e.read_observation(body(now,pair,ask,bid),pair,now,P)
    assert err is None, err
    return obs


def idea(obs, protocol):
    return {'family':'TEST_ONLY','stop':90.0,'max_entry':113.0,'bar_end':obs['bar_end'],'atr':2.0}


def opened():
    s = e.initial_state(P,NOW)
    with patch.object(e,'setup',side_effect=idea):
        s,events = e.advance(s,{'LINKEUR':observation()},P,NOW)
        s,events2 = e.advance(s,{'LINKEUR':observation(NOW+3600)},P,NOW+3600)
    return s,events+events2


class EvidenceTests(unittest.TestCase):
    def test_good_read_has_no_crosscheck_or_convert_claim(self):
        obs,err = e.read_observation(body(),'LINKEUR',NOW,P)
        self.assertIsNone(err)
        self.assertEqual(obs['volume_ratio'],1)
        self.assertEqual(obs['convert_quote'],'NOT_OBSERVED')
        self.assertEqual(obs['independent_crosscheck'],'NOT_COLLECTED_RESEARCH_ONLY')
    def test_bad_schema_or_unsafe_source(self):
        for key,val in [('schema','OTHER'),('contains_account_data',True),('private_exchange_api',True),('automatic_trading',True),('data_health','PARTIAL_DATA')]:
            b = body(); b[key] = val
            self.assertIsNone(e.read_observation(b,'LINKEUR',NOW,P)[0])
    def test_pair_mismatch(self):
        self.assertIsNone(e.read_observation(body(),'GALAEUR',NOW,P)[0])
    def test_eligibility_fail_closed(self):
        for key,val in [('country_filter','US'),('status','offline'),('aclass_base','tokenized_asset'),('execution_venue','other')]:
            b = body(); b['pair_metadata'][key] = val
            self.assertIsNone(e.read_observation(b,'LINKEUR',NOW,P)[0])
    def test_excluded_stable_base(self):
        b = body(); b['pair_metadata']['base_display']='USDC'
        self.assertIsNone(e.read_observation(b,'LINKEUR',NOW,P)[0])
    def test_spread_future_or_stale(self):
        for stamp in [e.iso(NOW+1),e.iso(NOW-601),None,'bad']:
            b=body(); b['timestamped_spread']['observed_at']=stamp
            self.assertIsNone(e.read_observation(b,'LINKEUR',NOW,P)[0])
    def test_fresh_envelope_does_not_hide_old_depth(self):
        b=body(); b['depth']['source']['retrieved_at']=e.iso(NOW-601)
        self.assertIsNone(e.read_observation(b,'LINKEUR',NOW,P)[0])
    def test_invalid_book_prices(self):
        for value in [0,-1,True,float('nan'),float('inf')]:
            b=body(); b['depth']['best_bid']=value
            self.assertIsNone(e.read_observation(b,'LINKEUR',NOW,P)[0])
    def test_crossed_book(self):
        b=body(); b['depth']['best_ask']=50
        self.assertIsNone(e.read_observation(b,'LINKEUR',NOW,P)[0])
    def test_same_provider_price_conflict(self):
        b=body(); b['timestamped_spread']['bid']=100;b['timestamped_spread']['ask']=101
        self.assertIsNone(e.read_observation(b,'LINKEUR',NOW,P)[0])
    def test_open_candle_not_used(self):
        b=body(); b['closed_4h']['recent_closed_rows'][-1][0]+=e.STEP
        self.assertIsNone(e.read_observation(b,'LINKEUR',NOW,P)[0])
    def test_gapped_and_duplicate_candles(self):
        for change in [-e.STEP,e.STEP]:
            b=body(); b['closed_4h']['recent_closed_rows'][20][0]+=change
            self.assertIsNone(e.read_observation(b,'LINKEUR',NOW,P)[0])
    def test_count_flag_is_not_exported_history(self):
        b=body(); b['closed_4h']['closed_bars_received']=720
        b['closed_4h']['recent_closed_rows']=b['closed_4h']['recent_closed_rows'][-10:]
        self.assertIsNone(e.read_observation(b,'LINKEUR',NOW,P)[0])
    def test_unfinished_flag_required(self):
        b=body(); b['closed_4h']['unfinished_final_bar_excluded']=False
        self.assertIsNone(e.read_observation(b,'LINKEUR',NOW,P)[0])
    def test_zero_baseline_volume_is_unknown(self):
        b=body()
        for row in b['closed_4h']['recent_closed_rows'][-48:-6]:row[6]=0
        obs,err=e.read_observation(b,'LINKEUR',NOW,P)
        self.assertIsNone(err);self.assertIsNone(obs['volume_ratio'])
    def test_fx_required_non_eur(self):
        b=body(pair='LINKUSD');b['pair_metadata']['quote_display']='USD'
        self.assertIsNone(e.read_observation(b,'LINKUSD',NOW,P)[0])
    def test_fx_conversion_uses_sides(self):
        b=body(pair='LINKUSD');b['pair_metadata']['quote_display']='USD'
        b['fx']={'quality':'VERIFIED','eur_per_quote_bid':.89,'eur_per_quote_ask':.9,
                 'source':b['metadata_source'],'observed_at':e.iso(NOW-3)}
        obs,err=e.read_observation(b,'LINKUSD',NOW,P)
        self.assertIsNone(err);self.assertAlmostEqual(obs['ask_eur'],112.1*.9)
    def test_alias(self):
        b=body(pair='XDGEUR')
        self.assertIsNotNone(e.read_observation(b,'DOGEEUR',NOW,P)[0])


class SimulationTests(unittest.TestCase):
    def test_no_fill_on_decision_observation(self):
        s=e.initial_state(P,NOW)
        with patch.object(e,'setup',side_effect=idea):s,ev=e.advance(s,{'LINKEUR':observation()},P,NOW)
        self.assertEqual(len(s['pending']),1);self.assertEqual(len(s['cohorts']),0)
        self.assertEqual(ev[0]['kind'],'PENDING_ENTRY')
    def test_next_observation_fill_only(self):
        s,ev=opened()
        self.assertEqual(len(s['cohorts']),1)
        self.assertTrue(all(x['actual_execution'] is False for x in ev))
        self.assertEqual(next(iter(s['cohorts'].values()))['entered_at'],e.iso(NOW+3600))
    def test_repeat_snapshot_never_fills_again(self):
        s,ev=opened();before=copy.deepcopy(s['cohorts'])
        s,ev=e.advance(s,{'LINKEUR':observation(NOW+3600)},P,NOW+3601)
        self.assertEqual(before,s['cohorts']);self.assertEqual(ev,[])
    def test_stale_quote_cannot_close(self):
        s,ev=opened();b=observation(NOW+3600,bid=89,ask=89.1)
        s,ev=e.advance(s,{'LINKEUR':b},P,NOW+7200)
        self.assertFalse(any(x['kind']=='VIRTUAL_EXIT' for x in ev))
    def test_missing_quotes_preserve_positions(self):
        s,ev=opened();before=copy.deepcopy(s['cohorts'])
        s,ev=e.advance(s,{},P,NOW+10800)
        self.assertEqual(before,s['cohorts']);self.assertGreater(s['gap_count'],0)
    def test_stale_nav_is_unknown(self):
        s,ev=opened();r=e.report(s,P,NOW+7200,{})
        self.assertIsNone(r['books']['STRUCTURE_2R']['BASE']['equity_eur_estimate'])
    def test_stop_uses_observed_bid_not_ideal_stop(self):
        s,ev=opened();o=observation(NOW+7200,bid=85,ask=85.1)
        s,ev=e.advance(s,{'LINKEUR':o},P,NOW+7200)
        c=next(iter(s['cohorts'].values()))
        self.assertEqual(c['paths']['STRUCTURE_2R']['exits'][0]['bid_eur'],85)
        self.assertEqual(c['paths']['HOLD_30D']['remaining'],1)
    def test_half_exit_once(self):
        s,ev=opened()
        for offset in [7200,10800]:s,ev=e.advance(s,{'LINKEUR':observation(NOW+offset,bid=146,ask=146.1)},P,NOW+offset)
        path=next(iter(s['cohorts'].values()))['paths']['HALF_1_5R_TRAIL']
        self.assertEqual(path['remaining'],.5);self.assertEqual(len(path['exits']),1)
    def test_time_exit(self):
        s,ev=opened();t=NOW+3600+86400
        s,ev=e.advance(s,{'LINKEUR':observation(t,bid=112,ask=112.1)},P,t)
        path=next(iter(s['cohorts'].values()))['paths']['TIME_24H']
        self.assertEqual(path['remaining'],0)
        self.assertEqual(path['exits'][0]['reason'],'TIME_STOP_NO_PROGRESS')
    def test_pending_expiry(self):
        s=e.initial_state(P,NOW)
        with patch.object(e,'setup',side_effect=idea):s,ev=e.advance(s,{'LINKEUR':observation()},P,NOW)
        s,ev=e.advance(s,{},P,NOW+P['pending_expiry_seconds']+1)
        self.assertFalse(s['pending']);self.assertTrue(any(x['kind']=='PENDING_EXPIRED' for x in ev))
    def test_pending_chase_is_invalidated(self):
        s=e.initial_state(P,NOW)
        with patch.object(e,'setup',side_effect=idea):s,ev=e.advance(s,{'LINKEUR':observation()},P,NOW)
        s,ev=e.advance(s,{'LINKEUR':observation(NOW+3600,bid=119,ask=119.1)},P,NOW+3600)
        self.assertFalse(s['cohorts']);self.assertFalse(s['pending'])
    def test_frozen_protocol_no_reset(self):
        s,ev=opened();p=copy.deepcopy(P);p['max_open_cohorts']+=1
        with self.assertRaisesRegex(ValueError,'NO_RESET'):e.advance(s,{},p,NOW+7200)
    def test_non_monotonic_time(self):
        s,ev=opened()
        with self.assertRaises(ValueError):e.advance(s,{},P,NOW)
    def test_costs_reduce_outcome_and_cash(self):
        s,ev=opened();c=next(iter(s['cohorts'].values()));p=c['paths']['STRUCTURE_2R']
        self.assertLess(e.path_value(c,p,.025,112),e.path_value(c,p,.0125,112))
        self.assertEqual(e.free_cash(s,P,'STRUCTURE_2R',.0175),980)
    def test_no_mutation_of_input_state(self):
        s,ev=opened();original=copy.deepcopy(s)
        e.advance(s,{},P,NOW+7200)
        self.assertEqual(s,original)
    def test_report_does_not_fabricate_production_comparison(self):
        s=e.initial_state(P,NOW);r=e.report(s,P,NOW,{})
        self.assertIsNone(r['realized_user_pnl']);self.assertFalse(r['auto_promotion'])
        self.assertEqual(r['unique_entry_cohorts'],0)
        self.assertEqual(r['evidence_stage'],'COLLECTING')
        self.assertIsNone(r['books']['STRUCTURE_2R']['BASE']['profit_factor_estimate'])
    def test_core_and_memes_observe_only(self):
        for asset in P['observe_only_assets']:
            o=observation();o['asset']=asset
            self.assertIsNone(e.setup(o,P))
    def test_liquidity_independent_of_volume_ratio(self):
        o=observation();o['volume_ratio']=None
        self.assertTrue(e.liquidity_pass(o,P))
        o['depth_eur']['asks']=1
        self.assertFalse(e.liquidity_pass(o,P))


class TransportTests(unittest.TestCase):
    def test_no_private_endpoint(self):
        reader=runner.PublicReader(P)
        self.assertEqual(reader.get('/0/private/AddOrder')['read_error'],'ENDPOINT_NOT_ALLOWED')
        self.assertEqual(reader.calls,0)
    def test_bounded_requests(self):
        reader=runner.PublicReader(P);reader.calls=P['max_public_requests_per_run']
        self.assertIn('BUDGET',reader.get('/universe.json')['read_error'])
    def test_rate_limit_stops_public_calls(self):
        reader=runner.PublicReader(P);reader.rate_limited=True
        self.assertIn('429',reader.get('/universe.json')['read_error'])
    def test_three_new_max_watched_retained(self):
        s=e.initial_state(P,NOW)
        uni={'schema':e.WORKER,'contains_account_data':False,'generated_at':e.iso(NOW-1),
             'candidates':[{'altname':f'A{i}EUR','status':'online','country_filter':'SI'} for i in range(30)]}
        pairs,watched,discovered=runner.plan_pairs(s,{'quotes_pairs':['LINKEUR','BNBEUR']},uni,P,NOW)
        self.assertEqual(len(discovered),3);self.assertTrue(set(watched).issubset(pairs))
        self.assertEqual(len(s['retained_candidates']),20)
    def test_retention_expiry(self):
        s=e.initial_state(P,NOW);s['retained_candidates']={'OLDEUR':{'seen_unix':NOW-49*3600}}
        runner.plan_pairs(s,{'quotes_pairs':[]},{},P,NOW)
        self.assertFalse(s['retained_candidates'])
    def test_open_shadow_kept_when_mover_disappears(self):
        s,ev=opened();pairs,_,_=runner.plan_pairs(s,{'quotes_pairs':[]},{},P,NOW+7200)
        self.assertIn('LINKEUR',pairs)
    def test_files_are_local_inputs_only(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIn('read_error',runner.read_file(Path(d)/'missing.json'))
    def test_code_has_no_exchange_write_or_production_output(self):
        text=Path(e.__file__).read_text()
        self.assertNotIn('urllib',text);self.assertNotIn('subprocess',text)
        text=Path(runner.__file__).read_text()
        self.assertIn("method='GET'",text);self.assertNotIn("method='POST'",text)


class EntryPatternTests(unittest.TestCase):
    def test_consolidation_pattern(self):
        o=observation();p=copy.deepcopy(P);p['min_stop_distance_pct']=.1;p['min_target_room_over_base_cost_multiple']=0
        for row in o['rows'][-7:-1]:row[1:5]=[111,111.3,110.7,111]
        o['rows'][-1][1:5]=[111.8,112.2,111.7,112]
        self.assertEqual(e.setup(o,p)['family'],'CONSOLIDATION_BREAKOUT')
    def test_retest_pattern(self):
        o=observation(ask=110.6,bid=110.5);p=copy.deepcopy(P);p['min_stop_distance_pct']=.1;p['min_target_room_over_base_cost_multiple']=0
        for row in o['rows'][-22:-2]:row[1:5]=[108,110,106,108]
        o['rows'][-2][1:5]=[110.5,111.5,110.2,111]
        o['rows'][-1][1:5]=[110.8,111,109.8,110.5]
        self.assertEqual(e.setup(o,p)['family'],'BREAKOUT_RETEST')
    def test_pullback_pattern(self):
        o=observation();p=copy.deepcopy(P);p['min_stop_distance_pct']=.1;p['min_target_room_over_base_cost_multiple']=0
        o['rows'][-2][1:5]=[110,110.5,108,109.8]
        o['rows'][-1][1:5]=[111,112.5,110.8,112]
        self.assertEqual(e.setup(o,p)['family'],'TREND_PULLBACK')
    def test_cost_gate_blocks_inadequate_room(self):
        o=observation()
        for row in o['rows'][-7:-1]:row[1:5]=[111,111.3,110.7,111]
        o['rows'][-1][1:5]=[111.8,112.2,111.7,112]
        self.assertIsNone(e.setup(o,P))

class PipelineIntegrationTests(unittest.TestCase):
    def test_two_runs_persist_without_touching_production(self):
        import time, urllib.parse
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'production';(root/'config').mkdir(parents=True)
            (root/'config'/'assets.json').write_text(json.dumps({'quotes_pairs':['LINKEUR','BNBEUR'],'pair_aliases':{}}))
            (root/'data').mkdir();(root/'data'/'untouched.json').write_text('{"production":true}')
            out=Path(d)/'shadow-output'/'research'/'v23'
            argv=['run.py','--main-data',str(root/'data'),'--core-data',str(Path(d)/'core'),'--out',str(out)]
            def get(path):
                now=time.time()
                if path=='/universe.json':return {'schema':e.WORKER,'contains_account_data':False,'generated_at':e.iso(now-1),'candidates':[]}
                pair=urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)['pair'][0]
                return body(now,pair)
            with patch.object(runner,'ROOT',root),patch('sys.argv',argv),patch.object(runner.PublicReader,'get',side_effect=get):
                self.assertEqual(runner.main(),0)
                self.assertEqual(runner.main(),0)
            state=json.loads((out/'state.json').read_text())
            report=json.loads((out/'report.json').read_text())
            self.assertEqual(state['run_count'],2)
            self.assertEqual(len(report['market_coverage']['valid_pairs']),5)
            self.assertEqual((root/'data'/'untouched.json').read_text(),'{"production":true}')
            self.assertFalse(report['auto_promotion']);self.assertTrue((out/'runs').exists())
    def test_oldest_module_age_rechecked_at_publication(self):
        s,ev=opened();o=observation(NOW+7200)
        o['evidence_oldest_at']=e.iso(NOW+7200-601)
        before=copy.deepcopy(s['cohorts'])
        s,ev=e.advance(s,{'LINKEUR':o},P,NOW+7200)
        self.assertEqual(before,s['cohorts'])

if __name__ == '__main__':
    unittest.main()
