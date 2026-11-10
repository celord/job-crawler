"""LOCATION_FILTERS option parsing (ticket 03, section B).

Comma-separated env var on the viewer; each token maps to a filter rule,
never free text:

  - country name or ISO code ("Costa Rica", "CR", "USA") -> country = <code>
  - region token (Latam, EMEA, APAC, North America) -> region match; EMEA
    spans both europe and middle_east_africa

Unknown tokens raise at startup with the offending token named, so a
typo in the env var cannot silently disable the location filter.

The crawler derives region from country when it writes catalog_jobs, so
a "Latam" token only needs to match region = 'latam' -- it never needs
the latam country set on this side of the contract.
"""

import re
import unicodedata
from dataclasses import dataclass

DEFAULT_LOCATION_FILTERS = "Costa Rica,Latam,USA"

REGION_TOKENS: dict[str, tuple[str, ...]] = {
    "latam": ("latam",),
    "emea": ("europe", "middle_east_africa"),
    "apac": ("asia_pacific",),
    "north america": ("north_america",),
}

_REGION_LABELS = {
    "latam": "Latam",
    "emea": "EMEA",
    "apac": "APAC",
    "north america": "North America",
}

_COUNTRY_TOKENS = {
    # USA aliases
    "us": "US",
    "usa": "US",
    "u s": "US",
    "u s a": "US",
    "united states": "US",
    "united states of america": "US",
    "america": "US",
    # Canada
    "canada": "CA",
    "can": "CA",
    # Latam set (single source of truth lives in crawler/location_classifier.py)
    "mexico": "MX",
    "costa rica": "CR",
    "cr": "CR",
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
    "argentina": "AR",
    "bolivia": "BO",
    "brazil": "BR",
    "br": "BR",
    "chile": "CL",
    "colombia": "CO",
    "ecuador": "EC",
    "guyana": "GY",
    "paraguay": "PY",
    "peru": "PE",
    "suriname": "SR",
    "uruguay": "UY",
    "venezuela": "VE",
    # Other commonly filtered countries
    "united kingdom": "GB",
    "uk": "GB",
    "gb": "GB",
    "england": "GB",
    "germany": "DE",
    "de": "DE",
    "france": "FR",
    "fr": "FR",
    "spain": "ES",
    "es": "ES",
    "portugal": "PT",
    "pt": "PT",
    "italy": "IT",
    "it": "IT",
    "netherlands": "NL",
    "nl": "NL",
    "ireland": "IE",
    "ie": "IE",
    "poland": "PL",
    "sweden": "SE",
    "norway": "NO",
    "denmark": "DK",
    "finland": "FI",
    "switzerland": "CH",
    "austria": "AT",
    "belgium": "BE",
    "greece": "GR",
    "romania": "RO",
    "czechia": "CZ",
    "czech republic": "CZ",
    "hungary": "HU",
    "india": "IN",
    "in": "IN",
    "china": "CN",
    "cn": "CN",
    "japan": "JP",
    "jp": "JP",
    "singapore": "SG",
    "sg": "SG",
    "korea": "KR",
    "south korea": "KR",
    "australia": "AU",
    "au": "AU",
    "new zealand": "NZ",
    "nz": "NZ",
    "south africa": "ZA",
    "za": "ZA",
    "israel": "IL",
    "il": "IL",
    "uae": "AE",
    "united arab emirates": "AE",
    "turkey": "TR",
    "turkiye": "TR",
}

_DIACRITIC_RE = re.compile(r"[\u0300-\u036f]")
_NON_ALPHANUM_RE = re.compile(r"[^a-z0-9 ]")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize(token: str) -> str:
    token = token.lower()
    token = unicodedata.normalize("NFD", token)
    token = _DIACRITIC_RE.sub("", token)
    token = _NON_ALPHANUM_RE.sub(" ", token)
    token = _WHITESPACE_RE.sub(" ", token)
    return token.strip()


@dataclass(frozen=True)
class LocationOption:
    token: str
    label: str
    country: str | None  # ISO alpha-2 when this is a country token
    regions: tuple[str, ...] | None  # region values when this is a region token


def resolve_location_token(token: str) -> LocationOption:
    """One LOCATION_FILTERS token -> its filter rule. Raises ValueError for
    unknown tokens, naming the token."""
    raw = token.strip()
    if not raw:
        raise ValueError("LOCATION_FILTERS contains an empty token")
    normalized = _normalize(raw)

    regions = REGION_TOKENS.get(normalized)
    if regions is not None:
        return LocationOption(token=raw, label=_REGION_LABELS[normalized], country=None, regions=regions)

    country = _COUNTRY_TOKENS.get(normalized)
    if country is not None:
        return LocationOption(token=raw, label=raw, country=country, regions=None)

    raise ValueError(
        f"Unknown LOCATION_FILTERS token: '{raw}'. Use a country name or ISO code, "
        f"or one of: {', '.join(_REGION_LABELS.values())}."
    )


def resolve_location_options(raw: str | None) -> list[LocationOption]:
    """The full comma-separated LOCATION_FILTERS value -> options in order.
    Falls back to the default set when unset or blank."""
    value = raw.strip() if raw else ""
    tokens = [token.strip() for token in value.split(",")] if value else []
    tokens = [token for token in tokens if token]
    if not tokens:
        tokens = DEFAULT_LOCATION_FILTERS.split(",")
    return [resolve_location_token(token) for token in tokens]
