# Search latency baseline

Ticket: `tickets/01-search-speed-and-progress-indicator.md`

## Method

- Catalog: copy of `crawler/state/catalog.sqlite` (588,783 jobs).
- Endpoint: `GET /api/jobs?<query>&limit=50` on the viewer container.
- Timing: `curl -w '%{time_total}'`, 10 sequential requests per query after a
  warm-up request; median reported (min/max in parentheses).
- "Before" = current `main` viewer image (LIKE scans, empty FTS index).
- "After" = new code plus the one-time FTS rebuild migration, measured on the
  rebuilt real viewer on port 3000. The FTS index now has 588,783 rows.
- An earlier after-run on a copied catalog on port 3001 produced the same
  shape (0.260 s / 0.157 s / 0.262 s medians), so the result is not specific
  to one container.

## Results

| Query | Before (median) | After (median) | Speed-up |
| --- | --- | --- | --- |
| `title=engineer` | 2.11 s (1.87–4.55 s) | 0.227 s (0.205–1.26 s) | ~9x |
| `title=engineer&sources=greenhouse` | 0.51 s (0.48–0.57 s) | 0.155 s (0.151–0.160 s) | ~3.3x |
| `title=engineer&myLoc=New%20York&sources=greenhouse` | 0.54 s (0.52–0.73 s) | 0.283 s (0.267–0.310 s) | ~1.9x |

All three warm medians are under the ~300 ms target. The first request after a
viewer restart can take 0.5–1.3 s while SQLite pages and the FTS index are
loaded; steady state is shown above.

## Notes

- The catalog already had a `catalog_jobs_fts` table, but its index was empty
  (`catalog_jobs_fts_docsize` = 0). The migration now rebuilds it whenever the
  index row count differs from `catalog_jobs`, in rowid chunks so no single
  write lock covers the whole build. The rebuild still runs once at startup and
  delays readiness; it is not triggered by the first search.
- Text-only searches now count matches in FTS directly. A `COUNT(*)` over the
  content table by rowid cost ~0.5 s for ~73k matches; the FTS count avoids
  those random row lookups. Text plus other filters keeps the normal SQL count.
- Baseline numbers were taken with the same three queries; `title=engineer`
  was slowest because it had no provider filter to narrow the LIKE scan.
