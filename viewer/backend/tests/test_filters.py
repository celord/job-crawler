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


def test_location_remote_included_by_default():
    where, params = add_job_filter_conditions(my_location="Miami")
    assert "is_remote = 1" in where
    assert params == ["%miami%"]


def test_location_only_no_remote():
    where, params = add_job_filter_conditions(my_location="Miami", include_remote=False)
    assert "is_remote" not in where
    assert params == ["%miami%"]


def test_location_remote_only_when_no_location_given():
    where, params = add_job_filter_conditions(my_location=None, include_remote=True)
    assert where == ""


def test_no_location_and_remote_excluded_is_impossible_filter():
    where, params = add_job_filter_conditions(my_location=None, include_remote=False)
    assert "1 = 0" in where


def test_company_filter():
    where, params = add_job_filter_conditions(company="Acme, Foo")
    assert where.count("LIKE") == 2
    assert params == ["%acme%", "%foo%"]


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
    assert params == ["%acme%", "-30 days"]
