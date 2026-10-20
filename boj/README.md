# BOJ speech hawk/dove index

Daily hawkish/dovish index of the Bank of Japan Policy Board's voting members, built from their
speeches. Each speech is scored by OpenAI's `gpt-6-luna` on the same absolute 11-level easy↔tight scale as the Fed
index (see `rubric.py`).

The scale and prompt core in `rubric.py` are shared word for word with the Fed and BOE projects, so
the three indexes are comparable. `prompt.py` fills in the Bank of Japan's names and adds a short
appendix of facts: who votes, its policy instruments and the
original-language quoting rule. The appendix never says which level to give.

## How the index is built
- **Voters on each date** come from `data/voters.csv`: one row per membership term, each with its
  official appointment notice. The ledger is checked against the attendance lists in the official
  meeting minutes (`voters.py --audit-history`), and the current roster is compared with the
  [Policy Board page](https://www.boj.or.jp/en/about/organization/policyboard/index.htm) every day.
  A difference writes `data/pending_roster.json`: check the effective dates in the official notices,
  add the term to `data/voters.csv` and rerun the audit.
- **Each voter's stance** is the score of their most recent policy-relevant speech available on or
  before that date (several on the same day are averaged). Every voter counts equally. The index starts
  on the first day every voter has a scored speech; after that, voters with no scored speech yet are
  left out and listed in `output/boj_coverage.csv`. A speech published late
  cannot replace a newer stance, and a new term does not inherit a previous term's stance.
- **What counts as text**: the member's own prepared words, speeches and essays. Press conferences,
  interviews, Q&As, slides and appendices are skipped. Policy statements and minutes are not used as
  texts. The scorer marks texts that say nothing about monetary policy as not relevant.
- **Policy setting** given to the scorer: the guideline sentences of the latest policy statement on or
  before the speech (short-term rate, 10-year yield target, purchase amounts), with votes, dissents
  and the economic assessment removed (`policy.py`).

## Where speeches come from
The Bank's Japanese yearly speech catalogues
([koen_all](https://www.boj.or.jp/about/press/koen_all/index.htm)), from the first complete committee
in `data/voters.csv` (2017-07-24) on. Japanese originals are kept; an English original is kept where
the Japanese version is missing or abridged, and the two are never both counted. A speech read out by
a deputy is the author's. Evidence quotes are in the language of the text.

## Outputs (`output/`)
| File | Meaning |
|---|---|
| `boj_speech_index.csv` | Mean of the voters' latest scores (`date`, `index`). 0 = emergency easing, 100 = aggressive tightening |
| `boj_speech_disagreement.csv` | Standard deviation of those scores |
| `boj_member_scores.csv` | Each voter's score on each day, with the speech date and age |
| `boj_coverage.csv` | Voters scored and missing on each day |
| `status.json` | Warnings: roster or collection checks not passed today, scoring backlog, missing voters |

## Setup (local)
Uses [uv](https://docs.astral.sh/uv/). Put your OpenAI key in `.env` (git-ignored):
```
OPENAI_API_KEY=your-key-here
```
```bash
uv sync
uv run backfill.py              # voters check, policy statements, all speeches since 2017-07-24
uv run score.py --limit 5       # check a few results in data/scores.jsonl first
uv run score.py --batch         # backfill via the Batch API; rerun until "Nothing left to score"
uv run build_index.py           # -> output/*.csv
uv run chart.py                 # -> charts/chart.png, charts/chart.html
```
Checks: `uv run coverage.py` (speeches per voter per year), `uv run audit.py` (store integrity),
`uv run score.py --preview SPEECH_ID` (exact prompt, no API call), `uv run calibrate_language.py`
(Japanese vs. English scores of the same speeches).

## Automatic updates (GitHub Action)
The repository's `.github/workflows/daily.yml` (at the root, shared by all three banks) runs the tests and `pipeline.py` daily (a full catalogue recheck on
Sundays), then commits `data/` and `output/`. Add `OPENAI_API_KEY` as a repository secret.

## Files
- `config.py`: model, paths, collection settings
- `rubric.py`: level definitions, prompt core and response schema, shared with the Fed and BOE projects
- `prompt.py`: the Bank of Japan's names and appendix for the shared rubric
- `voters.py`, `history.py`: membership ledger, daily roster check, minutes audit
- `sources.py`, `documents.py`: catalogue, page and PDF reading, policy statements
- `policy.py`: the policy setting on a date
- `update.py`: collection; `score.py`: scoring; `build_index.py`: the index
- `data/speeches/{year}.csv`: speech texts; `data/scores.jsonl`: one score per speech, with quotes
- `data/policy_context.json`: policy statement text by decision date
- `data/processing.json`: the outcome of every URL checked

Changing `MODEL` in `config.py` or `VERSION` in `rubric.py` (which must then change in all three
projects) starts a new score vintage. A revised speech text is re-scored automatically. Changing
`COLLECTOR_VERSION` rechecks every page.

Tests:
```bash
uv run python -m unittest discover -s tests -v
```
