# job-scrapper

## What Is It

`job-scrapper` is a job-intelligence stack for public ATS job boards. It crawls postings into a local SQLite catalog, scores them against a candidate profile with NVIDIA NIM models, and serves a local feed UI.

Four Python services plus a React frontend, all run with Docker Compose:

| Service | What it does | Port |
|---|---|---|
| `crawler` | Pulls postings from ATS providers into `crawler/state/catalog.sqlite`. Run on demand. | — |
| `scheduler` | Runs the crawler on a daytime schedule via the Docker socket. | — |
| `viewer` | FastAPI backend + built React UI. Reads the catalog, saved searches, favorites, queue. | 3000 |
| `matcher` | FastAPI service. Parses job pages (`/parse/batch`) and scores jobs (`/analyze/quick`, `/analyze/ensemble`). | 8001 |

The profile files under `matcher/$CAREER_OPS_DIR/` are generated local assets from `career-ops` (https://github.com/santifer/career-ops). They are not tracked in Git.

```text
scheduler ──docker compose run──▶ crawler ──writes──▶ crawler/state/catalog.sqlite
                                                        │ (shared volume)
viewer (FastAPI + React UI, :3000) ◀────────────────────┘
   │  HTTP
   ▼
matcher (FastAPI, :8001) ──▶ NVIDIA NIM (chat completions)
```

## How It Works

1. **Crawl.** The crawler reads source slug lists (`sources/*.json`), fetches each provider's postings, and upserts them into `catalog_jobs`. Sources that return 404/410 are quarantined in `exclude.jsonl` and skipped on later runs.
2. **Browse.** The viewer lists the catalog with filters (provider, title, company, location, age, tier, employment type, keywords). Filter state lives in the URL.
3. **Analyze.** Clicking **Analyze** on a job queues a match run:
   - **Quick (`mode: "claude"`)** scores the job with one model (`NVIDIA_MODEL`).
   - **Ensemble (`mode: "claude-ensemble"`)** runs several scorers (`NVIDIA_ENSEMBLE_SCORERS`) and a synthesizer (`NVIDIA_ENSEMBLE_SYNTHESIZER`). It is slower and costs more.
   - Each run goes through **parse → score → discord**. Progress and failures appear in the **Queue** drawer.
4. **Store.** Scores are saved in `job_analyses` and mirrored to `catalog_jobs.analysis_score`.

### Automatic behaviour (off by default)

| Setting | Default | Effect when on |
|---|---|---|
| `SAVED_SEARCH_ANALYZER_ENABLED` | `0` (off) | Background loop analyzes the next unscored job matching a saved search, one at a time. Set `1` to enable. |
| `AUTO_RETRY_ENABLED` | `0` (off) | A failed match run is retried with backoff. When off, a failure is `permanent_error` and only a manual **Retry** re-runs it. Set `1` to enable. |

With both defaults off, LLM calls happen only when you click Analyze or call the API. Jobs with a `permanent_error` queue item are skipped by the background analyzer, so a failing job is not re-run every minute.

## Quick Start

### 1. Build

```bash
docker compose build
```

### 2. Crawl job boards

```bash
docker compose run --rm crawler
```

Flags are set in the compose `command:`. To run with explicit flags:

```bash
docker compose run --rm crawler \
  --concurrency 8 \
  --provider-concurrency ashby=1,workday=4,lever=3,teamtailor=2,bamboohr=3,greenhouse=6,workable=2,smartrecruiters=2 \
  --timeout-ms 30000 \
  --retries 5 \
  --exclude-sources /app/state/exclude.jsonl \
  --catalog-db /app/state/catalog.sqlite \
  --catalog-file /app/output/current-jobs.jsonl
```

Outputs:

- SQLite catalog: `crawler/state/catalog.sqlite`
- Jobs JSONL: `crawler/output/current-jobs.jsonl`
- Report: `crawler/output/report.json`

Parameters:

```text
--sources <dir>          Source JSON directory (default: /data/sources)
--providers <list|all>   Providers to crawl (default: all)
--concurrency <n>        Global source concurrency (default: 50)
--out <file>             JSONL output path (default: /app/output/jobs.jsonl)
--report <file>          Report JSON path (default: /app/output/report.json)
--catalog-db <file>      SQLite catalog state file (default: /app/state/catalog.sqlite)
--exclude-sources <file> JSONL source quarantine file
--sample <n>             Crawl only first n sources per provider
--max-jobs-per-source <n> Emit at most n jobs per source
--max-age-hours <n>      Keep only jobs where updated_at is within last n hours
--progress-every-ms <n>  Progress log interval, 0 disables it (default: 10000)
--provider-concurrency <spec> Per-provider limits, e.g. ashby=2,workday=10 (default: ashby=2)
--timeout-ms <n>         Per-request timeout (default: 15000)
--retries <n>            Transient retry count (default: 2)
```

### 3. Start the viewer

```bash
docker compose up -d viewer
```

Open `http://localhost:3000`.

The viewer container serves the React build from `frontend/dist`. The no-build UI in `viewer/frontend-next/` is not the default yet. To serve it, override `STATIC_DIR` and `SAVED_SEARCHES_PATH` in compose (see the comment in `docker-compose.yml`).

### 4. Run the scheduler (optional)

```bash
docker compose up -d scheduler
```

The scheduler runs the crawler on a weekday 08:00–20:00 window (limited weekend hours) and skips a run if the last one was too recent.

## Checking That the Database Is Populated

Run these read-only checks. They use the Python image, so nothing is installed on the host.

```bash
docker run --rm -v "$(pwd)/crawler/state:/s" python:3.12-slim python -c '
import sqlite3
c = sqlite3.connect("/s/catalog.sqlite")
for t in ["catalog_jobs", "job_analyses"]:
    print(t, c.execute(f"select count(*) from {t}").fetchone()[0])
print("by provider:", c.execute("select provider, count(*) from catalog_jobs group by provider order by 2 desc").fetchall())
print("first_seen range:", c.execute("select min(first_seen_at), max(first_seen_at) from catalog_jobs").fetchone())
'
```

What a healthy catalog looks like:

- `catalog_jobs` has rows for each provider you crawled. A full crawl of all providers was about 589k rows.
- `first_seen_at` max is close to your last crawl date. An old max means the crawler has not run.
- `job_analyses` grows when you run Analyze. It is empty on a fresh catalog.

The queue is not in SQLite. It is `crawler/state/retry-queue.json`, and match-run artifacts are under `crawler/state/match-runs/`.

Check the API, too:

```bash
curl -s "http://localhost:3000/api/jobs?limit=1"          # one job, with total count
curl -s http://localhost:3000/api/auto-analyzer           # {"enabled":false,...} by default
curl -s http://localhost:3000/api/queue                   # queue items
```

## Using the Analyzer

### From the UI

1. Open the feed and find a job.
2. Click **Analyze**. The job is queued in quick mode.
3. Open the **Queue** drawer to watch parse → score → discord. A failure shows a reason.
4. Click the job to open the analysis panel with score, scorecard, and verdict.

### From the API

Quick score for one job:

```bash
curl -s -X POST http://localhost:3000/api/match-runs \
  -H 'Content-Type: application/json' \
  -d '{"job_keys":["ashby|popl|5dda8b68-dcba-41fe-aad8-522619e0cfc5"],"mode":"claude"}'
```

Ensemble score for several jobs (slower, more LLM calls, roughly 4 per job):

```bash
curl -s -X POST http://localhost:3000/api/match-runs \
  -H 'Content-Type: application/json' \
  -d '{"job_keys":["ashby|popl|5dda8b68-dcba-41fe-aad8-522619e0cfc5","ashby|preply|aa3c3376-7df9-48a3-b1d6-869ff42a0c8e"],"mode":"claude-ensemble"}'
```

Job keys are `provider|source_key|job_id`. The response gives a `run_id`. Its status is in `crawler/state/match-runs/<run_id>/manifest.json`.

Turn background analysis on or off without changing config, using the UI toggle or:

```bash
curl -s -X POST http://localhost:3000/api/auto-analyzer -H 'Content-Type: application/json' -d '{"paused": false}'
```

Note: this only pauses or resumes a running analyzer. Set `SAVED_SEARCH_ANALYZER_ENABLED=1` to enable it at startup.

## Configuration

Set in `.env` (gitignored). The values that matter most:

| Variable | Purpose |
|---|---|
| `NVIDIA_API_KEY` | Required for any LLM scoring. Keep it out of Git. |
| `NVIDIA_MODEL` | Quick-mode scorer. |
| `NVIDIA_ENSEMBLE_SCORERS` | Comma-separated scorer models for ensemble mode. |
| `NVIDIA_ENSEMBLE_SYNTHESIZER` | Model that merges scorer results. |
| `SAVED_SEARCH_ANALYZER_ENABLED` | `1` to run the background analyzer. Default off. |
| `AUTO_RETRY_ENABLED` | `1` to retry failed runs automatically. Default off. |
| `CAREER_OPS_DIR` | Local profile folder under `matcher/`. |

Retired NIM models return `410 Gone`, and some models are listed but cannot be called. Test a model with a one-token request before you put it in `.env`. The matcher's LLM read timeout is 180 seconds.

## Known Limitations

- **iCIMS is not parsed.** Postings from iCIMS fail at parse with `Unsupported provider for URL`. The crawler still records them, but they cannot be scored with a full description.
- **Ensemble runs can fail.** Recent runs failed with `ReadTimeout` and with `returned empty content` from `mistralai/mistral-large-4-1t-a52b`. Check `matcher.log` and `manifest.json` for the run.
- **Saved searches with `location: remote`** can return HTTP 500 (`no such column: is_remote`) in the viewer backend.
- **HiBob** is not implemented in the crawler.

## Development

Each Python service has `just` recipes. Run them from the repo root:

```bash
just lint | fmt | test                  # viewer/backend
just matcher-test | scheduler-test | crawler-test
just test-all                           # all four services
```

Run Python through Docker only. Do not install Python or Node on the host for this project.

## Module Docs

- [crawler/README.md](crawler/README.md)
- [matcher/README.md](matcher/README.md)
- [viewer/README.md](viewer/README.md)

## Let's Connect

[![LinkedIn](https://img.shields.io/badge/LinkedIn-Michael%20Levy-0A66C2?style=for-the-badge&logo=linkedin&logoColor=white)](https://www.linkedin.com/in/michael-levy-product/)
