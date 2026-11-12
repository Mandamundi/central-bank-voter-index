import contextlib
import datetime as dt
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import config
import build_index
import voters
import http_get
import sources
import store
import update


class Response:
    def __init__(self, text='', content=None, url='https://www.kansascityfed.org/documents/speech.pdf', status=200, headers=None):
        self.text = text
        self.content = content if content is not None else text.encode()
        self.url = url
        self.status_code = status
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise http_get.requests.exceptions.HTTPError(f'HTTP {self.status_code}', response=self)


class CollectorTests(unittest.TestCase):
    def test_metadata_beyond_old_limits_without_body_mentions(self):
        text = ('Heading\n' * 110 + '\nRemarks by\nEsther L. George\nPresident and Chief Executive Officer\n'
                + 'Venue\n' * 90 + '\nOctober 15, 2019\nThe views expressed are her own.\n'
                + 'In January 1, 2000, John Smith was President.')
        date, speaker, _ = sources._generic_meta('', text)
        self.assertEqual((date, speaker), ('2019-10-15', 'Esther L. George'))
        self.assertIsNone(sources._generic_meta('', 'I appreciate the invitation.\nJanuary 1, 2000\nJohn Smith President')[0])

    def test_structured_metadata_and_invalid_date(self):
        page = "<meta content='2020-05-04' itemprop='date'><meta content='Jane Doe' name='citation_author'>"
        self.assertEqual(sources._generic_meta(page, '')[:2], ('2020-05-04', 'Jane Doe'))
        self.assertIsNone(sources.parse_date('2020-99-99'))
        page = '<script type="application/ld+json">'+json.dumps({'@type':'Article','datePublished':'2021-05-24','author':{'@type':'Person','name':'Esther L. George'}})+'</script>'
        self.assertEqual(sources._generic_meta(page, '')[:2], ('2021-05-24', 'Esther L. George'))

    def test_pdf_redirect_and_direct_pdf_recover_metadata(self):
        text = 'A Monetary Policy Outlook\nRemarks by\nEsther L. George\nPresident\nMay 24, 2021\nThe views expressed are her own.\n' + 'Speech text. ' * 120
        pdf = type('PDF', (), {'pages': [type('Page', (), {'extract_text': lambda self: text})()]})()
        for url in ['https://www.kansascityfed.org/speeches/outlook/', 'https://www.kansascityfed.org/documents/outlook.pdf']:
            with patch.object(http_get, 'get', return_value=Response(content=b'%PDF-1.5', headers={'content-type':'application/pdf'})), patch.object(sources, 'PdfReader', return_value=pdf):
                record, reason = sources.fetch(sources.entry('kansascity', url))
            self.assertEqual(reason, '')
            self.assertEqual(record['date'], '2021-05-24')
            self.assertEqual(record['speaker'], 'Esther L. George')
            self.assertEqual(record['title'], 'A Monetary Policy Outlook')

    def test_relative_links(self):
        self.assertEqual(sources.absolute('https://example.org/path/page', '../speech.pdf'), 'https://example.org/speech.pdf')
        rss = '<item><link>/publications/research/econ_focus/2021/q1/presidents_message</link></item>'
        with patch.object(http_get, 'get', return_value=Response(text=rss)):
            self.assertTrue(next(sources.list_richmond(True))['url'].startswith('https://www.richmondfed.org/'))

    def test_http_404_no_retry_and_timeout_retry(self):
        with patch.object(http_get._session, 'get', return_value=Response(status=404)) as get, patch.object(http_get.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(http_get.requests.exceptions.HTTPError):
                http_get.get('https://example.org/missing')
            self.assertEqual(get.call_count, 1)
        error = http_get.requests.exceptions.Timeout('timed out')
        with patch.object(http_get._session, 'get', side_effect=[error, Response()]) as get, patch.object(http_get.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()) as output:
            http_get.get('https://example.org/slow')
            self.assertEqual(get.call_count, 2)
            self.assertIn('retrying', output.getvalue())
        with patch.object(http_get._session, 'get') as get:
            with self.assertRaises(ValueError):
                http_get.get('/relative')
            get.assert_not_called()

    def test_failure_cooldown_and_version_recovery(self):
        now = dt.datetime.now(dt.timezone.utc)
        row = update._outcome('board', 'metadata_failure', 'missing date', None)
        self.assertFalse(update._eligible(row, now, False))
        self.assertTrue(update._eligible(row, now, True))
        row['collector_version'] = 'old'
        self.assertTrue(update._eligible(row, now, False))
        self.assertFalse(update._eligible({'status':'stored'}, now, True))

    def test_repeated_failures_back_off_up_to_30_days(self):
        def wait(row):
            return (dt.datetime.fromisoformat(row['retry_after']) - dt.datetime.fromisoformat(row['last_attempt'])).days
        row = None
        waits = []
        for _ in range(7):
            row = update._outcome('kansascity', 'metadata_failure', 'missing speaker', row)
            waits.append(wait(row))
        self.assertEqual(waits, [1, 2, 4, 8, 16, 30, 30])
        self.assertEqual(wait(update._outcome('atlanta', 'http_failure', '404', None)), 7)

    def test_only_current_voters_contribute_but_prior_speeches_remain_usable(self):
        roster = voters.Roster([
            {'date':'2020-01-01', 'names':['Jane Smith']},
            {'date':'2021-01-01', 'names':['John Doe']},
        ])
        speeches = [
            {'date':'2020-12-31','id':'doe','key':voters.name_key('John Doe'),'value':75.0},
            {'date':'2020-12-31','id':'smith','key':voters.name_key('Jane Smith'),'value':25.0},
        ]
        with patch.object(config, 'START_DATE', '2020-12-31'):
            result = build_index.build(speeches, roster, dt.date(2021,1,1))
        # Smith (25) is the only voter on the first day, Doe (75, spoken before his term) on the second.
        (d1, v1), (d2, v2) = result['fed_speech_index']
        self.assertEqual((d1, d2), ('2020-12-31', '2021-01-01'))
        self.assertLess(v1, 50)
        self.assertGreater(v2, 50)

    def test_richmond_magazine_is_excluded_without_request(self):
        e = sources.entry('richmond','/publications/research/econ_focus/2021/q1/presidents_message')
        with patch.object(http_get,'get') as get:
            record, reason = sources.fetch(e)
        self.assertIsNone(record)
        self.assertTrue(reason.startswith('not a speech'))
        get.assert_not_called()

    def test_failures_revisited_without_current_listing_and_keep_entry_metadata(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            root = Path(directory)
            for key, value in [('DATA',root),('SPEECH_DIR',root/'speeches'),('PROCESSING',root/'processing.json')]:
                stack.enter_context(patch.object(config,key,value))
            e = sources.entry('atlanta','https://www.atlantafed.org/speeches/old',date='2020-05-04',speaker='Jane Doe',title='Old speech')
            row = update._outcome('atlanta','metadata_failure','missing',None,entry=e)
            row['retry_after'] = '2000-01-01T00:00:00+00:00'
            store.save_processing({e['url']:row})
            role,_,metadata = sources.SOURCES['atlanta']
            stack.enter_context(patch.dict(sources.SOURCES,atlanta=(role,lambda full:iter([]),metadata)))
            with patch.object(sources,'fetch',return_value=(None,'no speech text (0 chars)')) as fetch, contextlib.redirect_stdout(io.StringIO()):
                update.pull(['atlanta'],False)
            fetch.assert_called_once_with(e)
            self.assertEqual(store.load_processing()[e['url']]['attempts'],2)

    def test_checkpoint_summary_and_interrupt(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            root = Path(directory)
            for key, value in [('DATA',root),('SPEECH_DIR',root/'speeches'),('PROCESSING',root/'processing.json')]:
                stack.enter_context(patch.object(config, key, value))
            urls = [f'https://www.kansascityfed.org/speeches/example-{i}/' for i in range(4)]
            entries = [sources.entry('kansascity',u,date='2021-05-24',speaker='Esther L. George',title=f'Example {i}') for i,u in enumerate(urls)]
            role, _, metadata = sources.SOURCES['kansascity']
            stack.enter_context(patch.dict(sources.SOURCES, kansascity=(role,lambda full: iter(entries),metadata)))
            stack.enter_context(patch.object(update,'SAVE_EVERY',2))
            def fetch(e):
                if e['url']==urls[2]:
                    return None,'metadata missing: date'
                return {**{key:e.get(key,'') or '' for key in store.FIELDS},'id':e['url'][-2:],'role':role,'text':'x'*1100},''
            with patch.object(sources,'fetch',side_effect=fetch), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(update.pull(['kansascity'],True),3)
            self.assertIn('stored=3',output.getvalue())
            self.assertEqual(len(store.load_speeches()),3)
            self.assertEqual(store.load_processing()[urls[2]]['status'],'metadata_failure')
            with patch.object(sources,'fetch') as mocked, contextlib.redirect_stdout(io.StringIO()):
                update.pull(['kansascity'],True)
                mocked.assert_not_called()
            # Interrupt after one buffered record: finally must save it and its outcome.
            store.save_processing({})
            for file in config.SPEECH_DIR.glob('*.csv'):
                file.unlink()
            with patch.object(sources,'fetch',side_effect=[fetch(entries[0]),KeyboardInterrupt]), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(KeyboardInterrupt):
                    update.pull(['kansascity'],True)
            self.assertEqual(len(store.load_speeches()),1)
            self.assertEqual(store.load_processing()[urls[0]]['status'],'stored')


class RosterTests(unittest.TestCase):
    def test_dissenter_after_a_reason_and_before_and_is_kept(self):
        # December 2025 minutes: Goolsbee was dropped with the reason that preceded him.
        line = ("Stephen I. Miran, who preferred to lower the target range for the federal funds rate by 1/2 percentage "
                "point at this meeting; and Austan D. Goolsbee and Jeffrey R. Schmid, who preferred no change to the "
                "target range for the federal funds rate at this meeting")
        self.assertEqual(voters.parse_vote_names(line), ['Stephen I. Miran', 'Austan D. Goolsbee', 'Jeffrey R. Schmid'])

    def test_minutes_listed_under_another_name_are_found(self):
        # June 2008 minutes are linked as /monetarypolicy/fomc20080625.htm; a minutes link wins where both exist.
        page = ('<a href="/monetarypolicy/fomc20080625.htm">Minutes</a>'
                '<a href="/monetarypolicy/fomcminutes20080430.htm">Minutes</a>'
                '<a href="/monetarypolicy/fomc20080430.htm">Statement</a>')
        with patch.object(voters.http_get, 'get', return_value=type('R', (), {'text': page})()):
            urls = dict(voters.minutes_urls())
        self.assertEqual(urls['2008-06-25'], voters.FED + '/monetarypolicy/fomc20080625.htm')
        self.assertEqual(urls['2008-04-30'], voters.FED + '/monetarypolicy/fomcminutes20080430.htm')


if __name__ == '__main__':
    unittest.main()
