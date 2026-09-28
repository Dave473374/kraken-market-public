#!/usr/bin/env python3
import json, os, sys, time, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CANDIDATES = DATA / "candidates"
CFG = json.loads((ROOT / "config/assets.json").read_text(encoding="utf-8"))
BASE = CFG["worker_base"].rstrip("/")
EXPECTED = CFG["expected_schema"]
TIMEOUT = int(CFG.get("request_timeout_seconds", 30))

def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def atomic_write(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)

def public_get(path):
    url = BASE + path
    started = now_iso()
    req = urllib.request.Request(url, method="GET", headers={
        "Accept": "application/json",
        "User-Agent": "kraken-market-public-mirror/1.0"
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
            status = resp.status
            ctype = resp.headers.get("Content-Type", "")
        if status != 200:
            return {"ok": False, "url": url, "started_at": started,
                    "retrieved_at": now_iso(), "error": f"HTTP_{status}"}
        if "json" not in ctype.lower():
            return {"ok": False, "url": url, "started_at": started,
                    "retrieved_at": now_iso(), "error": "NON_JSON_CONTENT_TYPE"}
        body = json.loads(raw.decode("utf-8"))
        if not isinstance(body, dict):
            return {"ok": False, "url": url, "started_at": started,
                    "retrieved_at": now_iso(), "error": "NON_OBJECT_JSON"}
        return {"ok": True, "url": url, "started_at": started,
                "retrieved_at": now_iso(), "body": body}
    except urllib.error.HTTPError as e:
        return {"ok": False, "url": url, "started_at": started,
                "retrieved_at": now_iso(), "error": f"HTTP_{e.code}"}
    except Exception as e:
        return {"ok": False, "url": url, "started_at": started,
                "retrieved_at": now_iso(), "error": type(e).__name__,
                "detail": str(e)[:300]}

def schema_ok(result):
    return bool(result.get("ok")) and result.get("body", {}).get("schema") == EXPECTED

def save_result(filename, result):
    if result.get("ok"):
        atomic_write(DATA / filename, result["body"])
    else:
        atomic_write(DATA / filename, {
            "mirror_status": "SOURCE_FETCH_FAILED",
            "mirror_generated_at": now_iso(),
            "source_url": result.get("url"),
            "error": result.get("error"),
            "detail": result.get("detail")
        })

def discovery_pairs(body, limit):
    found = []
    candidates = body.get("candidates")
    if not isinstance(candidates, list):
        return found
    for c in candidates:
        if not isinstance(c, dict):
            continue
        pair = c.get("pair_key") or c.get("altname") or c.get("pair")
        if isinstance(pair, str):
            pair = pair.upper().replace("/", "").strip()
            if pair and pair not in found:
                found.append(pair)
        if len(found) >= limit:
            break
    return found

def main():
    DATA.mkdir(exist_ok=True)
    CANDIDATES.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": "KRAKEN_MARKET_PUBLIC_MIRROR_V1",
        "generated_at": now_iso(),
        "worker_base": BASE,
        "expected_worker_schema": EXPECTED,
        "status": "STARTED",
        "files": {},
        "candidate_pairs_attempted": [],
        "candidate_pairs_ok": [],
        "candidate_pairs_failed": [],
        "notes": [
            "Public read-only market-data mirror only.",
            "Mirror success is not a BUY/SELL signal.",
            "Consumer must recompute freshness from source timestamps."
        ]
    }

    health = public_get("/health")
    save_result("health.json", health)
    manifest["files"]["health.json"] = {
        "ok": health.get("ok", False),
        "schema_ok": schema_ok(health),
        "source_retrieved_at": health.get("retrieved_at")
    }
    if not schema_ok(health):
        manifest["status"] = "WORKER_SCHEMA_OR_HEALTH_FAILED"
        atomic_write(DATA / "manifest.json", manifest)
        return 2

    universe = public_get("/universe.json")
    save_result("universe.json", universe)
    manifest["files"]["universe.json"] = {
        "ok": universe.get("ok", False),
        "schema_ok": schema_ok(universe),
        "source_retrieved_at": universe.get("retrieved_at")
    }

    pairs = ",".join(CFG["quotes_pairs"])
    quotes = public_get("/quotes.json?pairs=" + urllib.parse.quote(pairs, safe=","))
    save_result("owned-quotes.json", quotes)
    manifest["files"]["owned-quotes.json"] = {
        "ok": quotes.get("ok", False),
        "schema_ok": schema_ok(quotes),
        "source_retrieved_at": quotes.get("retrieved_at")
    }

    deep_pairs = list(CFG["deep_pairs"])
    if schema_ok(universe):
        deep_pairs.extend(discovery_pairs(
            universe["body"],
            int(CFG.get("discovery_deep_review_limit", 3))
        ))

    unique = []
    for p in deep_pairs:
        p = str(p).upper().replace("/", "").strip()
        if p and p not in unique:
            unique.append(p)

    notional = float(CFG.get("synthetic_notional_eur", 60))
    delay = float(CFG.get("candidate_delay_seconds", 0.75))

    for pair in unique:
        manifest["candidate_pairs_attempted"].append(pair)
        path = "/candidate.json?pair=" + urllib.parse.quote(pair) + "&notional_eur=" + urllib.parse.quote(str(notional))
        result = public_get(path)
        filename = f"candidates/{pair}.json"
        save_result(filename, result)
        if schema_ok(result):
            manifest["candidate_pairs_ok"].append(pair)
        else:
            manifest["candidate_pairs_failed"].append({
                "pair": pair,
                "error": result.get("error"),
                "schema": result.get("body", {}).get("schema") if result.get("ok") else None
            })
        time.sleep(delay)

    manifest["status"] = "OK" if schema_ok(universe) and schema_ok(quotes) else "PARTIAL"
    manifest["generated_at"] = now_iso()
    manifest["summary"] = {
        "universe_ok": schema_ok(universe),
        "quotes_ok": schema_ok(quotes),
        "candidate_ok_count": len(manifest["candidate_pairs_ok"]),
        "candidate_failed_count": len(manifest["candidate_pairs_failed"])
    }
    atomic_write(DATA / "manifest.json", manifest)
    return 0 if manifest["status"] == "OK" else 1

if __name__ == "__main__":
    sys.exit(main())
