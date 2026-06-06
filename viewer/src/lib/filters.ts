import type { TitleTokenGroup } from "./types.js";

// Returns an array of token groups with exclude flags.
// Single-item group = AND bare word. Multi-item group = OR group (AND-ed with rest).
// Prefix with - to negate: "product -(owner,builder)" → include product, exclude owner/builder
// Input: "product (manager,owner) -(growth,marketing)"
export function parseTitleQuery(input: string): TitleTokenGroup[] {
  const tokens: TitleTokenGroup[] = [];
  const str = input.trim();
  let i = 0;
  while (i < str.length) {
    const isNegated = str[i] === "-";
    if (isNegated) i++;

    if (str[i] === "(") {
      const end = str.indexOf(")", i);
      const inner = end === -1 ? str.slice(i + 1) : str.slice(i + 1, end);
      const terms = inner.split(",").map((t) => t.trim().toLowerCase()).filter(Boolean);
      if (terms.length > 0) tokens.push({ terms, exclude: isNegated });
      i = end === -1 ? str.length : end + 1;
    } else {
      const match = str.slice(i).match(/^[^\s(]+/);
      if (match) {
        const word = match[0].toLowerCase();
        if (word) tokens.push({ terms: [word], exclude: isNegated });
        i += match[0].length;
      } else {
        i++;
      }
    }
  }
  return tokens;
}

// Escape a term for FTS5 query syntax (wrap in double quotes to treat as a phrase).
function ftsEscape(term: string): string {
  return `"${term.replace(/"/g, '""')}"`;
}

// Returns true when all token groups are simple single-term non-negated ANDs —
// the easy case that maps directly to FTS5 AND queries.
function isSimpleFtsQuery(tokens: TitleTokenGroup[]): boolean {
  return tokens.length > 0 && tokens.every((t) => t.terms.length === 1 && !t.exclude);
}

export function addJobFilterConditions(
  filters: {
    title?: string | number | null;
    myLocation?: string | number | null;
    includeRemote?: boolean | null;
    company?: string | number | null;
    sources?: string | string[] | null;
    days?: string | number | null;
    favCompanies?: string[] | null;
    include?: string | null;
    exclude?: string | null;
    tiers?: string | null;
    types?: string | null;
  },
): { conditions: string[]; params: unknown[] } {
  const conditions: string[] = [];
  const params: unknown[] = [];
  const title = String(filters.title ?? "");
  const myLocation = String(filters.myLocation ?? "").trim();
  const includeRemote = filters.includeRemote !== false; // default true
  const company = String(filters.company ?? "");
  const days = String(filters.days ?? "");
  const sources = Array.isArray(filters.sources) ? filters.sources.join(",") : String(filters.sources ?? "");

  if (title.trim()) {
    const tokens = parseTitleQuery(title);

    if (isSimpleFtsQuery(tokens)) {
      // FTS5 path: all plain AND terms — emit a single rowid IN (SELECT rowid FROM fts WHERE title MATCH ?)
      const ftsQuery = tokens.map((t) => ftsEscape(t.terms[0]!)).join(" ");
      conditions.push(
        `rowid IN (SELECT rowid FROM catalog_jobs_fts WHERE catalog_jobs_fts MATCH ?)`,
      );
      params.push(`title:${ftsQuery}`);
    } else {
      // Fallback: complex query (negation / OR groups) — use LIKE as before
      for (const token of tokens) {
        if (token.terms.length === 1) {
          conditions.push(token.exclude ? "LOWER(title) NOT LIKE ?" : "LOWER(title) LIKE ?");
          params.push(`%${token.terms[0]}%`);
        } else {
          if (token.exclude) {
            const notClauses = token.terms.map(() => "LOWER(title) NOT LIKE ?");
            conditions.push(`(${notClauses.join(" AND ")})`);
            for (const t of token.terms) params.push(`%${t}%`);
          } else {
            const orClauses = token.terms.map(() => "LOWER(title) LIKE ?");
            conditions.push(`(${orClauses.join(" OR ")})`);
            for (const t of token.terms) params.push(`%${t}%`);
          }
        }
      }
    }
  }

  // Location: match jobs near myLocation OR remote jobs (if includeRemote).
  // If myLocation is empty and includeRemote is true → remote only.
  // If myLocation is set and includeRemote is false → on-site/hybrid near myLocation only.
  if (myLocation || !includeRemote) {
    const locTerms = myLocation ? myLocation.split(/[,]+/).map((l) => l.trim()).filter(Boolean) : [];
    const locClauses = locTerms.map(() => "LOWER(location) LIKE ?");
    if (includeRemote) {
      // (location matches myLocation) OR is_remote
      const parts = [...locClauses, "is_remote = 1"];
      conditions.push(`(${parts.join(" OR ")})`);
      for (const l of locTerms) params.push(`%${l.toLowerCase()}%`);
    } else {
      // location matches myLocation only (no remote)
      if (locClauses.length > 0) {
        conditions.push(`(${locClauses.join(" OR ")})`);
        for (const l of locTerms) params.push(`%${l.toLowerCase()}%`);
      } else {
        // remote unchecked and no location typed → show nothing (impossible filter, return empty)
        conditions.push("1 = 0");
      }
    }
  }

  if (company.trim()) {
    const companies = company.split(/[,]+/).map((c) => c.trim()).filter(Boolean);
    const companyClauses = companies.map(() => "LOWER(source_key) LIKE ?");
    conditions.push(`(${companyClauses.join(" OR ")})`);
    for (const comp of companies) params.push(`%${comp.toLowerCase()}%`);
  }

  if (sources.trim()) {
    const providerList = sources.split(/[,]+/).map((s) => s.trim()).filter(Boolean);
    if (providerList.length > 0) {
      const providerClauses = providerList.map(() => "provider = ?");
      conditions.push(`(${providerClauses.join(" OR ")})`);
      for (const provider of providerList) params.push(provider);
    }
  }

  if (days.trim()) {
    const n = parseInt(days, 10);
    if (!Number.isNaN(n) && n > 0) {
      conditions.push("COALESCE(posted_at, first_seen_at) >= datetime('now', ?)");
      params.push(`-${n} days`);
    }
  }

  // types: comma-separated canonical employment types to SHOW. NULLs always shown.
  const typeList = String(filters.types ?? "").split(",").map((s) => s.trim()).filter(Boolean);
  if (typeList.length > 0) {
    const typeClauses = typeList.map(() => "employment_type_canonical = ?");
    conditions.push(`(employment_type_canonical IS NULL OR ${typeClauses.join(" OR ")})`);
    for (const t of typeList) params.push(t);
  }

  // tiers: comma-separated tier names to SHOW (intern/entry/mid/senior). If empty = show all.
  // NULLs are included only when all 4 tiers are selected (= no filtering).
  const tierList = String(filters.tiers ?? "").split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
  if (tierList.length > 0 && tierList.length < 4) {
    const tierClauses = tierList.map(() => "skill_tier = ?");
    conditions.push(`(${tierClauses.join(" OR ")})`);
    for (const tier of tierList) params.push(tier);
  }

  // include: comma-separated terms, each must appear in title OR location OR source_key
  const includeTerms = String(filters.include ?? "").split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
  for (const term of includeTerms) {
    conditions.push("(LOWER(title) LIKE ? OR LOWER(location) LIKE ? OR LOWER(source_key) LIKE ?)");
    params.push(`%${term}%`, `%${term}%`, `%${term}%`);
  }

  // exclude: comma-separated terms, none may appear in title OR location OR source_key
  const excludeTerms = String(filters.exclude ?? "").split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
  for (const term of excludeTerms) {
    conditions.push("(LOWER(title) NOT LIKE ? AND LOWER(location) NOT LIKE ? AND LOWER(source_key) NOT LIKE ?)");
    params.push(`%${term}%`, `%${term}%`, `%${term}%`);
  }

  if (filters.favCompanies && filters.favCompanies.length > 0) {
    const favClauses = filters.favCompanies.map(() => "LOWER(source_key) = ?");
    conditions.push(`(${favClauses.join(" OR ")})`);
    for (const fc of filters.favCompanies) params.push(fc.toLowerCase());
  }

  // evaluatedKeys filtering is handled via the analysis_score column in server.ts

  return { conditions, params };
}
