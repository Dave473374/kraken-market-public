#!/usr/bin/env python3
import json, os, sys, time, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timezone
from pathlib import Path
from quote_guard import coverage

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"
CANDIDATES=DATA/"candidates"
CFG=json.loads((ROOT/"config/assets.json").read_text(encoding="utf-8"))
BASE=CFG["worker_base"].rstrip("/")
EXPECTED=CFG["expected_schema"]
TIMEOUT=int(CFG.get("request_timeout_seconds",30))
ALIASES={str(k).upper():str(v).upper() for k,v in CFG.get("pair_aliases",{}).items()}

def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")

def normalize_pair(pair):
    return str(pair).upper().replace("/","").strip()

def request_pair(pair):
    p=normalize_pair(pair)
    return ALIASES.get(p,p)

def atomic_write(path,obj):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    os.replace(tmp,path)

def public_get(path):
    url=BASE+path
    started=now_iso()
    req=urllib.request.Request(url,method="GET",headers={"Accept":"application/json","User-Agent":"kraken-market-public-mirror/1.2"})
    try:
        with urllib.request.urlopen(req,timeout=TIMEOUT) as resp:
            raw=resp.read(); status=resp.status; ctype=resp.headers.get("Content-Type","")
        if status!=200:
            return {"ok":False,"url":url,"started_at":started,"retrieved_at":now_iso(),"error":f"HTTP_{status}"}
        if "json" not in ctype.lower():
            return {"ok":False,"url":url,"started_at":started,"retrieved_at":now_iso(),"error":"NON_JSON_CONTENT_TYPE"}
        body=json.loads(raw.decode("utf-8"))
        if not isinstance(body,dict):
            return {"ok":False,"url":url,"started_at":started,"retrieved_at":now_iso(),"error":"NON_OBJECT_JSON"}
        return {"ok":True,"url":url,"started_at":started,"retrieved_at":now_iso(),"body":body}
    except urllib.error.HTTPError as e:
        return {"ok":False,"url":url,"started_at":started,"retrieved_at":now_iso(),"error":f"HTTP_{e.code}"}
    except Exception as e:
        return {"ok":False,"url":url,"started_at":started,"retrieved_at":now_iso(),"error":type(e).__name__,"detail":str(e)[:300]}

def candidate_get(path):
    result=public_get(path); attempts=1
    if result.get("error")!="HTTP_429":
        return result,attempts
    for delay in CFG.get("candidate_429_retry_delays_seconds",[5,10]):
        time.sleep(float(delay)); attempts+=1; result=public_get(path)
        if result.get("error")!="HTTP_429":
            break
    return result,attempts

def schema_ok(result):
    return bool(result.get("ok")) and result.get("body",{}).get("schema")==EXPECTED

def candidate_usable(result):
    if not schema_ok(result): return False
    body=result.get("body",{})
    return body.get("data_health")=="DATA_OK" and not body.get("pair_error")

def quotes_research_usable(result,expected_pairs):
    # Kept as a compatibility wrapper; the expected identities must be known.
    if isinstance(expected_pairs, int):
        if expected_pairs != len(CFG["quotes_pairs"]): return False
        expected_pairs = CFG["quotes_pairs"]
    return coverage(result, expected_pairs, EXPECTED, ALIASES)["research_usable"]

def save_result(filename,result):
    if result.get("ok"):
        atomic_write(DATA/filename,result["body"])
    else:
        atomic_write(DATA/filename,{"mirror_status":"SOURCE_FETCH_FAILED","mirror_generated_at":now_iso(),"source_url":result.get("url"),"error":result.get("error"),"detail":result.get("detail")})

def discovery_pairs(body,limit):
    found=[]
    candidates=body.get("candidates")
    if not isinstance(candidates,list): return found
    for c in candidates:
        if not isinstance(c,dict): continue
        pair=c.get("pair_key") or c.get("altname") or c.get("pair")
        if isinstance(pair,str):
            pair=normalize_pair(pair)
            if pair and pair not in found: found.append(pair)
        if len(found)>=limit: break
    return found

def main():
    DATA.mkdir(exist_ok=True); CANDIDATES.mkdir(parents=True,exist_ok=True)
    manifest={
      "schema":"KRAKEN_MARKET_PUBLIC_MIRROR_V1",
      "mirror_revision":"1.2-core-health",
      "generated_at":now_iso(),
      "worker_base":BASE,
      "expected_worker_schema":EXPECTED,
      "status":"STARTED",
      "pair_aliases":ALIASES,
      "files":{},
      "candidate_pairs_attempted":[],
      "candidate_pairs_ok":[],
      "candidate_pairs_partial_or_failed":[],
      "notes":[
        "Public read-only market-data mirror only.",
        "Mirror success is not a BUY/SELL signal.",
        "Consumer must recompute freshness from source timestamps.",
        "Logical DOGEEUR is requested from Kraken REST as XDGEUR.",
        "owned-quotes may be research-usable while data_health is PARTIAL_DATA; it is never executable evidence.",
        "A single optional candidate failure does not fail the core mirror workflow."
      ]
    }

    health=public_get("/health"); save_result("health.json",health)
    manifest["files"]["health.json"]={"ok":health.get("ok",False),"schema_ok":schema_ok(health),"source_retrieved_at":health.get("retrieved_at")}
    if not schema_ok(health):
        manifest["status"]="WORKER_SCHEMA_OR_HEALTH_FAILED"; atomic_write(DATA/"manifest.json",manifest); return 2

    universe=public_get("/universe.json"); save_result("universe.json",universe)
    manifest["files"]["universe.json"]={"ok":universe.get("ok",False),"schema_ok":schema_ok(universe),"source_retrieved_at":universe.get("retrieved_at")}

    logical_quotes=[normalize_pair(p) for p in CFG["quotes_pairs"]]
    requested_quotes=[request_pair(p) for p in logical_quotes]
    quotes=public_get("/quotes.json?pairs="+urllib.parse.quote(",".join(requested_quotes),safe=","))
    save_result("owned-quotes.json",quotes)
    quotes_ok=quotes_research_usable(quotes,logical_quotes)
    manifest["files"]["owned-quotes.json"]={
      "ok":quotes.get("ok",False),
      "schema_ok":schema_ok(quotes),
      "research_usable":quotes_ok,
      "executable_evidence":False,
      "requested_pairs":requested_quotes,
      "logical_pairs":logical_quotes,
      "source_retrieved_at":quotes.get("retrieved_at")
    }

    deep_pairs=[normalize_pair(p) for p in CFG["deep_pairs"]]
    if schema_ok(universe):
        deep_pairs.extend(discovery_pairs(universe["body"],int(CFG.get("discovery_deep_review_limit",3))))
    unique=[]
    for p in deep_pairs:
        p=normalize_pair(p)
        if p and p not in unique: unique.append(p)

    notional=float(CFG.get("synthetic_notional_eur",60))
    delay=float(CFG.get("candidate_delay_seconds",1.0))
    for logical_pair in unique:
        requested_pair=request_pair(logical_pair)
        path="/candidate.json?pair="+urllib.parse.quote(requested_pair)+"&notional_eur="+urllib.parse.quote(str(notional))
        result,attempts=candidate_get(path)
        manifest["candidate_pairs_attempted"].append({"logical_pair":logical_pair,"requested_pair":requested_pair,"attempts":attempts})
        save_result(f"candidates/{logical_pair}.json",result)
        if candidate_usable(result):
            manifest["candidate_pairs_ok"].append(logical_pair)
        else:
            body=result.get("body",{}) if result.get("ok") else {}
            manifest["candidate_pairs_partial_or_failed"].append({
              "logical_pair":logical_pair,"requested_pair":requested_pair,"attempts":attempts,
              "transport_ok":bool(result.get("ok")),"schema_ok":schema_ok(result),
              "data_health":body.get("data_health"),"pair_error":body.get("pair_error"),"error":result.get("error")
            })
        time.sleep(delay)

    quote_coverage = coverage(quotes, logical_quotes, EXPECTED, ALIASES)
    manifest["files"]["owned-quotes.json"].update(quote_coverage)
    quotes_ok = quote_coverage["research_usable"]
    core_ok=schema_ok(universe) and quotes_ok
    manifest["status"]="OK" if core_ok else "PARTIAL"
    manifest["generated_at"]=now_iso()
    manifest["summary"]={
      "core_ok":core_ok,
      "universe_ok":schema_ok(universe),
      "owned_quotes_research_usable":quotes_ok,
      "owned_quotes_executable_evidence":False,
      "candidate_ok_count":len(manifest["candidate_pairs_ok"]),
      "candidate_partial_or_failed_count":len(manifest["candidate_pairs_partial_or_failed"])
    }
    atomic_write(DATA/"manifest.json",manifest)
    return 0 if core_ok else 1

if __name__=="__main__":
    sys.exit(main())
