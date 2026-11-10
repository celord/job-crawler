from location_classifier import LATAM_COUNTRIES, classify_location

# --- Ticket acceptance list -------------------------------------------------


def test_remote_costa_rica():
    c = classify_location("Remote - Costa Rica")
    assert c.country == "CR"
    assert c.region == "latam"
    assert c.remote is True  # remote alone does not clear geography


def test_remote_latam_region_only():
    c = classify_location("Remote, LATAM")
    assert c.region == "latam"
    assert c.country is None
    assert c.remote is True


def test_us_remote():
    c = classify_location("US remote")
    assert c.country == "US"
    assert c.region == "north_america"
    assert c.remote is True


def test_remote_only_is_remote_unspecified():
    c = classify_location("Remote")
    assert c.region == "remote_unspecified"
    assert c.country is None
    assert c.remote is True


def test_anywhere_is_global():
    c = classify_location("Anywhere")
    assert c.region == "global"
    assert c.country is None


def test_indiana_is_us_state_not_india():
    c = classify_location("Indiana")
    assert c.country == "US"  # a US state, never the country India
    assert c.country != "IN"
    assert c.region == "north_america"


def test_jamaica_is_jm_not_mexico():
    c = classify_location("Jamaica")
    assert c.country == "JM"
    assert c.country != "MX"
    assert c.region == "north_america"  # Anglophone Caribbean, outside the latam set


# --- Remote postings with geographic hints (ticket parse order) --------------


def test_remote_mexico_city():
    c = classify_location("Remote (Mexico City)")
    assert c.country == "MX"
    assert c.region == "latam"
    assert c.remote is True


def test_remote_new_mexico_is_us_not_mexico():
    # Longest-phrase match: "new mexico" (US state) must beat the "mexico"
    # country token inside it.
    c = classify_location("Remote - New Mexico")
    assert c.country == "US"
    assert c.region == "north_america"


def test_remote_dash_us():
    c = classify_location("Remote \u2013 US")  # en dash
    assert c.country == "US"
    assert c.region == "north_america"


def test_remote_usa_only():
    c = classify_location("Remote, USA only")
    assert c.country == "US"
    assert c.region == "north_america"


def test_remote_brazil():
    c = classify_location("Remote, Brazil")
    assert c.country == "BR"
    assert c.region == "latam"


def test_remote_emea_region_only():
    c = classify_location("Remote (EMEA)")
    assert c.region == "europe"
    assert c.country is None


def test_remote_apac_region_only():
    c = classify_location("Remote - APAC")
    assert c.region == "asia_pacific"
    assert c.country is None


def test_remote_north_america_region_only():
    c = classify_location("Remote in North America")
    assert c.region == "north_america"
    assert c.country is None


def test_remote_latin_america_region_only():
    c = classify_location("Remote - Latin America")
    assert c.region == "latam"
    assert c.country is None


def test_remote_worldwide_is_global():
    c = classify_location("Remote - Worldwide")
    assert c.region == "global"
    assert c.country is None
    assert c.remote is True


# --- On-site / hybrid postings keep geography too ---------------------------


def test_city_country_on_site():
    c = classify_location("San Jos\u00e9, Costa Rica")
    assert c.country == "CR"
    assert c.region == "latam"
    assert c.remote is False


def test_city_us_state_on_site():
    c = classify_location("Miami, FL")
    assert c.country == "US"
    assert c.region == "north_america"
    assert c.remote is False


def test_european_city_country():
    c = classify_location("Berlin, Germany")
    assert c.country == "DE"
    assert c.region == "europe"


def test_bare_iso_code_location():
    c = classify_location("CR")
    assert c.country == "CR"
    assert c.region == "latam"


# --- No-geo and junk inputs --------------------------------------------------


def test_none_and_blank_locations_are_unknown():
    for location in (None, "", "   "):
        c = classify_location(location)
        assert c.region == "unknown"
        assert c.country is None
        assert c.remote is False


def test_unparseable_text_is_unknown_not_remote_unspecified():
    c = classify_location("Remotely hosted, Berlin")  # no \bremote\b word
    assert c.region == "unknown"
    assert c.country is None
    assert c.remote is False


# --- Latam set contract ------------------------------------------------------


def test_latam_set_membership():
    # Mexico, Central America, Caribbean Latin (PR excluded), South America.
    expected = {
        "MX",
        "CR",
        "PA",
        "GT",
        "SV",
        "HN",
        "NI",
        "BZ",
        "DO",
        "CU",
        "HT",
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
    assert expected <= LATAM_COUNTRIES
    assert "PR" not in LATAM_COUNTRIES
    assert "US" not in LATAM_COUNTRIES
    assert "JM" not in LATAM_COUNTRIES
    assert "CA" not in LATAM_COUNTRIES
