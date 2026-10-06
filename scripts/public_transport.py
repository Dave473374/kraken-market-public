"""Bounded, paced public GET transport. No credentials, trading or freshening."""
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def stamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def retry_seconds(header, body, wall_time):
    """Accept standard Retry-After and explicit numeric second fields only."""
    values = []
    if header:
        try:
            values.append(float(header))
        except (ValueError, TypeError):
            try:
                when = parsedate_to_datetime(header)
                if when.tzinfo is not None:
                    values.append(when.timestamp() - wall_time)
            except (ValueError, TypeError, OverflowError):
                pass
    if isinstance(body, dict):
        for key in ("retry_after_seconds", "retryAfterSeconds", "cooldown_seconds"):
            value = body.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.append(float(value))
    valid = [v for v in values if math.isfinite(v) and v >= 0]
    return max(valid) if valid else None


def local_worker_cooldown(body):
    """Recognize the observed relay's own throttle, not an exchange/API error."""
    return (isinstance(body, dict)
            and body.get("schema") == "KRAKEN_PUBLIC_TRANSPORT_V2.0.2"
            and body.get("role") == "PUBLIC_READ_ONLY_EVIDENCE_TRANSPORT"
            and body.get("status") == "LOCAL_COOLDOWN"
            and body.get("note") == "BEST_EFFORT_PER_ISOLATE_ONLY")


def diagnostic_headers(headers):
    """Public response metadata only. Never log request headers/cookies/tokens."""
    keys = ("Date", "Age", "Cache-Control", "CF-Cache-Status", "Retry-After", "Content-Type")
    return {k: str(headers.get(k))[:256] for k in keys if headers.get(k) is not None}


class PublicGetter:
    """Paced public GET with an origin-wide cooldown and bounded diagnostic data.

    Known relay-local rejection honors Retry-After plus progressive quiet time.
    Unknown/upstream HTTP429 retains the conservative >=60s behavior. No response
    is promoted to DATA_OK here, and a retry must be made by a bounded caller.
    """
    def __init__(self, base, timeout=30, min_interval=1.25):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.min_interval = min_interval
        self.last_request = None
        self.cooldown_until = 0.0
        self.blocked_by_url = None
        self.blocked_at = None
        self.local_429_streak = 0
        self.http_429_count = 0

    def get(self, path):
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("Only relative public GET paths are allowed")
        url = self.base + path
        started = stamp()
        remaining = self.cooldown_until - time.monotonic()
        if remaining > 0:
            return {"ok": False, "url": url, "started_at": started,
                    "retrieved_at": stamp(), "error": "LOCAL_RATE_LIMIT_COOLDOWN",
                    "detail": "No HTTP request made; honoring prior HTTP_429 from " + str(self.blocked_by_url),
                    "retry_after_seconds": round(remaining, 3),
                    "blocked_at": self.blocked_at, "http_request_made": False}
        if self.last_request is not None:
            delay = self.min_interval - (time.monotonic() - self.last_request)
            if delay > 0:
                time.sleep(delay)
        req = urllib.request.Request(url, method="GET", headers={
            "Accept": "application/json", "User-Agent": "kraken-market-public-mirror/1.4.1-cooldown"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read(16 * 1024 * 1024 + 1)
                status = resp.status
                ctype = resp.headers.get("Content-Type", "")
                response_headers = diagnostic_headers(resp.headers)
            if status != 200:
                raise ValueError("Unexpected successful HTTP status: " + str(status))
            if len(raw) > 16 * 1024 * 1024:
                raise ValueError("Public response exceeds 16 MiB")
            if "json" not in ctype.lower():
                return self.failure(url, started, "NON_JSON_CONTENT_TYPE")
            body = json.loads(raw.decode("utf-8"))
            if not isinstance(body, dict):
                return self.failure(url, started, "NON_OBJECT_JSON")
            self.local_429_streak = 0
            return {"ok": True, "url": url, "started_at": started,
                    "retrieved_at": stamp(), "body": body,
                    "http_response_headers": response_headers}
        except urllib.error.HTTPError as exc:
            try:
                raw_error = exc.read(4096).decode("utf-8", errors="replace")
            except Exception:
                raw_error = ""
            headers = exc.headers or {}
            try:
                error_body = json.loads(raw_error)
            except (ValueError, TypeError):
                error_body = None
            hint = retry_seconds(headers.get("Retry-After"), error_body, time.time())
            result = self.failure(url, started, "HTTP_" + str(exc.code))
            result["detail"] = raw_error[:1000]
            result["retry_after_seconds"] = hint
            result["http_response_headers"] = diagnostic_headers(headers)
            if exc.code == 429:
                self.http_429_count += 1
                recognized = local_worker_cooldown(error_body)
                if recognized and hint is not None:
                    self.local_429_streak += 1
                    # 2,4,8,...s progressive backoff: never faster than either the
                    # server hint or the existing 1.25s post-response quiet gap.
                    delay = max(self.min_interval, hint, min(60.0, 2.0 ** min(self.local_429_streak, 6)))
                    kind = "RELAY_LOCAL_CONFIRMED"
                else:
                    delay = max(60.0, hint if hint is not None else 60.0)
                    kind = "UPSTREAM_OR_UNKNOWN"
                self.cooldown_until = time.monotonic() + delay
                self.blocked_by_url = url
                self.blocked_at = result["retrieved_at"]
                result["local_cooldown_seconds"] = delay
                result["rate_limit_kind"] = kind
            return result
        except Exception as exc:
            return self.failure(url, started, type(exc).__name__, str(exc)[:300])
        finally:
            self.last_request = time.monotonic()

    @staticmethod
    def failure(url, started, error, detail=None):
        return {"ok": False, "url": url, "started_at": started,
                "retrieved_at": stamp(), "error": error, "detail": detail}
