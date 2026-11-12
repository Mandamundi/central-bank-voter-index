"""Methodology and persistence regressions. No network or paid model calls."""
from __future__ import annotations
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import build_index
import config
import documents
import policy
import prompt
import rubric
import score
import store
import update
import voters

NAMES=['Alice Alpha','Bob Beta','Cathy Gamma','David Delta','Ella Epsilon','Fred Zeta','Gina Eta','Henry Theta','Iris Iota']

def terms():
    return [{'start_date':'2020-01-01','end_date':'','name':name,'role':'Voter','source':'official fixture','aliases':''} for name in NAMES]


def speech(name,day,level=5,available=None,identifier=None):
    return {'id':identifier or name,'date':day,'available_date':available or day,'key':voters.raw_key(name),
            'level':level,'value':prompt.level_to_value(level),'availability_basis':'fixture'}


def missing(status):
    error=RuntimeError(f'HTTP Error {status}: ');error.response=type('Response',(),{'status_code':status})()
    return error


class DocumentScopeTests(unittest.TestCase):
    def test_supporting_paper_is_not_an_individual_stance_observation(self):
        with self.assertRaises(documents.Excluded):
            documents.prepared_format('Inflation model - accompanying paper to a speech')

    def test_slides_presented_by_are_not_prepared_speeches(self):
        with self.assertRaises(documents.Excluded):
            documents.prepared_format('International monetary system - slides presented by Mark Carney')


class RosterAndIndexTests(unittest.TestCase):
    def test_injected_ledger_and_exact_identity(self):
        roster=voters.Roster(terms())
        self.assertEqual(len(roster.on('2020-01-01')),9)
        self.assertFalse(roster.is_voter('Albert Alpha','2020-01-02'))
        self.assertFalse(roster.is_voter(NAMES[0],'2019-12-31'))

    def test_inclusive_retirement_and_vacancy(self):
        rows=terms();rows[0]['end_date']='2020-01-02'
        rows.append(dict(rows[0],start_date='2020-01-04',end_date='',name='New Arrival'))
        roster=voters.Roster(rows)
        self.assertEqual([len(roster.on(d)) for d in ['2020-01-02','2020-01-03','2020-01-04']],[9,8,9])

    def test_overlapping_terms_rejected(self):
        with self.assertRaises(ValueError):
            voters.Roster(terms()+[terms()[0]])

    def test_more_than_nine_rejected(self):
        with self.assertRaises(ValueError):
            voters.Roster(terms()+[dict(terms()[0],name='New Arrival')])

    def test_full_roster_mean_gives_each_member_one_weight(self):
        rows=[speech(n,'2020-01-01',10 if i==0 else 0) for i,n in enumerate(NAMES)]
        with patch.object(config,'START_DATE',None):
            single,members=build_index.build_daily(rows,voters.Roster(terms()),dt.date(2020,1,1))
            double,_=build_index.build_daily(rows+[speech(NAMES[0],'2020-01-01',10,identifier='second-speech')],
                                             voters.Roster(terms()),dt.date(2020,1,1))
        # The index is the equal-weight mean of the members' estimates; a second same-day text adds no weight.
        self.assertAlmostEqual(single[0]['index'],sum(m['estimate'] for m in members)/9,places=4)
        self.assertAlmostEqual(double[0]['index'],single[0]['index'])
        self.assertEqual(len(members),9)
        self.assertGreater(members[0]['estimate'],max(m['estimate'] for m in members[1:]))

    def test_index_starts_on_the_first_day_every_voter_has_a_stance(self):
        rows=[speech(n,'2020-01-01') for n in NAMES[:-1]]+[speech(NAMES[-1],'2020-01-03',10)]
        with patch.object(config,'START_DATE',None):
            daily,members=build_index.build_daily(rows,voters.Roster(terms()),dt.date(2020,1,4))
        self.assertEqual([r['date'] for r in daily],['2020-01-03','2020-01-04'])
        self.assertGreater(daily[0]['index'],50)
        self.assertLess(daily[0]['index'],100)
        self.assertEqual(members[0]['date'],'2020-01-03')

    def test_after_the_start_a_new_voter_without_a_stance_counts_at_the_committee_view_and_is_listed(self):
        rows=terms();rows[-1]['end_date']='2020-01-01'
        rows.append(dict(rows[-1],start_date='2020-01-02',end_date='',name='New Arrival'))
        speeches=[speech(n,'2020-01-01') for n in NAMES[:-2]]+[speech(NAMES[-2],'2020-01-01',10),speech(NAMES[-1],'2020-01-01',0)]
        with patch.object(config,'START_DATE',None):
            daily,members=build_index.build_daily(speeches,voters.Roster(rows),dt.date(2020,1,2))
        self.assertEqual(daily[1]['scored_voters'],8)
        self.assertEqual(daily[1]['missing_members'],'New Arrival')
        self.assertEqual(members[-1]['member'],'New Arrival')
        self.assertEqual(members[-1]['score'],'')
        today=[m['estimate'] for m in members if m['date']=='2020-01-02']
        self.assertEqual(len(today),9)
        self.assertAlmostEqual(daily[1]['index'],sum(today)/9,places=4)
        # Readings so far average 50, so the committee's view, and the newcomer's estimate, is about 50.
        self.assertAlmostEqual(members[-1]['estimate'],50,delta=5)

    def test_no_stance_at_all_gives_no_index_rows(self):
        daily,_=build_index.build_daily([],voters.Roster(terms()),dt.date(2020,1,1))
        self.assertEqual(daily,[])

    def test_warnings_never_blank_todays_index(self):
        today=dt.datetime.now(build_index.ZoneInfo(config.BANK_TIMEZONE)).date().isoformat()
        rows=[speech(n,'2020-01-01') for n in NAMES]
        with tempfile.TemporaryDirectory() as out, patch('sys.argv',['build_index.py']), patch.object(config,'OUTPUT',Path(out)), \
                patch.object(build_index,'policy_speeches',return_value=rows), \
                patch.object(build_index.voters,'Roster',return_value=voters.Roster(terms())), \
                patch.object(build_index,'warnings',return_value=['Collector has not completed today']):
            self.assertEqual(build_index.main(),0)
            index=(Path(out)/f'{config.BANK}_speech_index.csv').read_text().splitlines()
            status=json.loads((Path(out)/'status.json').read_text())
        self.assertEqual(index[-1],f'{today},50.00')
        self.assertEqual(status['warnings'],['Collector has not completed today'])

    def test_publication_delay_does_not_backdate_score(self):
        stances=build_index.Stances([speech(NAMES[0],'2020-01-01',available='2020-01-05')])
        self.assertIsNone(stances.latest(voters.raw_key(NAMES[0]),'2020-01-04'))
        self.assertEqual(stances.latest(voters.raw_key(NAMES[0]),'2020-01-05'),50)

    def test_late_old_speech_cannot_replace_newer_delivered_stance(self):
        stances=build_index.Stances([speech(NAMES[0],'2020-01-01',0,'2020-01-05','old'),
                                    speech(NAMES[0],'2020-01-03',10,'2020-01-03','new')])
        self.assertEqual(stances.latest(voters.raw_key(NAMES[0]),'2020-01-06'),100)

    def test_reappointment_after_gap_cannot_inherit_old_stance(self):
        stances=build_index.Stances([speech(NAMES[0],'2020-01-01')])
        self.assertIsNone(stances.latest_speech(voters.raw_key(NAMES[0]),'2020-02-02','2020-02-01'))

    def test_same_day_documents_are_averaged_not_arbitrary_url_order(self):
        stances=build_index.Stances([speech(NAMES[0],'2020-01-01',0,identifier='z'),
                                    speech(NAMES[0],'2020-01-01',10,identifier='a')])
        self.assertEqual(stances.latest(voters.raw_key(NAMES[0]),'2020-01-01'),50)


class ScoringTests(unittest.TestCase):
    def test_scale(self):
        self.assertEqual([prompt.level_to_value(i) for i in range(0,11)],[10.0*i for i in range(11)])

    def test_quote_fabrication_rejected(self):
        self.assertEqual(score.verify_quotes(['We will hold.','We will cut.'],'We will hold.'),[True,False])
        self.assertEqual(score.verify_quotes([''],'abc'),[False])

    def test_japanese_pdf_whitespace_normalization(self):
        self.assertEqual(score.verify_quotes(['金融緩和を継続します。'],'金融緩和を\n 継続します。'),[True])

    def test_same_response_schema_as_the_fed(self):
        self.assertEqual(list(prompt.SpeechScore.model_fields),['policy_relevant','evidence','rationale','level'])

    def test_user_prompt_uses_the_shared_template(self):
        s={'speaker':'Person','role':'Voter','date':'2020-01-01','title':'Policy','text':'x'*(rubric.MAX_TEXT_CHARS+10)}
        text=prompt.user_prompt(s,'Bank Rate 5%.')
        self.assertTrue(text.startswith('Speaker: Person (Voter)\nDate: 2020-01-01\nTitle: Policy\nCurrent policy setting on this date: Bank Rate 5%.\n\nSPEECH TEXT:\n'))
        self.assertTrue(text.endswith('x'*rubric.MAX_TEXT_CHARS))

    def test_unverified_quotes_are_flagged_not_rejected(self):
        payload=dict(policy_relevant=True,level=5,evidence=['Actual prepared words.','another invention'],
                     rationale='A restrictive hold with no clear next move preference.')
        from types import SimpleNamespace
        response=SimpleNamespace(status_code=200,json=lambda:{'status':'completed','output':[{'type':'message',
                                 'content':[{'type':'output_text','text':json.dumps(payload)}]}]})
        s={'speaker':'Person','role':'Voter','date':'2020-01-01','title':'Policy','text':'Actual prepared words.'}
        with patch('openai_api.requests.post',return_value=response):
            result=score.call_openai('dummy',s,'unknown')
        self.assertEqual(result.level,5)
        self.assertEqual(score.verify_quotes(result.evidence,s['text']),[True,False])

    def test_system_prompt_is_the_shared_core_plus_bank_appendix(self):
        text=prompt.system_prompt()
        self.assertEqual(text,rubric.system_prompt(prompt.BANK))
        self.assertTrue(prompt.APPENDIX.strip())
        self.assertIn(prompt.APPENDIX,text)
        self.assertNotRegex(prompt.APPENDIX.lower(),r'level \d|= ?\d')   # the appendix states facts, never levels


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.patchers=[patch.object(config,'DATA',self.root),patch.object(config,'SPEECH_DIR',self.root/'speeches'),
                       patch.object(config,'PROCESSING',self.root/'processing.json'),patch.object(config,'SCORES',self.root/'scores.jsonl'),
                       patch.object(policy,'context_for',return_value='known dated setting')]
        for p in self.patchers:p.start()
    def tearDown(self):
        for p in reversed(self.patchers):p.stop()
        self.temp.cleanup()

    def record(self):
        return {'id':'id','date':'2020-01-01','published_date':'2020-01-01','available_date':'2020-01-01',
                'speaker':'Alice Alpha','role':'Voter','title':'Policy','url':'https://example.org/speech',
                'text':'Prepared source text.','content_hash':store.content_hash('Prepared source text.')}

    def test_score_with_unverified_quote_still_counts(self):
        record=self.record();store.add_speeches([record])
        store.append_score({'id':'id','scorer_version':config.SCORER_VERSION,'input_hash':store.score_input_hash(record),
                            'evidence':['a','b'],'evidence_verified':[True,False],'policy_relevant':True,'level':5})
        self.assertIn('id',store.load_scores())

    def test_upsert_revision_invalidates_scoring_vintage(self):
        record=self.record();store.add_speeches([record])
        store.append_score({'id':'id','scorer_version':config.SCORER_VERSION,'input_hash':store.score_input_hash(record),
                            'evidence':[],'evidence_verified':[],'policy_relevant':False,'level':None})
        self.assertIn('id',store.load_scores())
        record['text']='Revised source text.';record['content_hash']=store.content_hash(record['text']);store.add_speeches([record])
        self.assertEqual(store.load_scores(),{})
        self.assertEqual(len(store.load_speeches()),1)

    def test_metadata_correction_invalidates_score_input(self):
        record=self.record();before=store.score_input_hash(record);record['date']='2020-01-02'
        self.assertNotEqual(before,store.score_input_hash(record))

    def test_discovery_limit_keeps_unattempted_urls_pending(self):
        entries=[{'url':'https://example.org/'+str(i),'title':'Policy','date':'2020-01-01'} for i in range(2)]
        with patch('sources.fetch_speech',side_effect=ValueError('broken metadata')):
            counts,unresolved=update.process(entries,'2020-01-01',limit=1)
        self.assertEqual(counts['failed'],1);self.assertEqual(len(unresolved),2)
        self.assertEqual(sorted(r['status'] for r in store.load_processing().values()),['failure','pending'])

    def test_old_failures_survive_live_lookback(self):
        entry={'url':'https://example.org/old','title':'Policy','date':'2020-01-01'}
        store.save_processing({entry['url']:{'status':'failure','entry':entry}})
        with patch('sources.fetch_speech',side_effect=documents.Excluded('No prepared text')):
            _,unresolved=update.process([],'2020-01-01')
        self.assertFalse(unresolved)
        self.assertEqual(store.load_processing()[entry['url']]['status'],'excluded')

    def test_unlisted_404_is_withdrawn_not_a_permanent_failure(self):
        entry={'url':'https://example.org/placeholder','title':'Policy (to be published at 9am)','date':None}
        later=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=2)).isoformat()
        store.save_processing({entry['url']:{'status':'failure','entry':entry,'collector_version':config.COLLECTOR_VERSION,
                                             'retry_after':later}})
        with patch('sources.fetch_speech',side_effect=missing(404)):
            counts,unresolved=update.process([],'2020-01-01')
        self.assertFalse(unresolved);self.assertEqual(counts['excluded'],1)
        self.assertTrue(store.load_processing()[entry['url']]['reason'].startswith('Withdrawn'))
        with patch('sources.fetch_speech',side_effect=ValueError('relisted page fetched again')) as fetch:
            update.process([entry],'2020-01-01')
        fetch.assert_called_once()

    def test_listed_404_stays_a_failure(self):
        entry={'url':'https://example.org/listed','title':'Policy','date':'2020-01-01'}
        with patch('sources.fetch_speech',side_effect=missing(404)):
            _,unresolved=update.process([entry],'2020-01-01')
        self.assertEqual(unresolved,[entry['url']])

    def test_listed_404_behind_embargo_label_is_scheduled(self):
        entry={'url':'https://example.org/mills','title':'Clair Mills: Conference, Exeter (to be published at 9am)','date':None}
        with patch('sources.fetch_speech',side_effect=missing(404)):
            counts,unresolved=update.process([entry],'2020-01-01')
        self.assertFalse(unresolved);self.assertEqual(counts['scheduled'],1)

    def test_embargo_stuck_for_days_fails_loudly(self):
        entry={'url':'https://example.org/stuck','title':'Policy (to be published 12:30pm)','date':None}
        store.save_processing({entry['url']:{'status':'scheduled','entry':entry,'collector_version':config.COLLECTOR_VERSION,
                                             'embargo_since':'2026-11-01','retry_after':'2026-11-01T00:00:00+00:00'}})
        with patch('sources.fetch_speech',side_effect=documents.Embargoed('2026-11-10')):
            _,unresolved=update.process([entry],'2020-01-01')
        self.assertEqual(unresolved,[entry['url']])
        with patch('sources.fetch_speech',side_effect=documents.Embargoed('2026-11-11')):
            _,unresolved=update.process([entry],'2020-01-01')
        self.assertEqual(unresolved,[entry['url']])
        self.assertEqual(store.load_processing()[entry['url']]['embargo_since'],'2026-11-01')

    def test_listed_failure_is_retried_before_its_backoff_expires(self):
        entry={'url':'https://example.org/lombardelli','title':'Policy (to be published 12:30pm)','date':None}
        later=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=2)).isoformat()
        store.save_processing({entry['url']:{'status':'failure','entry':entry,'collector_version':config.COLLECTOR_VERSION,
                                             'reason':'ValueError: No suitable full-text PDF linked from speech page','retry_after':later}})
        with patch('sources.fetch_speech',side_effect=documents.Embargoed('2026-11-10')) as fetch:
            counts,unresolved=update.process([entry],'2020-01-01')
        fetch.assert_called_once();self.assertFalse(unresolved);self.assertEqual(counts['scheduled'],1)

    def test_collector_version_retries_old_exclusions(self):
        entry={'url':'https://example.org/old','title':'Policy','date':'2020-01-01'}
        store.save_processing({entry['url']:{'status':'excluded','entry':entry,'collector_version':'old'}})
        with patch('sources.fetch_speech',side_effect=ValueError('new parser exercised')) as fetch:
            update.process([entry],'2020-01-01')
        fetch.assert_called_once()

    def test_reference_to_another_speech_does_not_make_it_a_duplicate(self):
        first=dict(self.record(), text_url='https://example.org/first.pdf',
                   document_urls=json.dumps(['https://example.org/second.pdf']))
        store.add_speeches([first])
        entry={'url':'https://example.org/second.pdf','title':'Policy','date':'2020-01-01'}
        second=dict(self.record(),url=entry['url'],text_url=entry['url'])
        with patch('sources.fetch_speech',return_value=second):
            counts,_=update.process([entry],'2020-01-01')
        self.assertEqual(counts['stored'],1)
        self.assertEqual(len(store.load_speeches()),2)

    def test_rechecking_duplicate_cannot_overwrite_canonical_record(self):
        first=dict(self.record(),text_url='https://example.org/first.pdf')
        store.add_speeches([first])
        entry={'url':'https://example.org/separate','title':'Policy','date':'2020-01-01'}
        store.save_processing({entry['url']:{'status':'duplicate','speech_id':'id','entry':entry}})
        second=dict(self.record(),url=entry['url'],text_url='https://example.org/separate.pdf')
        with patch('sources.fetch_speech',return_value=second):
            update.process([entry],'2020-01-01',force=True)
        records={r['id']:r for r in store.load_speeches()}
        self.assertEqual(len(records),2)
        self.assertEqual(records['id']['url'],first['url'])

    def test_legacy_duplicate_repair_keeps_one_record_and_valid_checkpoints(self):
        first=dict(self.record(),text_url='https://example.org/first.pdf')
        store.add_speeches([first,dict(first,id='old-copy')])
        store.save_processing({first['url']:{'status':'stored','speech_id':'old-copy'}})
        self.assertEqual(store.reconcile_duplicates(),1)
        records=store.load_speeches()
        self.assertEqual(len(records),1)
        self.assertEqual(store.load_processing()[first['url']]['speech_id'],records[0]['id'])

    def test_quarantine_preserves_source_before_removal(self):
        store.add_speeches([self.record()]);store.quarantine('id','duplicate')
        self.assertFalse(store.load_speeches())
        self.assertEqual(json.loads((self.root/'quarantine/id.json').read_text())['record']['text'],'Prepared source text.')


if __name__=='__main__':unittest.main()
