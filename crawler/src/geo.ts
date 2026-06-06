import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

export type Coords = { lat: number; lon: number };

type LocationsMaps = {
  cityAdminCountry: Map<string, Coords>;
  cityCountry: Map<string, Coords>;
  cityAdmin: Map<string, Coords>;
  city: Map<string, Coords>;
};

// ---------------------------------------------------------------------------
// Static lookup tables
// ---------------------------------------------------------------------------

const US_STATES: Record<string, string> = {
  al: "AL", alabama: "AL", ak: "AK", alaska: "AK", az: "AZ", arizona: "AZ",
  ar: "AR", arkansas: "AR", ca: "CA", california: "CA", co: "CO", colorado: "CO",
  ct: "CT", connecticut: "CT", de: "DE", delaware: "DE", fl: "FL", florida: "FL",
  ga: "GA", georgia: "GA", hi: "HI", hawaii: "HI", id: "ID", idaho: "ID",
  il: "IL", illinois: "IL", in: "IN", indiana: "IN", ia: "IA", iowa: "IA",
  ks: "KS", kansas: "KS", ky: "KY", kentucky: "KY", la: "LA", louisiana: "LA",
  me: "ME", maine: "ME", md: "MD", maryland: "MD", ma: "MA", massachusetts: "MA",
  mi: "MI", michigan: "MI", mn: "MN", minnesota: "MN", ms: "MS", mississippi: "MS",
  mo: "MO", missouri: "MO", mt: "MT", montana: "MT", ne: "NE", nebraska: "NE",
  nv: "NV", nevada: "NV", nh: "NH", "new hampshire": "NH", nj: "NJ", "new jersey": "NJ",
  nm: "NM", "new mexico": "NM", ny: "NY", "new york": "NY", nc: "NC", "north carolina": "NC",
  nd: "ND", "north dakota": "ND", oh: "OH", ohio: "OH", ok: "OK", oklahoma: "OK",
  or: "OR", oregon: "OR", pa: "PA", pennsylvania: "PA", ri: "RI", "rhode island": "RI",
  sc: "SC", "south carolina": "SC", sd: "SD", "south dakota": "SD", tn: "TN", tennessee: "TN",
  tx: "TX", texas: "TX", ut: "UT", utah: "UT", vt: "VT", vermont: "VT",
  va: "VA", virginia: "VA", wa: "WA", washington: "WA", wv: "WV", "west virginia": "WV",
  wi: "WI", wisconsin: "WI", wy: "WY", wyoming: "WY",
  dc: "DC", "d c": "DC", "district of columbia": "DC", "washington dc": "DC", "washington d c": "DC",
};

const COUNTRY_ALIASES: Record<string, string> = {
  us: "US", usa: "US", "u.s.": "US", "u.s.a.": "US",
  "united states": "US", "united states of america": "US", america: "US",
  can: "CA", canada: "CA", mx: "MX", mex: "MX", mexico: "MX",
  gb: "GB", uk: "GB", "united kingdom": "GB", england: "GB", scotland: "GB", wales: "GB",
  de: "DE", germany: "DE", deutschland: "DE",
  fr: "FR", france: "FR",
  es: "ES", spain: "ES",
  it: "IT", italy: "IT",
  nl: "NL", netherlands: "NL", holland: "NL",
  se: "SE", sweden: "SE",
  no: "NO", norway: "NO",
  dk: "DK", denmark: "DK",
  fi: "FI", finland: "FI",
  ch: "CH", switzerland: "CH",
  at: "AT", austria: "AT",
  be: "BE", belgium: "BE",
  pl: "PL", poland: "PL",
  pt: "PT", portugal: "PT",
  au: "AU", australia: "AU",
  nz: "NZ", "new zealand": "NZ",
  in: "IN", india: "IN",
  cn: "CN", china: "CN",
  jp: "JP", japan: "JP",
  sg: "SG", singapore: "SG",
  br: "BR", brazil: "BR",
  za: "ZA", "south africa": "ZA",
  il: "IL", israel: "IL",
  ae: "AE", uae: "AE", "united arab emirates": "AE",
};

const CITY_ALIASES: Record<string, string> = {
  "new york": "new york city",
  nyc: "new york city",
  "new york ny": "new york city",
  bangalore: "bengaluru",
  bombay: "mumbai",
  madras: "chennai",
  calcutta: "kolkata",
  peking: "beijing",
};

const NYC_BOROUGHS = new Set(["brooklyn", "queens", "bronx", "staten island", "manhattan"]);

const REMOTE_KEYWORDS = ["remote", "anywhere", "worldwide", "work from home", "wfh"];
const TIMEZONE_KEYWORDS = ["time zone", "timezone"];

const GARBAGE_LOCATIONS = new Set([
  "", "not specified", "n/a", "none", "tbd", "unspecified",
  "multiple locations", "various", "flexible", "other", "global",
  "multiple", "varies", "various locations",
]);

const WORK_ARRANGEMENT_PREFIXES = [
  "hybrid in ", "hybrid - ", "hybrid: ", "hybrid, ", "hybrid ",
  "on-site in ", "on site in ", "onsite in ",
  "in-office in ", "in office in ",
  "based in ", "located in ",
  "remote in ", "remote - ", "remote: ",
];

const JUNK_SUFFIXES = new Set(["area", "metro", "region", "greater", "metropolitan"]);

// ---------------------------------------------------------------------------
// Build lookup maps from locations.json (called once, result cached)
// ---------------------------------------------------------------------------

let _maps: LocationsMaps | null = null;

function normalize(s: string): string {
  return s
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")   // strip diacritics
    .replace(/[^a-z0-9 ]/g, " ")       // non-alphanum → space
    .replace(/\s+/g, " ")
    .trim();
}

function buildMaps(): LocationsMaps {
  const dataPath = join(dirname(fileURLToPath(import.meta.url)), "../data/locations.json");
  const raw = JSON.parse(readFileSync(dataPath, "utf8")) as {
    schema: string[];
    data: unknown[][];
  };

  const { schema, data } = raw;
  const CITY_I = schema.indexOf("city");
  const ADMIN_I = schema.indexOf("admin");
  const COUNTRY_I = schema.indexOf("country");
  const LAT_I = schema.indexOf("lat");
  const LNG_I = schema.indexOf("lng");

  const maps: LocationsMaps = {
    cityAdminCountry: new Map(),
    cityCountry: new Map(),
    cityAdmin: new Map(),
    city: new Map(),
  };

  for (const row of data) {
    const cityN = normalize(String(row[CITY_I] ?? ""));
    const adminN = normalize(String(row[ADMIN_I] ?? ""));
    const country = String(row[COUNTRY_I] ?? "");
    const coords: Coords = { lat: Number(row[LAT_I]), lon: Number(row[LNG_I]) };

    if (!cityN) continue;

    if (adminN && country) {
      const k = `${cityN}|${adminN}|${country}`;
      if (!maps.cityAdminCountry.has(k)) maps.cityAdminCountry.set(k, coords);
    }
    if (country) {
      const k = `${cityN}|${country}`;
      if (!maps.cityCountry.has(k)) maps.cityCountry.set(k, coords);
    }
    if (adminN) {
      const k = `${cityN}|${adminN}`;
      if (!maps.cityAdmin.has(k)) maps.cityAdmin.set(k, coords);
    }
    if (!maps.city.has(cityN)) maps.city.set(cityN, coords);
  }

  return maps;
}

function getMaps(): LocationsMaps {
  if (_maps === null) _maps = buildMaps();
  return _maps;
}

// ---------------------------------------------------------------------------
// Parsing helpers
// ---------------------------------------------------------------------------

function cleanToken(t: string): string {
  const words = t.split(" ");
  while (words.length > 0 && JUNK_SUFFIXES.has(words[words.length - 1]!)) {
    words.pop();
  }
  return words.join(" ");
}

function stripWorkArrangement(s: string): string {
  for (const prefix of WORK_ARRANGEMENT_PREFIXES) {
    if (s.startsWith(prefix)) return s.slice(prefix.length);
  }
  return s;
}

function extractUsState(tokens: string[]): [string | null, string[]] {
  for (let i = tokens.length - 1; i >= 0; i--) {
    const t = tokens[i]!;
    if (t in US_STATES) return [US_STATES[t]!, [...tokens.slice(0, i), ...tokens.slice(i + 1)]];
  }
  return [null, tokens];
}

function extractCountry(tokens: string[]): [string | null, string[]] {
  if (tokens.length < 2) return [null, tokens];
  const last = tokens[tokens.length - 1]!;
  if (last in COUNTRY_ALIASES) return [COUNTRY_ALIASES[last]!, tokens.slice(0, -1)];
  return [null, tokens];
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export function resolveCoords(location: string | null | undefined): Coords | null {
  if (!location) return null;

  const maps = getMaps();
  let s = normalize(location);

  if (!s || GARBAGE_LOCATIONS.has(s)) return null;
  if (REMOTE_KEYWORDS.some((kw) => s.includes(kw))) return null;
  if (TIMEZONE_KEYWORDS.some((kw) => s.includes(kw))) return null;

  // Apply abbreviation expansions before stripping work arrangement
  s = s.replace(/\bft\b/g, "fort").replace(/\bmt\b/g, "mount").replace(/\bst\b/g, "saint");
  s = s.replace(/\bn\b/g, "north").replace(/\bs\b/g, "south");

  s = stripWorkArrangement(s);

  // Strip parentheticals: "(HQ)", "(Remote)", etc.
  s = s.replace(/\s*\([^)]*\)\s*/g, " ").trim();
  // Strip "- remote" / "— remote" suffixes
  s = s.replace(/[-—]\s*remote/g, "").trim();
  s = s.replace(/^[-,—\s]+|[-,—\s]+$/g, "");

  if (!s) return null;

  let tokens = s.split(",").map((t) => cleanToken(t.trim())).filter(Boolean);

  // Dedupe consecutive identical tokens
  tokens = tokens.filter((t, i) => i === 0 || t !== tokens[i - 1]);

  if (tokens.length === 0) return null;

  // Check full joined string against city aliases
  const joined = tokens.join(" ");
  if (joined in CITY_ALIASES) tokens = [CITY_ALIASES[joined]!];

  // Single token: check if it's "city state" space-separated (e.g. "miami fl")
  if (tokens.length === 1) {
    const words = tokens[0]!.split(" ");
    if (words.length >= 2 && words[words.length - 1]! in US_STATES) {
      const state = words[words.length - 1]!;
      const city = words.slice(0, -1).join(" ");
      tokens = [city, state];
    }
  }

  // Single token: country-only
  if (tokens.length === 1 && tokens[0]! in COUNTRY_ALIASES) return null;

  let country: string | null;
  [country, tokens] = extractCountry(tokens);

  if (country === "US" || country === null) {
    let state: string | null;
    [state, tokens] = extractUsState(tokens);
    if (state) {
      if (country === null) country = "US";
      // Rebuild: remaining tokens are city parts
      let city = tokens.join(" ");
      if (city in CITY_ALIASES) city = CITY_ALIASES[city]!;
      if (!city) return null;

      // NYC borough → New York City
      if (NYC_BOROUGHS.has(city) && state === "NY") city = "new york city";

      const adminN = state.toLowerCase();
      return (
        maps.cityAdminCountry.get(`${city}|${adminN}|${country}`) ??
        maps.cityCountry.get(`${city}|${country}`) ??
        maps.cityAdmin.get(`${city}|${adminN}`) ??
        maps.city.get(city) ??
        null
      );
    }
  }

  // No US state extracted — use remaining tokens
  let city: string | null = null;
  let admin: string | null = null;

  if (tokens.length >= 2) {
    admin = tokens[tokens.length - 1]!;
    city = tokens.slice(0, -1).join(" ");
  } else if (tokens.length === 1) {
    city = tokens[0]!;
  }

  if (!city) return null;
  if (city in CITY_ALIASES) city = CITY_ALIASES[city]!;
  if (NYC_BOROUGHS.has(city) && admin?.toLowerCase() === "ny") city = "new york city";

  const adminN = admin ? normalize(admin) : null;

  if (adminN && country) {
    const hit = maps.cityAdminCountry.get(`${city}|${adminN}|${country}`);
    if (hit) return hit;
  }
  if (country) {
    const hit = maps.cityCountry.get(`${city}|${country}`);
    if (hit) return hit;
  }
  if (adminN) {
    const hit = maps.cityAdmin.get(`${city}|${adminN}`);
    if (hit) return hit;
  }
  return maps.city.get(city) ?? null;
}
