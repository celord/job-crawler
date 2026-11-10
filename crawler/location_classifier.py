"""Location string -> (region, country, remote) classification.

Complements geo.py (which geocodes lat/lon): this module decides the
continent-level `region` and ISO alpha-2 `country` stored in
catalog_jobs, including for remote postings whose text names a place
("Remote - Costa Rica", "Remote, LATAM", "US remote").

Region values are the on-disk contract shared with the viewer backend
(tickets/03): north_america, latam, europe, asia_pacific,
middle_east_africa, global, remote_unspecified, unknown.

Parse order (per ticket 03, A.3), applied to the normalized text:
  1. explicit country phrase -> country, region derived from country
  2. region keyword (LATAM, EMEA, APAC, North America, ...) -> region only
  3. US state / US-only phrasing -> country US, region north_america
  4. "Anywhere" / "Worldwide" -> global
  5. otherwise remote_unspecified when the location says remote, else unknown

Matching is exact on normalized phrases (whitespace-separated n-grams),
longest phrase first, so "Indiana" never matches India and "New Mexico"
resolves to the US state, not Mexico. Bare 2-letter ISO codes are only
matched when they do not collide with a US state abbreviation ("Remote -
CA" stays California; Canada needs "Canada"/"CAN"). Known limitation,
inherited from geo.COUNTRY_ALIASES: a bare "DE" token classifies as
Germany, not Delaware ("il" as Israel, not Illinois) -- geo.py parses
country before US state and this module stays consistent with that.

iCIMS provides no location data from its XML sitemaps, so its rows
carry no location text and stay region=unknown / country=NULL.

"Americas" and "EMEA" each span more than one region value, but the
column holds a single value: "Americas" -> latam (the product's default
focus), "EMEA" -> europe (the dominant share). The viewer's EMEA filter
token matches both europe and middle_east_africa rows.
"""

import re
import unicodedata
from dataclasses import dataclass

from geo import COUNTRY_ALIASES, US_STATES
from remote import is_remote_location

NORTH_AMERICA = "north_america"
LATAM = "latam"
EUROPE = "europe"
ASIA_PACIFIC = "asia_pacific"
MIDDLE_EAST_AFRICA = "middle_east_africa"
GLOBAL = "global"
REMOTE_UNSPECIFIED = "remote_unspecified"
UNKNOWN = "unknown"

# Single source of truth for the viewer's "Latam" filter token
# (ticket 03, A.4): Mexico, Central America, Caribbean Latin countries
# (Puerto Rico excluded -- US territory), and all of South America.
# Anglophone Caribbean islands (Jamaica, Trinidad, ...) are outside it.
LATAM_COUNTRIES = frozenset(
    {
        # Mexico
        "MX",
        # Central America
        "CR",
        "PA",
        "GT",
        "SV",
        "HN",
        "NI",
        "BZ",
        # Caribbean Latin
        "DO",
        "CU",
        "HT",
        # South America
        "AR",
        "BO",
        "BR",
        "CL",
        "CO",
        "EC",
        "GY",
        "PY",
        "PE",
        "SR",
        "UY",
        "VE",
    }
)

NORTH_AMERICA_COUNTRIES = frozenset({"US", "CA", "JM", "TT", "BB", "BS", "AG", "DM", "GD", "LC", "VC", "KN"})

EUROPE_COUNTRIES = frozenset(
    {
        "GB",
        "IE",
        "FR",
        "DE",
        "IT",
        "ES",
        "PT",
        "NL",
        "BE",
        "LU",
        "CH",
        "AT",
        "SE",
        "NO",
        "DK",
        "FI",
        "IS",
        "PL",
        "CZ",
        "SK",
        "HU",
        "RO",
        "BG",
        "GR",
        "HR",
        "SI",
        "RS",
        "ME",
        "MK",
        "AL",
        "BA",
        "EE",
        "LV",
        "LT",
        "UA",
        "BY",
        "MD",
        "MT",
        "CY",
        "MC",
        "LI",
        "AD",
        "SM",
    }
)

ASIA_PACIFIC_COUNTRIES = frozenset(
    {
        "CN",
        "JP",
        "KR",
        "KP",
        "MN",
        "IN",
        "PK",
        "BD",
        "LK",
        "NP",
        "BT",
        "MM",
        "TH",
        "VN",
        "KH",
        "LA",
        "MY",
        "SG",
        "ID",
        "PH",
        "BN",
        "TL",
        "TW",
        "HK",
        "MO",
        "AU",
        "NZ",
        "KZ",
        "UZ",
        "TM",
        "TJ",
        "KG",
        "AF",
    }
)

MIDDLE_EAST_AFRICA_COUNTRIES = frozenset(
    {
        "TR",
        "SY",
        "LB",
        "IL",
        "JO",
        "SA",
        "YE",
        "OM",
        "AE",
        "QA",
        "KW",
        "BH",
        "IQ",
        "IR",
        "EG",
        "LY",
        "TN",
        "DZ",
        "MA",
        "SD",
        "ET",
        "ER",
        "SO",
        "DJ",
        "KE",
        "TZ",
        "UG",
        "RW",
        "BI",
        "CM",
        "GA",
        "GQ",
        "AO",
        "ZM",
        "ZW",
        "BW",
        "NA",
        "ZA",
        "LS",
        "SZ",
        "GN",
        "GW",
        "SL",
        "LR",
        "CI",
        "ML",
        "BF",
        "NE",
        "NG",
        "GH",
        "TG",
        "BJ",
        "SN",
        "GM",
        "CV",
    }
)

COUNTRY_REGION: dict[str, str] = {}
for _codes, _region in (
    (LATAM_COUNTRIES, LATAM),
    (NORTH_AMERICA_COUNTRIES, NORTH_AMERICA),
    (EUROPE_COUNTRIES, EUROPE),
    (ASIA_PACIFIC_COUNTRIES, ASIA_PACIFIC),
    (MIDDLE_EAST_AFRICA_COUNTRIES, MIDDLE_EAST_AFRICA),
):
    for _code in _codes:
        COUNTRY_REGION[_code] = _region

# Country names and aliases (normalized, lowercase). geo.COUNTRY_ALIASES
# supplies the common aliases ("usa", "uk", ...). Bare 2-letter ISO codes
# are added only when no US state abbreviation uses the same letters.
_EXTRA_COUNTRY_NAMES = {
    "costa rica": "CR",
    "panama": "PA",
    "guatemala": "GT",
    "el salvador": "SV",
    "honduras": "HN",
    "nicaragua": "NI",
    "belize": "BZ",
    "dominican republic": "DO",
    "dominican rep": "DO",
    "cuba": "CU",
    "haiti": "HT",
    "jamaica": "JM",
    "trinidad and tobago": "TT",
    "trinidad": "TT",
    "barbados": "BB",
    "bahamas": "BS",
    "argentina": "AR",
    "bolivia": "BO",
    "chile": "CL",
    "colombia": "CO",
    "ecuador": "EC",
    "guyana": "GY",
    "paraguay": "PY",
    "peru": "PE",
    "suriname": "SR",
    "uruguay": "UY",
    "venezuela": "VE",
    "puerto rico": "US",  # US territory, deliberately outside the latam set
    "ireland": "IE",
    "czech republic": "CZ",
    "czechia": "CZ",
    "slovakia": "SK",
    "hungary": "HU",
    "romania": "RO",
    "bulgaria": "BG",
    "greece": "GR",
    "croatia": "HR",
    "slovenia": "SI",
    "serbia": "RS",
    "estonia": "EE",
    "latvia": "LV",
    "lithuania": "LT",
    "ukraine": "UA",
    "moldova": "MD",
    "malta": "MT",
    "cyprus": "CY",
    "iceland": "IS",
    "luxembourg": "LU",
    "liechtenstein": "LI",
    "korea": "KR",
    "south korea": "KR",
    "mongolia": "MN",
    "pakistan": "PK",
    "bangladesh": "BD",
    "sri lanka": "LK",
    "nepal": "NP",
    "bhutan": "BT",
    "myanmar": "MM",
    "burma": "MM",
    "thailand": "TH",
    "vietnam": "VN",
    "viet nam": "VN",
    "cambodia": "KH",
    "laos": "LA",
    "malaysia": "MY",
    "indonesia": "ID",
    "philippines": "PH",
    "brunei": "BN",
    "taiwan": "TW",
    "hong kong": "HK",
    "kazakhstan": "KZ",
    "uzbekistan": "UZ",
    "afghanistan": "AF",
    "turkey": "TR",
    "turkiye": "TR",
    "syria": "SY",
    "lebanon": "LB",
    "jordan": "JO",
    "saudi arabia": "SA",
    "yemen": "YE",
    "oman": "OM",
    "qatar": "QA",
    "kuwait": "KW",
    "bahrain": "BH",
    "iraq": "IQ",
    "iran": "IR",
    "egypt": "EG",
    "libya": "LY",
    "tunisia": "TN",
    "algeria": "DZ",
    "morocco": "MA",
    "sudan": "SD",
    "ethiopia": "ET",
    "eritrea": "ER",
    "somalia": "SO",
    "djibouti": "DJ",
    "kenya": "KE",
    "tanzania": "TZ",
    "uganda": "UG",
    "rwanda": "RW",
    "burundi": "BI",
    "cameroon": "CM",
    "gabon": "GA",
    "angola": "AO",
    "zambia": "ZM",
    "zimbabwe": "ZW",
    "botswana": "BW",
    "namibia": "NA",
    "lesotho": "LS",
    "eswatini": "SZ",
    "swaziland": "SZ",
    "guinea": "GN",
    "sierra leone": "SL",
    "liberia": "LR",
    "ivory coast": "CI",
    "cote d ivoire": "CI",
    "mali": "ML",
    "burkina faso": "BF",
    "niger": "NE",
    "nigeria": "NG",
    "ghana": "GH",
    "togo": "TG",
    "benin": "BJ",
    "senegal": "SN",
    "gambia": "GM",
    "cabo verde": "CV",
    "cape verde": "CV",
    # Unambiguous bare ISO codes (no US state abbreviation shares them).
    "cr": "CR",
    "gt": "GT",
    "sv": "SV",
    "hn": "HN",
    "ni": "NI",
    "bz": "BZ",
    "cu": "CU",
    "ht": "HT",
    "jm": "JM",
    "bo": "BO",
    "cl": "CL",
    "ec": "EC",
    "gy": "GY",
    "py": "PY",
    "pe": "PE",
    "sr": "SR",
    "uy": "UY",
    "ve": "VE",
    "ie": "IE",
    "gr": "GR",
    "pt": "PT",
    "lu": "LU",
    "cy": "CY",
    "tw": "TW",
    "hk": "HK",
    "tr": "TR",
    "eg": "EG",
    "dz": "DZ",
    "ng": "NG",
    "gh": "GH",
    "ke": "KE",
    "et": "ET",
    "sa": "SA",
    "qa": "QA",
    "om": "OM",
    "bh": "BH",
    "jo": "JO",
    "iq": "IQ",
    "ir": "IR",
    "ye": "YE",
    "lb": "LB",
    "sy": "SY",
    "kz": "KZ",
    "ge": "GE",
    "am": "AM",
}

COUNTRY_CODES: dict[str, str] = {**COUNTRY_ALIASES, **_EXTRA_COUNTRY_NAMES}

# Region keywords: region-only matches for remote postings.
REGION_KEYWORDS: dict[str, str] = {
    "latam": LATAM,
    "latin america": LATAM,
    "south america": LATAM,
    "americas": LATAM,  # spans both americas; latam is the stored value
    "north america": NORTH_AMERICA,
    "emea": EUROPE,  # spans europe + middle_east_africa; europe is stored
    "europe": EUROPE,
    "apac": ASIA_PACIFIC,
    "asia pacific": ASIA_PACIFIC,
    "africa": MIDDLE_EAST_AFRICA,
    "middle east": MIDDLE_EAST_AFRICA,
}

GLOBAL_KEYWORDS = {"anywhere", "worldwide"}

_MAX_PHRASE_TOKENS = max(len(phrase.split()) for phrase in (*COUNTRY_CODES, *REGION_KEYWORDS, *US_STATES))

# Priority for equally long phrases: country beats region keyword beats
# US state beats global keyword (geo.py parses country before US state;
# this module stays consistent with that ordering).
_COUNTRY_PRIORITY, _REGION_PRIORITY, _STATE_PRIORITY, _GLOBAL_PRIORITY = 0, 1, 2, 3

_DIACRITIC_RE = re.compile(r"[\u0300-\u036f]")
_NON_ALPHANUM_RE = re.compile(r"[^a-z0-9 ]")
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class LocationClass:
    region: str
    country: str | None
    remote: bool


def _normalize(s: str) -> str:
    s = s.lower()
    s = unicodedata.normalize("NFD", s)
    s = _DIACRITIC_RE.sub("", s)
    s = _NON_ALPHANUM_RE.sub(" ", s)
    s = _WHITESPACE_RE.sub(" ", s)
    return s.strip()


def _phrase_priority(phrase: str) -> tuple[int, str] | None:
    """(priority, value) for a matched phrase, or None when unmatched."""
    country = COUNTRY_CODES.get(phrase)
    if country is not None:
        return _COUNTRY_PRIORITY, country
    region = REGION_KEYWORDS.get(phrase)
    if region is not None:
        return _REGION_PRIORITY, region
    state = US_STATES.get(phrase)
    if state is not None:
        return _STATE_PRIORITY, state
    if phrase in GLOBAL_KEYWORDS:
        return _GLOBAL_PRIORITY, GLOBAL
    return None


def classify_location(location: str | None) -> LocationClass:
    """Classify a free-text location into (region, country, remote).

    `remote` keeps the existing is_remote_location() semantics (the word
    "remote" on a word boundary); a remote posting can still carry a
    country and region.
    """
    remote = bool(is_remote_location(location))
    if not location or not location.strip():
        return LocationClass(region=UNKNOWN, country=None, remote=False)

    tokens = _normalize(location).split()
    if not tokens:
        return LocationClass(region=UNKNOWN, country=None, remote=False)

    best: tuple[int, int, str] | None = None  # (phrase_len, priority, value)
    for size in range(min(len(tokens), _MAX_PHRASE_TOKENS), 0, -1):
        for i in range(len(tokens) - size + 1):
            match = _phrase_priority(" ".join(tokens[i : i + size]))
            if match is None:
                continue
            priority, value = match
            if best is None or (size, -priority) > (best[0], -best[1]):
                best = (size, priority, value)
        if best is not None:
            break  # sizes scan longest-first, so the first size with a match wins

    if best is None:
        if remote:
            return LocationClass(region=REMOTE_UNSPECIFIED, country=None, remote=True)
        return LocationClass(region=UNKNOWN, country=None, remote=False)

    _size, priority, value = best
    if priority == _COUNTRY_PRIORITY:
        return LocationClass(region=COUNTRY_REGION.get(value, UNKNOWN), country=value, remote=remote)
    if priority == _REGION_PRIORITY:
        return LocationClass(region=value, country=None, remote=remote)
    if priority == _STATE_PRIORITY:
        return LocationClass(region=NORTH_AMERICA, country="US", remote=remote)
    return LocationClass(region=GLOBAL, country=None, remote=remote)
