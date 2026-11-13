# Pre-registration: confirmation tests for combined scoring

Written 12 November 2026, before any BOE or BOJ comparison was run. The prompts in `confirm.py`
(extraction and comparison) are frozen: fingerprint **4ccf1acfeddb23ee** (`confirm.prompt_hash()`). Every
verdict records the fingerprint it was asked with. If a prompt changes, these tests start again on texts
the change was not tuned on.

Changes made before freezing, all tuned on Fed pilot texts only: the crisis rule in the comparison prompt;
the crisis line in the extraction prompt (tested on the three Fed crisis texts it had missed); bank-neutral
wording so one prompt serves all banks and cross-bank pairs; a verbatim check that works in any script.

## Design
- Every relevant voter text in the bank's history (`build_index.policy_speeches()`), v10 level as scored.
- Passages extracted once per text; texts with none are compared on their full text.
- Adaptive pairing, 3 rounds, about 6 comparisons per text (simulation: matches the pilot's 8).
- Side tests: 60 pairs asked again in reversed order, 40 asked again on full texts.
- Combined model (v10 level + comparisons); v10 noise and points per comparison unit chosen by held-out
  log loss (5 folds by pair) over the grid in `confirm_report.py`.

## Test 1 pass marks (each bank)
| | Mark |
|---|---|
| P1 | Held-out accuracy: combined > v10 alone **and** combined > comparisons alone |
| P2 | Held-out pairs v10 scores equal: combined accuracy >= 60% with the 95% range above 50% |
| P3 | v10 hold texts average 50 +/- 3; v10 level-0 texts (if any) average <= 10 |
| P4 | Order bias: speaker A wins 45-55% of decisive comparisons |
| P5 | Passages vs full text: opposite-winner rate no more than 5 points above the reversed-pair rate |
| P6 | Cross-bank (BOE vs BOJ pairs matched on their separately fitted scores): neither bank more hawkish in more than 60% of decisive pairs (a common scale shows no offset) |

## Test 2 pass marks (votes; BOE first)
For each member at each meeting: the member's stance the day before (stance filter; only texts available
by then), relative to the mean of that day's voters, against the member's vote relative to the decision
(BOE: preferred Bank Rate minus decided Bank Rate). Three versions of the stance: v10 carried forward
(old method), v10 through the filter (current), combined scores through the filter (proposed; filter
parameters refitted for these inputs by the same maximum-likelihood fit).

| | Mark |
|---|---|
| P7 | Rank correlation of relative stance with relative vote: combined >= current - 0.02 (no worse) |
| P8 | Dissents: the relative stance ranks tighter-than-decision dissents above looser ones (AUC) at least as well as current - 0.02 |

Reported either way: whether combined is *better*, with bootstrap 95% ranges. Failing P1, P2, P3, P7 or
P8 stops the rollout until the cause is found.

## Results (12 November 2026)
All verdicts asked with prompt fingerprint 4ccf1acfeddb23ee. Details in `boe/results.json`,
`boj/results.json`, `cross_boe_boj/results.json`, `boe/vote_test.json`, `boj/vote_test.json`.

| Mark | BOE | BOJ |
|---|---|---|
| P1 combined beats both | pass (68.1% vs v10 57.9%, comparisons 63.8%) | pass (66.4% vs 57.8%, 64.7%) |
| P2 resolves v10 ties | pass (64.0%, range 58.6-69.3%) | pass (62.7%, range 56.9-68.6%) |
| P3 anchors | pass (hold 48.6; level 0 -3.5) | pass (hold 48.6; level 0 2.5) |
| P4 order bias | pass (50.2%) | **fail (56.7%)** |
| P5 passages vs full text | pass | pass |
| P6 common scale (BOE vs BOJ) | **fail: BOE judged more hawkish in 69% of score-matched pairs (about 6 points)** | |
| P7 votes, no worse than current | pass (+0.024, range +0.002 to +0.048) | pass after correction (+0.017; range -0.041 to +0.080); first run: fail (-0.021) |
| P8 dissents, no worse | pass (+0.005) | pass after correction (0.962 vs 0.936); first run: not computable |

Accuracies are lower than in the Fed pilot because adaptive pairing compares close texts, the hard pairs;
they are not comparable with the pilot's random-plus-neighbour pairs.

Not pre-registered (exploratory), both banks: combined scores *carried forward* predict votes best of all
(BOE rank correlation 0.366, dissent AUC 0.937; BOJ 0.391, looser-dissent AUC 0.896). The stance filter,
whether on v10 or combined scores, does not improve member-level vote prediction over carry-forward and at
the BOE may worsen dissent prediction (-0.04; range -0.09 to +0.002). The filter was adopted because it
predicts members' next speeches better; it smooths a member's fresh shift toward their history and the
committee, which is the shift that precedes a dissent.

**Correction (12 November 2026).** The first BOJ vote run missed every dissent for a higher rate: those notes read
"Voting against the action: ... proposed ... around 0.75 percent" and never use the word "dissented", which the
parser searched for. Twelve such votes (Tamura Dec 2024; Takata and Tamura Sep and Oct 2025; Takata Jan, Mar, Jul and
Oct 2026; Nakagawa, Takata and Tamura Apr 2026) were coded as votes with the majority. `boj_vote_test.py` now reads
these proposals and codes direction by proposed rate against decided rate. Only the coding of votes changed; scores,
prompts and pass marks did not. With the votes coded correctly, P7 and P8 pass at the BOJ; the looser-dissent
substitute (not pre-registered) still shows proposed 0.867 vs current 0.890.

The first run's P7 failure at the BOJ was caused by this coding error. The evidence
points to the filter's member-level smoothing, not to the combined scores.
