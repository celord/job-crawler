import { appendFileSync } from "node:fs";
import { join } from "node:path";
import Database from "better-sqlite3";

type TrendEntry = {
  date: string;
  total: number;
  by_provider: Record<string, number>;
  by_tier: Record<string, number>;
};

export function appendTrendEntry(dbPath: string, stateDir: string): void {
  const db = new Database(dbPath, { readonly: true });
  try {
    const total = (db.prepare("SELECT COUNT(*) as n FROM catalog_jobs").get() as { n: number }).n;

    const providerRows = db
      .prepare("SELECT provider, COUNT(*) as n FROM catalog_jobs GROUP BY provider")
      .all() as Array<{ provider: string; n: number }>;
    const by_provider: Record<string, number> = {};
    for (const row of providerRows) by_provider[row.provider] = row.n;

    const tierRows = db
      .prepare("SELECT skill_tier, COUNT(*) as n FROM catalog_jobs WHERE skill_tier IS NOT NULL GROUP BY skill_tier")
      .all() as Array<{ skill_tier: string; n: number }>;
    const by_tier: Record<string, number> = {};
    for (const row of tierRows) by_tier[row.skill_tier] = row.n;

    const entry: TrendEntry = {
      date: new Date().toISOString().slice(0, 10),
      total,
      by_provider,
      by_tier,
    };

    const line = `${JSON.stringify(entry)}\n`;
    appendFileSync(join(stateDir, "trends.jsonl"), line, "utf8");
  } finally {
    db.close();
  }
}
