#!/usr/bin/env python3
import json, py_compile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
cfg = json.loads((root/"config/assets.json").read_text(encoding="utf-8"))
assert cfg["pair_aliases"]["DOGEEUR"] == "XDGEUR"
assert "DOGEEUR" in cfg["quotes_pairs"]
assert "DOGEEUR" in cfg["deep_pairs"]
py_compile.compile(str(root/"scripts/collect.py"), doraise=True)

src=(root/"scripts/collect.py").read_text(encoding="utf-8")
for needle in [
    "def request_pair(",
    "def candidate_usable(",
    "def quotes_usable(",
    'filename = f"candidates/{logical_pair}.json"'
]:
    assert needle in src
print("DOGE/XDG PATCH STATIC TESTS OK")
