#!/usr/bin/env python3
import json
import shutil
import sys
import time
import urllib.parse
from pathlib import Path

# Reuse the proven transport/schema helpers from the hourly collector.
from collect import (
    CFG,
    DATA,
    EXPECTED,
    atomic_write,
    discovery_pairs,
    normalize_pair,
    now_iso,
    public_get,
    quotes_research_usable,
    request_pair,
    schema_ok,
)

CORE = DATA / "core"
CORE_CANDIDATES = CORE / "candidates"
CORE_SCHEMA = "KRAKEN_FAST_CORE_MIRROR_V1"
REVISION = "1.0-fast-core"


def retry_get(path, delays=(2, 4)):
    result = public_get(path)
    if result.get("ok"):
        return result, 1
    attempts = 1
    for delay in delays:
        time.sleep(float(delay))
        attempts += 1
        result = public_get(path)
        if result.get("ok"):
            break
    return result, attempts


def candidate_get_fast(path):
    result = public_get(path)
    attempts = 1
    if result.get("error") == "HTTP_429":
        time.sleep(2)
        attempts += 1
        result = public_get(path)
    return result, attempts


def save_core(filename, result):
    path = CORE / filename
    if result.get("ok"):
        atomic_write(path, result["body"])
    else:
        atomic_write(
            path,
            {
                "mirror_status": "SOURCE_FETCH_FAILED",
                "mirror_generated_at": now_iso(),
                "source_url": result.get("url"),
                "error": result.get("error"),
                "detail": result.get("detail"),
            },
        )


def candidate_usable(result):
    if not schema_ok(result):
        return False
    body = result.get("body", {})
    return body.get("data_health") == "DATA_OK" and not body.get("pair_error")


def main():
    CORE.mkdir(parents=True, exist_ok=True)
    if CORE_CANDIDATES.exists():
        shutil.rmtree(CORE_CANDIDATES)
    CORE_CANDIDATES.mkdir(parents=True, exist_ok=True)

    manifest = {
        "schema": CORE_SCHEMA,
        "revision": REVISION,
        "generated_at": now_iso(),
        "expected_worker_schema": EXPECTED,
        "status": "STARTED",
        "files": {},
        "candidate_pairs_attempted": [],
        "candidate_pairs_ok": [],
        "candidate_pairs_partial_or_failed": [],
        "notes": [
            "Fast public read-only evidence transport for hourly Crypto Alpha Watch.",
            "No private Kraken API, account data, trading, transfers, or order actions.",
            "Consumer must recompute freshness from embedded source retrieval timestamps.",
            "Candidate failure never proves no opportunity and does not fail owned-quote core.",
            "Rolling core-live publication is intentionally separate from hourly main history.",
        ],
    }

    health, health_attempts = retry_get("/health")
    save_core("health.json", health)
    manifest["files"]["health.json"] = {
        "ok": bool(health.get("ok")),
        "schema_ok": schema_ok(health),
        "attempts": health_attempts,
        "source_retrieved_at": health.get("retrieved_at"),
    }

    universe, universe_attempts = retry_get("/universe.json")
    save_core("universe.json", universe)
    manifest["files"]["universe.json"] = {
        "ok": bool(universe.get("ok")),
        "schema_ok": schema_ok(universe),
        "attempts": universe_attempts,
        "source_retrieved_at": universe.get("retrieved_at"),
    }

    logical_quotes = [normalize_pair(p) for p in CFG["quotes_pairs"]]
    requested_quotes = [request_pair(p) for p in logical_quotes]
    quote_path = "/quotes.json?pairs=" + urllib.parse.quote(",".join(requested_quotes), safe=",")
    quotes, quote_attempts = retry_get(quote_path)
    save_core("owned-quotes.json", quotes)
    quotes_ok = quotes_research_usable(quotes, len(logical_quotes))
    manifest["files"]["owned-quotes.json"] = {
        "ok": bool(quotes.get("ok")),
        "schema_ok": schema_ok(quotes),
        "research_usable": quotes_ok,
        "attempts": quote_attempts,
        "requested_pairs": requested_quotes,
        "logical_pairs": logical_quotes,
        "source_retrieved_at": quotes.get("retrieved_at"),
    }

    deep_limit = min(3, int(CFG.get("discovery_deep_review_limit", 3)))
    selected = []
    if schema_ok(universe):
        selected = discovery_pairs(universe["body"], deep_limit)

    notional = float(CFG.get("synthetic_notional_eur", 60))
    for logical_pair in selected:
        logical_pair = normalize_pair(logical_pair)
        requested_pair = request_pair(logical_pair)
        path = (
            "/candidate.json?pair="
            + urllib.parse.quote(requested_pair)
            + "&notional_eur="
            + urllib.parse.quote(str(notional))
        )
        result, attempts = candidate_get_fast(path)
        manifest["candidate_pairs_attempted"].append(
            {
                "logical_pair": logical_pair,
                "requested_pair": requested_pair,
                "attempts": attempts,
            }
        )
        save_core(f"candidates/{logical_pair}.json", result)
        if candidate_usable(result):
            manifest["candidate_pairs_ok"].append(logical_pair)
        else:
            body = result.get("body", {}) if result.get("ok") else {}
            manifest["candidate_pairs_partial_or_failed"].append(
                {
                    "logical_pair": logical_pair,
                    "requested_pair": requested_pair,
                    "attempts": attempts,
                    "transport_ok": bool(result.get("ok")),
                    "schema_ok": schema_ok(result),
                    "data_health": body.get("data_health"),
                    "pair_error": body.get("pair_error"),
                    "error": result.get("error"),
                }
            )
        time.sleep(0.4)

    # Actionability core is owned quotes + universe. Health and optional candidates
    # remain visible diagnostics but do not turn a valid quote snapshot into a false failure.
    core_ok = quotes_ok and schema_ok(universe)
    manifest["status"] = "OK" if core_ok else "PARTIAL"
    manifest["generated_at"] = now_iso()
    manifest["summary"] = {
        "core_ok": core_ok,
        "health_schema_ok": schema_ok(health),
        "universe_ok": schema_ok(universe),
        "owned_quotes_research_usable": quotes_ok,
        "candidate_ok_count": len(manifest["candidate_pairs_ok"]),
        "candidate_partial_or_failed_count": len(manifest["candidate_pairs_partial_or_failed"]),
    }
    atomic_write(CORE / "manifest.json", manifest)
    return 0 if core_ok else 1


if __name__ == "__main__":
    sys.exit(main())
