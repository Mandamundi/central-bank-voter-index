"""The shared rubric is the same file in the Fed, BOJ and BOE projects (identical copies of this test)."""
import hashlib
from pathlib import Path
import unittest

import config
import rubric

# After editing rubric.py: copy it to all three projects, update this checksum in all three, bump rubric.VERSION.
RUBRIC_SHA256 = '1aa5965570ddfa8da4f316618ca7daa7388fa40f2190255f14c43ef362ffed4b'


class SharedRubricTests(unittest.TestCase):
    def test_rubric_is_the_shared_version(self):
        source = Path(rubric.__file__).read_bytes()
        self.assertEqual(hashlib.sha256(source).hexdigest(), RUBRIC_SHA256)

    def test_score_vintage_follows_the_rubric_version(self):
        self.assertEqual(config.PROMPT_VERSION, rubric.VERSION)
        self.assertEqual(config.SCORER_VERSION, f'{config.MODEL}:{rubric.VERSION}')

    def test_scale(self):
        self.assertEqual([rubric.level_to_value(i) for i in range(1, 8)], [0, 100 / 6, 200 / 6, 50, 400 / 6, 500 / 6, 100])


if __name__ == '__main__':
    unittest.main()
