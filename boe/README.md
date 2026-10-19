# BOE speech hawk/dove index

Daily hawkish/dovish index of the Bank of England Monetary Policy Committee's members, built from
their speeches. Each speech is scored by Gemini on the same absolute 7-level easy↔tight scale as the
Fed index (see `rubric.py`).

The scale and prompt core in `rubric.py` are shared word for word with the Fed and BOJ projects, so
the three indexes are comparable. `prompt.py` fills in the Bank of England's names and adds a short
appendix of facts: who votes, how the Bank's speeches are written and its policy instruments. The
appendix never says which level to give.

## How the index is built
- **Voters on each date** come from `data/voters.csv`: one row per membership term, each with its
  official source. The ledger is checked against every vote in the official
  [MPC voting record](https://www.bankofengland.co.uk/-/media/boe/files/monetary-policy-summary-and-minutes/mpcvoting.xlsx)
  (`voters.py --audit-history`), and the current roster is compared with the
  [MPC page](https://www.bankofengland.co.uk/about/people/monetary-policy-committee) every day. A
  difference writes `data/pending_roster.json`: check the effective dates, add the term to
  `data/voters.csv` and rerun the audit.
- **Each voter's stance** is the score of their most recent policy-relevant speech available on or
  before that date (several on the same day are averaged). Every voter counts equally. Voters with no
  scored speech yet are left out and listed in `output/boe_coverage.csv`. A speech published late
  cannot replace a newer stance, and a new term does not inherit a previous term's stance.
- **What counts as text**: the member's own prepared words, speeches and lectures. Interviews, Q&A
  transcripts, slides, chart packs and supporting papers are skipped. Monetary Policy Summaries and
  minutes are not used as texts. The scorer marks texts that say nothing about monetary policy as not
  relevant.
- **Policy setting** given to the scorer: Bank Rate on the speech date, from the official
  [change history](https://www.bankofengland.co.uk/boeapps/database/Bank-Rate.asp).

## Where speeches come from
The Bank's [speech sitemap](https://www.bankofengland.co.uk/sitemap/speeches), HTML pages and direct
PDFs, from the first complete committee in `data/voters.csv` (2017-09-04) on. A landing page and its
PDF are one speech; where the page is only the website's summary, the full-text PDF is used. One
speech with a broken download (Ramsden, 22 February 2022) is recovered from the BIS copy by OCR; see
`data/source_overrides.json`.

## Outputs (`output/`)
| File | Meaning |
|---|---|
| `boe_speech_index.csv` | Mean of the voters' latest scores (`date`, `index`). 0 = emergency easing, 100 = aggressive tightening |
| `boe_speech_disagreement.csv` | Standard deviation of those scores |
| `boe_member_scores.csv` | Each voter's score on each day, with the speech date and age |
| `boe_coverage.csv` | Voters scored and missing on each day |
| `status.json` | Warnings: roster or collection checks not passed today, scoring backlog, missing voters |

## Setup (local)
Uses [uv](https://docs.astral.sh/uv/). Put your Gemini key in `.env` (git-ignored):
```
GEMINI_API_KEY=your-key-here
```
```bash
uv sync
uv run backfill.py              # voters check, Bank Rate, all speeches since 2017-09-04
uv run score.py --limit 5       # check a few results in data/scores.jsonl first
uv run score.py                 # rerun daily until nothing is left to score
uv run build_index.py           # -> output/*.csv
uv run chart.py                 # -> charts/chart.png, charts/chart.html
```
Checks: `uv run coverage.py` (speeches per voter per year), `uv run audit.py` (store integrity),
`uv run score.py --preview SPEECH_ID` (exact prompt, no API call).

## Automatic updates (GitHub Action)
The repository's `.github/workflows/daily.yml` (at the root, shared by all three banks) runs the tests and `pipeline.py` daily (a full sitemap recheck on
Sundays), then commits `data/` and `output/`. Add `GEMINI_API_KEY` as a repository secret.

## Files
- `config.py`: model, paths, collection settings
- `rubric.py`: level definitions, prompt core and response schema, shared with the Fed and BOJ projects
- `prompt.py`: the Bank of England's names and appendix for the shared rubric
- `voters.py`, `history.py`: membership ledger, daily roster check, voting-record audit
- `sources.py`, `documents.py`: sitemap, page and PDF reading, Bank Rate history
- `policy.py`: the policy setting on a date
- `update.py`: collection; `score.py`: scoring; `build_index.py`: the index
- `data/speeches/{year}.csv`: speech texts; `data/scores.jsonl`: one score per speech, with quotes
- `data/policy_rates.csv`: Bank Rate history
- `data/processing.json`: the outcome of every URL checked

Changing `MODEL` in `config.py` or `VERSION` in `rubric.py` (which must then change in all three
projects) starts a new score vintage. A revised speech text is re-scored automatically. Changing
`COLLECTOR_VERSION` rechecks every page.

Tests:
```bash
uv run python -m unittest discover -s tests -v
```
