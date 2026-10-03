"""Offline regression tests; no account data, network calls or trade signals."""
import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import collect_core as core

NOW = datetime(2026, 10, 3, 4, 0, tzinfo=timezone.utc)
STAMP = NOW.isoformat()
PRIORITY = ["GALAEUR", "NIGHTEUR", "MEGAEUR"]


def envelope(body):
    return {"ok": True, "retrieved_at": STAMP, "body": {"schema": core.EXPECTED, **body}}


def quote(pair):
    return {"altname": core.request_pair(pair), "ticker": {"bid": 1, "ask": 1.001,
        "source": {"ok": True, "retrieved_at": STAMP}}}


def candidate(pair):
    return envelope({"data_health": "DATA_OK", "generated_at": STAMP,
        "pair_metadata": {"altname": core.request_pair(pair)},
        "timestamped_spread": {"quality": "VERIFIED", "bid": 1, "ask": 1.001,
            "observed_at": STAMP, "source": {"ok": True, "retrieved_at": STAMP}},
        "depth": {"quality": "VERIFIED", "best_bid": 1, "best_ask": 1.001,
            "source": {"ok": True, "retrieved_at": STAMP}}})


class OwnedCoverageTests(unittest.TestCase):
    def test_config_retains_original_and_adds_three(self):
        original = {"XBTEUR", "ETHEUR", "SOLEUR", "DOGEEUR", "LTCEUR", "SUIEUR",
            "LINKEUR", "BNBEUR", "AAVEEUR", "ONDOEUR", "INJEUR", "PENDLEEUR", "RENDEREUR", "TAOEUR"}
        for key in ("quotes_pairs", "deep_pairs"):
            self.assertTrue((original | set(PRIORITY)).issubset(core.CFG[key]))
            self.assertEqual(len(core.CFG[key]), len(set(core.CFG[key])))
        self.assertTrue(set(PRIORITY).issubset(core.CFG["fast_owned_pairs"]))
        self.assertEqual(core.request_pair("DOGE/EUR"), "XDGEUR")

    def test_complete_support_does_not_claim_execution(self):
        pairs = core.CFG["quotes_pairs"]
        result = envelope({"rows": [quote(p) for p in pairs], "unresolved": []})
        status = core.owned_quote_coverage(result, pairs, NOW)
        self.assertTrue(status["research_usable"])
        self.assertFalse(status["executable_evidence"])

    def test_equal_row_count_cannot_hide_missing_or_duplicate(self):
        result = envelope({"rows": [quote("GALAEUR"), quote("GALAEUR"), quote("NIGHTEUR")]})
        status = core.owned_quote_coverage(result, PRIORITY, NOW)
        self.assertFalse(status["research_usable"])
        self.assertEqual(status["missing_pairs"], ["MEGAEUR"])
        self.assertEqual(status["duplicate_pairs"], ["GALAEUR"])

    def test_unresolved_or_bad_schema_blocks_support(self):
        for changes in ({"unresolved": ["MEGAEUR"]}, {"schema": "WRONG"}):
            result = envelope({"rows": [quote(p) for p in PRIORITY], **changes})
            self.assertFalse(core.owned_quote_coverage(result, PRIORITY, NOW)["research_usable"])

    def test_invalid_or_stale_quotes_block_support(self):
        for bid, ask in ((0, 1), (2, 1), (float("nan"), 1), (True, 1), (None, 1)):
            row = quote("GALAEUR")
            row["ticker"].update(bid=bid, ask=ask)
            self.assertFalse(core.owned_quote_coverage(envelope({"rows": [row]}), ["GALAEUR"], NOW)["research_usable"])
        row = quote("GALAEUR")
        row["ticker"]["source"]["retrieved_at"] = (NOW - timedelta(minutes=11)).isoformat()
        self.assertFalse(core.owned_quote_coverage(envelope({"rows": [row]}), ["GALAEUR"], NOW)["research_usable"])

    def test_owned_not_displaced_by_three_movers(self):
        universe = envelope({"candidates": [{"pair_key": p} for p in PRIORITY + ["SANDUSD", "CTUSD", "TREADUSD", "FOURTHUSD"]]})
        selected = core.selected_pairs(universe)
        self.assertEqual(selected[:3], [(p, "OWNED_PRIORITY") for p in PRIORITY])
        self.assertEqual(sum(role == "DISCOVERY_ONLY" for p, role in selected), 3)
        self.assertEqual(len(selected), len({p for p, role in selected}))

    def test_owned_survive_universe_failure(self):
        self.assertEqual(core.selected_pairs({"ok": False}), [(p, "OWNED_PRIORITY") for p in PRIORITY])

    def test_fresh_candidate_is_evidence_not_trade_signal(self):
        status = core.owned_candidate_status(candidate("MEGAEUR"), "MEGAEUR", NOW)
        self.assertTrue(status["fresh_quote_evidence"])
        self.assertIsNone(status["trade_signal"])
        self.assertEqual(status["independent_crosscheck"], "NOT_PROVIDED")

    def test_partial_failed_or_wrong_pair_not_healthy(self):
        partial = candidate("GALAEUR")
        partial["body"]["data_health"] = "PARTIAL_DATA"
        for result in (partial, {"ok": False, "error": "HTTP_429"}, candidate("MEGAEUR")):
            self.assertFalse(core.owned_candidate_status(result, "GALAEUR", NOW)["fresh_quote_evidence"])

    def test_cached_data_ok_cannot_hide_stale_sources(self):
        for path in (("generated_at",), ("timestamped_spread", "observed_at"),
                     ("timestamped_spread", "source", "retrieved_at"), ("depth", "source", "retrieved_at")):
            for value in (None, "bad", (NOW - timedelta(seconds=601)).isoformat(), (NOW + timedelta(seconds=1)).isoformat()):
                result = candidate("NIGHTEUR")
                obj = result["body"]
                for key in path[:-1]:
                    obj = obj[key]
                obj[path[-1]] = value
                self.assertFalse(core.owned_candidate_status(result, "NIGHTEUR", NOW)["fresh_quote_evidence"])

    def test_freshness_boundaries(self):
        self.assertTrue(core.fresh_time((NOW - timedelta(seconds=600)).isoformat(), NOW))
        self.assertFalse(core.fresh_time((NOW - timedelta(seconds=601)).isoformat(), NOW))
        self.assertFalse(core.fresh_time("2026-10-03T04:00:00", NOW))

    def test_workflows_watch_config_and_helpers(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("collect-core.yml", "collect.yml"):
            text = (root / ".github/workflows" / name).read_text()
            self.assertIn('"config/assets.json"', text)
            self.assertIn('"scripts/collect.py"', text)
            self.assertIn("test_owned_coverage.py", text)

    def test_main_publishes_partial_for_missing_owned_not_optional(self):
        cfg = copy.deepcopy(core.CFG)
        cfg["quotes_pairs"] = PRIORITY
        universe = envelope({"candidates": [{"pair_key": "OPTIONALEUR"}]})
        def fake_retry(path):
            if path.startswith("/quotes.json"):
                return envelope({"rows": [quote(p) for p in PRIORITY]}), 1
            return (universe if path == "/universe.json" else envelope({})), 1
        for failed in ("OPTIONALEUR", "MEGAEUR"):
            def fake_candidate(path):
                pair = path.split("pair=", 1)[1].split("&", 1)[0]
                return ({"ok": False, "error": "HTTP_429"} if pair == failed else candidate(pair)), 1
            with tempfile.TemporaryDirectory() as tmp:
                dest = Path(tmp) / "core"
                # Time is fixed only for synthetic test fixtures, never for live data.
                fresh = lambda value, now=None: core.datetime.fromisoformat(value.replace("Z", "+00:00")) == NOW if isinstance(value, str) else False
                with patch.object(core, "CFG", cfg), patch.object(core, "CORE", dest), patch.object(core, "CORE_CANDIDATES", dest / "candidates"), patch.object(core, "retry_get", side_effect=fake_retry), patch.object(core, "candidate_get_fast", side_effect=fake_candidate), patch.object(core, "fresh_time", side_effect=fresh), patch.object(core.time, "sleep"):
                    code = core.main()
                manifest = json.loads((dest / "manifest.json").read_text())
                expected = 0 if failed == "OPTIONALEUR" else 1
                self.assertEqual(code, expected)
                self.assertEqual(manifest["status"], "OK" if expected == 0 else "PARTIAL")
                self.assertEqual(set(manifest["owned_priority_status"]), set(PRIORITY))
                self.assertTrue((dest / "candidates" / "MEGAEUR.json").exists())


if __name__ == "__main__":
    unittest.main()
