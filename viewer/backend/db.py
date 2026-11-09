import asyncio
import sqlite3
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any, ParamSpec, TypeVar

import config

P = ParamSpec("P")
T = TypeVar("T")

SqlParams = Sequence[Any]
RowDict = dict[str, Any]
EXECUTOR = ThreadPoolExecutor(max_workers=4)


def _row_to_dict(row: sqlite3.Row) -> RowDict:
    return dict(row)


async def fetchall(sql: str, params: SqlParams = ()) -> list[RowDict]:
    def _work(conn: sqlite3.Connection) -> list[RowDict]:
        cur = conn.execute(sql, tuple(params))
        rows = cur.fetchall()
        return [_row_to_dict(row) for row in rows]

    return await _run_db(_with_conn, _work)


async def fetchone(sql: str, params: SqlParams = ()) -> RowDict | None:
    def _work(conn: sqlite3.Connection) -> RowDict | None:
        cur = conn.execute(sql, tuple(params))
        row = cur.fetchone()
        return _row_to_dict(row) if row is not None else None

    return await _run_db(_with_conn, _work)


async def execute(sql: str, params: SqlParams = ()) -> None:
    def _work(conn: sqlite3.Connection) -> None:
        conn.execute(sql, tuple(params))
        conn.commit()

    await _run_db(_with_conn, _work)


async def executemany(sql: str, params_list: Iterable[SqlParams]) -> None:
    batch = [tuple(p) for p in params_list]

    def _work(conn: sqlite3.Connection) -> None:
        conn.executemany(sql, batch)
        conn.commit()

    await _run_db(_with_conn, _work)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.CATALOG_DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL lets the crawler write and this service read concurrently without
    # "database is locked" errors (readers no longer block on the writer).
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


async def _run_db[**P, T](fn: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(EXECUTOR, lambda: fn(*args, **kwargs))


def _with_conn[T](work_fn: Callable[[sqlite3.Connection], T]) -> T:
    conn = _connect()
    try:
        return work_fn(conn)
    finally:
        conn.close()


async def _add_column_if_missing(column: str, sql_type: str) -> None:
    try:
        await execute(f"ALTER TABLE catalog_jobs ADD COLUMN {column} {sql_type}")
    except sqlite3.OperationalError as exc:
        if "duplicate column" not in str(exc).lower():
            raise


_FTS_STATEMENTS = [
    "CREATE VIRTUAL TABLE IF NOT EXISTS catalog_jobs_fts USING fts5("
    "title, content=catalog_jobs, content_rowid=rowid)",
    "CREATE TRIGGER IF NOT EXISTS catalog_jobs_fts_ai AFTER INSERT ON catalog_jobs BEGIN "
    "INSERT INTO catalog_jobs_fts(rowid, title) VALUES (new.rowid, new.title); END",
    "CREATE TRIGGER IF NOT EXISTS catalog_jobs_fts_ad AFTER DELETE ON catalog_jobs BEGIN "
    "INSERT INTO catalog_jobs_fts(catalog_jobs_fts, rowid, title) "
    "VALUES ('delete', old.rowid, old.title); END",
    "CREATE TRIGGER IF NOT EXISTS catalog_jobs_fts_au AFTER UPDATE OF title ON catalog_jobs "
    "WHEN old.title IS NOT new.title BEGIN "
    "INSERT INTO catalog_jobs_fts(catalog_jobs_fts, rowid, title) "
    "VALUES ('delete', old.rowid, old.title); "
    "INSERT INTO catalog_jobs_fts(rowid, title) VALUES (new.rowid, new.title); END",
]

_FTS_REBUILD_CHUNK = 5000


async def _fts_needs_rebuild() -> bool:
    indexed = await fetchone("SELECT COUNT(*) AS n FROM catalog_jobs_fts_docsize")
    rows = await fetchone("SELECT COUNT(*) AS n FROM catalog_jobs")
    return bool(indexed and rows and indexed["n"] != rows["n"])


async def _rebuild_fts() -> None:
    # Rebuild in rowid chunks so no single write transaction holds the lock
    # for the whole catalog. Startup still waits for the rebuild (readiness
    # includes it), but the first search does not trigger it.
    await execute("INSERT INTO catalog_jobs_fts(catalog_jobs_fts) VALUES('delete-all')")
    last_rowid = 0
    while True:
        rows = await fetchall(
            "SELECT rowid AS rowid, title FROM catalog_jobs WHERE rowid > ? ORDER BY rowid LIMIT ?",
            (last_rowid, _FTS_REBUILD_CHUNK),
        )
        if not rows:
            break
        await executemany(
            "INSERT INTO catalog_jobs_fts(rowid, title) VALUES (?, ?)",
            [(row["rowid"], row["title"]) for row in rows],
        )
        last_rowid = rows[-1]["rowid"]


async def run_migrations() -> None:
    await _add_column_if_missing("analysis_score", "REAL")
    await _add_column_if_missing("parsed_jd", "TEXT")
    await execute(
        "CREATE TABLE IF NOT EXISTS job_analyses ("
        "job_key TEXT NOT NULL, "
        "pipeline TEXT NOT NULL, "
        "analysis TEXT NOT NULL, "
        "analyzed_at TEXT NOT NULL, "
        "run_id TEXT NOT NULL, "
        "PRIMARY KEY (job_key, pipeline))"
    )
    for statement in _FTS_STATEMENTS:
        await execute(statement)
    # The catalog may predate the FTS index (or have been left partially
    # indexed by an interrupted rebuild / older viewer), so rebuild whenever
    # the index row count differs from the catalog row count.
    if await _fts_needs_rebuild():
        await _rebuild_fts()
