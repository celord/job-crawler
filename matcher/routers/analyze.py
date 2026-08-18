import asyncio
import random
import string
from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel

from config import settings
from services.ensemble_scorer import score_job_ensemble
from services.profile import get_profile
from services.quick_scorer import score_job_quick

router = APIRouter(prefix="/analyze")

_RUN_ID_SUFFIX_ALPHABET = string.ascii_lowercase + string.digits


def _generate_run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    suffix = "".join(random.choices(_RUN_ID_SUFFIX_ALPHABET, k=6))
    return f"run_{timestamp}_{suffix}"


class QuickAnalyzeRequest(BaseModel):
    jobs: list[dict]
    pipeline: str = "maverick"


class EnsembleAnalyzeRequest(BaseModel):
    jobs: list[dict]
    pipeline: str = "ensemble"


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


async def _score_ensemble_row(job: dict, profile_data: dict) -> dict:
    row = _job_row_base(job)
    try:
        analysis = await score_job_ensemble(job, profile_data)
        row["status"] = "ok"
        row["analysis"] = analysis
    except Exception as exc:
        row["status"] = "error"
        row["error"] = str(exc)
    return row


@router.post("/ensemble", response_model=AnalyzeResponse)
async def analyze_ensemble(body: EnsembleAnalyzeRequest) -> AnalyzeResponse:
    profile_data = get_profile()

    if settings.ensemble_job_concurrency <= 1:
        results = [await _score_ensemble_row(job, profile_data) for job in body.jobs]
    else:
        semaphore = asyncio.Semaphore(settings.ensemble_job_concurrency)

        async def _bounded(job: dict) -> dict:
            async with semaphore:
                return await _score_ensemble_row(job, profile_data)

        # gather() preserves output order to match input order regardless of
        # which task actually finishes first.
        results = await asyncio.gather(*(_bounded(job) for job in body.jobs))

    return AnalyzeResponse(results=results, run_id=_generate_run_id())
