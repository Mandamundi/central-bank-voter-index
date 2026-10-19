"""Real Japanese archive, PDF and meeting-minute regressions; all fixtures have source hashes."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import hashlib
import json
import unittest
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


def entry(urlpart):
    records=sources.parse_catalogue((FIXTURES/'catalogue.html').read_bytes(),'https://www.boj.or.jp/about/press/koen_2026/index.htm')
    return next(r for r in records if urlpart in r['url'])


class OfficialSourceTests(unittest.TestCase):
    def test_fixture_provenance(self):
        for name,meta in MANIFEST.items():self.assertEqual(hashlib.sha256((FIXTURES/name).read_bytes()).hexdigest(),meta['sha256'])

    def test_current_roster_exactly_nine_and_correct_deputy_roles(self):
        with patch('http_get.get',side_effect=get):found=sources.current_voters()
        self.assertEqual({voters.raw_key(r['name']) for r in found},set(voters.Roster().on('2026-10-16')))
        self.assertEqual(next(r for r in found if r['name']=='UCHIDA Shinichi')['role'],'Deputy Governor')
        self.assertTrue(next(r for r in found if r['name']=='UEDA Kazuo')['source'].endswith('gv_ueda.htm'))

    def test_proxy_delivery_belongs_to_author_not_reader(self):
        record=entry('261016')
        self.assertEqual(record['speaker'],'UEDA Kazuo')
        self.assertEqual(voters.resolve_speaker('植田総裁（内田副総裁代読）'),'UEDA Kazuo')

    def test_single_character_surname_only_matches_whole_byline(self):
        self.assertEqual(voters.resolve_speaker('増審議委員'),'MASU Kazuyuki')
        self.assertIsNone(voters.resolve_speaker('増加する物価と金利'))

    def test_sato_and_uchida_correct_japanese_names(self):
        self.assertEqual(voters.resolve_speaker('佐藤綾野'),'SATO Ayano')
        self.assertEqual(voters.resolve_speaker('内田眞一'),'UCHIDA Shinichi')
        self.assertEqual(voters.resolve_speaker('内田真一'),'UCHIDA Shinichi')

    def test_real_pdf_only_page_keeps_japanese_text(self):
        with patch('http_get.get',side_effect=get):record=sources.fetch_speech(entry('261015'))
        self.assertEqual(record['speaker'],'KOEDA Junko')
        self.assertEqual(record['date'],'2026-10-15')
        self.assertTrue(record['text_url'].endswith('ko261015a1.pdf'))
        self.assertIn('金融政策',record['text'])
        self.assertGreater(len(record['text']),5000)

    def test_translation_release_does_not_backdate_to_delivery(self):
        e={'url':MANIFEST['translated.html']['url'],'date':'2019-10-03','speaker_label':'若田部副総裁',
           'title':'日本経済は変化したのか：課題と展望','role':'副総裁'}
        with patch('http_get.get',side_effect=get):record=sources.fetch_speech(e)
        self.assertEqual(record['date'],'2019-10-03')
        self.assertEqual(record['available_date'],'2019-10-04')

    def test_actual_2017_minutes_exclude_government_and_reporting_staff(self):
        content=documents.pdf_text(SimpleNamespace(content=(FIXTURES/'minutes.pdf').read_bytes()))
        names=history.parse_minutes(content,'2017-09-21')
        self.assertEqual(len(names),9)
        self.assertIn('KURODA Haruhiko',names)
        self.assertNotIn('AMAMIYA Masayoshi',names) # Executive Director then, a voter only from 2018.

    def test_retired_members_and_exact_transitions(self):
        roster=voters.Roster()
        self.assertTrue(roster.is_voter('NAKAGAWA Junko','2021-06-30'))
        self.assertFalse(roster.is_voter('KOEDA Junko','2025-03-25'))
        self.assertTrue(roster.is_voter('KOEDA Junko','2025-03-26'))
        self.assertTrue(roster.is_voter('TAMURA Naoki','2022-07-24'))
        self.assertFalse(roster.is_voter('TAMURA Naoki','2022-07-23'))
        self.assertTrue(roster.is_voter('NAKAGAWA Junko','2026-06-29'))
        self.assertFalse(roster.is_voter('NAKAGAWA Junko','2026-06-30'))
        self.assertTrue(roster.is_voter('SATO Ayano','2026-06-30'))

    def test_negative_rate_statement_retains_framework_not_unsigned_scalar(self):
        content=documents.pdf_text(SimpleNamespace(content=(FIXTURES/'negative_rate.pdf').read_bytes()))
        context=sources.extract_policy_context(content)
        self.assertIn('minus 0.1 percent',context)
        self.assertIn('three-tier system',context)

    def test_policy_year_collects_separate_jgb_plan_and_omits_reference_duplicates(self):
        with patch('http_get.get',return_value=get(MANIFEST['states.html']['url'])):links=sources.policy_documents(2026)
        self.assertTrue(any('k260616a.pdf' in u for _,u in links))
        self.assertTrue(any('k260616b.pdf' in u for _,u in links))
        self.assertFalse(any('k260616c.pdf' in u for _,u in links))

    def test_nonvoting_executive_directors_are_explicitly_identified(self):
        records=sources.parse_catalogue((FIXTURES/'catalogue.html').read_bytes(),MANIFEST['catalogue.html']['url'])
        self.assertTrue(any(r['role']=='nonvoter' for r in records))


if __name__=='__main__':unittest.main()
