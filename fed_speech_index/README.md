# Fed speech hawk/dove index

Daily hawkish/dovish index of the FOMC's voting members, built from their speeches. Each speech
is scored by OpenAI's `gpt-6-luna` on an absolute 11-level easy↔tight scale (see `rubric.py` and the
repository README).

The scale and prompt core in `rubric.py` are shared word for word with the BOJ and BOE projects, so
the three indexes are scored with the same instrument; each bank's `prompt.py` only fills in its names
(and, for BOJ and BOE, adds a short appendix of undated facts about the bank). The current version is v5.
`tests/test_prompt.py` pins the rendered prompt.

## How the index is built
- **Voters on each date** come from the vote lines in each FOMC meeting's minutes (1996 on) and
  the "Committee Members" list on [fomc.htm](https://www.federalreserve.gov/monetarypolicy/fomc.htm),
  checked daily. Every name in a vote line, dissenters and alternates included, is a voter at
  that meeting. No member list is kept by hand. See `voters.py`.
- **Each voter's stance** is the score of their most recent policy-relevant text on or before that
  date (several on the same day are averaged). Every voter counts equally, however often they
  speak. The index starts on the first day every voter has a scored text; after that, voters with no
  scored text yet are left out.
- **What counts as text**: the member's own prepared words: speeches, signed essays, statements
  (including dissent statements) and testimony. Interviews, Q&As, conversations, podcasts and
  video-only pages are skipped. The scorer marks texts that say nothing about monetary policy as
  not relevant, and those are ignored.

## Where speeches come from
| Period | Source |
|---|---|
| Archive | [FRASER](https://fraser.stlouisfed.org), the St. Louis Fed's library: one collection per official, extended each January with the previous year. `fraser.py` |
| Since FRASER's last load, and people FRASER has no collection for | The Board's RSS feed and the twelve Reserve Banks' own pages. `sources.py`, `update.py` |

FRASER is read through its keyless public endpoints (OAI-PMH, item metadata, text files); no API
key is needed. When FRASER and a Reserve Bank site both have a speech (same person, same day), the
FRASER copy is kept.

FRASER has no collection for Neel Kashkari, Jeffrey Schmid, Lorie Logan as Dallas president or
Alberto Musalem as St. Louis president. Their texts come from their banks' sites. Kashkari's events
are mostly video Q&As; his written views are the signed essays on his Minneapolis Fed page, which
are collected.

## Outputs (`output/`)
Each file has two columns, `date` and `index`, with one row per calendar day.

| File | Meaning |
|---|---|
| `fed_speech_index.csv` | Mean of the voters' latest scores. 0 = emergency easing, 100 = aggressive tightening |
| `fed_speech_disagreement.csv` | Standard deviation of those scores |

## One-time setup (local)
Uses [uv](https://docs.astral.sh/uv/). `uv sync` installs Python 3.12 and the locked dependencies into `.venv`.
Put your OpenAI key in `.env`, which is git-ignored:
```
OPENAI_API_KEY=your-key-here
```
```bash
uv sync
uv run backfill.py                      # voters, FRASER archive, then bank sites (resumable)
uv run score.py --limit 5               # check a few results in data/scores.jsonl first
uv run score.py --batch                 # backfill via the Batch API; rerun until "Nothing left to score"
uv run coverage.py                      # speeches per voter per year; pick config.START_DATE
uv run build_index.py                   # -> output/*.csv
uv run chart.py                         # -> charts/chart.png, charts/chart.html (progress view)
```
Commit `data/` and `output/` when you're done.

## Automatic updates (GitHub Action)
The repository's `.github/workflows/daily.yml` (at the root, shared by all three banks) runs daily: `update.py` (new voters and speeches) → `score.py` →
`build_index.py`, then commits the results. On the 1st of each month it also runs `fraser.py`,
which picks up FRASER's yearly load. Add `OPENAI_API_KEY` as a repository secret.

## Files
- `config.py`: model, prompt version, start date, paths
- `rubric.py`: level definitions, prompt core and response schema, shared with the BOJ and BOE projects
- `prompt.py`: the Fed's names for the shared rubric
- `voters.py`: FOMC voters by date
- `fraser.py`: FRASER archive harvest
- `sources.py`: the Board and Reserve Bank sources, page reading, and Fed target-rate history
- `update.py`: daily pull from those sources
- `store.py`: speech store and score file I/O
- `data/speeches/{year}.csv`: speech texts (`source` is `fraser` or the bank)
- `data/scores.jsonl`: one score per speech, with verbatim evidence quotes
- `data/voters.csv`: voter roster changes (date, source, names)
- `data/fed_funds.csv`: target-rate history, used as prompt context
- `data/processing.json`: the outcome of every page checked, so it is not fetched twice

Changing `MODEL` in `config.py` or `VERSION` in `rubric.py` (which must then change in all three
projects) starts a new score vintage, and `score.py` then re-scores every speech. Changing `COLLECTOR_VERSION` rechecks pages that were excluded or failed.

## Bank-site collection details
Both a delivery date and a speaker are required; neither is guessed. The collector reads listing
and article metadata, then the heading/byline block of the HTML or PDF. Failed pages are retried
after a day (seven days for persistent HTTP errors such as 404); after two consecutive network
failures a bank is paused for an hour and collection moves to the next bank. Each run's log and
report are saved under `data/runs/` (not committed).

```bash
uv run backfill.py --skip-voters --skip-rates --skip-fraser --sources kansascity --limit 25
uv run backfill.py --skip-voters --skip-rates --skip-fraser --sources kansascity --recover
```

Tests:
```bash
uv run python -m unittest discover -s tests -v
```
