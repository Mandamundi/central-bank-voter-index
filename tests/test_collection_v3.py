import contextlib
import datetime as dt
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import config
import http_get
import runlog
import sources
import speech_metadata as metadata
import store
import update
from test_collection import Response

BODY = 'Thank you for the invitation. Monetary policy responds to the economic outlook. ' * 35


class V3Tests(unittest.TestCase):
    def setUp(self):
        http_get.begin_source(None)

    def tearDown(self):
        http_get.begin_source(None)

    def test_person_names_cannot_be_role_words(self):
        for name in ['Assistant Vice','Senior Vice','Federal Reserve','President','Chief Executive Officer']:
            self.assertIsNone(metadata.normalize_speaker(name))
        self.assertEqual(metadata.byline('Nathan Kauffman\nAssistant Vice President'), 'Nathan Kauffman')
        self.assertEqual(metadata.byline('Nathan Kauffman, Assistant Vice President'), 'Nathan Kauffman')
        self.assertEqual(metadata.normalize_speaker('Barkin, Tom'), 'Tom Barkin')
        self.assertEqual(metadata.normalize_speaker('Logan'), 'Logan')
        self.assertEqual(metadata.normalize_speaker('and Chair of the Financial Stability Board Randal K. Quarles'), 'Randal K. Quarles')
        self.assertEqual(metadata.normalize_speaker('Brian F. Madigan, Director, Division of Monetary Affairs'), 'Brian F. Madigan')

    def test_dallas_publisher_header_overrides_misfiled_directory_and_generic_title(self):
        page = '<div class="dal-main-content"><div class="dal-crouton">Speech by Richard W. Fisher, President and CEO (2005–2015)</div><h1 class="dal-headline">Economic Outlook</h1><span class="report-date">June 9, 2004</span><p>' + BODY + '</p></div><meta property="og:title" content="Federal Reserve Bank of Dallas">'
        url = 'https://www.dallasfed.org/news/speeches/fisher/2005/fs040609.aspx'
        with patch.object(http_get, 'get', return_value=Response(text=page, url=url)):
            record, reason = sources.fetch(sources.entry('dallas', url, speaker='Fisher', date='2005-06-09'))
        self.assertEqual(reason, '')
        self.assertEqual((record['date'], record['speaker'], record['title']), ('2004-06-09', 'Richard W. Fisher', 'Economic Outlook'))

    def test_dallas_url_dates_accept_aspx_and_translation_suffixes(self):
        rss = '<link>https://www.dallasfed.org/news/speeches/fisher/2009/fs091016.aspx</link><link>https://www.dallasfed.org/news/speeches/kaplan/2017/rsk170713sp</link><link>https://www.dallasfed.org/news/speeches/fisher/2005/fs040609.aspx</link>'
        with patch.object(http_get, 'get', return_value=Response(text=rss)):
            entries = list(sources.list_dallas(False))
        self.assertEqual([e['date'] for e in entries], ['2009-10-16', '2017-07-13', None])

    def test_chicago_schedule_description_lowercase_role_and_single_digit_day(self):
        page = '<div class="cfedSidebar"><a href="/publications/speeches/index">Charles Evans Speech Schedule</a></div>'
        url = 'https://www.chicagofed.org/publications/speeches/2004/11-5-cdfi'
        self.assertEqual(metadata.bank_metadata('chicago', page, '', url, sources.parse_date, sources.meta)[:2], ('2004-11-05', 'Charles Evans'))
        page = '<meta name="description" content="Austan Goolsbee, president and CEO of the Chicago Fed, delivered prepared remarks.">'
        self.assertEqual(metadata.bank_metadata('chicago', page, '', url, sources.parse_date, sources.meta)[1], 'Austan Goolsbee')

    def test_generic_pdf_download_must_be_in_speech_article_and_not_research(self):
        page = '<footer><a href="/other.pdf">Download PDF</a></footer><main><a href="/uploads/paper.pdf">Download PDF</a><a href="/uploads/000614.pdf">Download PDF (42 KB)</a></main>'
        self.assertEqual(sources._speech_pdf(page, 'https://www.frbsf.org/news-and-media/speeches/parry-speeches/2000/06/event/'), 'https://www.frbsf.org/uploads/000614.pdf')
        self.assertIsNone(sources._speech_pdf(page, 'https://www.frbsf.org/research/'))

    def test_chicago_disclaimer_and_explicit_delivery_override_filename(self):
        url = 'https://www.chicagofed.org/publications/speeches/2010/03-04-cfa-speech'
        page = '<div class="cfedDetail__introduction">A speech delivered on March 3, 2010, in Chicago.</div><strong>Note: Opinions expressed in this article are those of Charles L. Evans and do not necessarily reflect the views of the Federal Reserve.</strong><p>' + BODY + '</p>'
        with patch.object(http_get, 'get', return_value=Response(text=page, url=url)):
            record, reason = sources.fetch(sources.entry('chicago', url, date='2010-03-04'))
        self.assertEqual(reason, '')
        self.assertEqual((record['date'], record['speaker']), ('2010-03-03', 'Charles L. Evans'))
        self.assertIsNone(metadata.bank_metadata('chicago', '<p>See Charles L. Evans, 2010, Policy Risks.</p>', BODY, url, sources.parse_date, sources.meta)[1])

    def test_chicago_article_byline_outside_extracted_body(self):
        page = '<div class="cfedContent__text">Remarks presented virtually by Sam Schulhofer-Wohl, senior vice president and director of financial policy, on October 25, 2021.</div>'
        self.assertEqual(metadata.bank_metadata('chicago', page, BODY, 'https://www.chicagofed.org/publications/speeches/2021/october-25-cftc-gmac-meeting', sources.parse_date, sources.meta)[1], 'Sam Schulhofer-Wohl')

    def test_atlanta_html_teaser_is_summary_and_article_beats_cookie_banner(self):
        teaser = 'President Bostic visited Alabama. Look at photos from the visit <a href="/photos">here</a>.'
        page = '<p>We use cookies on our website.</p><meta name="afo_teaser" content="' + teaser.replace('"', '&quot;') + '"><article class="article-frame"><h1>Alabama Visit</h1><span>March 4, 2019</span>Speech<p>' + teaser + '</p></article>'
        text = sources.html_text(page)
        self.assertIn('President Bostic visited', text)
        self.assertEqual(metadata.non_speech_reason(page, text, 'Alabama Visit'), 'event summary without prepared text')
        self.assertIsNone(metadata.non_speech_reason(page, BODY, 'Opening remarks'))

    def test_dallas_truncated_year_rescued_only_by_consistent_filename(self):
        page = '<span class="report-date">October 6, 201</span>'
        url = 'https://www.dallasfed.org/news/speeches/fisher/2011/fs111006.aspx'
        self.assertEqual(metadata.bank_metadata('dallas', page, BODY, url, sources.parse_date, sources.meta)[0], '2011-10-06')
        self.assertIsNone(metadata.bank_metadata('dallas', page, BODY, url.replace('/2011/', '/2012/'), sources.parse_date, sources.meta)[0])

    def test_publisher_error_page_is_retryable_and_slides_are_excluded(self):
        url = 'https://www.kansascityfed.org/speeches/event/'
        with patch.object(http_get, 'get', return_value=Response(text='<p>An error occurred.</p><p>Unable to execute JavaScript.</p>', url=url)):
            with self.assertRaises(TimeoutError) as result:
                sources.fetch(sources.entry('kansascity', url))
        self.assertTrue(http_get.temporary_failure(result.exception))
        self.assertEqual(metadata.non_speech_reason('', 'Slides presented during a lecture.', 'Policy'), 'slides without prepared speech text')
        page = '<meta name="brightspot-dataLayer" content="{&quot;pageType&quot;:&quot;radio-episode&quot;}">'
        self.assertEqual(metadata.non_speech_reason(page, BODY, 'Economy'), 'radio interview rather than prepared remarks')

    def test_transcript_with_lowercase_name_particles_is_excluded(self):
        self.assertEqual(metadata.non_speech_reason('', 'Gloria Guzman: Welcome.\nKaren Leone de Nie: Thank you.\nGloria Guzman: A question.', 'Survey Results'), 'multi-speaker transcript')

    def test_pdf_title_starting_we_does_not_hide_cover_metadata(self):
        cover = 'Regulatory Reform and the Economy:\nWe Can Do Better\nThomas M. Hoenig\nPresident\nFederal Reserve Bank of Kansas City\nDenver, Colorado\nOctober 6, 2009\n'
        date, speaker, title = sources._generic_meta('', cover)
        self.assertEqual((date, speaker, title), ('2009-10-06', 'Thomas M. Hoenig', 'Regulatory Reform and the Economy: We Can Do Better'))
        self.assertEqual(metadata.frontmatter('Policy Outlook\nWe expect inflation to fall.\nOctober 6, 2009'), 'Policy Outlook')

    def test_publisher_essay_paper_cancellation_and_joint_authors_are_excluded(self):
        self.assertEqual(metadata.non_speech_reason('<div class="dal-crouton">Essay by President Robert S. Kaplan</div>', BODY, 'Policy'), 'essay rather than prepared remarks')
        self.assertEqual(metadata.non_speech_reason('', '| Paper by Governor Donald Kohn and Brian Sack |\n' + BODY, 'Policy'), 'research paper rather than prepared remarks')
        self.assertEqual(metadata.non_speech_reason('', 'Speech cancelled due to inclement weather.', 'Policy'), 'cancelled speech without prepared remarks')
        page = '<h4>by Ernest T. Patrikis, First Vice President</h4><h4>Joyce M. Hansen, Deputy General Counsel and Senior Vice President</h4>'
        self.assertEqual(metadata.non_speech_reason(page, BODY, 'Policy'), 'joint-author publication cannot be assigned to one speaker')

    def test_recovery_attempts_unseen_pages_before_repeated_network_failures(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            root = Path(directory)
            for key, value in [('DATA', root), ('SPEECH_DIR', root/'speeches'), ('PROCESSING', root/'processing.json'), ('SEEN_URLS', root/'seen.txt')]:
                stack.enter_context(patch.object(config, key, value))
            entries = [sources.entry('kansascity', f'https://www.kansascityfed.org/speeches/{slug}/') for slug in ['timeout-a', 'timeout-b', 'fresh']]
            store.save_processing({e['url']: update._outcome('kansascity', 'request_failure', 'timeout', None, entry=e) for e in entries[:2]})
            role, _, reader = sources.SOURCES['kansascity']
            with patch.dict(sources.SOURCES, kansascity=(role, lambda full: iter(entries), reader)), patch.object(sources, 'fetch', return_value=(None, 'not a speech (slide deck)')) as fetch, contextlib.redirect_stdout(io.StringIO()):
                update.pull(['kansascity'], False, recover=True, limit=1)
            fetch.assert_called_once_with(entries[2])

    def test_cleveland_empty_search_uses_sitemap_without_edit_dates(self):
        response = Response()
        response.json = lambda: {'isNullSearch': True, 'results': []}
        sitemap = Response(text='<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://www.clevelandfed.org/collections/speeches/sp-20260901-outlook</loc><lastmod>2026-10-01</lastmod></url><url><loc>https://www.clevelandfed.org/collections/speeches</loc></url><url><loc>https://other.org/collections/speeches/wrong</loc></url></urlset>')
        with patch.object(http_get, 'get', side_effect=[response, sitemap]):
            entries = list(sources.list_cleveland(True))
        self.assertEqual(len(entries), 1)
        self.assertIsNone(entries[0]['date'])
        page = '<meta name="releaseDate" content="2026-09-01T04:00:00Z"><meta name="citation_author" content="Mester, Loretta J.">'
        self.assertEqual(sources._generic_meta(page, BODY)[:2], ('2026-09-01', 'Loretta J. Mester'))

    def test_chicago_full_archive_uses_sitemap_instead_of_retired_indexes(self):
        page = '<urlset><url><loc>https://www.chicagofed.org/publications/speeches/2023/august-1-chicago-business-week</loc></url><url><loc>https://www.chicagofed.org/publications/speeches/2023/index</loc></url></urlset>'
        with patch.object(http_get, 'get', return_value=Response(text=page)) as get:
            entries = list(sources.list_chicago(True))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['date'], '2023-08-01')
        get.assert_called_once_with('https://www.chicagofed.org/sitemap.xml')

    def test_legacy_board_table_body_and_font_byline(self):
        page = '<html><head><title>FRB: Speech, Kelley -- Clearinghouses and risk management -- December 3, 1996</title></head><body><table><tr><td><b><font>Remarks by Governor Edward W. Kelley, Jr.</font><br>At a conference<br>December 3, 1996</b></td></tr><tr><td><p>' + BODY + '</p></td></tr></table></body></html>'
        url = 'https://www.federalreserve.gov/boarddocs/speeches/1996/19961203.htm'
        with patch.object(http_get, 'get', return_value=Response(text=page, url=url)):
            record, reason = sources.fetch(sources.entry('board', url))
        self.assertEqual(reason, '')
        self.assertEqual((record['date'], record['speaker']), ('1996-12-03', 'Edward W. Kelley, Jr.'))
        self.assertGreater(len(record['text']), config.MIN_TEXT_CHARS)

    def test_legacy_ny_url_date_and_short_prepared_remarks_with_video(self):
        self.assertEqual(metadata.bank_metadata('ny', '', '', 'https://www.newyorkfed.org/newsevents/speeches/1996/ep960125', sources.parse_date, sources.meta)[0], '1996-01-25')
        self.assertIsNone(metadata.bank_metadata('ny', '', '', 'https://www.newyorkfed.org/newsevents/speeches/1996/ep970125', sources.parse_date, sources.meta)[0])
        text = 'Thank you for inviting me. ' + 'I appreciate this opportunity to discuss the economic outlook. ' * 6
        page = '<article><iframe src="https://www.youtube.com/embed/example"></iframe></article>'
        self.assertTrue(metadata.short_prepared_text(text))
        self.assertIsNone(metadata.non_speech_reason(page, text, 'Opening remarks'))
        with patch.object(http_get, 'get', return_value=Response(text=page, url='https://www.newyorkfed.org/newsevents/speeches/1996/ep960125')), patch.object(sources, 'html_text', return_value=text):
            record, reason = sources.fetch(sources.entry('ny', 'https://www.newyorkfed.org/newsevents/speeches/1996/ep960125', speaker='Ernest T. Patrikis', title='Opening remarks'))
        self.assertEqual(reason, '')
        self.assertIsNotNone(record)

    def test_interrupt_during_listing_reports_stage_and_preserves_store(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            root = Path(directory)
            for key, value in [('DATA', root), ('SPEECH_DIR', root/'speeches'), ('PROCESSING', root/'processing.json'), ('SEEN_URLS', root/'seen.txt')]:
                stack.enter_context(patch.object(config, key, value))
            role, _, reader = sources.SOURCES['ny']
            with patch.dict(sources.SOURCES, ny=(role, lambda full: (_ for _ in ()).throw(KeyboardInterrupt()), reader)), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(KeyboardInterrupt):
                    update.pull(['ny'], True)
            report = json.loads((root/'last_run.json').read_text())
            self.assertEqual(report['status'], 'interrupted')
            self.assertEqual(report['sources']['ny']['status'], 'interrupted')
            self.assertEqual(report['added'], 0)

    def test_minneapolis_sitecore_headers_even_when_extractor_omits_them(self):
        route={'placeholders':{'author':[{'fields':{'authors':[{'name':'Narayana Kocherlakota'}]}}]}}
        data={'props':{'pageProps':{'layoutData':{'sitecore':{'route':route}}}}}
        page='<meta name="ess:publishdatetime" content="2011-10-13T18:30:00Z"><script id="__NEXT_DATA__" type="application/json">'+json.dumps(data)+'</script>'
        with patch.object(http_get,'get',return_value=Response(text=page,url='https://www.minneapolisfed.org/speeches/2011/making-monetary-policy')), patch.object(sources,'html_text',return_value=BODY):
            record, reason=sources.fetch(sources.entry('minneapolis','https://www.minneapolisfed.org/speeches/2011/making-monetary-policy'))
        self.assertEqual(reason,'')
        self.assertEqual((record['date'],record['speaker']),('2011-10-13','Narayana Kocherlakota'))

    def test_chicago_delivery_url_not_last_updated(self):
        page='<div class="cfedDetail__lastUpdated">Last Updated: 12/17/09</div>'
        text='Regional Conditions\nRemarks by Michael H. Moskow\nPresident and CEO\n'+BODY
        with patch.object(http_get,'get',return_value=Response(text=page,url='https://www.chicagofed.org/publications/speeches/2004/02-19-regional-conditions')), patch.object(sources,'html_text',return_value=text):
            record, _=sources.fetch(sources.entry('chicago','https://www.chicagofed.org/publications/speeches/2004/02-19-regional-conditions'))
        self.assertEqual(record['date'],'2004-02-19')
        self.assertEqual(record['speaker'],'Michael H. Moskow')

    def test_chicago_article_sidebar_and_self_introduction_identify_speaker(self):
        page = '<nav><a href="/people/e/wrong-name">Wrong Name\'s Biography</a></nav><div class="cfedSidebar--noBigHeader"><a href="/people/e/evans-charles">Charles Evans\' Biography</a></div>'
        url = 'https://www.chicagofed.org/publications/speeches/2011/09-07-dual-mandate'
        self.assertEqual(metadata.bank_metadata('chicago', page, BODY, url, sources.parse_date, sources.meta)[:2], ('2011-09-07', 'Charles Evans'))
        text = "Good morning. I'm Charlie Evans, president and CEO of the Federal Reserve Bank of Chicago. " + BODY
        self.assertEqual(metadata.bank_metadata('chicago', '', text, url, sources.parse_date, sources.meta)[1], 'Charlie Evans')
        self.assertIsNone(metadata.bank_metadata('chicago', '', 'Charles Evans said that policy would change.', url, sources.parse_date, sources.meta)[1])
        url = 'https://www.chicagofed.org/publications/speeches/2023/august-1-chicago-business-week'
        self.assertEqual(metadata.bank_metadata('chicago', '', text, url, sources.parse_date, sources.meta)[0], '2023-08-01')

    def test_disclaimer_above_pdf_title_does_not_hide_byline(self):
        text='The views expressed are the author\'s own.\nits governors, officers or representatives.\nThe U.S. Outlook\nEsther L. George\nPresident and Chief Executive Officer\nFebruary 2, 2016\n'+BODY
        self.assertEqual(sources._generic_meta('',text)[:2],('2016-02-02','Esther L. George'))
        self.assertEqual(metadata.byline('Opening Remarks by Esther L. George\nAugust 22, 2019\nI am pleased to welcome you.'), 'Esther L. George')
        self.assertEqual(sources.parse_date('2013/05/15'), '2013-05-15')
        self.assertIsNone(sources.parse_date('July 2016'))

    def test_slides_rejected_but_wrapped_speech_is_not(self):
        cover='Economic Outlook\nNathan Kauffman\nAssistant Vice President\nJanuary 13, 2016'
        slides=['FEDERAL RESERVE BANK\nGDP Growth\n0\n1\n2015\n2016\nSource: BEA']*5
        self.assertTrue(metadata.looks_like_slides([cover]+slides))
        prose=('Monetary policy should respond to economic conditions and the outlook for inflation.\n'*25)
        self.assertFalse(metadata.looks_like_slides([cover,prose,prose]))
        chart = 'GDP growth\n' + 'Sep. 2009\nOct. 2009\n0%\n1%\n' * 8 + 'Source: BEA'
        self.assertTrue(metadata.looks_like_slides([cover] + [chart]*5))

    def test_press_release_multispeaker_and_video_are_exclusions(self):
        self.assertEqual(metadata.non_speech_reason('', 'For immediate release\n'+BODY, 'Rates'), 'press release')
        self.assertEqual(metadata.non_speech_reason('', 'Jane Smith: Hello.\nJohn Doe: Hello.', 'Outlook'), 'multi-speaker transcript')
        self.assertEqual(metadata.non_speech_reason('<div class="cfedContent__text"><iframe src="https://www.youtube.com/embed/x"></iframe></div>','Brief summary','Perspectives'), 'video event without prepared text')
        for summary in ['Presentation, with underlying data, delivered by Joshua Frost.', 'Presentation on the economic overview delivered by Andrew Haughwout.', 'Recording\nPanel with Nathaniel Wuerffel and other individuals.']:
            self.assertEqual(metadata.non_speech_reason('', summary, 'Outlook'), 'presentation without prepared speech text')
        self.assertEqual(metadata.non_speech_reason('', 'June 10, 2010\nDear Senator Lincoln:\n' + BODY + '\nSincerely,\nThomas M. Hoenig\nPresident', 'Policy'), 'letter or memorandum')

    def test_speech_pdf_does_not_follow_cited_research_or_slides(self):
        page='<a href="/paper.pdf">Research paper</a><a href="/slides.pdf">Slides</a><a href="./remarks.pdf?download=1">Prepared remarks</a>'
        self.assertEqual(sources._speech_pdf(page,'https://example.org/speeches/event/'),'https://example.org/speeches/event/remarks.pdf?download=1')

    def test_sanfrancisco_listing_accepts_whitespace_and_link_attributes(self):
        page='<li><h2 class="wp-block-post-title has-medium-font-size"><a href="https://www.frbsf.org/news-and-media/speeches/mary-c-daly/2026/04/event/" target="_self" >Prepared speech</a></h2><div class="wp-block-post-date"><time datetime="2026-04-08T00:00:00Z">April 8</time></div></li>'
        with patch.object(http_get,'get',return_value=Response(text=page,url='https://www.frbsf.org/news-and-media/speeches/mary-c-daly/')):
            entries=list(sources.list_sanfrancisco(False))
        self.assertEqual(len(entries),1)
        self.assertEqual(entries[0]['date'],'2026-04-08')

    def test_status_zero_is_network_failure_not_http_error(self):
        error=http_get.requests.exceptions.Timeout('timeout',response=Response(status=0))
        self.assertIsNone(http_get.status_code(error))
        self.assertTrue(http_get.temporary_failure(error))
        error=http_get.requests.exceptions.HTTPError('404',response=Response(status=404))
        self.assertEqual(http_get.status_code(error),404)
        self.assertFalse(http_get.temporary_failure(error))

    def test_source_guard_stops_requests_and_resets_for_next_bank(self):
        http_get.begin_source('kansascity')
        error=http_get.requests.exceptions.Timeout('timeout',response=Response(status=0))
        with patch.object(http_get._session,'get',side_effect=error) as get, patch.object(http_get.time,'sleep'),contextlib.redirect_stdout(io.StringIO()):
            for _ in range(config.SOURCE_FAILURE_LIMIT):
                with self.assertRaises(http_get.requests.exceptions.Timeout):
                    http_get.get('https://example.org/page',attempts=1)
            with self.assertRaises(http_get.SourcePaused):
                http_get.get('https://example.org/next')
            self.assertEqual(get.call_count,config.SOURCE_FAILURE_LIMIT)
        http_get.begin_source('dallas')
        self.assertIsNone(http_get.paused_reason())

    def test_pause_moves_to_next_bank_and_report_records_remaining(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            root=Path(directory)
            for key,value in [('DATA',root),('SPEECH_DIR',root/'speeches'),('PROCESSING',root/'processing.json'),('SEEN_URLS',root/'seen.txt'),('SCORES',root/'scores.jsonl')]:
                stack.enter_context(patch.object(config,key,value))
            for name in ['kansascity','dallas']:
                role,_,reader=sources.SOURCES[name]
                entries=[sources.entry(name,sources.SOURCE_ORIGINS[name]+f'/test-{i}') for i in range(5)]
                stack.enter_context(patch.dict(sources.SOURCES,{name:(role,lambda full,entries=entries:iter(entries),reader)}))
            def fetch(e):
                if e['source']=='kansascity':
                    error=http_get.requests.exceptions.Timeout('timed out',response=Response(status=0))
                    http_get._failed(error)
                    raise error
                return None,'not a speech (video)'
            with patch.object(sources,'fetch',side_effect=fetch) as fetch, contextlib.redirect_stdout(io.StringIO()):
                update.pull(['kansascity','dallas'],False)
            self.assertEqual(fetch.call_count,config.SOURCE_FAILURE_LIMIT+5)
            report=json.loads((root/'last_run.json').read_text())
            self.assertEqual(report['status'],'partial')
            self.assertEqual(report['sources']['kansascity']['status'],'paused')
            self.assertEqual(report['sources']['kansascity']['remaining'],5-config.SOURCE_FAILURE_LIMIT)
            self.assertEqual(report['sources']['dallas']['status'],'completed')
            self.assertTrue(Path(report['log']).exists())
            self.assertTrue(all(r['status']=='request_failure' for r in store.load_processing().values() if r['source']=='kansascity'))

    def test_quarantine_preserves_original_and_reopens_timeout(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            root=Path(directory)
            for key,value in [('DATA',root),('SPEECH_DIR',root/'speeches'),('PROCESSING',root/'processing.json'),('SEEN_URLS',root/'seen.txt'),('SCORES',root/'scores.jsonl')]:
                stack.enter_context(patch.object(config,key,value))
            e=sources.entry('kansascity','https://www.kansascityfed.org/speeches/bad/',date='2016-01-13',speaker='Assistant Vice',title='Outlook')
            speech={key:e.get(key,'') or '' for key in store.FIELDS};speech.update(id='bad',text=BODY,role='Kansas City')
            store.add_speeches([speech])
            outcomes={e['url']:{'source':'kansascity','status':'stored'},'https://www.kansascityfed.org/timeout':{'source':'kansascity','status':'http_failure','http_status':0,'retry_after':'2099-01-01T00:00:00+00:00'}}
            with contextlib.redirect_stdout(io.StringIO()):
                summary=update.repair_collection(outcomes)
            self.assertEqual(summary,{'quarantined':1,'restored':0,'joint_author_exclusions':0,'reclassified_timeouts':1})
            self.assertFalse(store.load_speeches())
            original=json.loads((root/'quarantine/collector-repair.jsonl').read_text())['speech']
            self.assertEqual(original,speech)
            self.assertEqual(outcomes[e['url']]['status'],'metadata_failure')
            self.assertEqual(outcomes['https://www.kansascityfed.org/timeout']['status'],'request_failure')

    def test_confirmed_non_speech_quarantine_is_not_automatically_restored(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            root = Path(directory)
            for key, value in [('DATA',root),('SPEECH_DIR',root/'speeches'),('PROCESSING',root/'processing.json'),('SEEN_URLS',root/'seen.txt'),('SCORES',root/'scores.jsonl')]:
                stack.enter_context(patch.object(config,key,value))
            e = sources.entry('dallas', 'https://www.dallasfed.org/news/speeches/kaplan/2017/rsk170713', date='2017-07-13', speaker='Robert S. Kaplan', title='Essay')
            speech = {key:e.get(key,'') or '' for key in store.FIELDS}
            speech.update(id='essay', text=BODY, role='Dallas')
            store.add_speeches([speech])
            store.quarantine_speeches([speech], 'not a speech (publisher labels this as an essay)')
            outcomes = {e['url']:update._outcome('dallas', 'excluded', 'not a speech (essay)', None, entry=e)}
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(update.repair_collection(outcomes)['restored'], 0)
            self.assertFalse(store.load_speeches())
            self.assertEqual(outcomes[e['url']]['status'], 'excluded')


if __name__=='__main__':
    unittest.main()
