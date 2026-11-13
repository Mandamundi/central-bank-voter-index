# Pre-registration: combined scoring for the Fed

Written 12 November 2026, before any Fed comparison with the frozen prompts has been run. Prompts as in
`confirm.py`, fingerprint **4ccf1acfeddb23ee**: the same prompts that passed at the BOE and BOJ. If a prompt
changes, this test starts again.

## What is being tested
The Fed index switches from v10 scores to combined scores (v10 level + head-to-head comparisons), both through
the stance filter. v10 scores are not changed or re-run. Everything below is decided now, before results exist.

## Design
- **Texts:** every relevant voter text from 2005-01-01 (`build_index.policy_speeches()`): 2,325 texts, to
  2026-11-10, v10 level as scored (`fed/texts.json`). The published index starts 2008-01-01
  (`config.START_DATE`). Texts from three years earlier are scored so that stances on that day rest on scored
  history: by the filter's fitted half-lives (8 months committee, 13 months member), anything older carries under
  about an eighth of its weight by then.
- **Passages:** extracted once per text; texts with none are compared on their full text.
- **Comparisons:** adaptive pairing, 3 rounds, about 6 per text, as at the BOE and BOJ.
- **Side tests**, more than at the BOE and BOJ because this is the bank being published: 150 pairs asked again in
  reversed order; 100 asked again on full texts.
- **Combined model:** v10 noise and points per comparison unit chosen by held-out log loss (5 folds by pair) over
  the grid in `confirm_report.py`.
- **Pilot texts:** the frozen prompts were tuned on the 180 Fed pilot texts. P1 and P2 are judged on pairs where
  neither text was in the pilot. All-pairs figures are reported alongside.
- **Estimated cost:** about 40M input tokens.

## Dissent record (fixed before any score exists)
`fed/dissents_coded.json`: every dissent in the FOMC minutes from 1996 to 2026, plus Fisher in June 2008 (his
minutes page returns 404; taken from the statement). The direction of each was coded by hand from the minutes'
wording, using one rule:
- **+1:** the dissenter wanted tighter policy than the committee decided: a higher rate, a smaller cut or no cut,
  fewer purchases, or firmer guidance.
- **-1:** looser.
- **Excluded:** dissents not about the policy stance: money-growth ranges (1996, 2000), the composition of
  purchases (Lacker 2009), foreign-currency swap lines (Lacker 2011).

Result: 93 tighter, 31 looser, 7 excluded; from 2008 on, which is what Test 2 uses: 73 tighter, 24 looser. Poole's dissent on the 22 January 2008 intermeeting cut is dated
22 January.

## Test 1 pass marks (as at the BOE and BOJ)
| | Mark |
|---|---|
| P1 | Held-out accuracy, non-pilot pairs: combined > v10 alone **and** combined > comparisons alone |
| P2 | Held-out pairs v10 scores equal, non-pilot: combined accuracy >= 60%, 95% range above 50% |
| P3 | v10 hold texts average 50 +/- 3; v10 level-0 texts average <= 10 |
| P4 | Order bias: speaker A wins 45-55% of decisive comparisons |
| P5 | Passages vs full text: opposite-winner rate no more than 5 points above the reversed-pair rate |

## Test 2 pass marks (votes and decisions, 2008-2026, the published index's span)
The stance used is each member's stance the day before each meeting, using only texts available by then. It is
measured relative to the mean of that day's voters. It is computed three ways:
- **old:** v10 carried forward;
- **current:** v10 through the filter, as live now;
- **proposed:** combined scores through the filter, with filter parameters refitted by the same maximum-likelihood fit.

| | Mark |
|---|---|
| P7 | Member votes (+1/0/-1): rank correlation, proposed >= current - 0.02 |
| P8 | Dissents: tighter-than-decision dissents ranked above looser ones (AUC), proposed >= current - 0.02 |
| P9 | **The index itself:** the index the day before each meeting, against the rate decision at that meeting (change in the federal funds target, from `data/fed_funds.csv`), relative to the index 90 days earlier. Rank correlation of the index's change with the decision, proposed >= current - 0.02 |

## Test 3 pass mark (the daily step for new speeches)
New speeches will be scored with history held fixed: past values are never revised. The method: a new text gets
its passages and 6 comparisons, in 3 rounds of 2, against existing texts near its current estimate. Its score is
then fitted with every other score held fixed. This is tested on the 100 most recent texts:
- remove them and their comparisons, then refit the history;
- run the daily step for each, in date order, with new comparisons (about 600 calls);
- compare each daily score with its score in the full fit.

| | Mark |
|---|---|
| P10 | Daily vs full-fit score: median absolute difference <= 4 points, and rank correlation >= 0.9 |

## What happens on failure
- **P1, P2, P3, P7, P8, P9 or P10 fails:** the live Fed index stays on v10 until the cause is found and the test
  is rerun.
- **P4 fails:** an order term is added to the model, a refit with no new calls.
- **P5 fails:** extraction is reviewed before any rollout.

Reported either way: whether combined is *better*, with bootstrap 95% ranges. Also reported, with no mark:
how far the published index moves (correlation, typical and largest gap, and 3-month moves), as was done for the
BOE and BOJ.

## Change before any result (13 November 2026)
The first version of this document covered every Fed text since 1986 (3,013 texts) and votes since 1996. The
published index starts in 2008, so the run was stopped and narrowed to texts from 2005 and votes from 2008, as
above. Before the stop, passages had been extracted for 155 texts, all from before 2005; they are not used. No
comparison had been run. The pass marks themselves did not change.

## Data problems found while preparing (live pipeline, not part of this test)
- The June 2008 FOMC meeting is missing from `data/voters.csv`: its minutes URL returns 404.
- Goolsbee is missing from the 10 December 2025 voter list. `voters.parse_vote_names` drops a name that follows
  a dissent reason and is itself followed by "and" ("...; and Austan D. Goolsbee and Jeffrey R. Schmid, who ...").
  He is out of the live index from 10 December to 31 December 2025.

## Results (13 November 2026)
All 6,375 run verdicts and 600 daily-test verdicts were asked with prompt fingerprint 4ccf1acfeddb23ee. Eight texts
were refused by the API's content filter at extraction on every retry (`error: content_filter` in
`fed/passages.jsonl`) and were compared on their full text; no comparison failed. Input tokens: about 22M for the
run, plus about 1M for the daily test. Details are in `fed/results.json`, `fed/vote_test.json` and
`fed/daily_test.json`.

| Mark | Result |
|---|---|
| P1 combined beats both (non-pilot pairs) | pass: 72.1% vs v10 57.8%, comparisons 66.6% |
| P2 resolves v10 ties (non-pilot) | pass: 67.1%, range 65.5-68.8% (3,007 pairs) |
| P3 anchors | pass: hold 49.0; level 0 0.7 |
| P4 order bias | pass: 50.3% |
| P5 passages vs full text | pass: opposite winner 15.0% vs reversed 18.7% |
| P7 votes, no worse | pass: 0.326 vs 0.325 (difference range -0.010 to +0.012) |
| P8 dissents, no worse | pass: AUC 0.980 vs 0.980 (72 tighter, 23 looser) |
| P9 index vs decisions, no worse | pass, narrowly: 0.103 vs 0.120 (difference range -0.067 to +0.035) |
| P10 daily step | pass: median gap 3.6 points, rank correlation 0.945 (100 texts, Nov 2025 - Nov 2026) |

Chosen settings: sigma_v 10, s 6. Combined scores do not improve vote or decision prediction at the Fed: P7 and P8
are flat and P9 is slightly lower, within the margin. This differs from the BOJ, where combined improved both.
The published index: correlation 0.980 with the live index from 2008, typical gap 2.0 points (90% under 6.1),
largest +11.8 (29 Sep 2022). The gaps concentrate in hiking cycles: the 2022 average is 85.1 vs 76.6, and
1 Nov 2026 is 76.6 vs 68.8.
