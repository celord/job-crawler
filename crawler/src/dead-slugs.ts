import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

type DeadEntry = { deadAt: string; code: number; ttlDays: number };
type DeadMap = Record<string, DeadEntry>;

// Statuses that permanently indicate a dead endpoint
const DEAD_STATUSES = new Set([404, 410]);

function randomTtl(): number {
  return 3 + Math.floor(Math.random() * 8); // 3–10 days inclusive
}

function filePath(stateDir: string, provider: string): string {
  return join(stateDir, `dead_slugs_${provider}.json`);
}

function isExpired(entry: DeadEntry): boolean {
  const age = (Date.now() - Date.parse(entry.deadAt)) / 86_400_000;
  return age > entry.ttlDays;
}

function load(stateDir: string, provider: string): DeadMap {
  try {
    const raw = readFileSync(filePath(stateDir, provider), "utf-8");
    const data = JSON.parse(raw) as DeadMap;
    // Prune expired entries on read
    let pruned = false;
    for (const slug of Object.keys(data)) {
      if (isExpired(data[slug]!)) {
        delete data[slug];
        pruned = true;
      }
    }
    if (pruned) save(stateDir, provider, data);
    return data;
  } catch {
    return {};
  }
}

function save(stateDir: string, provider: string, data: DeadMap): void {
  try {
    writeFileSync(filePath(stateDir, provider), JSON.stringify(data, null, 2), "utf-8");
  } catch {
    // Non-fatal — state dir may not exist yet
  }
}

export function loadDeadSlugs(stateDir: string, provider: string): Set<string> {
  return new Set(Object.keys(load(stateDir, provider)));
}

export function markDead(stateDir: string, provider: string, slug: string, code: number): void {
  if (!DEAD_STATUSES.has(code)) return;
  const data = load(stateDir, provider);
  data[slug] = { deadAt: new Date().toISOString(), code, ttlDays: randomTtl() };
  save(stateDir, provider, data);
}
