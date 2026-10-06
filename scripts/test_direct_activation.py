"""Offline tests of explicit direct-source wiring; no network or financial state."""
from pathlib import Path
import unittest
import direct_kraken
import collect_direct

class DirectActivationTests(unittest.TestCase):
    def test_existing_runner_calls_direct_wrapper(self):
        text=(Path(__file__).parent/'run_public_collector.py').read_text()
        self.assertIn("'-B', 'scripts/collect_direct.py'",text)
        self.assertNotIn("'-B', 'scripts/collect_core.py'",text)
    def test_wrapper_preserves_old_history_and_groups_new_transport(self):
        text=(Path(__file__).parent/'collect_direct.py').read_text()
        self.assertIn('by_transport_revision',text)
        self.assertIn("history[-1]['transport_revision']",text)
        self.assertNotIn('history=[]',text)
    def test_no_worker_called_in_direct_adapter(self):
        text=(Path(__file__).parent/'direct_kraken.py').read_text()
        self.assertNotIn('workers.dev',text)
        self.assertNotIn('/0/private/',text)
    def test_archive_contains_separate_reference_file(self):
        text=(Path(__file__).parent/'run_public_collector.py').read_text()
        self.assertIn("'data/independent-quotes.json'",text)
        self.assertIn('is_independent_freshness_fallback=False',text)

if __name__=='__main__':unittest.main()
