import re
from typing import Any

_COMP_JUNK_RE = re.compile(r"^(req|r|jr|job)[-_]?\d+[a-z0-9-]*$", re.IGNORECASE)
_COMP_JOB_PATH_RE = re.compile(r"^/?job/", re.IGNORECASE)
_COMP_HINT_RE = re.compile(
    r"(salary|compensation|base pay|pay range|ote|equity|bonus|hour|annual|year|yr|"
    r"[$€£]|\b\d{2,3}\s?k\b|\b\d{2,3}[,\s]\d{3}\b)",
    re.IGNORECASE,
)
_COMP_VALUE_RE = re.compile(
    r"[$€£]\s?\d|\b\d{2,3}\s?k\b|\b\d{2,3}[,\s]\d{3}\b|\b\d+\s?-\s?\d+\b",
    re.IGNORECASE,
)


def is_real_compensation(value: Any) -> bool:
    text = str(value if value is not None else "").strip()
    if not text:
        return False
    if _COMP_JUNK_RE.match(text):
        return False
    if _COMP_JOB_PATH_RE.match(text):
        return False
    if not _COMP_HINT_RE.search(text):
        return False
    return bool(_COMP_VALUE_RE.search(text))


def sanitize_job(row: dict) -> dict:
    job = dict(row)
    job["compensation"] = job.get("compensation") if is_real_compensation(job.get("compensation")) else None
    return job


def company_name(job: dict) -> str:
    source_key = job.get("source_key") or ""
    if job.get("provider") == "workday":
        return source_key.split("/")[0] if source_key else source_key
    return source_key
