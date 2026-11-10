import pytest

import config
from services.location_filter import resolve_location_options, resolve_location_token


def test_unset_env_yields_default_options():
    options = resolve_location_options(None)
    assert [o.token for o in options] == ["Costa Rica", "Latam", "USA"]
    assert [o.label for o in options] == ["Costa Rica", "Latam", "USA"]
    costa_rica, latam, usa = options
    assert (costa_rica.country, costa_rica.regions) == ("CR", None)
    assert (latam.country, latam.regions) == (None, ("latam",))
    assert (usa.country, usa.regions) == ("US", None)


def test_config_default_env_string_matches_module_default():
    assert config.LOCATION_FILTERS is None or isinstance(config.LOCATION_FILTERS, str)


def test_custom_env_changes_options():
    options = resolve_location_options("Brazil,EMEA")
    brazil, emea = options
    assert (brazil.country, brazil.regions) == ("BR", None)
    assert (emea.country, emea.regions) == (None, ("europe", "middle_east_africa"))


def test_region_tokens_case_insensitive_with_canonical_labels():
    latam = resolve_location_token("LATAM")
    assert latam.regions == ("latam",)
    assert latam.label == "Latam"

    north_america = resolve_location_token("north america")
    assert north_america.regions == ("north_america",)
    assert north_america.label == "North America"


def test_country_token_accepts_iso_code():
    assert resolve_location_token("CR").country == "CR"
    assert resolve_location_token("US").country == "US"
    assert resolve_location_token("USA").country == "US"


def test_empty_tokens_are_dropped():
    assert [o.token for o in resolve_location_options("Costa Rica, ,USA,")] == ["Costa Rica", "USA"]


def test_unknown_token_raises_error_naming_the_token():
    with pytest.raises(ValueError, match="BadToken"):
        resolve_location_options("Costa Rica,BadToken")


def test_unknown_token_error_lists_supported_region_tokens():
    with pytest.raises(ValueError, match="EMEA"):
        resolve_location_token("Nowhere")


def test_invalid_token_fails_app_startup(isolated_env, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(config, "LOCATION_FILTERS", "Costa Rica,Nowhere")
    import main

    with pytest.raises(ValueError, match="Nowhere"):
        with TestClient(main.app):
            pass
