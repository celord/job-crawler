import random
import string
from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel

from config import settings
from services.profile import get_profile
from services.quick_scorer import score_job_quick

router = APIRouter(prefix="/analyze")

_RUN_ID_SUFFIX_ALPHABET = string.ascii_lowercase + string.digits


def _generate_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    suffix = "".join(random.choices(_RUN_ID_SUFFIX_ALPHABET, k=6))
    return f"run_{timestamp}_{suffix}"


class QuickAnalyzeRequest(BaseModel):
    jobs: list[dict]
    pipeline: str = "maverick"


class AnalyzeResponse(BaseModel):
    results: list[dict]
    run_id: str


def _job_row_base(job: dict) -> dict:
    return {
        "provider": job.get("provider"),
        "source_key": job.get("source_key"),
        "job_id": job.get("job_id"),
        "title": job.get("title"),
        "company": job.get("company"),
        "location": job.get("location"),
        "job_url": job.get("job_url") or job.get("url"),
    }


@router.post("/quick", response_model=AnalyzeResponse)
async def analyze_quick(body: QuickAnalyzeRequest) -> AnalyzeResponse:
    profile_data = get_profile()
    results: list[dict] = []

    # Sequential, matching the current job_fit_analyzer.py batch behavior.
    for job in body.jobs:
        row = _job_row_base(job)
        try:
            analysis = await score_job_quick(job, profile_data, model=settings.nvidia_model)
            row["status"] = "ok"
            row["analysis"] = analysis
        except Exception as exc:
            row["status"] = "error"
            row["error"] = str(exc)
        results.append(row)

    return AnalyzeResponse(results=results, run_id=_generate_run_id())
