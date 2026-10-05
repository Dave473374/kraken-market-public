#!/usr/bin/env python3
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
cfg = json.loads((root / "config/assets.json").read_text(encoding="utf-8"))
assert cfg["expected_schema"] == "KRAKEN_PUBLIC_TRANSPORT_V2.0.2"
assert cfg["worker_base"].startswith("https://")
assert len(cfg["quotes_pairs"]) <= 25
assert len(set(cfg["quotes_pairs"])) == len(cfg["quotes_pairs"])
assert cfg["discovery_deep_review_limit"] <= 3
workflow = (root / ".github/workflows/collect.yml").read_text(encoding="utf-8")
assert 'cron: "1 * * * *"' in workflow
assert "contents: write" in workflow
# The secondary workflow is an archive, not a second upstream request owner.
assert "run_public_collector.py archive" in workflow
assert "python scripts/collect.py" not in workflow
collector = (root / "scripts/collect_core.py").read_text(encoding="utf-8")
for route in ["/health", "/universe.json", "/quotes.json?pairs=", "/candidate.json?pair="]:
    assert route in collector
print("STATIC TESTS OK")
