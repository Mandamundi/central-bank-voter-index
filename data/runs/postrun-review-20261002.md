# Backfill review — October 2, 2026

## Your completed run

- All 13 sources were reached in 32 minutes 28 seconds.
- 624 speeches were added; 4,257 were stored at the end.
- The report correctly marks the run partial: Kansas City paused after two exhausted requests, leaving 478 pages unattempted.
- Missing metadata and text also left recoverable failures, especially Dallas, Chicago, and San Francisco.

## Repairs and verified recovery

- 275 additional speeches were saved across four targeted checks.
- 13 publisher-labelled Dallas essays were moved to quarantine; originals remain preserved.
- Active store: **4,519 speeches**, with unique IDs and valid date/name formats.
- 4,196 stored speeches match the saved history of people who have been FOMC voters. The score stage applies that filter; the daily index uses the roster for each day.
- No AI scoring was run in this recovery.

Collector v3.4 handles Dallas legacy and translated URLs and header dates; Chicago article bylines, named disclaimers, and explicit delivery dates; San Francisco generic speech PDF downloads; and PDF covers with titles beginning “We”. Event teasers, essays, papers, photo galleries, slides, cancelled speeches, and radio or multi-speaker interviews are classified as exclusions when publisher evidence supports that classification. Publisher error pages remain retryable. Fresh pages precede repeatedly failing URLs.

## What remains

- 15 Chicago pages still lack a reliable delivery date or speaker in the inspected publisher material.
- One Kansas City document supplies only a month rather than an exact delivery date.
- Eight Kansas City URLs remain request failures, including older failures deferred by targeted checks.
- One Atlanta URL returns HTTP 404.
- Kansas City still needs a full recovery pass. The successful 25-page check saved 20 speeches without a timeout or source pause; it was deliberately limited and does not establish complete coverage.

These unresolved pages remain tracked. Dates and authors have not been guessed. Successfully stored speeches are retained; older failed or excluded outcomes reopen after the collector version changes.

## Verification

42 regression tests pass. The dependency lock validates offline and code diff whitespace checks pass. These checks validate the repaired cases and collection state; they do not establish that every publisher page is accessible or every stored speech has been manually audited.

## Resume Kansas City

```bash
uv run backfill.py --skip-voters --skip-rates --sources kansascity --recover
```

Use `--limit 25` to bound each pass. Request guards can still pause an unavailable bank; completed work is saved.

See `postrun-review-20261002.json` for exact report paths and every outstanding failed URL. The original full-run report is `20261002T031556.387915Z-8729.json`; `last_run.json` now points to the later targeted check.
