# Parity checklist: frontend-next vs old frontend

Run against the viewer serving `viewer/frontend-next/` (no build step). Tick each item with the date and who checked it.

Legend: **[x]** verified in browser on the frontend-next preview · **[ ]** still to do by owner (needs matcher, or a scored job on the page).

## Feed and filters
- [x] Feed loads from `/api/jobs`, shows rows, no console errors
- [x] Filters restore from URL on reload (`?title=…&remote=0&page=2`)
- [ ] Each filter (provider, tiers, employment type, age, score, evaluated, include/exclude, location, company) changes results as the old frontend does — 2026-10-29: verified in UI: provider, title, company, location (`myLoc`), tier (intern), employment (full time), include keyword (`inc`). Verified against `/api/jobs` totals only (no UI): age (`days=7`: 230,837 of 588,783), exclude keyword (`exc=senior`: 371,616). Not verifiable: score (4plus) and evaluated return 0 because the catalog has no scored jobs (`analysis_score` and `job_analyses` both empty). Not compared against the old frontend.
- [x] "Clear all" resets filters and URL — 2026-10-29

## Saved searches
- [x] Saved-search chips render from `saved-searches.json`
- [x] Clicking a chip adds `?searches=<id>`, disables filter bar, and merges results
- [ ] Known pre-existing defect, same in old frontend: saved searches whose location is `remote` return HTTP 500 (`no such column: is_remote`, `viewer/backend/services/filters.py`). Not a frontend regression; fix belongs with the crawler/catalog schema.

## Hide, visit, favourite (localStorage, key `job-viewer-local-store`)
- [x] Hidden job stays hidden after reload
- [ ] Visited marker appears after clicking a title, survives reload — 2026-10-29: marker (`job-card-visited`) appears and is stored; visual state after reload not checked.
- [ ] Favourite star toggles, survives reload; Favorites modal add/remove works — 2026-10-29: star toggles, toast shows, persists after reload; modal opens, add and remove both update storage. Verified.

## Analysis panel and paste-JD
- [ ] Score badge opens panel with score, scorecard, verdict (needs a scored job on page) — 2026-10-29: blocked. No scored jobs in catalog. Panel opens from a queue item and shows the "Not yet analyzed" state; score and verdict not reached.
- [ ] Re-run queues a run and reports a toast — 2026-10-29: not tested (queues a real run).
- [ ] Paste-JD dialog opens, Analyze queues a run; failure shows a toast with reason — 2026-10-29: blocked. Paste JD only appears for unparsed jobs. Reaching one's panel needs Analyze (queues a run). Not tested.

## Queue drawer
- [x] Queue button opens and closes the drawer
- [ ] Queue items show status and failure reason; Retry / Stop / Restart / Dismiss each toast success or failure — 2026-10-29: status and failure reason display verified. 2026-10-29 (cost-limited pass): Dismiss verified, removes the row and shows a success toast ("Dismissed …"). Stop not testable: the button only shows on `running` items, and none were running. Retry and Restart not tested (each starts a matcher run, which calls the LLM). Queue has hundreds of auto-analyzer `permanent_error` items, which makes the drawer noisy.

## Toasts
- [x] Hide shows a toast with working Undo (covered by code path; confirm Undo in browser) — 2026-10-29: Hide and favourite toasts shown; Undo button not clicked.
- [ ] Every action (analyze, re-run, queue actions, paste-JD, favourites, auto-analyzer) shows a success or failure toast — 2026-10-29: favourite, hide, analyze ("Analysis queued.") and dismiss toasts seen. Retry, Restart, Stop, and paste-JD submit not tested (they start LLM runs or were not reachable).

## Pagination
- [x] Out-of-range page shows "Page N of M" (no crash)
- [ ] Next/Prev keep previous rows visible (dimmed) until the next page arrives; total matches listed jobs — 2026-10-29: Next keeps rows visible, dims them while loading, no empty flash (Page 1→2 of 4). Total-vs-listed count not checked.

## Phone width (390px)
- [x] No horizontal page scroll (scrollWidth equals clientWidth)
- [x] 16px side gutter

## Backend contract
- [x] `viewer/backend` test suite passes unchanged (221 passed)
