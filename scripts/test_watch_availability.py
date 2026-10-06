"""Offline synthetic eligibility checks; not market evidence or listing claims."""
import copy
import unittest
from datetime import datetime, timedelta, timezone
from watch_availability import unavailable_pair_evidence, watchlist_status

NOW = datetime(2026, 10, 6, 5, 16, tzinfo=timezone.utc)
SCHEMA = 'KRAKEN_PUBLIC_TRANSPORT_V2.0.2'
URL = 'https://api.kraken.com/0/public/AssetPairs?pair=IOEUR&country_code=SI&aclass_base=currency&execution_venue=international'


def fixture():
    return {'ok': True, 'body': {'schema': SCHEMA, 'generated_at': NOW.isoformat(),
        'data_health': 'DATA_UNAVAILABLE', 'stage': 'AssetPairs',
        'source': {'source_url': URL, 'method': 'GET', 'http_status': 200,
            'ok': False, 'status': 'KRAKEN_API_ERROR', 'errors': ['EQuery:Unknown asset pair'],
            'retrieved_at': NOW.isoformat()}}}


class WatchAvailabilityTests(unittest.TestCase):
    def test_exact_fresh_pair_rejection_is_not_price_evidence(self):
        s = unavailable_pair_evidence(fixture(), 'IOEUR', SCHEMA, now=NOW)
        self.assertEqual(s['quality'], 'VERIFIED')
        self.assertFalse(s['fresh_quote_evidence']); self.assertIsNone(s['trade_signal'])
        self.assertEqual(s['whole_asset_availability'], 'NOT_DETERMINED')
    def test_stale_future_and_wrong_schema_remain_unknown(self):
        for now in (NOW-timedelta(seconds=1), NOW+timedelta(seconds=601)):
            self.assertEqual(unavailable_pair_evidence(fixture(), 'IOEUR', SCHEMA, now=now)['quality'], 'UNKNOWN')
        self.assertEqual(unavailable_pair_evidence(fixture(), 'IOEUR', 'wrong', now=NOW)['quality'], 'UNKNOWN')
    def test_different_pair_region_origin_or_endpoint_not_accepted(self):
        for url in (URL.replace('IOEUR','IOUSD'), URL.replace('SI','US'), URL.replace('api.kraken.com','other.test'),
                    URL.replace('/AssetPairs','/Ticker'), URL+'&pair=IOUSD',URL.replace('international','derivatives')):
            r=fixture(); r['body']['source']['source_url']=url
            self.assertEqual(unavailable_pair_evidence(r,'IOEUR',SCHEMA,now=NOW)['quality'],'UNKNOWN')
    def test_transport_or_other_api_errors_not_eligibility(self):
        for errors in ([], ['EAPI:Rate limit exceeded'], ['EQuery:Unknown asset pair','EGeneral:Temporary error']):
            r=fixture();r['body']['source']['errors']=errors
            self.assertEqual(unavailable_pair_evidence(r,'IOEUR',SCHEMA,now=NOW)['quality'],'UNKNOWN')
        self.assertEqual(unavailable_pair_evidence({'ok':False,'error':'HTTP_429'},'IOEUR',SCHEMA,now=NOW)['quality'],'UNKNOWN')
    def test_malformed_sources_fail_closed(self):
        for value in (None, [], 4, 'bad'):
            r=fixture();r['body']['source']=value
            self.assertEqual(unavailable_pair_evidence(r,'IOEUR',SCHEMA,now=NOW)['quality'],'UNKNOWN')
    def test_watch_completeness_does_not_relabel_unavailable_price(self):
        a={'IOEUR':unavailable_pair_evidence(fixture(),'IOEUR',SCHEMA,now=NOW)}
        s={'IOEUR':{'fresh_quote_evidence':False,'closed_4h_current':False},
           'HYPEEUR':{'fresh_quote_evidence':True,'closed_4h_current':True}}
        self.assertEqual(watchlist_status(['IOEUR','HYPEEUR'],s,a),'CHECKED_WITH_UNAVAILABLE_PAIRS')
        self.assertFalse(s['IOEUR']['fresh_quote_evidence'])
        s['HYPEEUR']['fresh_quote_evidence']=False
        self.assertEqual(watchlist_status(['IOEUR','HYPEEUR'],s,a),'PARTIAL_DATA')
    def test_new_valid_listing_no_permanent_exclusion(self):
        s={'IOEUR':{'fresh_quote_evidence':True,'closed_4h_current':True}}
        self.assertEqual(watchlist_status(['IOEUR'],s,{}),'DATA_OK')
    def test_current_source_time_also_required(self):
        r=fixture();r['body']['source']['retrieved_at']=(NOW-timedelta(seconds=601)).isoformat()
        self.assertEqual(unavailable_pair_evidence(r,'IOEUR',SCHEMA,now=NOW)['quality'],'UNKNOWN')

if __name__=='__main__':unittest.main()
