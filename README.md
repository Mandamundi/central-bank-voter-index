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
- **Score:** each text answers one question: how far, and how urgently, does the speaker want policy
  to move from its current setting over the near term (the next few meetings, roughly six months)?
  The scale is directional: the level of rates is not scored, only the change the speaker wants from
  it, so the same view gets the same score in any era and at any bank. The model is given the bank's
  setting on the speech date only as the starting point. The answer is one of 11 levels, 0 to 100 in
  steps of 10:

  | Ease | | | | | Hold | | | | | Tighten |
  |---|---|---|---|---|---|---|---|---|---|---|
  | 0 | 10 | 20 | 30 | 40 | **50** | 60 | 70 | 80 | 90 | 100 |
  | Emergency | Faster than usual | Easing now | Leans (expects a cut within ~6 months) | Tilts (risks or eventual direction) | No lean | Tilts | Leans | Tightening now | Faster than usual | Outsized, accepts recession |

  Crisis easing while the crisis is unfolding is 0; once the emergency measures are in place and
  sufficient, keeping them is a hold. A move the committee has already made is part of the setting,
  so voters who got the move they wanted and want nothing further return to 50: the index shows
  pressure building before moves and through a cycle, not the level of rates. After a decision the
  speaker dissented from, what counts is what they want from the new setting. Standard wording ("data
  dependent", "will act as appropriate") is not a lean; a conditional statement naming a likely
  development is a lean, not a move. Texts that say nothing about monetary policy, or only repeat the
  latest decision, are marked not relevant and ignored. Recorded votes are never used. The full rules
  are in `rubric.py`.
- **Stance:** a voter's stance on a day is the score of their latest relevant text up to that day.
  Several texts on the same day are averaged. A stance carries forward unchanged until the voter next
  speaks, however long that is: a changed view moves the index the day it is expressed. It is never
  adjusted for policy decisions made since.
- **Index:** the equal-weight mean of the current voters' stances; the Chair counts like everyone
  else. **Disagreement:** their standard deviation. Each series starts on the first day every voter
  has a scored text; after that, a new voter is left out until their first relevant text. Changes in
  the committee (appointments, the Fed's yearly rotation) move the index too, since the voters
  changed.

Each bank's `output/` has the two daily series (`*_speech_index.csv`, `*_speech_disagreement.csv`)
and `*_member_scores.csv`, which gives each voter's score per day for member pages.
`data/scores.jsonl` holds each text's level (0-10; ×10 for the 0-100 value), verbatim evidence quotes and a one- or two-sentence rationale
(what the speaker said about the policy outlook and why that is the level), for dashboard summaries.

## Shared scoring rubric
`rubric.py` holds the scale, the prompt core and the response schema. It is the same file in all
three folders, so the three indexes are scored with one instrument. Each bank's `prompt.py` only
fills in the bank's names and, for the BOE and BOJ, a short appendix of undated facts (who votes,
the bank's instruments). The appendix never says which level to give. `tests/test_rubric.py` in
each folder pins the file's checksum.

**The rubric is frozen at v10.** v9 used an absolute easy↔tight grid, which made the index mostly a
copy of the policy rate (the rate explained 84-89% of it); v10 replaced it with the directional scale
after a pilot of 140 texts scored twice (`pilot_v10/`): 85% of levels identical between runs, 95%
within one, and the correlation of scores with the policy rate fell from +0.6/+0.8 to about zero.
It changes only when the full-history output shows a systematic
error: a whole episode misplaced (for example a crisis not registering as one), or a member's stance
wrong across many texts. A single borderline text one level off is expected scoring noise, not a
reason to change it. Model-scored texts, like texts scored by human analysts, are not perfectly
repeatable: in a rescore of the full BOJ history, about 8% of texts moved by one level, almost all
on genuinely ambiguous wording. One voter moving one level moves a nine-member index by about one
point, so this noise is small next to the movements the index is built to show.

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
To score only part of a backlog, add `--limit N` (the N newest unscored speeches per round), and
collect that round without sending another with:
```bash
uv run score.py --batch --collect-only
```
Every run prints the tokens used (plain input, cache reads, cache writes, output), which should match
the OpenAI usage page. Only the shared scoring instructions are written to OpenAI's prompt cache and
read back cheaply; the speech text is billed at the plain input rate (see `openai_api.py`).
Batch mode costs half as much and returns results within 24 hours. Batches are recorded in each
project's `data/batches.json`, so stopping or closing the terminal loses nothing. The three banks can
be submitted at the same time. The Fed backlog is larger than one batch queue allows, so it goes up in
rounds: each rerun submits more once earlier batches finish.

When all three are done, commit `data/` and `output/` and push. The daily job then scores only new
texts, one request at a time.
