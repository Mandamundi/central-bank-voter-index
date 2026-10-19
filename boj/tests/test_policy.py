"""The BOJ policy setting given to the scorer: setting sentences only, never votes, dissents or later decisions."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import config
import policy

# Shapes taken from real statements: vote phrases, notes, dissents, proposals and PDF word splits.
YCC = ("1. At the Monetary Policy Meeting held today, the Policy Board of the Bank of Japan decided upon the following. "
       "(1) Yield curve control The Bank decided, by a 7-2 majority vote, to set the following guideline for market "
       "operations for the intermeeting period. [Note 1] The short-term policy interest rate: The Bank will apply a "
       "negative interest rate of minus 0.1 percent to the Policy-Rate Balances in current accounts held by financial "
       "institutions at the Bank. The long-term interest rate: The Bank will purchase Japanese government bonds (JGBs) "
       "so that 10-year JGB yields will remain at around zero percent. 2. Japan's economy has turned to a moderate "
       "expansion. [Note 1] Voting for the action: Mr. H. Kuroda, Mr. K. Iwata. Voting against the action: Mr. T. Sato. "
       "Mr. T. Sato dissented, considering that the Bank will apply a negative interest rate that was harmful.")
HIKE = ("Guideline for Money Market Operations 1. At the Monetary Policy Meeting held today, the Policy Board of the "
        "Bank of Japan decided, by an 8 -1 majority vote, to set the following guideline for money market operations "
        "for the intermeeting period: [Note 1] The Bank will encourage the uncollateralize d overnight call rate to "
        "remain at around 0. 75 percent.1 2. Japan's economy has recovered moderately. [Note 1] Takata Hajime dissented. "
        "He proposed that the Bank set the guideline as follows: the Bank would encourage the uncollateralized overnight "
        "call rate to remain at around 1.0 percent. The proposal was defeated by a majority vote.")
PLAN = ("At the Monetary Policy Meeting held today, the Policy Board decided on a plan to reduce the amount of its "
        "monthly outright purchases of JGBs so that it will be about 2 trillion yen in January-March 2027.")


class SettingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        rows = {'a': {'date': '2017-07-20', 'source': 'k170720a.pdf', 'context': YCC},
                'b': {'date': '2026-03-19', 'source': 'k260319a.pdf', 'context': HIKE},
                'c': {'date': '2026-04-10', 'source': 'k260410a.pdf', 'context': PLAN}}
        (Path(self.temp.name) / 'policy_context.json').write_text(json.dumps(rows))
        self.patch = patch.object(config, 'DATA', Path(self.temp.name))
        self.patch.start()
        policy._statements.cache_clear()

    def tearDown(self):
        self.patch.stop()
        policy._statements.cache_clear()
        self.temp.cleanup()

    def test_ycc_setting_without_votes_or_dissents(self):
        text = policy.context_for('2017-08-01')
        self.assertIn('negative interest rate of minus 0.1 percent', text)
        self.assertIn('10-year JGB yields will remain at around zero percent', text)
        for leak in ('Kuroda', 'Sato', 'dissent', 'majority', 'Voting', 'economy'):
            self.assertNotIn(leak, text)

    def test_rate_guideline_survives_pdf_spacing_and_drops_proposals(self):
        text = policy.context_for('2026-03-20')
        self.assertIn('around 0.75 percent', text)
        for leak in ('Takata', '1.0 percent', 'proposal', 'majority'):
            self.assertNotIn(leak, text)

    def test_supplement_follows_latest_guideline(self):
        lines = policy.context_for('2026-05-01').splitlines()
        self.assertTrue(lines[0].startswith('guideline decided 2026-03-19:'))
        self.assertIn('2 trillion yen', lines[1])

    def test_no_future_or_missing_setting(self):
        self.assertNotIn('0.75', policy.context_for('2026-03-18'))
        self.assertEqual(policy.context_for('2016-01-01'), policy.UNKNOWN)


if __name__ == '__main__':
    unittest.main()
