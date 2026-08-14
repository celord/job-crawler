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


def _fts_escape(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def _is_simple_fts_query(tokens: list[dict]) -> bool:
    return len(tokens) > 0 and all(
        len(t["terms"]) == 1 and not t["exclude"] for t in tokens
    )


def _parse_int(raw: str) -> int | None:
    match = _LEADING_INT_RE.match(raw.strip())
    return int(match.group(0)) if match else None


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
    conditions: list[str] = []
    params: list = []

    title = str(title) if title is not None else ""
    my_location = str(my_location).strip() if my_location is not None else ""
    company = str(company) if company is not None else ""
    days_str = str(days) if days is not None else ""
    sources_str = (
        ",".join(sources)
        if isinstance(sources, list)
        else str(sources) if sources is not None else ""
    )

    if title.strip():
        tokens = parse_title_query(title)

        if _is_simple_fts_query(tokens):
            fts_query = " ".join(_fts_escape(t["terms"][0]) for t in tokens)
            conditions.append(
                "rowid IN (SELECT rowid FROM catalog_jobs_fts WHERE catalog_jobs_fts MATCH ?)"
            )
            params.append(f"title:{fts_query}")
        else:
            for token in tokens:
                terms = token["terms"]
                if len(terms) == 1:
                    conditions.append(
                        "LOWER(title) NOT LIKE ?"
                        if token["exclude"]
                        else "LOWER(title) LIKE ?"
                    )
                    params.append(f"%{terms[0]}%")
                elif token["exclude"]:
                    conditions.append(
                        "("
                        + " AND ".join("LOWER(title) NOT LIKE ?" for _ in terms)
                        + ")"
                    )
                    params.extend(f"%{t}%" for t in terms)
                else:
                    conditions.append(
                        "(" + " OR ".join("LOWER(title) LIKE ?" for _ in terms) + ")"
                    )
                    params.extend(f"%{t}%" for t in terms)

    # Location: match jobs near my_location OR remote jobs (if include_remote).
    # Empty my_location + include_remote -> remote only.
    # my_location set + include_remote False -> on-site/hybrid near my_location only.
    if my_location or not include_remote:
        loc_terms = (
            [l.strip() for l in my_location.split(",") if l.strip()]
            if my_location
            else []
        )
        loc_clauses = ["LOWER(location) LIKE ?" for _ in loc_terms]
        if include_remote:
            conditions.append("(" + " OR ".join([*loc_clauses, "is_remote = 1"]) + ")")
            params.extend(f"%{l.lower()}%" for l in loc_terms)
        elif loc_clauses:
            conditions.append("(" + " OR ".join(loc_clauses) + ")")
            params.extend(f"%{l.lower()}%" for l in loc_terms)
        else:
            # remote excluded and no location given -> impossible filter, return nothing
            conditions.append("1 = 0")

    if company.strip():
        companies = [c.strip() for c in company.split(",") if c.strip()]
        conditions.append(
            "(" + " OR ".join("LOWER(source_key) LIKE ?" for _ in companies) + ")"
        )
        params.extend(f"%{c.lower()}%" for c in companies)

    if sources_str.strip():
        provider_list = [s.strip() for s in sources_str.split(",") if s.strip()]
        if provider_list:
            conditions.append(
                "(" + " OR ".join("provider = ?" for _ in provider_list) + ")"
            )
            params.extend(provider_list)

    if days_str.strip():
        n = _parse_int(days_str)
        if n is not None and n > 0:
            conditions.append(
                "COALESCE(posted_at, first_seen_at) >= datetime('now', ?)"
            )
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
    include_terms = [
        t.strip().lower() for t in str(include or "").split(",") if t.strip()
    ]
    for term in include_terms:
        conditions.append(
            "(LOWER(title) LIKE ? OR LOWER(location) LIKE ? OR LOWER(source_key) LIKE ?)"
        )
        params.extend([f"%{term}%", f"%{term}%", f"%{term}%"])

    # exclude: no term may appear in title OR location OR source_key
    exclude_terms = [
        t.strip().lower() for t in str(exclude or "").split(",") if t.strip()
    ]
    for term in exclude_terms:
        conditions.append(
            "(LOWER(title) NOT LIKE ? AND LOWER(location) NOT LIKE ? AND LOWER(source_key) NOT LIKE ?)"
        )
        params.extend([f"%{term}%", f"%{term}%", f"%{term}%"])

    if fav_companies:
        conditions.append(
            "(" + " OR ".join("LOWER(source_key) = ?" for _ in fav_companies) + ")"
        )
        params.extend(fc.lower() for fc in fav_companies)

    if evaluated:
        conditions.append("analysis_score IS NOT NULL AND analysis_score > 0")
    if score == "none":
        conditions.append("(analysis_score IS NULL OR analysis_score = 0)")
    elif score == "4plus":
        conditions.append("analysis_score >= 4")

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    return where_clause, params
