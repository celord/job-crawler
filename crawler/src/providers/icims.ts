import { firstString } from "../normalizers.js";
import { CrawlContext, IdentifierSource, NormalizedJob, ProviderCrawler, SourceEntry } from "../types.js";

export const icimsProvider: ProviderCrawler = {
  provider: "icims",
  async crawl(source: SourceEntry, context: CrawlContext): Promise<NormalizedJob[]> {
    const { identifier } = source as IdentifierSource;
    // The source list was harvested from Common Crawl full domain names, so some
    // identifiers already carry the "careers-" prefix (e.g. "careers-acme") while
    // others are bare slugs (e.g. "acme"). Normalise to the bare slug before
    // constructing the URL to avoid "careers-careers-acme.icims.com".
    const slug = identifier.replace(/^careers-/, "").replace(/^-/, "");
    if (slug === "") {
      return [];
    }
    const sitemapUrl = `https://careers-${slug}.icims.com/sitemap.xml`;
    const xml = await context.http.getText(sitemapUrl);
    return parseIcimsSitemap(slug, xml, context.fetchedAt());
  }
};

export function parseIcimsSitemap(identifier: string, xml: string, fetchedAt: string): NormalizedJob[] {
  // Extract all <loc> values from the sitemap
  const locs = [...xml.matchAll(/<loc>\s*(https?:\/\/[^\s<]+)\s*<\/loc>/g)]
    .map((m) => m[1])
    .filter((u): u is string => u !== undefined);

  const jobs: NormalizedJob[] = [];

  for (const jobUrl of locs) {
    // Job URLs look like: https://careers-{slug}.icims.com/jobs/{id}/{title-slug}/job
    // Skip non-job URLs (intro pages, search pages, etc.)
    if (!jobUrl.includes("/jobs/") || jobUrl.endsWith("/jobs/intro") || !jobUrl.endsWith("/job")) {
      continue;
    }

    const afterJobs = jobUrl.split("/jobs/")[1];
    if (afterJobs === undefined) {
      continue;
    }

    // parts: ["<id>", "<title-slug>", "job"]
    const parts = afterJobs.split("/");
    const jobId = parts[0] ?? null;
    const titleSlug = parts[1] ?? null;

    if (jobId === null || titleSlug === null) {
      continue;
    }

    // Reconstruct title from URL slug: "financial-service-representative" → "Financial Service Representative"
    const title = decodeURIComponent(titleSlug).replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

    jobs.push({
      provider: "icims",
      source_key: identifier,
      job_id: jobId,
      title,
      location: null,      // iCIMS sitemaps do not expose location data
      employment_type: null,
      compensation: null,
      department: null,
      office: null,
      language: null,
      updated_at: null,
      posted_at: null,
      job_url: jobUrl,
      fetched_at: fetchedAt
    });
  }

  return jobs;
}
