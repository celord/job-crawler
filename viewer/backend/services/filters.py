import re

_TOKEN_RE = re.compile(r"[^\s(]+")
_LEADING_INT_RE = re.compile(r"^-?\d+")

ORDER_BY_CLAUSE = "ORDER BY COALESCE(posted_at, first_seen_at) DESC"


def parse_title_query(raw: str) -> list[dict]:
    tokens: list[dict] = []
    s = raw.strip()
    n = len(s)
    i = 0
    while i < n:
        is_negated = s[i] == "-"
        if is_negated:
            i += 1

        if i < n and s[i] == "(":
            end = s.find(")", i)
            inner = s[i + 1 :] if end == -1 else s[i + 1 : end]
            terms = [t.strip().lower() for t in inner.split(",") if t.strip()]
            if terms:
                tokens.append({"terms": terms, "exclude": is_negated})
            i = n if end == -1 else end + 1
        else:
            match = _TOKEN_RE.match(s[i:])
            if match:
                word = match.group(0).lower()
                if word:
                    tokens.append({"terms": [word], "exclude": is_negated})
                i += len(match.group(0))
            else:
                i += 1
    return tokens


def _parse_int(raw: str) -> int | None:
    match = _LEADING_INT_RE.match(raw.strip())
    return int(match.group(0)) if match else None


_SLUG_SEPARATORS = ("-", "_", ".", "/", " ")


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _company_slug_condition() -> str:
    return " OR ".join(["LOWER(source_key) LIKE ? ESCAPE '\\'"] * (1 + len(_SLUG_SEPARATORS)))


def _company_slug_patterns(term: str) -> list[str]:
    escaped = _escape_like(term.lower())
    return [f"{escaped}%"] + [f"%{_escape_like(sep)}{escaped}%" for sep in _SLUG_SEPARATORS]


_LOCATION_SEPARATORS = (",", "-", "/", "(", ")", ".")


def _word_boundary_text(column: str, separators: tuple[str, ...]) -> str:
    text = f"LOWER({column})"
    for sep in separators:
        text = f"REPLACE({text}, '{sep}', ' ')"
    return f"(' ' || {text} || ' ')"


def _fts_phrase(term: str) -> str:
    # FTS5 double-quoted strings escape an embedded quote by doubling it.
    phrase = f'"{term.replace(chr(34), chr(34) * 2)}"'
    # unicode61 strips punctuation, so a prefix match on "c#" would collapse
    # to "c*" and match nearly every title. Only purely alphanumeric terms get
    # the prefix operator; punctuation terms stay an exact tokenized phrase,
    # preserving the old LIKE's literal behavior.
    return f"{phrase}*" if term.isalnum() else phrase


def _title_groups(raw: str | None) -> list[tuple[list[str], bool]]:
    return [
        ([_fts_phrase(term.lower()) for term in token["terms"]], token["exclude"])
        for token in parse_title_query(raw or "")
    ]


def _title_match_query(groups: list[tuple[list[str], bool]]) -> str | None:
    def join(phrases: list[str]) -> str:
        return phrases[0] if len(phrases) == 1 else "(" + " OR ".join(phrases) + ")"

    positive = [join(phrases) for phrases, exclude in groups if not exclude]
    negative = [join(phrases) for phrases, exclude in groups if exclude]
    if not positive:
        return None
    query = " AND ".join(positive)
    if negative:
        query = f"({query}) NOT ({' OR '.join(negative)})"
    return query


def title_fts_query(raw: str | None) -> str | None:
    """One FTS5 MATCH expression for all title tokens, or None for
    pure-negation queries (FTS5 has no unary NOT to anchor them)."""
    return _title_match_query(_title_groups(raw))


def _title_fts_condition(negated: bool) -> str:
    operator = "NOT IN" if negated else "IN"
    return f"rowid {operator} " "(SELECT rowid FROM catalog_jobs_fts WHERE catalog_jobs_fts MATCH ?)"


def _location_word_condition() -> str:
    return f"{_word_boundary_text('location', _LOCATION_SEPARATORS)} LIKE ? ESCAPE '\\'"


def _location_word_pattern(term: str) -> str:
    for sep in _LOCATION_SEPARATORS:
        term = term.replace(sep, " ")
    return f"% {_escape_like(term.strip().lower())} %"


def build_job_filter(
    title: str | None = None,
    my_location: str | None = None,
    include_remote: bool = True,
    company: str | None = None,
    sources: str | list[str] | None = None,
    days: str | int | None = None,
    fav_companies: list[str] | None = None,
    include: str | None = None,
    exclude: str | None = None,
    tiers: str | None = None,
    types: str | None = None,
    evaluated: bool = False,
    score: str | None = None,
) -> tuple[str, list, str | None, bool]:
    """Build the job filter once, including the title FTS expression and
    whether title filtering is the only condition (used for the fast FTS
    count path)."""
    conditions: list[str] = []
    params: list = []

    title = str(title) if title is not None else ""
    my_location = str(my_location).strip() if my_location is not None else ""
    company = str(company) if company is not None else ""
    days_str = str(days) if days is not None else ""
    sources_str = (
        ",".join(sources) if isinstance(sources, list) else str(sources) if sources is not None else ""
    )

    title_groups = _title_groups(title)
    for phrases, negated in title_groups:
        conditions.append(_title_fts_condition(negated=negated))
        params.append(" OR ".join(phrases))

    # Location: match jobs near my_location OR remote jobs (if include_remote).
    # Empty my_location + include_remote -> remote only.
    # my_location set + include_remote False -> on-site/hybrid near my_location only.
    if my_location or not include_remote:
        loc_terms = [loc.strip() for loc in my_location.split(",") if loc.strip()] if my_location else []
        loc_terms = [loc for loc in loc_terms if _location_word_pattern(loc).strip("% ")]
        loc_clauses = [_location_word_condition() for _ in loc_terms]
        if include_remote:
            conditions.append("(" + " OR ".join([*loc_clauses, "is_remote = 1"]) + ")")
            for loc in loc_terms:
                params.append(_location_word_pattern(loc))
        elif loc_clauses:
            conditions.append("(" + " OR ".join(loc_clauses) + ")")
            params.extend(_location_word_pattern(loc) for loc in loc_terms)
        else:
            # remote excluded and no location given -> impossible filter, return nothing
            conditions.append("1 = 0")

    if company.strip():
        companies = [c.strip() for c in company.split(",") if c.strip()]
        conditions.append("(" + " OR ".join(_company_slug_condition() for _ in companies) + ")")
        for c in companies:
            params.extend(_company_slug_patterns(c))

    if sources_str.strip():
        provider_list = [s.strip() for s in sources_str.split(",") if s.strip()]
        if provider_list:
            conditions.append("(" + " OR ".join("provider = ?" for _ in provider_list) + ")")
            params.extend(provider_list)

    if days_str.strip():
        n = _parse_int(days_str)
        if n is not None and n > 0:
            conditions.append("datetime(COALESCE(posted_at, first_seen_at)) >= datetime('now', ?)")
            params.append(f"-{n} days")

    # types: canonical employment types to show. NULLs always included.
    type_list = [t.strip() for t in str(types or "").split(",") if t.strip()]
    if type_list:
        type_clauses = " OR ".join("employment_type_canonical = ?" for _ in type_list)
        conditions.append(f"(employment_type_canonical IS NULL OR {type_clauses})")
        params.extend(type_list)

    # tiers: skill_tier values to show. Selecting all 4 == no filter (NULLs shown too).
    tier_list = [t.strip().lower() for t in str(tiers or "").split(",") if t.strip()]
    if 0 < len(tier_list) < 4:
        conditions.append("(" + " OR ".join("skill_tier = ?" for _ in tier_list) + ")")
        params.extend(tier_list)

    # include: each term must appear in title OR location OR source_key
    include_terms = [t.strip().lower() for t in str(include or "").split(",") if t.strip()]
    for term in include_terms:
        conditions.append("(LOWER(title) LIKE ? OR LOWER(location) LIKE ? OR LOWER(source_key) LIKE ?)")
        params.extend([f"%{term}%", f"%{term}%", f"%{term}%"])

    # exclude: no term may appear in title OR location OR source_key
    exclude_terms = [t.strip().lower() for t in str(exclude or "").split(",") if t.strip()]
    for term in exclude_terms:
        conditions.append(
            "(LOWER(title) NOT LIKE ? AND LOWER(location) NOT LIKE ? AND LOWER(source_key) NOT LIKE ?)"
        )
        params.extend([f"%{term}%", f"%{term}%", f"%{term}%"])

    if fav_companies:
        conditions.append("(" + " OR ".join("LOWER(source_key) = ?" for _ in fav_companies) + ")")
        params.extend(fc.lower() for fc in fav_companies)

    if evaluated:
        conditions.append("analysis_score IS NOT NULL AND analysis_score > 0")
    if score == "none":
        conditions.append("(analysis_score IS NULL OR analysis_score = 0)")
    elif score == "4plus":
        conditions.append("analysis_score >= 4")

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    title_only = bool(title_groups) and len(conditions) == len(title_groups)
    return where_clause, params, _title_match_query(title_groups), title_only


def add_job_filter_conditions(
    title: str | None = None,
    my_location: str | None = None,
    include_remote: bool = True,
    company: str | None = None,
    sources: str | list[str] | None = None,
    days: str | int | None = None,
    fav_companies: list[str] | None = None,
    include: str | None = None,
    exclude: str | None = None,
    tiers: str | None = None,
    types: str | None = None,
    evaluated: bool = False,
    score: str | None = None,
) -> tuple[str, list]:
    where, params, _fts_query, _title_only = build_job_filter(
        title=title,
        my_location=my_location,
        include_remote=include_remote,
        company=company,
        sources=sources,
        days=days,
        fav_companies=fav_companies,
        include=include,
        exclude=exclude,
        tiers=tiers,
        types=types,
        evaluated=evaluated,
        score=score,
    )
    return where, params
