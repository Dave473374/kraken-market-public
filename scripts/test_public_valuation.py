import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from public_valuation import EXTRA_PAIRS, build_report, price_row, source_ok

AT = 1800000000.0

def source(path, seconds=0):
    d = datetime.fromtimestamp(AT + seconds, timezone.utc)
    return {'ok': True, 'method': 'GET', 'http_status': 200,
            'retrieved_at': d.isoformat(), 'source_url': 'https://api.kraken.com/0/public/' + path,
            'http_metadata': {'Date': format_datetime(d, usegmt=True), 'Age': '0'}}

def fixture(pair='ZECEUR'):
    b = pair[:-3]
    return {'pair_metadata': {'wsname': b + '/EUR', 'status': 'online',
                             'aclass_base': 'currency', 'execution_venue': 'international',
                             'country_filter': 'SI'},
            'metadata_source': source('AssetPairs'),
            'depth': {'quality': 'VERIFIED', 'best_bid': 10, 'best_ask': 12,
                      'source': source('Depth?pair=' + pair)}}

class FakeClient:
    def __init__(self): self.calls = []
    def _resolve(self, pair): return pair, {'altname': pair}
    def _api(self, path, args):
        self.calls.append((path, args))
        return fixture(args['pair'])['depth']
    def _pairmeta(self, key, raw): return fixture(key)['pair_metadata']
    def _meta(self): return {'source': source('AssetPairs')}

class ValuationTests(unittest.TestCase):
    def row(self, x=None, pair='ZECEUR'):
        x = fixture(pair) if x is None else x
        return price_row(pair, x['pair_metadata'], x['metadata_source'], x['depth'], AT)
    def test_valid(self): self.assertEqual(self.row()['mid'], 11)
    def test_wrong_identity(self):
        x = fixture(); x['pair_metadata']['wsname'] = 'BCH/EUR'
        self.assertEqual(self.row(x)['quality'], 'MISSING')
    def test_wrong_quote(self):
        x = fixture(); x['pair_metadata']['wsname'] = 'ZEC/USD'
        self.assertEqual(self.row(x)['quality'], 'MISSING')
    def test_stale(self):
        x = fixture(); x['depth']['source'] = source('Depth', -601)
        self.assertEqual(self.row(x)['quality'], 'MISSING')
    def test_future(self):
        x = fixture(); x['depth']['source'] = source('Depth', 1)
        self.assertEqual(self.row(x)['quality'], 'MISSING')
    def test_bad_prices(self):
        for value in (float('nan'), float('inf'), 0, -1, True):
            with self.subTest(value=value):
                x = fixture(); x['depth']['best_bid'] = value
                self.assertEqual(self.row(x)['quality'], 'MISSING')
    def test_crossed_book(self):
        x = fixture(); x['depth']['best_bid'] = 20
        self.assertEqual(self.row(x)['quality'], 'MISSING')
    def test_bad_metadata(self):
        for k, v in [('status','cancel_only'), ('country_filter','US'), ('execution_venue','other')]:
            with self.subTest(k=k):
                x = fixture(); x['pair_metadata'][k] = v
                self.assertEqual(self.row(x)['quality'], 'MISSING')
    def test_stale_metadata(self):
        x = fixture(); x['metadata_source'] = source('AssetPairs', -601)
        self.assertEqual(self.row(x)['quality'], 'MISSING')
    def test_untrusted_route(self):
        x = source('Depth'); x['source_url'] = 'https://example.org/0/public/Depth'
        self.assertFalse(source_ok(x, AT))
    def test_old_http_date(self):
        x = source('Depth'); x['http_metadata']['Date'] = source('Depth', -601)['http_metadata']['Date']
        self.assertFalse(source_ok(x, AT))
    def test_price_inventory_reuses_existing_books_and_adds_only_five(self):
        pairs = ['XBTEUR','ETHEUR','SOLEUR','DOGEEUR','LTCEUR','SUIEUR','LINKEUR','BNBEUR',
                 'AAVEEUR','ONDOEUR','INJEUR','PENDLEEUR','RENDEREUR','TAOEUR','GALAEUR','NIGHTEUR','MEGAEUR']
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root/'candidates').mkdir()
            for p in pairs: (root/'candidates'/f'{p}.json').write_text(json.dumps(fixture(p)))
            client = FakeClient()
            r = build_report(client, root, pairs, at=AT, depth_parser=lambda raw,key: raw)
            self.assertEqual(r['received_count'], 22)
            self.assertEqual(r['status'], 'COMPLETE')
            self.assertEqual(len(client.calls), 5)
            self.assertTrue(all(p == '/0/public/Depth' for p, a in client.calls))
            self.assertFalse(r['contains_account_data'])
            self.assertIsNone(r['trade_signal'])
    def test_missing_main_price_is_not_silently_zero(self):
        with tempfile.TemporaryDirectory() as d:
            r = build_report(FakeClient(), d, ['XBTEUR'], at=AT, depth_parser=lambda raw,key: raw)
            self.assertEqual(r['status'], 'PARTIAL')
            self.assertEqual(r['missing_pairs'], ['XBTEUR'])
            self.assertNotIn('mid', r['rows'][0])
    def test_unresolved_extra_price_remains_missing(self):
        class Missing(FakeClient):
            def _resolve(self, pair): return None, None
        with tempfile.TemporaryDirectory() as d:
            r = build_report(Missing(), d, [], at=AT, depth_parser=lambda raw,key: raw)
            self.assertEqual(r['missing_pairs'], list(EXTRA_PAIRS))
            self.assertEqual(r['received_count'], 0)

if __name__ == '__main__': unittest.main()
