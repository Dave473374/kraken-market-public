#!/usr/bin/env python3
"""Rolling public evidence; priority-owned coverage is not Movers discovery."""
import math
import shutil
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from collections import Counter

from collect import (
    CFG, DATA, EXPECTED, atomic_write, discovery_pairs, normalize_pair,
    now_iso, public_get, request_pair, schema_ok,
)

CORE = DATA / "core"
CORE_CANDIDATES = CORE / "candidates"
CORE_SCHEMA = "KRAKEN_FAST_CORE_MIRROR_V1"
REVISION = "1.0-fast-core"


def retry_get(path, delays=(2, 4)):
    result = public_get(path)
    attempts = 1
    for delay in delays:
        if result.get("ok") or result.get("error") in ("HTTP_429", "LOCAL_RATE_LIMIT_COOLDOWN"):
            break
        time.sleep(float(delay))
        attempts += 1
        result = public_get(path)
    return result, attempts


def candidate_get_fast(path):
    # Respect transport cooldown instead of issuing a two-second 429 retry.
    return public_get(path), 1


def save_core(filename, result):
    body = result.get("body") if result.get("ok") else {
        "mirror_status": "SOURCE_FETCH_FAILED", "mirror_generated_at": now_iso(),
        "source_url": result.get("url"), "error": result.get("error"),
        "detail": result.get("detail"),
        "retry_after_seconds": result.get("retry_after_seconds"),
        "http_request_made": result.get("http_request_made", True),
    }
    atomic_write(CORE / filename, body)


def fresh_time(value, now=None):
    """Recompute age from source timestamps, never from a cached FRESH flag."""
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return False
        age = ((now or datetime.now(timezone.utc)) - stamp).total_seconds()
        return 0 <= age <= 600
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False


def valid_book(bid, ask):
    try:
        return (not isinstance(bid, bool) and not isinstance(ask, bool)
                and math.isfinite(float(bid)) and math.isfinite(float(ask))
                and 0 < float(bid) <= float(ask))
    except (TypeError, ValueError, OverflowError):
        return False


def owned_quote_coverage(result, logical_pairs, now=None):
    expected = [request_pair(p) for p in logical_pairs]
    body = result.get("body", {})
    rows = body.get("rows") if isinstance(body, dict) else None
    rows = rows if isinstance(rows, list) else []
    counts = Counter()
    invalid = []
    for row in rows:
        if not isinstance(row, dict):
            invalid.append("MALFORMED_ROW")
            continue
        pair = request_pair(row.get("altname") or row.get("pair_key") or "")
        counts[pair] += 1
        ticker = row.get("ticker") or {}
        source = ticker.get("source") or {}
        if not (valid_book(ticker.get("bid"), ticker.get("ask"))
                and source.get("ok") is True
                and fresh_time(source.get("retrieved_at"), now)):
            invalid.append(pair)
    missing = [p for p in expected if counts[p] == 0]
    duplicates = [p for p, n in counts.items() if n > 1]
    unexpected = [p for p in counts if p not in expected]
    complete = (schema_ok(result) and bool(expected)
                and len(set(expected)) == len(expected)
                and not body.get("unresolved")
                and not (missing or duplicates or unexpected or invalid))
    return {"research_usable": bool(complete), "expected_count": len(expected),
            "received_count": len(rows), "missing_pairs": missing,
            "duplicate_pairs": duplicates, "unexpected_pairs": unexpected,
            "invalid_or_stale_pairs": invalid, "executable_evidence": False}


def candidate_usable(result):
    if not schema_ok(result):
        return False
    body = result.get("body", {})
    return body.get("data_health") == "DATA_OK" and not body.get("pair_error")


def owned_candidate_status(result, logical_pair, now=None):
    body = result.get("body", {}) if result.get("ok") else {}
    meta = body.get("pair_metadata") or {}
    spread = body.get("timestamped_spread") or {}
    depth = body.get("depth") or {}
    spread_source = spread.get("source") or {}
    depth_source = depth.get("source") or {}
    pair_ok = request_pair(meta.get("altname") or meta.get("pair_key") or "") == request_pair(logical_pair)
    evidence_ok = bool(candidate_usable(result) and pair_ok
        and spread.get("quality") == "VERIFIED" and depth.get("quality") == "VERIFIED"
        and valid_book(spread.get("bid"), spread.get("ask"))
        and valid_book(depth.get("best_bid"), depth.get("best_ask"))
        and spread_source.get("ok") is True and depth_source.get("ok") is True
        and fresh_time(body.get("generated_at"), now)
        and fresh_time(spread.get("observed_at"), now)
        and fresh_time(spread_source.get("retrieved_at"), now)
        and fresh_time(depth_source.get("retrieved_at"), now))
    return {"data_health": "DATA_OK" if evidence_ok else "PARTIAL_DATA",
            "source_data_health": body.get("data_health"),
            "pair_matches": pair_ok, "fresh_quote_evidence": evidence_ok,
            "spread_observed_at": spread.get("observed_at"),
            "spread_source_retrieved_at": spread_source.get("retrieved_at"),
            "depth_source_retrieved_at": depth_source.get("retrieved_at"),
            "best_bid": depth.get("best_bid"), "best_ask": depth.get("best_ask"),
            "error": result.get("error"), "pair_error": body.get("pair_error"),
            "trade_signal": None, "independent_crosscheck": "NOT_PROVIDED"}


def selected_pairs(universe):
    owned = list(dict.fromkeys(normalize_pair(p) for p in CFG.get("fast_owned_pairs", [])))
    configured = {normalize_pair(p) for p in CFG["quotes_pairs"]}
    if any(p not in configured for p in owned):
        raise ValueError("fast_owned_pairs must be included in quotes_pairs")
    limit = max(0, min(3, int(CFG.get("discovery_deep_review_limit", 3))))
    discovered = discovery_pairs(universe.get("body", {}), limit + len(owned)) if schema_ok(universe) and limit else []
    selected = [(p, "OWNED_PRIORITY") for p in owned]
    seen = {request_pair(p) for p in owned}
    for pair in discovered:
        if request_pair(pair) not in seen:
            selected.append((pair, "DISCOVERY_ONLY"))
            seen.add(request_pair(pair))
            if len(selected) >= len(owned) + limit:
                break
    return selected


def main():
    CORE.mkdir(parents=True, exist_ok=True)
    if CORE_CANDIDATES.exists():
        shutil.rmtree(CORE_CANDIDATES)
    CORE_CANDIDATES.mkdir(parents=True, exist_ok=True)
    manifest = {"schema": CORE_SCHEMA, "revision": REVISION,
        "coverage_revision": "1.1-owned-priority", "generated_at": now_iso(),
        "transport_revision": "1.3-paced-owned-first",
        "expected_worker_schema": EXPECTED, "status": "STARTED", "files": {},
        "candidate_pairs_attempted": [], "candidate_pairs_ok": [],
        "candidate_pairs_partial_or_failed": [], "owned_priority_status": {},
        "notes": ["Public read-only evidence only. No private APIs, trades or account data.",
                  "Recompute freshness from embedded source times.",
                  "Ticker rows are research support, not timestamped executable evidence.",
                  "Priority-owned candidates are mandatory and separate from max-three Movers discovery.",
                  "Optional discovery failure does not fail the owned-risk core.",
                  "An evidence snapshot is not a BUY/SELL signal or independent cross-check."]}
    health, attempts = retry_get("/health")
    save_core("health.json", health)
    manifest["files"]["health.json"] = {"ok": bool(health.get("ok")),
        "schema_ok": schema_ok(health), "attempts": attempts,
        "source_retrieved_at": health.get("retrieved_at")}
    logical = [normalize_pair(p) for p in CFG["quotes_pairs"]]
    requested = [request_pair(p) for p in logical]
    path = "/quotes.json?pairs=" + urllib.parse.quote(",".join(requested), safe=",")
    quotes, attempts = retry_get(path)
    save_core("owned-quotes.json", quotes)
    coverage = owned_quote_coverage(quotes, logical)
    manifest["files"]["owned-quotes.json"] = {
        "ok": bool(quotes.get("ok")), "schema_ok": schema_ok(quotes),
        "attempts": attempts, "requested_pairs": requested, "logical_pairs": logical,
        "source_retrieved_at": quotes.get("retrieved_at"), **coverage}
    universe = {"ok": False}
    def priority_then_discovery():
        nonlocal universe
        # Do not spend the request budget on discovery before owned-risk books.
        yield from selected_pairs({"ok": False})
        universe, attempts = retry_get("/universe.json")
        save_core("universe.json", universe)
        manifest["files"]["universe.json"] = {"ok": bool(universe.get("ok")),
            "schema_ok": schema_ok(universe), "attempts": attempts,
            "source_retrieved_at": universe.get("retrieved_at")}
        for pair, role in selected_pairs(universe):
            if role == "DISCOVERY_ONLY":
                yield pair, role
    notional = float(CFG.get("synthetic_notional_eur", 60))
    owned_results = {}
    for pair, role in priority_then_discovery():
        requested_pair = request_pair(pair)
        path = "/candidate.json?pair=" + urllib.parse.quote(requested_pair) + "&notional_eur=" + urllib.parse.quote(str(notional))
        result, attempts = candidate_get_fast(path)
        manifest["candidate_pairs_attempted"].append({"logical_pair": pair,
            "requested_pair": requested_pair, "role": role, "attempts": attempts,
            "http_request_made": result.get("http_request_made", True)})
        save_core(f"candidates/{pair}.json", result)
        usable = candidate_usable(result)
        if role == "OWNED_PRIORITY":
            owned_results[pair] = result
            status = owned_candidate_status(result, pair)
            manifest["owned_priority_status"][pair] = status
            usable = status["fresh_quote_evidence"]
        if usable:
            manifest["candidate_pairs_ok"].append(pair)
        else:
            body = result.get("body", {}) if result.get("ok") else {}
            manifest["candidate_pairs_partial_or_failed"].append({"logical_pair": pair,
                "requested_pair": requested_pair, "role": role, "attempts": attempts,
                "transport_ok": bool(result.get("ok")), "schema_ok": schema_ok(result),
                "data_health": body.get("data_health"), "pair_error": body.get("pair_error"),
                "error": result.get("error"), "detail": result.get("detail")})
        time.sleep(0.4)
    # Recheck at publication too: slow optional fetches must not extend freshness.
    coverage = owned_quote_coverage(quotes, logical)
    manifest["files"]["owned-quotes.json"].update(coverage)
    for pair, result in owned_results.items():
        manifest["owned_priority_status"][pair] = owned_candidate_status(result, pair)
    priority_ok = all(s["fresh_quote_evidence"] for s in manifest["owned_priority_status"].values())
    core_ok = coverage["research_usable"] and schema_ok(universe) and priority_ok
    manifest["status"] = "OK" if core_ok else "PARTIAL"
    manifest["generated_at"] = now_iso()
    manifest["summary"] = {"core_ok": core_ok,
        "health_schema_ok": manifest["files"]["health.json"]["schema_ok"],
        "universe_ok": schema_ok(universe),
        "owned_quotes_research_usable": coverage["research_usable"],
        "owned_quotes_executable_evidence": False,
        "owned_priority_complete": priority_ok,
        "owned_priority_count": len(manifest["owned_priority_status"]),
        "candidate_ok_count": len(manifest["candidate_pairs_ok"]),
        "candidate_partial_or_failed_count": len(manifest["candidate_pairs_partial_or_failed"])}
    atomic_write(CORE / "manifest.json", manifest)
    return 0 if core_ok else 1


if __name__ == "__main__":
    sys.exit(main())
