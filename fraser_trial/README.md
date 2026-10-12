# FRASER speech coverage trial

This is an isolated, local-only audit. It reads `data/voters.csv` and `data/speeches/` from the parent project but does not edit them.

## 1. Request and save a key

From this directory:

```sh
uv run request_api_key.py request
```

Enter the email address that should receive the key. FRASER emails the API key. Save it without displaying it or putting it in shell history:

```sh
uv run request_api_key.py save-key
```

The key is stored in this folder's ignored `.env` file. The key request is submitted only when you run the first command.

## 2. Inspect the planned scope

```sh
uv run fraser_backfill.py --dry-run
```

The script derives voter-name aliases from `data/voters.csv` and the local speech corpus. It enumerates `GET /api/author` using `fields=name!recordInfo`, matches those author entities to historical voter aliases, then requests each matched author's records from `/api/author/{authorId}/records` with `role=creator`. That role filter keeps records where FRASER explicitly relates the person as a creator. Speech genre, exact date, and whether the person was a voting member on that date are then checked against the local project data.

List requests use `format=json`, `page`, and `limit=100`. Record metadata is limited to `titleInfo!name!originInfo!genre!identifier!location!recordInfo`, the fields needed for titles, names, dates, genre, identifiers, record types, and file locations. The script does not send a guessed genre filter: it checks speech genre locally so an uncertain genre label cannot silently exclude matching records. Downloads use FRASER's `location.pdfUrl` and `location.textUrl` values.

## 3. Probe, then backfill and download

The probe sends at most one live API request to inspect the author-directory response and stops. Its page is cached and reused by the full run:

```sh
uv run fraser_backfill.py --probe
```

Run the full historical scan and download confirmed text documents:

```sh
uv run fraser_backfill.py
```

For a small first run, cap live API requests or limit member names:

```sh
uv run fraser_backfill.py --member Powell --max-api-requests 5
```

To compare metadata without downloading files:

```sh
uv run fraser_backfill.py --metadata-only
```

The default date range is 2000-01-01 through the local current date. Override it with `--start-date` and `--end-date`.

## Resume and outputs

- `cache/api/` stores each successful API response by request URL; reruns reuse cached pages unless `--refresh-cache` is set.
- `state.sqlite` records FRASER record IDs, classification, download state, and hashes. Existing completed downloads are skipped.
- `downloads/YYYY/` holds downloaded PDFs and text documents.
- `manifest.csv` lists confirmed speeches, records needing metadata review, records without downloadable files, file paths, hashes, and FRASER record IDs.
- `logs/fraser_backfill.log` records progress, retries, cache hits, and errors without logging the API key.

The harvester is sequential and spaces every live API attempt, including retries, by at least 2.1 seconds. It caches successful responses, retries transient failures, honors `Retry-After`, stops on invalid credentials or the request cap, validates FRASER-hosted file URLs, enforces a per-file size cap, and uses atomic cache/download writes. Completed downloads are hashed and recorded in SQLite so a rerun skips them; interrupted partial files are replaced on retry.

FRASER terms require attribution and reserve rights in third-party documents. Keep this trial local; obtain any required permissions before redistributing downloaded documents or integrating them into a product for other users.
