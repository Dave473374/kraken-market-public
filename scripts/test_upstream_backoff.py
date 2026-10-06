"""Synthetic provider error tests. No network, no actual Kraken price evidence."""
import copy
import json
import unittest
from unittest.mock import patch
from public_transport import PublicGetter, upstream_rate_limit
from collector_runtime import CycleTransport


def payload(code='EGeneral:Too many requests'):
    return {'schema':'KRAKEN_PUBLIC_TRANSPORT_V2.0.2', 'data_health':'PARTIAL_DATA',
        'generated_at':'original', 'btc_same_period_reference': {'quality':'MISSING', 'source': {
            'source_url':'https://api.kraken.com/0/public/OHLC?pair=XBTEUR&interval=240',
            'method':'GET', 'http_status':200, 'ok':False, 'status':'KRAKEN_API_ERROR',
            'errors':[code], 'retrieved_at':'original', 'http_metadata':{}}}}


class Response:
    status=200;headers={'Content-Type':'application/json'}
    def __init__(self,body):self.body=json.dumps(body).encode()
    def read(self,n):return self.body[:n]
    def __enter__(self):return self
    def __exit__(self,*args):return False


class UpstreamBackoffTests(unittest.TestCase):
    def test_embedded_provider_error_is_recognized(self):
        self.assertEqual(upstream_rate_limit(payload())['cooldown_seconds'],60)
        self.assertEqual(upstream_rate_limit(payload())['kind'],'KRAKEN_API_RATE_LIMIT_IN_HTTP200')
    def test_unavailable_pair_is_not_a_rate_limit(self):
        self.assertIsNone(upstream_rate_limit(payload('EQuery:Unknown asset pair')))
    def test_wrong_provider_or_success_not_inferred(self):
        for field,value in (('source_url','https://elsewhere.test/0/public/OHLC'),('ok',True),('status','OTHER')):
            b=payload();b['btc_same_period_reference']['source'][field]=value
            self.assertIsNone(upstream_rate_limit(b))
    def test_long_provider_retry_after_is_respected(self):
        b=payload();b['btc_same_period_reference']['source']['http_metadata']['Retry-After']='180'
        self.assertEqual(upstream_rate_limit(b)['cooldown_seconds'],180)
    def test_success_http_does_not_bypass_provider_cooldown(self):
        body=payload();client=PublicGetter('https://public.test')
        with patch('public_transport.time.monotonic',return_value=100), patch('public_transport.urllib.request.urlopen',return_value=Response(body)) as net:
            result=client.get('/candidate.json'); following=client.get('/candidate.json?pair=OTHER')
        self.assertTrue(result['ok']);self.assertEqual(result['body'],body)
        self.assertEqual(client.cooldown_until,160);self.assertEqual(client.upstream_rate_limit_count,1)
        self.assertFalse(following['http_request_made']);self.assertEqual(net.call_count,1)
    def test_pacing_is_more_conservative_without_extra_retries(self):
        client=CycleTransport('https://public.test')
        self.assertEqual(client.min_interval,3.0);self.assertEqual(client.wait_budget,120)
    def test_malformed_objects_do_not_raise(self):
        for body in (None,[],3,{}, {'schema':'KRAKEN_PUBLIC_TRANSPORT_V2.0.2','source':{'source_url':[],'errors':'bad'}}):
            self.assertIsNone(upstream_rate_limit(body))

if __name__=='__main__':unittest.main()
