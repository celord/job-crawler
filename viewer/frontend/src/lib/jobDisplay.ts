export function companyName(job: { provider: string; source_key: string }): string {
  if (job.provider === "workday") {
    return job.source_key.split("/")[0] || job.source_key;
  }
  return job.source_key;
}

export function jobMode(location: string | null | undefined, employmentType: string | null | undefined): string {
  const combined = `${location ?? ""} ${employmentType ?? ""}`.toLowerCase();
  if (/\bremote\b/.test(combined)) return "Remote";
  if (/\bhybrid\b/.test(combined)) return "Hybrid";
  if (/\bonsite\b|\bon-site\b|\bin-office\b|\boffice\b/.test(combined)) return "On-site";
  return (location ?? employmentType ?? "n/a").trim() || "n/a";
}

const UNITS: Array<[Intl.RelativeTimeFormatUnit, number]> = [
  ["year", 365 * 24 * 60 * 60 * 1000],
  ["month", 30 * 24 * 60 * 60 * 1000],
  ["week", 7 * 24 * 60 * 60 * 1000],
  ["day", 24 * 60 * 60 * 1000],
  ["hour", 60 * 60 * 1000],
  ["minute", 60 * 1000],
];

const relativeFormatter = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

export function formatRelativeTime(isoDate: string | null | undefined): string | null {
  if (!isoDate) return null;
  const timestamp = new Date(isoDate).getTime();
  if (Number.isNaN(timestamp)) return null;

  const diffMs = timestamp - Date.now();
  for (const [unit, ms] of UNITS) {
    if (Math.abs(diffMs) >= ms) {
      return relativeFormatter.format(Math.round(diffMs / ms), unit);
    }
  }
  return relativeFormatter.format(0, "minute");
}

export function scoreOutOf100(score5: number | null | undefined): number | null {
  if (score5 === null || score5 === undefined || Number.isNaN(score5)) return null;
  return Math.round(score5 * 20);
}

const REGION_LABELS: Record<string, string> = {
  north_america: "North America",
  latam: "Latam",
  europe: "Europe",
  asia_pacific: "Asia Pacific",
  middle_east_africa: "Middle East & Africa",
  global: "Global",
};

const countryNames = new Intl.DisplayNames("en", { type: "region" });

export function locationLabel(job: {
  region?: string | null;
  country?: string | null;
  location?: string | null;
  employment_type?: string | null;
}): string | null {
  if (job.region === "remote_unspecified") return "Remote (location not specified)";
  let place: string | undefined;
  if (job.country) {
    try {
      place = countryNames.of(job.country);
    } catch {
      place = job.country;
    }
  }
  place ??= job.region ? REGION_LABELS[job.region] : undefined;
  if (!place) return null;
  return jobMode(job.location, job.employment_type) === "Remote" ? `${place} · Remote` : place;
}
