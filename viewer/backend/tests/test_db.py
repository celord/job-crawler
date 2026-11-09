import sqlite3

import pytest

import db as db_module
from db import execute, executemany, fetchall, fetchone, run_migrations
from tests.conftest import insert_job


@pytest.fixture(autouse=True)
def _env(isolated_env):
    return isolated_env


async def test_fetchall_and_fetchone(isolated_env):
    insert_job(isolated_env["catalog_db"], job_id="1", title="A")
    insert_job(isolated_env["catalog_db"], job_id="2", title="B")

    rows = await fetchall("SELECT title FROM catalog_jobs ORDER BY title")
    assert [r["title"] for r in rows] == ["A", "B"]

    row = await fetchone("SELECT title FROM catalog_jobs WHERE job_id = ?", ("1",))
    assert row["title"] == "A"

    missing = await fetchone("SELECT title FROM catalog_jobs WHERE job_id = ?", ("nope",))
    assert missing is None


async def test_execute_and_executemany(isolated_env):
    await execute(
        "INSERT INTO catalog_jobs (provider, source_key, job_id, title) VALUES (?, ?, ?, ?)",
        ("gh", "acme", "1", "Solo"),
    )
    await executemany(
        "INSERT INTO catalog_jobs (provider, source_key, job_id, title) VALUES (?, ?, ?, ?)",
        [("gh", "acme", "2", "Batch1"), ("gh", "acme", "3", "Batch2")],
    )
    rows = await fetchall("SELECT title FROM catalog_jobs ORDER BY job_id")
    assert [r["title"] for r in rows] == ["Solo", "Batch1", "Batch2"]


async def test_run_migrations_adds_columns_and_tables(isolated_env):
    await run_migrations()
    # analysis_score / parsed_jd columns should now exist without error
    await execute("UPDATE catalog_jobs SET analysis_score = 4.0, parsed_jd = '{}' WHERE 1=0")
    tables = await fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")
    names = {t["name"] for t in tables}
    assert "job_analyses" in names
    assert "catalog_jobs_fts" in names  # title search uses the FTS5 index
    triggers = await fetchall("SELECT name FROM sqlite_master WHERE type = 'trigger'")
    trigger_names = {t["name"] for t in triggers}
    assert {"catalog_jobs_fts_ai", "catalog_jobs_fts_ad", "catalog_jobs_fts_au"} <= trigger_names


async def test_run_migrations_rebuilds_fts_from_existing_rows(isolated_env):
    insert_job(isolated_env["catalog_db"], job_id="1", title="Backend Engineer")
    await run_migrations()

    rows = await fetchall(
        "SELECT rowid FROM catalog_jobs_fts WHERE catalog_jobs_fts MATCH ?", ('"engineer"*',)
    )
    assert len(rows) == 1


async def test_run_migrations_rebuilds_partially_populated_fts_index(isolated_env):
    for i in range(3):
        insert_job(isolated_env["catalog_db"], job_id=str(i), title=f"Engineer {i}")
    await run_migrations()

    missing = await fetchone("SELECT rowid AS row_id, title FROM catalog_jobs WHERE job_id = '1'")
    await execute(
        "INSERT INTO catalog_jobs_fts(catalog_jobs_fts, rowid, title) VALUES ('delete', ?, ?)",
        (missing["row_id"], missing["title"]),
    )
    indexed = await fetchone("SELECT COUNT(*) AS n FROM catalog_jobs_fts_docsize")
    assert indexed["n"] == 2

    await run_migrations()

    rows = await fetchall(
        "SELECT rowid FROM catalog_jobs_fts WHERE catalog_jobs_fts MATCH ?", ('"engineer"*',)
    )
    assert len(rows) == 3


async def test_fts_update_trigger_skips_unchanged_title_upserts(migrated_env):
    insert_job(migrated_env["catalog_db"], job_id="1", title="Backend Engineer")
    before = await fetchall("SELECT id, block FROM catalog_jobs_fts_data ORDER BY id")

    await execute(
        "UPDATE catalog_jobs SET title = 'Backend Engineer', location = 'Elsewhere' WHERE job_id = '1'"
    )

    after = await fetchall("SELECT id, block FROM catalog_jobs_fts_data ORDER BY id")
    assert after == before


async def test_fts_triggers_keep_index_current(migrated_env):
    await execute(
        "INSERT INTO catalog_jobs (provider, source_key, job_id, title) VALUES (?, ?, ?, ?)",
        ("gh", "acme", "1", "Backend Engineer"),
    )
    rows = await fetchall(
        "SELECT rowid FROM catalog_jobs_fts WHERE catalog_jobs_fts MATCH ?", ('"engineer"*',)
    )
    assert len(rows) == 1

    await execute("UPDATE catalog_jobs SET title = 'Data Scientist' WHERE job_id = '1'")
    assert (
        await fetchall("SELECT rowid FROM catalog_jobs_fts WHERE catalog_jobs_fts MATCH ?", ('"engineer"*',))
        == []
    )
    assert (
        len(
            await fetchall(
                "SELECT rowid FROM catalog_jobs_fts WHERE catalog_jobs_fts MATCH ?",
                ('"scientist"*',),
            )
        )
        == 1
    )

    await execute("DELETE FROM catalog_jobs WHERE job_id = '1'")
    assert (
        await fetchall("SELECT rowid FROM catalog_jobs_fts WHERE catalog_jobs_fts MATCH ?", ('"scientist"*',))
        == []
    )


async def test_rebuild_fts_uses_one_atomic_rebuild_statement(isolated_env, monkeypatch):
    statements = []

    async def record_execute(sql, params=()):
        statements.append(sql)

    async def no_chunked_reads(*args, **kwargs):
        raise AssertionError("chunked rebuild must not read catalog rows in batches")

    async def no_chunked_writes(*args, **kwargs):
        raise AssertionError("chunked rebuild must not insert catalog rows in batches")

    monkeypatch.setattr(db_module, "execute", record_execute)
    monkeypatch.setattr(db_module, "fetchall", no_chunked_reads)
    monkeypatch.setattr(db_module, "executemany", no_chunked_writes)

    await db_module._rebuild_fts()

    assert len(statements) == 1
    assert "catalog_jobs_fts(catalog_jobs_fts) VALUES('rebuild')" in statements[0]


async def test_rebuild_fts_is_atomic_and_keeps_the_index_usable(isolated_env):
    row_count = 5001  # more than one chunk in the previous chunked rebuild
    conn = sqlite3.connect(isolated_env["catalog_db"])
    conn.executemany(
        "INSERT INTO catalog_jobs (provider, source_key, job_id, title) VALUES (?, ?, ?, ?)",
        [("gh", "acme", str(i), f"Engineer {i}") for i in range(row_count)],
    )
    conn.commit()
    conn.close()

    await run_migrations()

    docsize = await fetchone("SELECT COUNT(*) AS n FROM catalog_jobs_fts_docsize")
    catalog = await fetchone("SELECT COUNT(*) AS n FROM catalog_jobs")
    assert docsize["n"] == catalog["n"] == row_count

    matches = await fetchall(
        "SELECT rowid FROM catalog_jobs_fts WHERE catalog_jobs_fts MATCH ?", ('"engineer"*',)
    )
    assert len(matches) == row_count

    # A trigger delete immediately after the rebuild must not hit a corrupt
    # external-content index.
    await execute("DELETE FROM catalog_jobs WHERE job_id = '0'")
    remaining = await fetchone("SELECT COUNT(*) AS n FROM catalog_jobs")
    assert remaining["n"] == row_count - 1


async def test_fts_needs_rebuild_reads_both_counts_in_one_statement(isolated_env, monkeypatch):
    calls = []

    async def fake_fetchone(sql, params=()):
        calls.append(sql)
        return {"indexed": 2, "catalog": 3}

    monkeypatch.setattr(db_module, "fetchone", fake_fetchone)

    assert await db_module._fts_needs_rebuild() is True
    assert len(calls) == 1
    assert calls[0].lower().count("count(*)") == 2

    calls.clear()

    async def equal_fetchone(sql, params=()):
        calls.append(sql)
        return {"indexed": 3, "catalog": 3}

    monkeypatch.setattr(db_module, "fetchone", equal_fetchone)

    assert await db_module._fts_needs_rebuild() is False
    assert len(calls) == 1


async def test_run_migrations_replaces_update_trigger_without_when_clause(migrated_env):
    await execute("DROP TRIGGER catalog_jobs_fts_au")
    await execute(
        "CREATE TRIGGER catalog_jobs_fts_au AFTER UPDATE OF title ON catalog_jobs BEGIN "
        "INSERT INTO catalog_jobs_fts(catalog_jobs_fts, rowid, title) "
        "VALUES ('delete', old.rowid, old.title); "
        "INSERT INTO catalog_jobs_fts(rowid, title) VALUES (new.rowid, new.title); END"
    )
    old_trigger = await fetchone(
        "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = 'catalog_jobs_fts_au'"
    )
    assert "WHEN old.title IS NOT new.title" not in old_trigger["sql"]

    await run_migrations()

    new_trigger = await fetchone(
        "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = 'catalog_jobs_fts_au'"
    )
    assert "WHEN old.title IS NOT new.title" in new_trigger["sql"]


async def test_run_migrations_is_idempotent(isolated_env):
    await run_migrations()
    await run_migrations()  # must not raise on the second pass (duplicate column etc.)
    rows = await fetchall("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'job_analyses'")
    assert len(rows) == 1


async def test_run_migrations_against_real_crawler_schema(isolated_env):
    """The crawler's actual catalog_jobs schema has a composite primary key
    (provider, source_key, job_id) and no explicit "id" column — unlike this
    test suite's own fixture schema. Migrations must work against that real
    shape too, and must leave the catalog rows intact."""
    import sqlite3

    conn = sqlite3.connect(isolated_env["catalog_db"])
    conn.executescript(
        "DROP TABLE catalog_jobs;"
        "CREATE TABLE catalog_jobs ("
        "  provider TEXT NOT NULL, source_key TEXT NOT NULL, job_id TEXT NOT NULL,"
        "  title TEXT, location TEXT, employment_type TEXT, compensation TEXT,"
        "  department TEXT, office TEXT, language TEXT, updated_at TEXT, posted_at TEXT,"
        "  job_url TEXT, fetched_at TEXT NOT NULL, first_seen_at TEXT NOT NULL,"
        "  last_seen_at TEXT NOT NULL, seen_run_id TEXT NOT NULL,"
        "  raw_json TEXT NOT NULL DEFAULT 'null',"
        "  PRIMARY KEY (provider, source_key, job_id)"
        ");"
    )
    conn.execute(
        "INSERT INTO catalog_jobs (provider, source_key, job_id, title, fetched_at, "
        "first_seen_at, last_seen_at, seen_run_id) "
        "VALUES ('gh', 'acme', '1', 'Backend Engineer', 'x', 'x', 'x', 'run1')"
    )
    conn.commit()
    conn.close()

    await run_migrations()  # must not raise sqlite3.OperationalError: no such column: T.id

    row = await fetchone("SELECT COUNT(*) AS n FROM catalog_jobs")
    assert row["n"] == 1
