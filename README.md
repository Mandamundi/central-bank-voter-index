# Central bank member speech indexes

Daily hawkish/dovish indexes of the voting members of three central banks, built from the members'
own speeches: the Federal Reserve (FOMC), the Bank of England (MPC) and the Bank of Japan (Policy Board).

The indexes track how the voters themselves are leaning between meetings, in their own words. Policy
decisions, statements, votes and minutes are deliberately left out: they are covered by a separate
policy-decision sentiment index, and this one is its forward-looking companion.

| Folder | Bank | Voters | Speech sources |
|---|---|---|---|
| [`fed_speech_index/`](fed_speech_index/README.md) | Federal Reserve | FOMC voters, from the vote lines in the minutes | FRASER archive, the Board and the twelve Reserve Banks |
| [`boe/`](boe/README.md) | Bank of England | The nine MPC members | The Bank's speech sitemap |
| [`boj/`](boj/README.md) | Bank of Japan | The nine Policy Board members | The Bank's Japanese speech archive |

## Method (the same for all three)
- **Texts:** each voter's own prepared words (speeches, plus essays, statements and testimony where
  the bank publishes them). Interviews, Q&As, press conferences and slides are skipped.
- **Score:** each text gets one level on an absolute 7-level easy↔tight scale (0 = emergency easing,
  100 = aggressive tightening). Texts that say nothing about monetary policy are marked not relevant
  and ignored.
- **Stance:** a voter's stance on a day is the score of their latest relevant text up to that day.
  Several texts on the same day are averaged.
- **Index:** the equal-weight mean of the current voters' stances. **Disagreement:** their standard
  deviation. Voters with no scored text yet are left out.

Each bank's `output/` has the two daily series (`*_speech_index.csv`, `*_speech_disagreement.csv`)
and `*_member_scores.csv`, which gives each voter's score per day for member pages.
`data/scores.jsonl` holds each text's level, verbatim evidence quotes and a one- or two-sentence rationale
(what the speaker said about the policy outlook and why that is the level), for dashboard summaries.

## Shared scoring rubric
`rubric.py` holds the scale, the prompt core and the response schema. It is the same file in all
three folders, so the three indexes are scored with one instrument. Each bank's `prompt.py` only
fills in the bank's names and, for the BOE and BOJ, a short appendix of undated facts (who votes,
the bank's instruments). The appendix never says which level to give. `tests/test_rubric.py` in
each folder pins the file's checksum.

To change the rubric: edit it in one folder, copy it to the other two, update the checksum in all
three `tests/test_rubric.py`, and bump `VERSION`. A new version (or a new `MODEL` in a `config.py`)
starts a new score vintage, and every text is scored again. Use the same model in all three banks.

## Layout
The three projects are independent. Each has its own `pyproject.toml`, `uv.lock` and `.venv`, and
you run every command from inside its folder. Nothing imports across folders.

```bash
cd boe
uv sync
uv run score.py --limit 5
uv run python -m unittest discover -s tests -v
```

See each folder's README for setup, backfill and its sources.

## Daily updates
`.github/workflows/daily.yml` runs once a day at 07:00 UTC, or on demand from the Actions tab. It runs the
Fed, then the BOE, then the BOJ, each in its own job, and each job commits only its own folder's
`data/` and `output/` (`.github/commit.sh`). A failure in one bank does not stop the others. The
Fed job also harvests FRASER on the 1st of each month.

Repository secret: `OPENAI_API_KEY`.

## Scoring a backlog locally
Put the OpenAI key in a `.env` file in this root folder (git-ignored; a project's own `.env` takes
precedence if it has one):
```
OPENAI_API_KEY=your-key-here
```
Then, for each bank, from its folder:
```bash
cd fed_speech_index                # or boe, or boj
uv sync
uv run score.py --limit 5          # a few speeches scored right away; check data/scores.jsonl
uv run score.py --batch            # submit the backlog to the Batch API
uv run score.py --batch            # rerun every few hours: collects finished batches, submits the rest
uv run build_index.py              # once it says "Nothing left to score"
```
Batch mode costs half as much and returns results within 24 hours. Batches are recorded in each
project's `data/batches.json`, so stopping or closing the terminal loses nothing. The three banks can
be submitted at the same time. The Fed backlog is larger than one batch queue allows, so it goes up in
rounds: each rerun submits more once earlier batches finish.

When all three are done, commit `data/` and `output/` and push. The daily job then scores only new
texts, one request at a time.
