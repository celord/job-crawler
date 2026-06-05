import Database from "better-sqlite3";
import type { JobRow } from "./types.js";
import { DB_PATH } from "./config.js";

export const db = new Database(DB_PATH, { fileMustExist: true });
db.pragma("busy_timeout = 30000");
db.pragma("journal_mode = WAL");

// Add analysis_score column if it doesn't exist yet (idempotent)
try {
  db.exec(`ALTER TABLE catalog_jobs ADD COLUMN analysis_score REAL DEFAULT NULL`);
} catch {
  // column already exists
}

// Add parsed_jd column if it doesn't exist yet (idempotent)
try {
  db.exec(`ALTER TABLE catalog_jobs ADD COLUMN parsed_jd TEXT DEFAULT NULL`);
} catch {
  // column already exists
}

// Add skill_tier column and backfill existing rows (idempotent)
try {
  db.exec(`ALTER TABLE catalog_jobs ADD COLUMN skill_tier TEXT DEFAULT NULL`);
} catch {
  // column already exists
}
// Backfill rows that have no tier yet using lightweight SQL heuristics.
// This avoids loading all titles into JS — runs once, fast.
db.exec(`
  UPDATE catalog_jobs SET skill_tier = CASE
    WHEN LOWER(title) GLOB '*intern*' THEN 'intern'
    WHEN LOWER(title) GLOB '*junior*' OR LOWER(title) GLOB '*jr.*'
      OR LOWER(title) GLOB '*entry level*' OR LOWER(title) GLOB '*entry-level*'
      OR LOWER(title) GLOB '*new grad*' OR LOWER(title) GLOB '*graduate*'
      OR LOWER(title) GLOB '*trainee*' OR LOWER(title) GLOB '*associate*' THEN 'entry'
    WHEN LOWER(title) GLOB '*senior*' OR LOWER(title) GLOB '*sr.*'
      OR LOWER(title) GLOB '*principal*' OR LOWER(title) GLOB '*staff *'
      OR LOWER(title) GLOB '*lead *' OR LOWER(title) GLOB '* lead'
      OR LOWER(title) GLOB '*director*' OR LOWER(title) GLOB '*vp *'
      OR LOWER(title) GLOB '*vice president*' OR LOWER(title) GLOB '*head of*'
      OR LOWER(title) GLOB '*manager*' OR LOWER(title) GLOB '*architect*' THEN 'senior'
    ELSE 'mid'
  END
  WHERE skill_tier IS NULL
`);

// Add employment_type_canonical column and backfill (idempotent)
try {
  db.exec(`ALTER TABLE catalog_jobs ADD COLUMN employment_type_canonical TEXT DEFAULT NULL`);
} catch {
  // column already exists
}
db.exec(`
  UPDATE catalog_jobs SET employment_type_canonical = CASE
    WHEN employment_type IS NULL OR TRIM(employment_type) = '' THEN NULL
    WHEN LOWER(employment_type) GLOB '*intern*' THEN 'Internship'
    WHEN LOWER(employment_type) GLOB '*volunteer*' THEN 'Volunteer'
    WHEN LOWER(employment_type) GLOB '*temp*' OR LOWER(employment_type) GLOB '*casual*' THEN 'Temporary'
    WHEN LOWER(employment_type) GLOB '*part*time*' OR LOWER(employment_type) = 'p/t' THEN 'Part-time'
    WHEN LOWER(employment_type) GLOB '*contract*' OR LOWER(employment_type) GLOB '*freelance*' THEN 'Contract'
    WHEN LOWER(employment_type) GLOB '*full*time*' OR LOWER(employment_type) GLOB '*permanent*'
      OR LOWER(employment_type) GLOB '*regular*' OR LOWER(employment_type) = 'cdi'
      OR LOWER(employment_type) = 'employee' THEN 'Full-time'
    ELSE NULL
  END
  WHERE employment_type_canonical IS NULL AND employment_type IS NOT NULL
`);

// FTS5 virtual table for fast title + location search
db.exec(`
  CREATE VIRTUAL TABLE IF NOT EXISTS catalog_jobs_fts USING fts5(
    title,
    location,
    content='catalog_jobs',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 1'
  );
`);

// Populate FTS index if empty (first boot or after manual rebuild)
{
  const ftsCount = (db.prepare("SELECT COUNT(*) as n FROM catalog_jobs_fts").get() as { n: number }).n;
  if (ftsCount === 0) {
    db.exec(`INSERT INTO catalog_jobs_fts(rowid, title, location) SELECT rowid, title, location FROM catalog_jobs`);
    console.log("[db] FTS5 index populated");
  }
}

// Triggers to keep FTS index in sync with catalog_jobs
db.exec(`
  CREATE TRIGGER IF NOT EXISTS catalog_jobs_fts_ai
  AFTER INSERT ON catalog_jobs BEGIN
    INSERT INTO catalog_jobs_fts(rowid, title, location) VALUES (new.rowid, new.title, new.location);
  END;

  CREATE TRIGGER IF NOT EXISTS catalog_jobs_fts_ad
  AFTER DELETE ON catalog_jobs BEGIN
    INSERT INTO catalog_jobs_fts(catalog_jobs_fts, rowid, title, location) VALUES ('delete', old.rowid, old.title, old.location);
  END;

  CREATE TRIGGER IF NOT EXISTS catalog_jobs_fts_au
  AFTER UPDATE ON catalog_jobs BEGIN
    INSERT INTO catalog_jobs_fts(catalog_jobs_fts, rowid, title, location) VALUES ('delete', old.rowid, old.title, old.location);
    INSERT INTO catalog_jobs_fts(rowid, title, location) VALUES (new.rowid, new.title, new.location);
  END;
`);

export const selectJobStatement = db.prepare(
  `SELECT provider, source_key, job_id, title, location, employment_type,
          compensation, department, job_url, updated_at, posted_at, first_seen_at, last_seen_at, parsed_jd
   FROM catalog_jobs
   WHERE provider = ? AND source_key = ? AND job_id = ?`,
);

export const updateParsedMetadataStatement = db.prepare(
  `UPDATE catalog_jobs
   SET
     location = CASE
       WHEN @location IS NOT NULL AND TRIM(@location) <> '' AND (location IS NULL OR TRIM(location) = '') THEN @location
       ELSE location
     END,
     compensation = CASE
       WHEN @compensation IS NOT NULL AND TRIM(@compensation) <> '' THEN @compensation
       ELSE compensation
     END,
     parsed_jd = CASE
       WHEN parsed_jd IS NULL AND @parsed_jd IS NOT NULL THEN @parsed_jd
       ELSE parsed_jd
     END
   WHERE provider = @provider AND source_key = @source_key AND job_id = @job_id`,
);

export const updateAnalysisScoreStatement = db.prepare(
  `UPDATE catalog_jobs SET analysis_score = @score
   WHERE provider = @provider AND source_key = @source_key AND job_id = @job_id`,
);

export const backfillAnalysisScoresStatement = db.prepare(
  `UPDATE catalog_jobs SET analysis_score = @score
   WHERE provider = @provider AND source_key = @source_key AND job_id = @job_id
     AND (analysis_score IS NULL OR analysis_score = 0)`,
);

export const selectJobUrlStatement = db.prepare(
  `SELECT job_url FROM catalog_jobs WHERE provider=? AND source_key=? AND job_id=?`,
);

export function jobCacheKey(job: Pick<JobRow, "provider" | "source_key" | "job_id">): string {
  return `${job.provider}|${job.source_key}|${job.job_id}`;
}

export function isRealCompensation(value: unknown): boolean {
  const text = String(value ?? "").trim();
  if (!text) return false;
  if (/^(req|r|jr|job)[-_]?\d+[a-z0-9-]*$/i.test(text)) return false;
  if (/^\/?job\//i.test(text)) return false;
  if (!/(salary|compensation|base pay|pay range|ote|equity|bonus|hour|annual|year|yr|[$€£]|\b\d{2,3}\s?k\b|\b\d{2,3}[,\s]\d{3}\b)/i.test(text)) {
    return false;
  }
  return /[$€£]\s?\d|\b\d{2,3}\s?k\b|\b\d{2,3}[,\s]\d{3}\b|\b\d+\s?-\s?\d+\b/i.test(text);
}

export function sanitizeJob(row: JobRow): JobRow {
  return {
    ...row,
    compensation: isRealCompensation(row.compensation) ? row.compensation : null,
  };
}
