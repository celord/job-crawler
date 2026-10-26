import sqlite3

from services.filters import add_job_filter_conditions, parse_title_query


def test_parse_title_query_simple_tokens():
    tokens = parse_title_query("backend engineer")
    assert tokens == [
        {"terms": ["backend"], "exclude": False},
        {"terms": ["engineer"], "exclude": False},
    ]


def test_parse_title_query_negation():
    tokens = parse_title_query("-intern")
    assert tokens == [{"terms": ["intern"], "exclude": True}]


def test_parse_title_query_group():
    tokens = parse_title_query("(pm,tpm)")
    assert tokens == [{"terms": ["pm", "tpm"], "exclude": False}]


def test_parse_title_query_negated_group():
    tokens = parse_title_query("-(intern,contract)")
    assert tokens == [{"terms": ["intern", "contract"], "exclude": True}]


def test_parse_title_query_unclosed_group_consumes_rest():
    tokens = parse_title_query("(pm,tpm")
    assert tokens == [{"terms": ["pm", "tpm"], "exclude": False}]


def test_add_job_filter_conditions_empty_returns_no_where():
    where, params = add_job_filter_conditions()
    assert where == ""
    assert params == []


def test_add_job_filter_conditions_simple_title_uses_fts():
    where, params = add_job_filter_conditions(title="backend")
    assert "catalog_jobs_fts" in where
    assert params == ['title:"backend"']


def test_add_job_filter_conditions_multiword_title_uses_like():
    where, params = add_job_filter_conditions(title="-intern (pm,tpm)")
    assert "catalog_jobs_fts" not in where
    assert "NOT LIKE" in where
    assert "OR" in where
    assert params == ["%intern%", "%pm%", "%tpm%"]


def test_add_job_filter_conditions_negated_group_is_and_not_like():
    where, params = add_job_filter_conditions(title="-(intern,contract)")
    assert "AND" in where
    assert params == ["%intern%", "%contract%"]


def _locations_matching(my_location: str, locations: list[str], include_remote: bool = False) -> list[str]:
    where, params = add_job_filter_conditions(my_location=my_location, include_remote=include_remote)
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE catalog_jobs (location TEXT, is_remote INTEGER)")
    conn.executemany("INSERT INTO catalog_jobs VALUES (?, 0)", [(loc,) for loc in locations])
    rows = conn.execute(f"SELECT location FROM catalog_jobs {where}", params)
    return sorted(r[0] for r in rows)


def test_location_or_does_not_match_inside_words():
    locations = ["Toronto, ON", "Florence, Italy", "Portland, OR", "Oregon"]
    assert _locations_matching("or", locations) == ["Portland, OR"]


def test_location_matches_whole_word_with_punctuation_boundaries():
    assert _locations_matching("miami", ["Greater Miami Area", "Miami-Dade", "Miamisburg"]) == [
        "Greater Miami Area",
        "Miami-Dade",
    ]


def test_location_matches_multi_word_phrase():
    assert _locations_matching("san francisco", ["San Francisco, CA", "Santa Fe"]) == ["San Francisco, CA"]


def test_location_remote_text_matches_as_a_word():
    assert _locations_matching("remote", ["Remote, USA", "Remotely hosted"]) == ["Remote, USA"]


def test_location_match_is_case_insensitive():
    assert _locations_matching("BERLIN", ["berlin, germany"]) == ["berlin, germany"]


def test_location_trailing_punctuation_in_term_still_matches():
    assert _locations_matching("Frankfurt.", ["Frankfurt", "Frankfurter Allee"]) == ["Frankfurt"]


def test_location_separator_only_term_is_ignored():
    assert _locations_matching("-", ["Berlin - Germany", "Paris"]) == []


def test_location_comma_list_is_or_with_whole_words():
    assert _locations_matching("miami, lima", ["Lima, Peru", "Miami, FL", "Limassol"]) == [
        "Lima, Peru",
        "Miami, FL",
    ]


def test_location_remote_included_by_default():
    where, params = add_job_filter_conditions(my_location="Miami")
    assert "is_remote = 1" in where
    assert params == ["% miami %"]


def test_location_only_no_remote():
    where, params = add_job_filter_conditions(my_location="Miami", include_remote=False)
    assert "is_remote" not in where
    assert params == ["% miami %"]


def test_location_remote_only_when_no_location_given():
    where, params = add_job_filter_conditions(my_location=None, include_remote=True)
    assert where == ""


def test_no_location_and_remote_excluded_is_impossible_filter():
    where, params = add_job_filter_conditions(my_location=None, include_remote=False)
    assert "1 = 0" in where


def _slugs_matching(company: str, slugs: list[str]) -> list[str]:
    where, params = add_job_filter_conditions(company=company)
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE catalog_jobs (source_key TEXT)")
    conn.executemany("INSERT INTO catalog_jobs VALUES (?)", [(s,) for s in slugs])
    sql = f"SELECT source_key FROM catalog_jobs {where}"
    return sorted(r[0] for r in conn.execute(sql, params))


def test_company_filter_matches_slug_at_word_start():
    slugs = ["metacareers", "acme-meta", "x_meta", "meta.jobs", "stripe"]
    assert _slugs_matching("meta", slugs) == ["acme-meta", "meta.jobs", "metacareers", "x_meta"]


def test_company_filter_rejects_mid_word_substring():
    assert _slugs_matching("ramp", ["ramp", "trampoline", "ramp-labs"]) == ["ramp", "ramp-labs"]


def test_company_filter_comma_list_is_or():
    assert _slugs_matching("acme, stripe", ["acme", "stripe", "other"]) == ["acme", "stripe"]


def test_company_filter_escapes_wildcards():
    assert _slugs_matching("100%", ["100%-co", "1000-co"]) == ["100%-co"]


def test_sources_filter_accepts_list_or_csv():
    where1, params1 = add_job_filter_conditions(sources=["greenhouse", "lever"])
    where2, params2 = add_job_filter_conditions(sources="greenhouse,lever")
    assert where1 == where2
    assert params1 == params2 == ["greenhouse", "lever"]


def test_days_filter():
    where, params = add_job_filter_conditions(days=7)
    assert "datetime('now'" in where
    assert params == ["-7 days"]


def test_days_filter_ignores_non_positive():
    where, params = add_job_filter_conditions(days=0)
    assert where == ""


def test_types_filter_includes_null():
    where, params = add_job_filter_conditions(types="full_time,contract")
    assert "employment_type_canonical IS NULL" in where
    assert params == ["full_time", "contract"]


def test_tiers_filter_skipped_when_all_four_selected():
    where, params = add_job_filter_conditions(tiers="junior,mid,senior,staff")
    assert where == ""


def test_tiers_filter_applied_when_subset():
    where, params = add_job_filter_conditions(tiers="junior,mid")
    assert "skill_tier" in where
    assert params == ["junior", "mid"]


def test_include_exclude_terms():
    where, params = add_job_filter_conditions(include="python", exclude="java")
    assert where.count("LIKE") == 6  # 3 columns x 2 terms
    assert params == ["%python%"] * 3 + ["%java%"] * 3


def test_fav_companies_filter():
    where, params = add_job_filter_conditions(fav_companies=["Acme", "Foo"])
    assert "LOWER(source_key) = ?" in where
    assert params == ["acme", "foo"]


def test_evaluated_filter():
    where, params = add_job_filter_conditions(evaluated=True)
    assert "analysis_score IS NOT NULL AND analysis_score > 0" in where


def test_score_none_filter():
    where, params = add_job_filter_conditions(score="none")
    assert "analysis_score IS NULL OR analysis_score = 0" in where


def test_score_4plus_filter():
    where, params = add_job_filter_conditions(score="4plus")
    assert "analysis_score >= 4" in where


def test_conditions_combine_with_and():
    where, params = add_job_filter_conditions(company="acme", days=30)
    assert where.startswith("WHERE ")
    assert " AND " in where
    assert params[-1] == "-30 days"
    assert "acme%" in params
