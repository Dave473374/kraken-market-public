"""Synthetic offline transport tests; fixtures are never market evidence."""
import io
import json
import unittest
import urllib.error
from email.message import Message
from unittest.mock import patch
from public_transport import PublicGetter, retry_seconds

class Response:
    def __init__(self, body, ctype="application/json"):
        self.body = body
        self.headers = {"Content-Type": ctype}
        self.status = 200
    def read(self, limit):
        return self.body[:limit]
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False

def error429(header=None, body=b'{"error":"LOCAL_COOLDOWN"}'):
    headers = Message()
    if header:
        headers["Retry-After"] = header
    return urllib.error.HTTPError("https://public.test/candidate.json", 429,
                                   "Too Many Requests", headers, io.BytesIO(body))

class TransportTests(unittest.TestCase):
    def test_retry_after_seconds(self):
        self.assertEqual(retry_seconds("120", None, 0), 120)
    def test_retry_after_date(self):
        self.assertEqual(retry_seconds("Thu, 01 Jan 1970 00:02:00 GMT", None, 0), 120)
    def test_explicit_body_seconds_maximum(self):
        self.assertEqual(retry_seconds("20", {"retry_after_seconds": 90}, 0), 90)
    def test_invalid_hints_not_invented(self):
        for value in ("garbage", "nan", "-1", "inf"):
            self.assertIsNone(retry_seconds(value, {"retry_after_seconds": True}, 0))
    def test_success_preserves_embedded_timestamp(self):
        body = {"schema": "EXAMPLE", "generated_at": "old-source-time"}
        with patch("public_transport.urllib.request.urlopen", return_value=Response(json.dumps(body).encode())):
            result = PublicGetter("https://public.test").get("/quotes.json")
        self.assertTrue(result["ok"])
        self.assertEqual(result["body"], body)
    def test_429_retains_reason_and_blocks_additional_network(self):
        client = PublicGetter("https://public.test")
        with patch("public_transport.time.monotonic", return_value=100), patch("public_transport.urllib.request.urlopen", side_effect=error429("120")) as request:
            first = client.get("/candidate.json?pair=ONEEUR")
            second = client.get("/candidate.json?pair=TWOEUR")
        self.assertEqual(first["error"], "HTTP_429")
        self.assertIn("LOCAL_COOLDOWN", first["detail"])
        self.assertEqual(first["retry_after_seconds"], 120)
        self.assertEqual(second["error"], "LOCAL_RATE_LIMIT_COOLDOWN")
        self.assertFalse(second["http_request_made"])
        self.assertNotIn("body", second)
        self.assertEqual(request.call_count, 1)
    def test_missing_header_has_bounded_default(self):
        client = PublicGetter("https://public.test")
        with patch("public_transport.time.monotonic", return_value=10), patch("public_transport.urllib.request.urlopen", side_effect=error429()):
            result = client.get("/universe.json")
        self.assertEqual(client.cooldown_until, 70)
        self.assertIsNone(result["retry_after_seconds"])
    def test_cooldown_expires_without_relabeling_old_data(self):
        client = PublicGetter("https://public.test")
        client.cooldown_until = 70
        with patch("public_transport.time.monotonic", return_value=71), patch("public_transport.urllib.request.urlopen", return_value=Response(b'{"generated_at":"original"}')) as request:
            result = client.get("/quotes.json")
        self.assertTrue(result["ok"])
        self.assertEqual(result["body"]["generated_at"], "original")
        self.assertEqual(request.call_count, 1)
    def test_pacing(self):
        client = PublicGetter("https://public.test", min_interval=1.25)
        client.last_request = 100
        with patch("public_transport.time.monotonic", return_value=100.25), patch("public_transport.time.sleep") as sleep, patch("public_transport.urllib.request.urlopen", return_value=Response(b'{}')):
            client.get("/health")
        sleep.assert_called_once_with(1.0)
    def test_slow_response_keeps_full_post_completion_gap(self):
        client = PublicGetter("https://public.test", min_interval=1.25)
        clock = [100.0]
        def slow_response(*args, **kwargs):
            clock[0] += 9.0
            return Response(b'{}')
        with patch("public_transport.time.monotonic", side_effect=lambda: clock[0]), patch("public_transport.urllib.request.urlopen", side_effect=slow_response), patch("public_transport.time.sleep") as sleep:
            self.assertTrue(client.get("/candidate.json?pair=ONEEUR")["ok"])
            self.assertEqual(client.last_request, 109.0)
            self.assertTrue(client.get("/candidate.json?pair=TWOEUR")["ok"])
        sleep.assert_called_once_with(1.25)
    def test_network_failure_also_starts_gap_at_completion(self):
        client = PublicGetter("https://public.test")
        clock = [100.0]
        def slow_error(*args, **kwargs):
            clock[0] += 9.0
            raise OSError("timeout")
        with patch("public_transport.time.monotonic", side_effect=lambda: clock[0]), patch("public_transport.urllib.request.urlopen", side_effect=slow_error):
            self.assertFalse(client.get("/health")["ok"])
        self.assertEqual(client.last_request, 109.0)
    def test_non_json_and_non_object_fail_closed(self):
        for raw, ctype in ((b'not json', 'text/html'), (b'[]', 'application/json')):
            with patch("public_transport.urllib.request.urlopen", return_value=Response(raw, ctype)):
                result = PublicGetter("https://public.test").get("/health")
            self.assertFalse(result["ok"])
    def test_network_error_not_healthy(self):
        with patch("public_transport.urllib.request.urlopen", side_effect=OSError("unreachable")):
            result = PublicGetter("https://public.test").get("/health")
        self.assertFalse(result["ok"])
    def test_only_get_and_relative_path(self):
        with patch("public_transport.urllib.request.urlopen", return_value=Response(b'{}')) as request:
            PublicGetter("https://public.test").get("/health")
            self.assertEqual(request.call_args.args[0].get_method(), "GET")
        for path in ("https://elsewhere.test/", "//elsewhere.test/"):
            with self.assertRaises(ValueError):
                PublicGetter("https://public.test").get(path)

if __name__ == "__main__":
    unittest.main()
