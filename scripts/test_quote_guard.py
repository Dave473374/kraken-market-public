import copy
import unittest
from datetime import datetime, timedelta, timezone
from quote_guard import coverage

NOW = datetime(2026, 10, 3, 6, tzinfo=timezone.utc)
PAIRS = ['DOGEEUR', 'LINKEUR']
ALIASES = {'DOGEEUR': 'XDGEUR'}

def fixture():
    return {'ok': True, 'body': {'schema': 'TEST_SCHEMA', 'unresolved': [], 'rows': [
        {'altname': p, 'ticker': {'bid': 1, 'ask': 1.01, 'source': {'ok': True, 'retrieved_at': NOW.isoformat()}}}
        for p in ['XDGEUR', 'LINKEUR']]}}

def check(result, now=NOW):
    return coverage(result, PAIRS, 'TEST_SCHEMA', ALIASES, now)

class QuoteIntegrityTests(unittest.TestCase):
    def test_valid_support_is_not_executable(self):
        r = check(fixture())
        self.assertTrue(r['research_usable'])
        self.assertFalse(r['executable_evidence'])
    def test_duplicate_same_row_count_is_not_coverage(self):
        r = fixture(); r['body']['rows'][1] = copy.deepcopy(r['body']['rows'][0])
        self.assertFalse(check(r)['research_usable'])
        self.assertEqual(check(r)['missing_pairs'], ['LINKEUR'])
    def test_unexpected_replacement(self):
        r = fixture(); r['body']['rows'][1]['altname'] = 'UNEXPECTEDEUR'
        self.assertFalse(check(r)['research_usable'])
    def test_schema_and_transport(self):
        for key, value in [('ok', False), ('body', []), ('body', {'schema': 'OTHER'})]:
            r = fixture(); r[key] = value
            self.assertFalse(check(r)['research_usable'])
    def test_malformed_fields_fail_closed(self):
        for value in [None, False, [], 'wrong']:
            r = fixture(); r['body']['rows'][0]['ticker'] = value
            self.assertFalse(check(r)['research_usable'])
    def test_bad_prices_fail_closed(self):
        for value in [0, -1, True, float('nan'), float('inf'), None, 'bad', 2]:
            r = fixture(); r['body']['rows'][0]['ticker']['bid'] = value
            self.assertFalse(check(r)['research_usable'])
    def test_time_stale_future_naive_and_missing(self):
        for value in [None, 'bad', NOW.replace(tzinfo=None).isoformat(),
                      (NOW-timedelta(seconds=601)).isoformat(), (NOW+timedelta(seconds=1)).isoformat()]:
            r = fixture(); r['body']['rows'][0]['ticker']['source']['retrieved_at'] = value
            self.assertFalse(check(r)['research_usable'])
    def test_publication_recheck(self):
        r = fixture()
        self.assertTrue(check(r)['research_usable'])
        self.assertFalse(check(r, NOW+timedelta(minutes=11))['research_usable'])
    def test_unresolved(self):
        r = fixture(); r['body']['unresolved'] = ['MISSINGEUR']
        self.assertFalse(check(r)['research_usable'])


class CollectorIntegrationTests(unittest.TestCase):
    def test_wrapper_uses_pair_identity(self):
        import collect
        from unittest.mock import patch
        r=fixture();r['body']['schema']=collect.EXPECTED
        r['body']['rows'][1]=copy.deepcopy(r['body']['rows'][0])
        with patch.object(collect,'CFG',{'quotes_pairs':PAIRS}), patch.object(collect,'ALIASES',ALIASES):
            self.assertFalse(collect.quotes_research_usable(r,PAIRS))
    def test_publication_recheck_is_present(self):
        from pathlib import Path
        text=Path(__file__).with_name('collect.py').read_text()
        self.assertIn('quote_coverage = coverage(quotes, logical_quotes, EXPECTED, ALIASES)',text)
        self.assertIn('manifest["files"]["owned-quotes.json"].update(quote_coverage)',text)

if __name__ == '__main__':
    unittest.main()
