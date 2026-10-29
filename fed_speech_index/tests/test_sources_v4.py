import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import config
import fraser
import sources
import store
import update
import voters

BODY = 'Thank you for the invitation. Monetary policy responds to the economic outlook. ' * 35

APRIL_2026 = (
    '<p>Voting for this action: Jerome H. Powell, John C. Williams, Michael S. Barr, Michelle W. Bowman, Lisa D. Cook, '
    'Philip N. Jefferson, Anna Paulson, and Christopher J. Waller.</p>'
    '<p>Voting against this action: Stephen I. Miran, who preferred to lower the target range for the federal funds rate '
    'by 1/4 percentage point at this meeting and Beth M. Hammack, Neel Kashkari, and Lorie K. Logan, who supported '
    'maintaining the target range for the federal funds rate but did not support inclusion of an easing bias in the '
    'statement at this time.</p>'
)


def speech(**fields):
    row = {key: '' for key in store.FIELDS}
    row.update(text=BODY, **fields)
    return row


class RosterTests(unittest.TestCase):
    def test_every_dissenter_is_a_voter_when_reasons_separate_them(self):
        self.assertEqual(voters.voters_in_minutes(APRIL_2026)[-4:],
                         ['Stephen I. Miran', 'Beth M. Hammack', 'Neel Kashkari', 'Lorie K. Logan'])
        self.assertEqual(len(voters.voters_in_minutes(APRIL_2026)), 12)

    def test_dissent_explanation_without_a_period_is_not_a_name(self):
        page = ('Voting for this action: Jerome H. Powell, John C. Williams, and Randal K. Quarles. '
                'Voting against this action: Loretta J. Mester President Mester was fully supportive of all of the actions '
                'taken to promote the smooth functioning of markets and the flow of credit to households and businesses '
                'but voted against the FOMC action because she preferred a smaller cut at this meeting. Consistent with')
        self.assertEqual(voters.voters_in_minutes(page)[-1], 'Loretta J. Mester')
        self.assertEqual(len(voters.voters_in_minutes(page)), 4)
        self.assertEqual(voters.parse_vote_names('Jerome H. Powell, Chair, Michelle W. Bowman, Vice Chair for Supervision'),
                         ['Jerome H. Powell', 'Michelle W. Bowman'])

    def test_unanimous_vote_without_against_line(self):
        page = ('<p>Voting for this action: Jerome H. Powell, John C. Williams, and Christopher J. Waller. '
                'Patrick Harker voted as an alternate member at this meeting.</p>')
        self.assertEqual(voters.voters_in_minutes(page), ['Jerome H. Powell', 'John C. Williams', 'Christopher J. Waller'])

    def test_alternate_member_note_is_not_a_name(self):
        page = ('Votes for this action: Greenspan, Bernanke, Parry, and Stewart. (Stewart voted as an alternate member.) '
                'Votes against this action: None.')
        self.assertEqual(voters.voters_in_minutes(page), ['Greenspan', 'Bernanke', 'Parry', 'Stewart'])

    def test_clean_splits_missing_commas_and_fixes_one_letter_slips(self):
        rows = [{'date': '1999-05-18', 'source': 'a', 'names': ['Greenspan', 'McDonough', 'Meyer', 'Kelley']},
                {'date': '1999-06-29', 'source': 'b', 'names': ['Greenspan', 'McDonough', 'Meyers', 'Kelley']},
                {'date': '1999-12-21', 'source': 'c', 'names': ['Greenspan', 'McDonough Kelley', 'Meyer']}]
        cleaned = voters.clean(rows)
        self.assertEqual(cleaned[1]['names'], ['Greenspan', 'McDonough', 'Meyer', 'Kelley'])
        self.assertEqual(cleaned[2]['names'], ['Greenspan', 'McDonough', 'Kelley', 'Meyer'])


class FraserTests(unittest.TestCase):
    def test_author_names(self):
        self.assertEqual(fraser.person_name('Fisher, Richard W., 1949-'), 'Richard W. Fisher')
        self.assertEqual(fraser.person_name('Stewart, Jamie B., Jr.'), 'Jamie B. Stewart Jr.')
        self.assertEqual(fraser.person_name('George, Esther L.'), 'Esther L. George')

    def test_surname_only_rosters_take_the_collection_active_at_the_time(self):
        rows = [{'date': '2006-01-31', 'source': 'm', 'names': ['Bernanke', 'Fisher']}]
        found = [{'id': '6147', 'name': 'Richard W. Fisher', 'start': '2005-05-10', 'end': '2015-03-09'},
                 {'id': '7074', 'name': 'Peter R. Fisher', 'start': '1996-10-08', 'end': '1999-12-01'}]
        with patch.object(fraser, 'collections', return_value=found):
            self.assertEqual([c['name'] for c in fraser.voter_collections(rows)], ['Richard W. Fisher'])

    def test_fraser_skips_a_speech_already_stored_from_a_bank(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            root = Path(directory)
            for key, value in [('DATA', root), ('SPEECH_DIR', root / 'speeches'), ('SCORES', root / 'scores.jsonl')]:
                stack.enter_context(patch.object(config, key, value))
            store.add_speeches([speech(id='same', date='2005-02-01', speaker='Alan Greenspan', source='board', url='https://board/x'),
                                speech(id='b', date='2012-01-10', speaker='George', source='kansascity', url='https://kc/x')])
            records = [speech(id='same', date='2005-02-01', speaker='Alan Greenspan', source='fraser', url='https://fraser/x'),
                       speech(id='f', date='2012-01-10', speaker='Esther L. George', source='fraser', url='https://fraser/y'),
                       speech(id='n', date='2012-01-11', speaker='Esther L. George', source='fraser', url='https://fraser/z')]
            outcomes = {r['url']: {'source': 'fraser', 'status': 'stored'} for r in records}
            self.assertEqual(fraser._save(records, outcomes), 1)
            self.assertEqual(sorted(s['id'] for s in store.load_speeches()), ['b', 'n', 'same'])
            self.assertEqual([s['source'] for s in store.load_speeches() if s['id'] == 'same'], ['board'])
            self.assertEqual(outcomes['https://fraser/x']['reason'], fraser.ALREADY_STORED)
            self.assertEqual(outcomes['https://fraser/y']['status'], 'excluded')
            self.assertEqual(outcomes['https://fraser/z']['status'], 'stored')

    def test_daily_pull_skips_a_speech_fraser_already_holds(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            root = Path(directory)
            for key, value in [('DATA', root), ('SPEECH_DIR', root / 'speeches'), ('PROCESSING', root / 'processing.json'),
                               ('SCORES', root / 'scores.jsonl')]:
                stack.enter_context(patch.object(config, key, value))
            store.add_speeches([speech(id='f', date='2025-12-01', speaker='Jerome H. Powell', source='fraser', url='https://fraser/p')])
            e = sources.entry('board', 'https://www.federalreserve.gov/newsevents/speech/powell20251201a.htm')
            role, _, reader = sources.SOURCES['board']
            stack.enter_context(patch.dict(sources.SOURCES, board=(role, lambda full: iter([e]), reader)))
            copy = speech(id='b', date='2025-12-01', speaker='Jerome H. Powell', source='board', url=e['url'], role=role)
            with patch.object(sources, 'fetch', return_value=(copy, '')), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(update.pull(['board'], False), 0)
            self.assertEqual(store.load_processing()[e['url']]['reason'], update.FRASER_COPY)


if __name__ == '__main__':
    unittest.main()
