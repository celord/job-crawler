"""One-off backfill for tickets/03: re-classify existing catalog_jobs rows
into the new region/country columns.

Run through the crawler image (repo Docker policy), against a *copy* of the
catalog first:

    docker run --rm -v "$(pwd)/crawler:/app" -w /app \
        -v /path/to/scratch:/scratch job-crawler-crawler \
        python backfill_region_country.py --catalog-db /scratch/catalog.sqlite

CatalogStore.__init__ runs the idempotent column migration (ALTER TABLE ADD
COLUMN region/country on an existing catalog, no data loss), then this
script UPDATEs only region and country for every row whose classification
changed. Re-running is a no-op: the classifier is deterministic and rows
already carrying the right values are skipped.
"""

import argparse
import json
from collections import Counter

from catalog_store import CatalogStore
from location_classifier import classify_location


def run_backfill(db_path: str) -> dict:
    store = CatalogStore(db_path)
    try:
        rows = store.read_classification()
        before = Counter(row["region"] or "NULL" for row in rows)

        updates = []
        for row in rows:
            classification = classify_location(row["location"])
            if (row["region"], row["country"]) != (classification.region, classification.country):
                updates.append(
                    (
                        row["provider"],
                        row["source_key"],
                        row["job_id"],
                        classification.region,
                        classification.country,
                    )
                )
        store.update_classification(updates)

        after = Counter(row["region"] or "NULL" for row in store.read_classification())
    finally:
        store.close()

    return {"rows": len(rows), "changed": len(updates), "before": dict(before), "after": dict(after)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="backfill-region-country",
        description="Re-classify catalog_jobs region/country (tickets/03)",
    )
    parser.add_argument(
        "--catalog-db",
        dest="catalog_db",
        default="/app/state/catalog.sqlite",
        help="SQLite catalog state file (use a copy, not the live catalog)",
    )
    args = parser.parse_args(argv)

    report = run_backfill(args.catalog_db)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
