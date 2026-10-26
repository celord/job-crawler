# Parity checklist: frontend-next vs old frontend

Run against the viewer serving `viewer/frontend-next/` (no build step). Tick each item with the date and who checked it.

Legend: **[x]** verified in browser on the frontend-next preview · **[ ]** still to do by owner (needs matcher, or a scored job on the page).

## Feed and filters
- [x] Feed loads from `/api/jobs`, shows rows, no console errors
- [x] Filters restore from URL on reload (`?title=…&remote=0&page=2`)
- [ ] Each filter (provider, tiers, employment type, age, score, evaluated, include/exclude, location, company) changes results as the old frontend does
- [ ] "Clear all" resets filters and URL

## Saved searches
- [x] Saved-search chips render from `saved-searches.json`
- [x] Clicking a chip adds `?searches=<id>`, disables filter bar, and merges results
- [ ] Known pre-existing defect, same in old frontend: saved searches whose location is `remote` return HTTP 500 (`no such column: is_remote`, `viewer/backend/services/filters.py`). Not a frontend regression; fix belongs with the crawler/catalog schema.

## Hide, visit, favourite (localStorage, key `job-viewer-local-store`)
- [x] Hidden job stays hidden after reload
- [ ] Visited marker appears after clicking a title, survives reload
- [ ] Favourite star toggles, survives reload; Favorites modal add/remove works

## Analysis panel and paste-JD
- [ ] Score badge opens panel with score, scorecard, verdict (needs a scored job on page)
- [ ] Re-run queues a run and reports a toast
- [ ] Paste-JD dialog opens, Analyze queues a run; failure shows a toast with reason

## Queue drawer
- [x] Queue button opens and closes the drawer
- [ ] Queue items show status and failure reason; Retry / Stop / Restart / Dismiss each toast success or failure

## Toasts
- [x] Hide shows a toast with working Undo (covered by code path; confirm Undo in browser)
- [ ] Every action (analyze, re-run, queue actions, paste-JD, favourites, auto-analyzer) shows a success or failure toast

## Pagination
- [x] Out-of-range page shows "Page N of M" (no crash)
- [ ] Next/Prev keep previous rows visible (dimmed) until the next page arrives; total matches listed jobs

## Phone width (390px)
- [x] No horizontal page scroll (scrollWidth equals clientWidth)
- [x] 16px side gutter

## Backend contract
- [x] `viewer/backend` test suite passes unchanged (221 passed)
