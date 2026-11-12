"""Offline regressions using real official pages retrieved 2026-10-16 (see fixture manifest)."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import hashlib
import json
import unittest
import config
import documents
import history
import sources
import voters

FIXTURES=Path(__file__).parent/'fixtures'
MANIFEST=json.loads((FIXTURES/'manifest.json').read_text())

def get(url):
    for name,meta in MANIFEST.items():
        if meta['url']==url:
            content=(FIXTURES/name).read_bytes()
            return SimpleNamespace(content=content,text=content.decode('utf-8',errors='replace'))
    raise AssertionError('Unexpected URL; fixture missing: '+url)


class OfficialSourceTests(unittest.TestCase):
    def test_author_suffixes_preserve_identity(self):
        self.assertEqual(sources.speaker_label('Remarks by Huw Pill - Monetary Policy Strategy'), 'Huw Pill')
        self.assertEqual(sources.speaker_label('Speech by Megan Greene transcript'), 'Megan Greene')
        self.assertEqual(sources.speaker_label('Remarks by Mark Carney given during the UN summit'), 'Mark Carney')

    def test_fixture_provenance(self):
        for name,meta in MANIFEST.items():
            self.assertEqual(hashlib.sha256((FIXTURES/name).read_bytes()).hexdigest(),meta['sha256'])

    def test_full_sitemap_has_legacy_and_direct_pdf_records(self):
        records=sources.parse_catalogue((FIXTURES/'catalogue.html').read_bytes())
        self.assertGreater(len(records),2000)
        self.assertIn(1997,{r['year'] for r in records})
        self.assertTrue(any(r['url'].endswith('.pdf') for r in records))
        self.assertTrue(all(r['date'] is None for r in records))

    def test_exact_nine_mpc_member_cards_not_treasury_or_other_committees(self):
        with patch('http_get.get',side_effect=get):found=sources.current_voters()
        self.assertEqual(len(found),9)
        self.assertEqual({voters.raw_key(r['name']) for r in found},set(voters.Roster().on('2026-10-16')))
        self.assertNotIn('Sam Woods',{r['name'] for r in found})

    def test_pdf_wrapper_uses_full_text_not_related_speeches(self):
        entry={'url':MANIFEST['breeden.html']['url'],'title':'Monetary policy as engineering? − speech by Sarah Breeden','speaker':'Sarah Breeden'}
        with patch('http_get.get',side_effect=get):record=sources.fetch_speech(entry)
        self.assertEqual(record['date'],'2023-12-19')
        self.assertTrue(record['text_url'].endswith('.pdf'))
        self.assertGreater(len(record['text']),10000)
        self.assertNotIn('Other speeches',record['text'])

    def test_html_body_does_not_include_navigation_or_related_releases(self):
        entry={'url':MANIFEST['bailey.html']['url'],'title':'Policy − speech by Andrew Bailey','speaker':'Andrew Bailey'}
        with patch('http_get.get',side_effect=get):record=sources.fetch_speech(entry)
        self.assertEqual(record['date'][:4],'2026')
        self.assertGreater(len(record['text']),5000)
        self.assertNotIn('Other speeches',record['text'])
        self.assertNotIn('Switchboard:',record['text'])

    def test_upcoming_speech_is_scheduled_not_failed_or_scored(self):
        entry={'url':MANIFEST['upcoming.html']['url'],'title':'Megan Greene: Speech at LSE','speaker':'Megan Greene'}
        import datetime
        with patch('http_get.get',side_effect=get), patch('sources.today',return_value=datetime.date(2026,10,16)):
            with self.assertRaises(documents.Scheduled) as error:sources.fetch_speech(entry)
        self.assertEqual(error.exception.day,'2026-10-20')

    def test_same_day_embargoed_placeholder_is_scheduled_not_failed(self):
        entry={'url':MANIFEST['upcoming.html']['url'],'title':'Megan Greene: Speech at LSE','speaker':'Megan Greene'}
        import datetime
        with patch('http_get.get',side_effect=get), patch('sources.today',return_value=datetime.date(2026,10,20)):
            with self.assertRaises(documents.Scheduled) as error:sources.fetch_speech(entry)
        self.assertEqual(error.exception.day,'2026-10-20')

    def test_embargoed_placeholder_with_long_blurb_is_scheduled(self):
        blurb=' '.join(['The conference brings together central bankers and academics to discuss policy.']*30)
        page=f'<html><head><meta property="og:description" content="Text to be published Tuesday 10 November 12.30pm"></head><body><main><h1>Clare Lombardelli: Speech at the Bank Watchers Conference, London (to be published 12:30pm)</h1><div class="content-block"><p>{blurb}</p></div></main></body></html>'.encode()
        url='https://www.bankofengland.co.uk/speech/2026/november/clare-lombardelli-speech-at-the-bank-watchers-conference'
        entry={'url':url,'title':'Clare Lombardelli: Speech at the Bank Watchers Conference, London (to be published 12:30pm)','speaker':'Clare Lombardelli'}
        self.assertGreater(len(sources.body(sources.tree(page))),config.MIN_HTML_TEXT_CHARS)
        import datetime
        with patch('http_get.get',return_value=SimpleNamespace(content=page,text=page.decode(),headers={})), \
             patch('sources.today',return_value=datetime.date(2026,11,10)):
            with self.assertRaises(documents.Embargoed) as error:sources.fetch_speech(entry)
        self.assertEqual(error.exception.day,'2026-11-10')

    def test_no_exact_day_means_no_invented_month_start(self):
        self.assertIsNone(sources.exact_date(sources.tree('<main><h1>Speech</h1></main>'),'https://www.bankofengland.co.uk/speech/2026/october/anything'))

    def test_published_decision_date_does_not_use_meeting_end(self):
        self.assertEqual(sources.decision_date('Meeting ending on 16 September 2026. Published on 17 September 2026.'),'2026-09-17')

    def test_real_workbook_all_meetings_agree_with_membership_history(self):
        snapshots=history.parse_votes((FIXTURES/'votes.xlsx').read_bytes());roster=voters.Roster()
        covered=[r for r in snapshots if r['date']>=roster.coverage_start()]
        self.assertGreaterEqual(len(covered),75)
        for row in covered:
            self.assertTrue(all(roster.is_voter(name,row['date']) for name in row['names']),row)

    def test_haldane_pill_gap_is_real_vacancy(self):
        roster=voters.Roster()
        self.assertTrue(roster.is_voter('Andrew Haldane','2021-06-30'))
        self.assertFalse(roster.is_voter('Huw Pill','2021-09-05'))
        self.assertEqual(len(roster.on('2021-07-01')),8)
        self.assertEqual(len(roster.on('2021-09-06')),9)

    def test_broadbent_was_still_voter_until_june_2024(self):
        roster=voters.Roster()
        self.assertTrue(roster.is_voter('Ben Broadbent','2024-06-30'))
        self.assertFalse(roster.is_voter('Ben Broadbent','2024-07-01'))
        self.assertTrue(roster.is_voter('Clare Lombardelli','2024-07-01'))

    def test_nested_content_blocks_are_not_counted_twice(self):
        page=sources.tree('<main><div class="content-block"><div class="content-block"><p>Only once.</p></div></div></main>')
        self.assertEqual(sources.body(page).count('Only once.'),1)


if __name__=='__main__':unittest.main()
