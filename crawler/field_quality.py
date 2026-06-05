#!/usr/bin/env python3
"""
Field quality diagnostic — shows presence and usability % for each column
in catalog.sqlite.

Usage:
  python field_quality.py [path/to/catalog.sqlite]

Default DB path: crawler/state/catalog.sqlite (relative to this script's dir).
"""
import sys
import sqlite3
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent / "state" / "catalog.sqlite"

FIELDS = [
    "title",
    "location",
    "employment_type",
    "department",
    "office",
    "job_url",
    "posted_at",
    "compensation",
    "skill_tier",
    "language",
]

GARBAGE = {
    "n/a", "na", "not specified", "not available", "tbd", "various",
    "multiple locations", "see job description", "competitive",
    "salary range", "doe", "negotiable", "null", "none", "-", "",
}

TIERS = [
    (85, "reliable"),
    (50, "partial"),
    (20, "sparse"),
    (0,  "unreliable"),
]

def tier(pct: float) -> str:
    for threshold, label in TIERS:
        if pct >= threshold:
            return label
    return "unreliable"

def run(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    total = conn.execute("SELECT COUNT(*) FROM catalog_jobs").fetchone()[0]
    if total == 0:
        print("DB is empty.")
        return

    print(f"\nDB: {db_path}")
    print(f"Total rows: {total:,}\n")

    col_w = 20
    print(f"{'Field':<{col_w}} {'Present':>9} {'Usable':>9} {'Tier':<12}  Garbage examples")
    print("-" * 80)

    for field in FIELDS:
        # Check if column exists
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info(catalog_jobs)").fetchall()]
        if field not in cols:
            print(f"{'  ' + field:<{col_w}} {'(column missing)':>9}")
            continue

        present = conn.execute(
            f"SELECT COUNT(*) FROM catalog_jobs WHERE {field} IS NOT NULL AND TRIM({field}) != ''"
        ).fetchone()[0]

        # Usable = present AND not a garbage value (case-insensitive)
        usable = conn.execute(
            f"SELECT COUNT(*) FROM catalog_jobs WHERE {field} IS NOT NULL AND TRIM({field}) != '' "
            f"AND LOWER(TRIM({field})) NOT IN ({','.join('?' for _ in GARBAGE)})",
            list(GARBAGE)
        ).fetchone()[0]

        present_pct = 100 * present / total
        usable_pct  = 100 * usable  / total
        t = tier(usable_pct)

        # Sample a few garbage values if any
        garbage_count = present - usable
        garbage_sample = ""
        if garbage_count > 0:
            samples = conn.execute(
                f"SELECT DISTINCT LOWER(TRIM({field})) FROM catalog_jobs "
                f"WHERE {field} IS NOT NULL AND TRIM({field}) != '' "
                f"AND LOWER(TRIM({field})) IN ({','.join('?' for _ in GARBAGE)}) LIMIT 3",
                list(GARBAGE)
            ).fetchall()
            garbage_sample = ", ".join(f'"{r[0]}"' for r in samples)

        print(f"{field:<{col_w}} {present_pct:>8.1f}% {usable_pct:>8.1f}%  {t:<12}  {garbage_sample}")

    conn.close()
    print()

if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else str(DEFAULT_DB)
    if not Path(db).exists():
        print(f"❌ DB not found: {db}")
        sys.exit(1)
    run(db)
