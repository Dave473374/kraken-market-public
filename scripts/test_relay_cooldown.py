"""Offline regression for observed relay-local Retry-After=1, never market evidence."""
import io
import json
import unittest
import urllib.error
from email.message import Message
from unittest.mock import patch
from public_transport import PublicGetter, local_worker_cooldown
from collector_runtime import CycleTransport

BODY = {'schema':'KRAKEN_PUBLIC_TRANSPORT_V2.0.2',
        'role':'PUBLIC_READ_ONLY_EVIDENCE_TRANSPORT','status':'LOCAL_COOLDOWN',
        'note':'BEST_EFFORT_PER_ISOLATE_ONLY'}


def limited(body=None, hint='1'):
    headers=Message()
    if hint is not None: headers['Retry-After']=hint
    headers['CF-Cache-Status']='DYNAMIC'
    headers['Set-Cookie']='must-not-be-stored'
    return urllib.error.HTTPError('https://public.test/candidate.json',429,'limit',headers,
        io.BytesIO(json.dumps(BODY if body is None else body).encode()))


class Response:
    status=200
    headers={'Content-Type':'application/json', 'Set-Cookie':'must-not-be-stored'}
    def read(self, n): return b'{"generated_at":"original-source-time"}'
    def __enter__(self): return self
    def __exit__(self, *args): return False


class RelayCooldownTests(unittest.TestCase):
    def test_only_exact_local_envelope_is_recognized(self):
        self.assertTrue(local_worker_cooldown(BODY))
        for k in BODY:
            b=dict(BODY);b.pop(k)
            self.assertFalse(local_worker_cooldown(b))
        for b in (None, [], {'error':['EAPI:Rate limit exceeded']}):
            self.assertFalse(local_worker_cooldown(b))
    def test_one_second_local_hint_is_not_invented_as_sixty(self):
        c=PublicGetter('https://public.test')
        with patch('public_transport.time.monotonic',return_value=100), patch('public_transport.urllib.request.urlopen',side_effect=limited()):
            r=c.get('/candidate.json')
        self.assertEqual(r['retry_after_seconds'],1)
        self.assertEqual(r['local_cooldown_seconds'],2)
        self.assertEqual(c.cooldown_until,102)
        self.assertEqual(r['rate_limit_kind'],'RELAY_LOCAL_CONFIRMED')
        self.assertNotIn('Set-Cookie',r['http_response_headers'])
    def test_unknown_or_missing_hint_keeps_conservative_delay(self):
        for body,hint in (({},'1'), (BODY,None),(BODY,'garbage')):
            with patch('public_transport.urllib.request.urlopen',side_effect=limited(body,hint)):
                r=PublicGetter('https://public.test').get('/candidate.json')
            self.assertEqual(r['local_cooldown_seconds'],60)
    def test_long_server_hint_is_never_shortened(self):
        with patch('public_transport.urllib.request.urlopen',side_effect=limited(hint='240')):
            r=PublicGetter('https://public.test').get('/candidate.json')
        self.assertEqual(r['local_cooldown_seconds'],240)
    def test_calls_during_cooldown_do_not_touch_network(self):
        c=PublicGetter('https://public.test')
        with patch('public_transport.time.monotonic',return_value=100), patch('public_transport.urllib.request.urlopen',side_effect=limited()) as net:
            c.get('/candidate.json');r=c.get('/other.json')
        self.assertEqual(net.call_count,1);self.assertFalse(r['http_request_made'])
    def test_local_backoff_progresses_after_actual_rejections(self):
        c=PublicGetter('https://public.test');delays=[]
        for t in (100,200,300):
            with patch('public_transport.time.monotonic',return_value=t), patch('public_transport.urllib.request.urlopen',side_effect=limited()):
                delays.append(c.get('/candidate.json')['local_cooldown_seconds'])
        self.assertEqual(delays,[2,4,8])
    def test_bounded_recovery_and_original_timestamps(self):
        clock=[1000.0]
        with patch('collector_runtime.time.time',side_effect=lambda:clock[0]), patch('collector_runtime.time.monotonic',side_effect=lambda:clock[0]), patch('collector_runtime.time.sleep',side_effect=lambda t:clock.__setitem__(0,clock[0]+t)), patch('public_transport.urllib.request.urlopen',side_effect=[limited(),Response()]) as net:
            c=CycleTransport('https://public.test');r,n=c.get_bounded('/candidate.json')
        self.assertTrue(r['ok']);self.assertEqual(n,2);self.assertEqual(net.call_count,2)
        self.assertGreaterEqual(c.recovery_wait_seconds,2);self.assertLess(c.recovery_wait_seconds,3)
        self.assertEqual(r['body']['generated_at'],'original-source-time')
        self.assertEqual(c.http_429_count,1)
        self.assertNotIn('Set-Cookie',r['http_response_headers'])
    def test_six_rejections_stop_other_paths_too(self):
        clock=[1000.0]
        with patch('collector_runtime.time.time',side_effect=lambda:clock[0]), patch('collector_runtime.time.monotonic',side_effect=lambda:clock[0]), patch('collector_runtime.time.sleep',side_effect=lambda t:clock.__setitem__(0,clock[0]+t)), patch('public_transport.urllib.request.urlopen',side_effect=lambda *a,**k:(_ for _ in ()).throw(limited())) as net:
            c=CycleTransport('https://public.test')
            for i in range(10): r,n=c.get_bounded('/candidate.json?pair=P'+str(i))
        self.assertEqual(net.call_count,6)
        self.assertFalse(r['ok']);self.assertEqual(n,0)
        self.assertEqual(r['error'],'COLLECTION_BUDGET_OR_COOLDOWN')


if __name__=='__main__': unittest.main()
