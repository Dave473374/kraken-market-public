"""Offline regressions derived from public diagnostic shapes, not live quotes."""
import unittest
from unittest.mock import patch
import direct_kraken as d
from independent_reference import IndependentReferences, NIGHT_CARDANO, compare_reference
from test_direct_kraken import ohlc, AT, source

class ObservedShapeTests(unittest.TestCase):
    def test_observed_flat_zero_volume_bar_preserves_zero(self):
        r=ohlc();r['data']['XXBTZEUR'][-20][1:]=[101,101,101,101,0,0,0]
        x=d.closed_candles(r,'XXBTZEUR',AT)
        self.assertEqual(x['quality'],'VERIFIED')
        self.assertEqual(x['recent_closed_rows'][-19][5:],[0,0,0])
    def test_zero_vwap_with_trades_is_invalid(self):
        r=ohlc();r['data']['XXBTZEUR'][-20][5]=0
        self.assertEqual(d.closed_candles(r,'XXBTZEUR',AT)['quality'],'MISSING')
    def test_polygon_midnight_is_not_cardano_night(self):
        x=IndependentReferences({})
        x.cg.get=lambda *a,**k:{'source':source(),'data':[{'id':'midnight','symbol':'night','platforms':{'polygon-pos':'wrong'}}]}
        self.assertEqual(x.get('NIGHT')['quality'],'MISSING')
    def test_correct_name_without_cardano_contract_stays_missing(self):
        x=IndependentReferences({})
        x.cg.get=lambda *a,**k:{'source':source(),'data':[{'id':'midnight-3','symbol':'night','platforms':{'cardano':'wrong'}}]}
        self.assertEqual(x.get('NIGHT')['quality'],'MISSING')
    def test_material_cross_provider_disagreement_blocks_action_check(self):
        r={'quality':'VERIFIED_PROVIDER_REFERENCE','asset_symbol':'NIGHT','quote':'EUR',
           'source':source(),'observed_unix':AT,'price':.00000904,'provider':'CoinGecko'}
        with patch('direct_kraken.time.time',return_value=AT):x=compare_reference(r,.044,1,'NIGHT')
        self.assertEqual(x['quality'],'CONFLICT');self.assertFalse(x['speculative_2pct_pass'])
        self.assertFalse(x['action_approved'])
    def test_correct_contract_and_price_reference_do_not_imply_trade(self):
        x=IndependentReferences({})
        def api(path,params):
            if path.endswith('list'):return {'source':source(),'data':[{'id':'midnight-3','symbol':'night','name':'Midnight','platforms':{'cardano':NIGHT_CARDANO}}]}
            return {'source':source(),'data':{'midnight-3':{'eur':.044,'last_updated_at':AT}}}
        x.cg.get=api
        with patch('direct_kraken.time.time',return_value=AT):
            r=x.get('NIGHT');c=compare_reference(r,.0441,1,'NIGHT')
        self.assertEqual(r['asset_id'],'midnight-3');self.assertTrue(c['liquid_1pct_pass'])
        self.assertFalse(c['action_approved'])

if __name__=='__main__':unittest.main()
