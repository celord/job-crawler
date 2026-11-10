import sqlite3

from backfill_region_country import run_backfill

_OLD_SCHEMA = """
CREATE TABLE catalog_jobs (
    provider TEXT NOT NULL,
    source_key TEXT NOT NULL,
    job_id TEXT NOT NULL,
    title TEXT,
    location TEXT,
    fetched_at TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    seen_run_id TEXT NOT NULL,
    raw_json TEXT NOT NULL DEFAULT 'null',
    is_remote INTEGER,
    lat REAL,
    lon REAL,
    PRIMARY KEY (provider, source_key, job_id)
)
"""


def _old_catalog(tmp_path, rows):
    db_path = str(tmp_path / "catalog.sqlite")
    conn = sqlite3.connect(db_path)
    conn.execute(_OLD_SCHEMA)
    for provider, source_key, job_id, title, location, is_remote in rows:
        conn.execute(
            "INSERT INTO catalog_jobs (provider, source_key, job_id, title, location,"
            " fetched_at, first_seen_at, last_seen_at, seen_run_id, is_remote)"
            " VALUES (?, ?, ?, ?, ?, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z',"
            " '2026-01-01T00:00:00Z', 'run0', ?)",
            (provider, source_key, job_id, title, location, is_remote),
        )
    conn.commit()
    conn.close()
    return db_path


def _snapshot(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "SELECT provider, source_key, job_id, title, location, fetched_at, first_seen_at,"
        " last_seen_at, seen_run_id, raw_json, is_remote, lat, lon FROM catalog_jobs"
        " ORDER BY provider, source_key, job_id"
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def _region_country(db_path):
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT job_id, region, country FROM catalog_jobs ORDER BY job_id").fetchall()
    conn.close()
    return rows


def test_backfill_classifies_rows_and_touches_only_region_country(tmp_path):
    db_path = _old_catalog(
        tmp_path,
        [
            ("greenhouse", "acme", "1", "CR job", "Remote - Costa Rica", 1),
            ("greenhouse", "acme", "2", "US job", "Remote - US", 1),
            ("greenhouse", "acme", "3", "Plain remote", "Remote", 1),
            ("icims", "corp", "9", "No location", None, None),
        ],
    )
    before = _snapshot(db_path)

    report = run_backfill(db_path)

    assert _snapshot(db_path) == before  # nothing but region/country changed
    assert _region_country(db_path) == [
        ("1", "latam", "CR"),
        ("2", "north_america", "US"),
        ("3", "remote_unspecified", None),
        ("9", "unknown", None),
    ]
    assert report["rows"] == 4
    assert report["changed"] == 4
    assert report["after"] == {
        "latam": 1,
        "north_america": 1,
        "remote_unspecified": 1,
        "unknown": 1,
    }


def test_backfill_is_idempotent(tmp_path):
    db_path = _old_catalog(
        tmp_path,
        [("greenhouse", "acme", "1", "CR job", "Remote - Costa Rica", 1)],
    )

    first = run_backfill(db_path)
    after_first = _region_country(db_path)
    second = run_backfill(db_path)

    assert first["changed"] == 1
    assert second["changed"] == 0
    assert _region_country(db_path) == after_first


def test_backfill_reports_before_counts(tmp_path):
    db_path = _old_catalog(
        tmp_path,
        [
            ("greenhouse", "acme", "1", "CR job", "Remote - Costa Rica", 1),
            ("greenhouse", "acme", "2", "US job", "Remote - US", 1),
        ],
    )

    report = run_backfill(db_path)

    assert report["before"] == {"NULL": 2}
    assert report["after"] == {"latam": 1, "north_america": 1}
