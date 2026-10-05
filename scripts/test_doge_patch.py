#!/usr/bin/env python3
"""Synthetic DOGE/XDG behavior tests; no market calls or trade evidence.

Legacy source-string checks referenced quotes_usable and an old filename local
that no longer existed before this PR. Assert the intended contract directly.
"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import collect
import collect_core as core
from test_owned_coverage import NOW, STAMP, candidate, quote, envelope


class DogeAliasTests(unittest.TestCase):
    def test_original_logical_identity_is_retained(self):
        self.assertEqual(collect.CFG['pair_aliases']['DOGEEUR'], 'XDGEUR')
        for key in ('quotes_pairs', 'deep_pairs'):
            self.assertIn('DOGEEUR', collect.CFG[key])
            self.assertNotIn('XDGEUR', collect.CFG[key])
    def test_public_request_uses_kraken_native_alias(self):
        for pair in ('DOGEEUR', 'DOGE/EUR', 'doge/eur'):
            self.assertEqual(collect.request_pair(pair), 'XDGEUR')
            self.assertEqual(core.request_pair(pair), 'XDGEUR')
    def test_native_quote_covers_logical_doge(self):
        payload = envelope({'rows': [quote('DOGEEUR')]})
        result = core.owned_quote_coverage(payload, ['DOGEEUR'], NOW)
        self.assertTrue(result['research_usable'])
        self.assertFalse(result['executable_evidence'])
        self.assertFalse(result['missing_pairs'])
    def test_native_candidate_identity_validates_against_logical_pair(self):
        payload = candidate('DOGEEUR')
        self.assertEqual(payload['body']['pair_metadata']['altname'], 'XDGEUR')
        self.assertTrue(core.owned_candidate_status(payload, 'DOGEEUR', NOW)['fresh_quote_evidence'])
        self.assertFalse(core.owned_candidate_status(payload, 'LTCEUR', NOW)['fresh_quote_evidence'])
    def test_published_filename_remains_logical_doge(self):
        payload = candidate('DOGEEUR')
        with tempfile.TemporaryDirectory() as tmp, patch.object(core, 'CORE', Path(tmp)):
            core.save_core('candidates/DOGEEUR.json', payload)
            saved = json.loads((Path(tmp) / 'candidates/DOGEEUR.json').read_text())
            self.assertEqual(saved['pair_metadata']['altname'], 'XDGEUR')
            self.assertFalse((Path(tmp) / 'candidates/XDGEUR.json').exists())


if __name__ == '__main__':
    unittest.main()
