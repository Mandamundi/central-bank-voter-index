# Fed speech hawk/dove index

Daily hawkish/dovish index of the FOMC's voting members, built from their speeches. Texts come
from the Board of Governors and the twelve Reserve Banks' own websites, and each speech is scored
by Gemini on an absolute 7-level easy↔tight scale (see `prompt.py`).

## How the index is built
- **Voters on each date** come from the Fed's own pages: the vote lines in each FOMC meeting's
  minutes (1996 on), and the "Committee Members" list on
  [fomc.htm](https://www.federalreserve.gov/monetarypolicy/fomc.htm), checked daily. No member list is
  kept by hand. See `voters.py`.
- **Each voter's stance** is the score of their most recent policy-relevant speech on or before that
  date. Every voter counts equally, however often they speak. Voters with no scored speech yet are left out.
- **Only prepared speech text** is used. Interviews, Q&As, podcasts, conversations, statements and
  video-only pages are skipped. Some presidents rarely publish speech text (in 2026, Minneapolis
  posted only Q&As and panels), so their seat can be empty or rest on an older speech.
  `python coverage.py` shows this by voter and year.

## Outputs (`output/`)
Each file has two columns, `date` and `index`, with one row per calendar day.

| File | Meaning |
|---|---|
| `fed_speech_index.csv` | Mean of the voters' latest speech scores. 0 = emergency easing, 100 = aggressive tightening |
| `fed_speech_disagreement.csv` | Standard deviation of those scores |

## Sources (`sources.py`)
| Source | New speeches from | Archive from |
|---|---|---|
| Board of Governors | RSS `federalreserve.gov/feeds/speeches.xml` | yearly speech pages |
| Richmond | RSS (holds the full archive) | same |
| Dallas | RSS `dallasfed.org/rss/speeches.xml` | one page per president |
| New York, Boston | listing page (full archive on one page) | same |
| Cleveland | site search JSON | same |
| Chicago | Office of the President page | yearly index pages + speech archive |
| St. Louis | site search JSON (president's remarks) | same |
| San Francisco | president's speech page | one paged list per president |
| Philadelphia, Atlanta, Minneapolis, Kansas City | listing page / sitemap | sitemap |

Boston's and Chicago's RSS feeds are stale or wrong and San Francisco's is empty, so their pages are read
instead. Requests use `curl_cffi` with a browser fingerprint because some Reserve Bank sites refuse
plain scripted requests.

## One-time setup (local)
Uses [uv](https://docs.astral.sh/uv/). `uv sync` installs Python 3.12 and the locked dependencies into `.venv`.
Put your key in `.env`, which is git-ignored:
```
GEMINI_API_KEY=your-key-here
```
```bash
uv sync
uv run backfill.py                      # voter history + every source's archive -> data/ (resumable)
uv run score.py --limit 5               # check a few results in data/scores.jsonl first
uv run score.py                         # rerun daily until "0 speeches to score"
uv run coverage.py                      # speeches per voter per year; pick config.START_DATE
uv run build_index.py                   # -> output/*.csv
uv run chart.py                         # -> charts/chart.png, charts/chart.html (progress view)
```
Commit `data/` and `output/` when you're done.

## Daily (GitHub Action)
`.github/workflows/daily.yml` runs `update.py` → `score.py` → `build_index.py` and commits the results. Add `GEMINI_API_KEY` as a repository secret.

## Files
- `config.py`: model, prompt version, start date, paths
- `prompt.py`: level definitions and the prompt
- `sources.py`: the 13 speech sources, page reading, and Fed target-rate history
- `voters.py`: FOMC voters by date
- `store.py`: speech store and score file I/O
- `data/speeches/{year}.csv`: speech texts
- `data/scores.jsonl`: one score per speech, with verbatim evidence quotes
- `data/voters.csv`: voter roster changes (date, source, names)
- `data/fed_funds.csv`: target-rate history, used as prompt context
- `data/processing.json`: URL outcomes, reasons, attempt counts, retry dates, and collector version
- `data/seen_urls.txt`: legacy checked URLs, retained as recovery candidates

Changing `MODEL` or `PROMPT_VERSION` in `config.py` starts a new score vintage. `score.py` will then re-score every speech.
`legacy_bis/` holds the old BIS-based data and can be deleted.

## Collection recovery and diagnostics

Both the speech date and speaker are required. The collector checks listing and article metadata,
then the heading/byline block of HTML or PDF text. PDF responses are detected after redirects.
It does not use arbitrary dates or people mentioned in the speech body as metadata.

The speech store can include nonvoters. Before AI scoring, speaker names are matched against
all recorded voter history; nonmatching speeches are reported and excluded. On each index date,
only that day's recorded voters contribute. A voter's latest speech can come from a nonvoting
period, so rotating into a voting seat does not erase their prior public stance.

The collector now records outcomes in `data/processing.json`. Successfully stored speeches and
confident title-based exclusions are skipped. Missing metadata, unreadable text, and transient
request failures are eligible again after one day; persistent HTTP failures such as 404 are
rechecked after seven days. Changing `config.COLLECTOR_VERSION` reopens unsuccessful/excluded
outcomes for the new parser. Stored speeches are preserved.

Legacy `seen_urls.txt` entries without a corresponding stored speech are automatically revisited
on full backfills, including URLs no longer in today's listing. Some previously excluded pages
will be inspected again because the legacy file did not record why they were skipped.

```bash
uv run backfill.py --skip-voters --skip-rates --sources kansascity --limit 25
uv run backfill.py --skip-voters --skip-rates --sources kansascity --recover
uv run backfill.py --skip-voters --skip-rates
```

`--limit` bounds attempted pages per source, but still gathers the archive listing.
`--recover` bypasses cooldowns for failures; it keeps stored speeches and current-version
confident exclusions completed. `--skip-rates` reuses existing rate history; ensure it exists
before scoring. Omit `--skip-voters` when voter history needs updating.

Collector v3.4 uses a 5-second connection timeout, a 15-second read allowance, at most two
attempts, and a 45-second overall retry budget. Only temporary HTTP/network failures receive
immediate retries. After two consecutive exhausted temporary failures, the bank is paused
for 60 minutes and collection proceeds to the next bank. Unattempted URLs remain recoverable;
`--recover` also bypasses the bank cooldown. An HTTP status of zero is a network failure.

Each run saves the terminal output and detailed request/outcome diagnostics under `data/runs/`.
The terminal shows progress and source totals; use `--verbose` to show every request too.
`data/last_run.json` points to the latest log and report and distinguishes completed, limited,
interrupted, paused, incomplete-listing, and needs-recovery sources. A partial run is not a
completed archive. Each source includes attempted, stored, excluded, failed, deferred, and
remaining page counts. Report timestamps are UTC.

HTML headers and publisher metadata supply missing delivery dates and author names, including
Minneapolis's Sitecore metadata and Chicago's dated URLs. PDFs use cover-page metadata and
are checked for slide decks. Short prepared opening remarks are accepted when speech cues are
present. Videos, event summaries, press releases, slides, and joint-author publications are
excluded. Cleveland falls back to its official speech sitemap when search returns nothing;
Chicago's full archive also uses its official sitemap, covering retired yearly indexes.
San Francisco's listing is parsed as HTML rather than depending on exact link attributes.
The PDF dependency includes AES decryption support for encrypted bank documents.

Dallas legacy `.aspx` URLs and translated filenames supply fallback dates; publisher headers
override directory dates. Chicago delivery statements override filename dates, and named
publisher disclaimers and article bylines identify authors. San Francisco's generic PDF
downloads are followed within speech articles. Publisher-labelled essays, cancelled speeches,
radio interviews, and pages containing only event teasers are excluded. Missing metadata
remains a recoverable failure when the publisher provides no reliable evidence.

Unseen pages are checked before repeated failures, preventing dead URLs from repeatedly pausing
a bank before its remaining archive is attempted. Previously excluded and failed outcomes reopen
when the collector version changes; successfully stored URLs are retained.

The repair pass preserves invalid stored records in `data/quarantine/collector-repair.jsonl`
before removing them from active CSVs. Valid bylines obscured by publisher role prefixes are
normalized and restored; role-only names such as "Assistant Vice" are reopened for extraction.
Original records remain in quarantine for review. Existing valid speeches are retained.

Speech CSVs and outcome files are replaced atomically. Checkpoints save speeches before marking
URLs completed, and a normal Ctrl-C saves the buffered completed work. Daily updates also revisit
recorded failures even when they no longer appear among the newest listing items.

Run the focused regression checks with:

```bash
uv run python -m unittest discover -s tests -v
```
