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
- `data/seen_urls.txt`: pages already checked

Changing `MODEL` or `PROMPT_VERSION` in `config.py` starts a new score vintage. `score.py` will then re-score every speech.
`legacy_bis/` holds the old BIS-based data and can be deleted.
