"""Offline provider fallback/cooldown tests. Never production price evidence."""
import unittest
from unittest.mock import patch
from independent_reference import IndependentReferences
from test_direct_kraken import AT, STAMP, source

class ReferenceCooldownTests(unittest.TestCase):
    def test_long_cg_cooldown_survives_new_collector(self):
        cache={}; first=IndependentReferences(cache)
        first.cg.until=AT+3600
        first._save_cooldowns()
        second=IndependentReferences(cache)
        with patch('direct_kraken.time.time',return_value=AT+300), patch('direct_kraken.urllib.request.urlopen') as net:
            result=second.get('MEGA')
        net.assert_not_called()
        self.assertEqual(second.cg.until,AT+3600)
        self.assertEqual(result['quality'],'MISSING')
    def test_cg_outage_uses_bounded_core_coinbase(self):
        x=IndependentReferences({})
        x.cg.get=lambda *a,**k:{'data':None,'source':source(False)}
        x.cb.get=lambda *a,**k:{'data':{'bid':'100','ask':'100.1','time':STAMP},'source':source()}
        with patch('direct_kraken.time.time',return_value=AT):r=x.get('BTC')
        self.assertEqual(r['provider'],'Coinbase Exchange')
        self.assertFalse(r['is_kraken_executable']); self.assertFalse(r['action_approved'])
    def test_both_cooldowns_survive_without_fabricated_quote(self):
        x=IndependentReferences({});x.cg.until=AT+3600;x.cb.until=AT+7200;x._save_cooldowns()
        y=IndependentReferences(x.cache)
        with patch('direct_kraken.time.time',return_value=AT+300),patch('direct_kraken.urllib.request.urlopen') as net:
            r=y.get('BTC')
        net.assert_not_called();self.assertEqual(r['quality'],'MISSING')
        self.assertEqual(y.cb.until,AT+7200)
    def test_corrupt_provider_state_is_not_erased(self):
        with self.assertRaises(ValueError):
            IndependentReferences({'independent:coingecko:cooldown':{'data':[]}})
    def test_operational_cache_does_not_claim_market_price(self):
        x=IndependentReferences({});x._save_cooldowns()
        row=x.cache['independent:coingecko:cooldown']
        self.assertEqual(row['source']['status'],'LOCAL_RATE_LIMIT_STATE_NOT_MARKET_EVIDENCE')
        self.assertNotIn('price',row['data'])

if __name__=='__main__':unittest.main()
