"""The BoE policy setting given to the scorer, and the summary-page fix (real pages, see fixture manifest)."""
import csv
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import json

import config
import policy
import sources

FIXTURES = Path(__file__).parent / 'fixtures'
MANIFEST = json.loads((FIXTURES / 'manifest.json').read_text())


def get(url):
    for name, meta in MANIFEST.items():
        if meta['url'] == url:
            content = (FIXTURES / name).read_bytes()
            return SimpleNamespace(content=content, text=content.decode('utf-8', errors='replace'))
    raise AssertionError('Unexpected URL; fixture missing: ' + url)


class SettingTests(unittest.TestCase):
    def test_bank_rate_sentence_as_of_the_speech_date(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'policy_rates.csv'
            with path.open('w', newline='') as file:
                csv.writer(file).writerows([['date', 'level', 'source'], ['2025-08-07', '4', 'x'], ['2025-12-18', '3.75', 'x']])
            with patch.object(config, 'POLICY_RATES', path):
                self.assertEqual(policy.context_for('2025-12-17'), 'Bank Rate 4%.')
                self.assertEqual(policy.context_for('2025-12-18'), 'Bank Rate 3.75%.')
                self.assertEqual(policy.context_for('2025-01-01'), policy.UNKNOWN)


class SummaryPageTests(unittest.TestCase):
    def test_summary_page_is_replaced_by_full_text_pdf(self):
        entry = {'url': MANIFEST['haskel_summary.html']['url'], 'speaker': 'Jonathan Haskel',
                 'title': 'Inflation now and then - speech by Jonathan Haskel'}
        with patch('http_get.get', side_effect=get):
            record = sources.fetch_speech(entry)
        self.assertEqual(record['text_url'], MANIFEST['haskel_summary.pdf']['url'])
        self.assertGreater(len(record['text']), 20000)
        self.assertEqual(record['date'], '2021-11-23')


if __name__ == '__main__':
    unittest.main()
