import json, py_compile
from pathlib import Path
root=Path(__file__).resolve().parents[1]
cfg=json.loads((root/"config/assets.json").read_text())
assert cfg["pair_aliases"]["DOGEEUR"]=="XDGEUR"
assert cfg["candidate_429_retry_delays_seconds"]==[5,10]
py_compile.compile(str(root/"scripts/collect.py"),doraise=True)
src=(root/"scripts/collect.py").read_text()
for x in ["def candidate_get(","def quotes_research_usable(", '"research_usable":quotes_ok', '"executable_evidence":False', "return 0 if core_ok else 1"]:
    assert x in src
print("CORE HEALTH PATCH STATIC TESTS OK")
