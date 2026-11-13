# Comparative-scoring pilot (Fed)

Tests scoring speeches by **pairwise comparison** instead of one absolute 0-10 level per text. The model
is shown two speakers' policy passages and asked which wants policy moved further toward tightening, or
sooner, from the setting current when each spoke (or "same"). A Bradley-Terry model with ties (the idea
behind chess Elo ratings) turns the answers into one continuous score per text. There are no level
boundaries and no spacing to choose; the scale comes from the comparisons.

Nothing here changes the Fed project's data or the live index.

## Sample (`sample.json`, `pairs.json`, fixed seed)
- All 148 Fed policy texts since July 2025, the known hard cases (Waller 8 Oct and 10 Nov 2026, the three
  corrected direction flips), the v10 pilot's Fed texts that scored differently on its two runs, six holds
  it agreed on, and a few older texts at the extremes of the scale: 180 texts.
- About 8 comparisons per text: random partners and partners at the same or a neighbouring v10 level.
- Side tests: 121 pairs asked again with A and B swapped (order bias, repeatability), and 80 asked again
  on the full speeches instead of the extracted passages.

## Steps
```bash
cd fed_speech_index
.venv/bin/python ../comparative_pilot/pilot.py sample     # texts and pairs
.venv/bin/python ../comparative_pilot/pilot.py extract    # verbatim policy passages, one call per text
.venv/bin/python ../comparative_pilot/pilot.py compare    # one call per pair
.venv/bin/python ../comparative_pilot/pilot.py report     # fit and tests -> results.json, scores.csv
```
`extract` and `compare` are resumable. Passages are checked against the speech text (letters only, since
PDF text has footnote numbers and broken ligatures inside sentences). Texts where extraction finds no
forward-looking passage (crisis speeches describing emergency measures already taken) are compared on
their full text.

## Tests (`report.py`)
1. Held-out comparisons (5 folds): does the fitted score predict comparisons it was not fitted on, and,
   above all, pairs that v10 scores **equal**, where v10 cannot say which is more hawkish?
2. Calibration of those predictions: if one score per text is enough, predicted and observed rates match.
3. Order bias and repeatability: the swapped pairs.
4. Passages vs full text: the full-text pairs.
5. Known cases: Waller 8 Oct vs 10 Nov 2026; the corrected flips.
6. Correlation with the policy-rate level, which v10 was built to remove.

## Results (12 November 2026; `results.json`, `scores.csv`)
- **Resolution within v10 levels.** On held-out pairs that v10 scores *equal* (142 decisive), the fitted
  score called the more hawkish speaker 72% of the time (95% range 64-79%; chance is 50%). Overall
  held-out accuracy 81% vs 78% for v10 levels.
- **One score is enough.** Held-out predictions are well calibrated (predicted 6/30/50/72/93% vs observed
  10/38/51/68/91%), so hawkishness behaves as one dimension; no second score is needed.
- **No order bias** (A won 51.6% of decisive comparisons). **Repeatability:** a pair asked again with A and
  B swapped gave the same verdict 81% of the time and the opposite winner 9%.
- **Passages lose nothing measurable:** full speeches and passages agreed 80% (opposite winner 5%), the
  same as asking the same pair twice.
- **No link to the rate level** (correlation -0.01; v10 0.05).
- **Waller:** asked directly, the model judged 10 Nov more hawkish than 8 Oct ("a December hike ... 50 if
  inflation is stronger ... while B leaves timing flexible"). Fitted scores 90 vs 81; 81% of bootstrap
  refits put 10 Nov higher.
- **It finds v10 errors:** e.g. Logan, 25 Aug 2025, v10 8 for continuing balance-sheet runoff (the rubric
  treats continuing runoff as part of the setting); comparison score 55.
- **It fails on crisis texts:** three level-0 texts (Bernanke Nov 2008, Kohn Jan 2009, Barkin Mar 2020)
  scored 40-51. Compared pairwise, support for emergency facilities already in place was read as "no
  further change in monetary policy". Absolute anchors such as the crisis rule do not carry over to
  relative judgements; extraction also found no forward-looking passage in them.
- **Precision per text** with about 8 comparisons: bootstrap sd 6-8 points, about one v10 level. More
  comparisons per text shrink it (by the square root of their number).
- **Cost:** a comparison on passages used about 1,700 input tokens on average (v10 scoring call: about
  5,400); extraction about 3,400 per text. Comparing only relevant texts, the add-on for a full backfill
  is roughly 0.6x of a v10 rescore.

## Combined model (`combined.py`; `combined_results.json`, `combined_scores.csv`)
Each text's score is measured twice: by its v10 level (score + noise) and by its comparisons
(Bradley-Terry with ties). The scores are fitted by maximum likelihood. How much to trust a v10 level
(its noise, in points) and how many points one unit of comparison odds is worth are chosen together by
held-out prediction over a grid; the loss is flat across v10 noise 5-10 and 3-6 points per unit, so the
choice is not fragile. Chosen: v10 noise 7 points, 4 points per unit.

The comparison prompt's crisis rule was sharpened after the first fit (supporting the emergency response
while a crisis unfolds is the most dovish position, even when the measures are already in place), and the
33 comparisons involving a level-0 text were asked again (`pilot.py recompare-crisis`,
`verdicts_crisis.jsonl`). Before, the crisis text was judged the more hawkish of the pair 11 times; after, 3.

Results, held-out comparisons:

| | Accuracy | On pairs v10 scores equal |
|---|---|---|
| v10 levels alone | 78% | 50% (cannot tell) |
| Comparisons alone (first prompt) | 81% | 72% |
| **Combined** | **87%** | **75%** |

- **Anchors kept:** v10 hold texts average 49.9; each level's texts average close to its value
  (0: 3, 2: 19, 5: 50, 7: 71, 8: 81, 9: 90). Crisis texts 0.6, -0.6 and 4.6 (the scale has no floor; a
  production version would clip at 0).
- **Waller:** 8 Oct 82, 10 Nov 87 (sd about 2.5); 78% of bootstrap refits put 10 Nov higher.
- **v10 errors corrected:** Logan 25 Aug 2025 (continuing runoff) 80 -> 67; Kashkari 31 Jul 2026 (dissent
  for an immediate hike, v10 7) 70 -> 81; Logan 15 Jul 2025 (cuts plausible fairly soon) 50 -> 39.
- **Precision:** about 2-5 points per text, against about 8.5 for one v10 reading in the stance filter.
