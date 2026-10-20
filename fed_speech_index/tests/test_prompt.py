"""The Fed's prompt, built from the shared rubric: system prompt and response schema pinned at v5; the user prompt is unchanged since v3."""
import json
from pathlib import Path
import unittest

import build_index
import prompt

FIXTURES = Path(__file__).parent / 'fixtures'
SPEECH = {'speaker': 'Jerome H. Powell', 'role': 'Chair', 'date': '2025-10-14', 'title': 'T', 'text': 'x' * 150_010}


class FedPromptTests(unittest.TestCase):
    def test_system_prompt_is_pinned_v5(self):
        self.assertEqual(prompt.system_prompt(), (FIXTURES / 'prompt_v5_system.txt').read_text(encoding='utf-8'))

    def test_response_schema_is_pinned_v5(self):
        schema = json.dumps(prompt.SpeechScore.model_json_schema(), sort_keys=True, indent=1)
        self.assertEqual(schema, (FIXTURES / 'prompt_v5_schema.json').read_text(encoding='utf-8'))

    def test_user_prompt_unchanged_since_v3(self):
        self.assertEqual(prompt.user_prompt(SPEECH, '4-4.25'), (FIXTURES / 'prompt_v3_user_range.txt').read_text(encoding='utf-8'))
        self.assertEqual(prompt.user_prompt(dict(SPEECH, text='Body.'), '5.25'),
                         (FIXTURES / 'prompt_v3_user_rate.txt').read_text(encoding='utf-8'))


class SameDayTests(unittest.TestCase):
    def test_same_day_speeches_are_averaged(self):
        key = ('bullard', 'j')
        speeches = [{'date': '2022-07-07', 'id': 'b', 'key': key, 'value': 400 / 6, 'level': 5},
                    {'date': '2022-07-07', 'id': 'a', 'key': key, 'value': 500 / 6, 'level': 6},
                    {'date': '2022-07-01', 'id': 'c', 'key': key, 'value': 50.0, 'level': 4}]
        stances = build_index.Stances(sorted(speeches, key=lambda s: (s['date'], s['id'])))
        latest = stances.latest_speech(key, '2022-07-10')
        self.assertAlmostEqual(latest['value'], 75.0)
        self.assertEqual(latest['level'], '')
        self.assertEqual(stances.latest(key, '2022-07-05'), 50.0)


if __name__ == '__main__':
    unittest.main()
