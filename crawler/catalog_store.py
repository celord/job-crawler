"""SQLite persistence for crawled jobs.

Ported from crawler/src/catalog-store.ts. Synchronous by design (stdlib
sqlite3, matching viewer/backend/db.py's pattern) -- better-sqlite3 was
synchronous too, so no async wrapper is needed here.

Preserves two quirks verbatim, not "fixed" during the port:
  - raw_json is always the literal string "null", never populated with
    real API response data, despite the column existing.
  - first_seen_at is deliberately excluded from the ON CONFLICT UPDATE
    clause -- it's set once on INSERT only, as
    job.posted_at or job.fetched_at or now (a misleading name: it
    defaults to the job's *posted* date if known, not literally "when
    this crawler first saw it").
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from geo import resolve_coords
from location_classifier import classify_location
from models import NormalizedJob
from normalizers import canonicalize_employment_type, classify_tier
from remote import is_remote_location

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS catalog_jobs (
    provider TEXT NOT NULL,
    source_key TEXT NOT NULL,
    job_id TEXT NOT NULL,
    title TEXT,
    location TEXT,
    employment_type TEXT,
    compensation TEXT,
    department TEXT,
    office TEXT,
    language TEXT,
    updated_at TEXT,
    posted_at TEXT,
    job_url TEXT,
    fetched_at TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    seen_run_id TEXT NOT NULL,
    raw_json TEXT NOT NULL DEFAULT 'null',
    PRIMARY KEY (provider, source_key, job_id)
);
CREATE INDEX IF NOT EXISTS catalog_jobs_seen_run_id_idx ON catalog_jobs(seen_run_id);
CREATE INDEX IF NOT EXISTS catalog_jobs_last_seen_at_idx ON catalog_jobs(last_seen_at);
DROP INDEX IF EXISTS catalog_jobs_title_lower_idx;
DROP INDEX IF EXISTS catalog_jobs_location_lower_idx;
"""

_UPSERT_SQL = """
INSERT INTO catalog_jobs (
    provider, source_key, job_id, title, location, employment_type,
    compensation, department, office, language, updated_at, posted_at,
    job_url, fetched_at, first_seen_at, last_seen_at, seen_run_id,
    raw_json, skill_tier, employment_type_canonical, lat, lon, is_remote,
    region, country
) VALUES (
    :provider, :source_key, :job_id, :title, :location, :employment_type,
    :compensation, :department, :office, :language, :updated_at, :posted_at,
    :job_url, :fetched_at, :first_seen_at, :last_seen_at, :seen_run_id,
    :raw_json, :skill_tier, :employment_type_canonical, :lat, :lon, :is_remote,
    :region, :country
)
ON CONFLICT(provider, source_key, job_id) DO UPDATE SET
    title = excluded.title,
    location = excluded.location,
    employment_type = excluded.employment_type,
    compensation = excluded.compensation,
    department = excluded.department,
    office = excluded.office,
    language = excluded.language,
    updated_at = excluded.updated_at,
    posted_at = excluded.posted_at,
    job_url = excluded.job_url,
    fetched_at = excluded.fetched_at,
    last_seen_at = excluded.last_seen_at,
    seen_run_id = excluded.seen_run_id,
    skill_tier = excluded.skill_tier,
    employment_type_canonical = excluded.employment_type_canonical,
    lat = excluded.lat,
    lon = excluded.lon,
    is_remote = excluded.is_remote,
    region = excluded.region,
    country = excluded.country
"""

_SELECT_ALL_SQL = """
SELECT provider, source_key, job_id, title, location, employment_type,
       compensation, department, office, language, updated_at, posted_at,
       job_url, fetched_at, first_seen_at, last_seen_at
FROM catalog_jobs
ORDER BY provider, source_key, job_id
"""


class CatalogStore:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path)
        self.db.execute("PRAGMA busy_timeout = 30000")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.execute("PRAGMA synchronous = NORMAL")
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(_CREATE_TABLE_SQL)
        self.db.commit()

        self._ensure_column("catalog_jobs", "posted_at", "TEXT")
        self._ensure_column("catalog_jobs", "skill_tier", "TEXT")
        self._ensure_column("catalog_jobs", "employment_type_canonical", "TEXT")
        self._ensure_column("catalog_jobs", "lat", "REAL")
        self._ensure_column("catalog_jobs", "lon", "REAL")
        self._ensure_column("catalog_jobs", "is_remote", "INTEGER")
        self._ensure_column("catalog_jobs", "region", "TEXT")
        self._ensure_column("catalog_jobs", "country", "TEXT")

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        rows = self.db.execute(f"PRAGMA table_info({table})").fetchall()
        if any(row[1] == column for row in rows):
            return
        self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        self.db.commit()

    def record_jobs(self, jobs: list[NormalizedJob], run_id: str) -> None:
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        with self.db:
            for job in jobs:
                coords = resolve_coords(job.location)
                lat, lon = coords if coords is not None else (None, None)
                classification = classify_location(job.location)
                self.db.execute(
                    _UPSERT_SQL,
                    {
                        "provider": job.provider,
                        "source_key": job.source_key,
                        "job_id": job.job_id,
                        "title": job.title,
                        "location": job.location,
                        "employment_type": job.employment_type,
                        "compensation": job.compensation,
                        "department": job.department,
                        "office": job.office,
                        "language": job.language,
                        "updated_at": job.updated_at,
                        "posted_at": job.posted_at,
                        "job_url": job.job_url,
                        "fetched_at": job.fetched_at,
                        "first_seen_at": job.posted_at or job.fetched_at or now,
                        "last_seen_at": job.fetched_at or now,
                        "seen_run_id": run_id,
                        "raw_json": "null",
                        "skill_tier": job.skill_tier or classify_tier(job.title),
                        "employment_type_canonical": canonicalize_employment_type(job.employment_type),
                        "lat": lat,
                        "lon": lon,
                        "is_remote": is_remote_location(job.location),
                        "region": classification.region,
                        "country": classification.country,
                    },
                )

    def read_classification(self) -> list[dict]:
        """Every row's key, raw location text, and current classification."""
        cur = self.db.execute(
            "SELECT provider, source_key, job_id, location, region, country FROM catalog_jobs"
        )
        columns = [description[0] for description in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]

    def update_classification(self, updates: list[tuple[str, str, str, str | None, str | None]]) -> None:
        """Set (provider, source_key, job_id) rows' region/country, touching
        no other columns. Used by the one-off backfill script."""
        with self.db:
            self.db.executemany(
                "UPDATE catalog_jobs SET region = ?, country = ? "
                "WHERE provider = ? AND source_key = ? AND job_id = ?",
                [
                    (region, country, provider, source_key, job_id)
                    for provider, source_key, job_id, region, country in updates
                ],
            )

    def finalize_run(self, run_id: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM catalog_jobs WHERE seen_run_id <> ?", (run_id,))

    def close(self) -> None:
        self.db.close()
