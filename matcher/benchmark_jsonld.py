#!/usr/bin/env python3
"""
A/B benchmark: compare Markdown vs JSON-LD job input format on Maverick.

Usage:
    python3 benchmark_jsonld.py [--n 5] [--profile-dir career-ops]

Picks N jobs that have parsed_jd in catalog.sqlite, runs Maverick on each
with both input formats, and prints a scorecard diff table.
"""
import argparse
import json
import os
import random
import sqlite3
import sys
import time
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
CATALOG_DB = os.environ.get("CATALOG_DB") or str(BASE_DIR.parent / "crawler" / "state" / "catalog.sqlite")
API_KEY = os.environ.get("NVIDIA_API_KEY", "").strip()
DEFAULT_MODEL = os.environ.get("NVIDIA_MODEL", "meta/llama-4-maverick-17b-128e-instruct")
URL = "https://integrate.api.nvidia.com/v1/chat/completions"

DIMS = ["core_skills", "relevant_experience", "target_alignment", "seniority_fit", "workplace_fit", "requirements_coverage"]


# ── profile loading ──────────────────────────────────────────────────────────

def load_profile(profile_dir):
    root = Path(profile_dir) if Path(profile_dir).is_absolute() else BASE_DIR / profile_dir
    data = {}
    for f in ["profile.yml", "portals.yml", "cv.md", "_profile.md"]:
        p = root / f
        data[f] = p.read_text(encoding="utf-8") if p.exists() else ""
    return data


def build_system_prompt(profile_data):
    return (
        "You are a senior technical recruiting evaluator.\n\n"
        f"=== IDENTITY (profile.yml) ===\n{profile_data.get('profile.yml', '')}\n\n"
        f"=== TARGET KEYWORDS (portals.yml) ===\n{profile_data.get('portals.yml', '')}\n\n"
        f"=== EXPERIENCE (cv.md) ===\n{profile_data.get('cv.md', '')}\n\n"
        f"=== APPLICATION STRATEGY (_profile.md) ===\n{profile_data.get('_profile.md', '')}\n\n"
        "Evaluate the job and return ONLY valid JSON with this structure:\n"
        '{"scorecard":{"core_skills":{"score":4.0,"reason":"..."},'
        '"relevant_experience":{"score":4.0,"reason":"..."},'
        '"target_alignment":{"score":4.0,"reason":"..."},'
        '"seniority_fit":{"score":4.0,"reason":"..."},'
        '"workplace_fit":{"score":4.0,"reason":"..."},'
        '"requirements_coverage":{"score":4.0,"reason":"..."}},'
        '"verdict":"yes","blockers":[]}'
    )


def _first_target_role(profile_data):
    for line in profile_data.get("profile.yml", "").splitlines():
        s = line.strip()
        if s.startswith("- ") and s[2:].strip().startswith('"'):
            return s[2:].strip().strip('"')
    return ""


# ── job input formatters ─────────────────────────────────────────────────────

def markdown_format(record):
    parts = []
    for key, label in [("title", "Title"), ("company", "Company"), ("location", "Location"),
                        ("employment_type", "Employment type"), ("compensation", "Compensation")]:
        if record.get(key):
            parts.append(f"{label}: {record[key]}")
    responsibilities = record.get("responsibilities") or []
    requirements = record.get("must_have_requirements") or record.get("requirements_summary") or []
    nice_to_have = record.get("nice_to_have_requirements") or []
    tools = record.get("technical_tools_mentioned") or []
    if responsibilities:
        parts.append("Responsibilities:\n" + "\n".join(f"- {r}" for r in responsibilities))
    if requirements:
        parts.append("Requirements:\n" + "\n".join(f"- {r}" for r in requirements))
    if nice_to_have:
        parts.append("Nice-to-have:\n" + "\n".join(f"- {r}" for r in nice_to_have))
    if tools:
        parts.append("Technical tools: " + ", ".join(tools))
    return "\n\n".join(parts)


def jsonld_format(record, target_role):
    from job_post_parser import to_jsonld
    return to_jsonld(record, target_role)


# ── model call ───────────────────────────────────────────────────────────────

def call_model(system, user_content, model):
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system, "cache_control": {"type": "ephemeral"}},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.2,
        "max_tokens": 1200,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    r = requests.post(URL, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()
    content = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    return json.loads(content), usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)


# ── main ─────────────────────────────────────────────────────────────────────

def pick_jobs(n):
    db = sqlite3.connect(CATALOG_DB)
    cur = db.execute(
        "SELECT provider, source_key, job_id, parsed_jd FROM catalog_jobs "
        "WHERE parsed_jd IS NOT NULL AND parsed_jd != '{}' "
        "ORDER BY RANDOM() LIMIT ?", (n * 4,)
    )
    rows = cur.fetchall()
    db.close()
    # prefer jobs that have responsibilities or requirements
    good = []
    for provider, sk, jid, pjd_raw in rows:
        try:
            pjd = json.loads(pjd_raw)
        except Exception:
            continue
        if (pjd.get("responsibilities") or pjd.get("requirements_summary") or pjd.get("must_have_requirements")):
            good.append((provider, sk, jid, pjd))
        if len(good) >= n:
            break
    return good


def score_avg(scorecard):
    vals = [v.get("score", 0) for v in scorecard.values() if isinstance(v, dict)]
    return round(sum(vals) / max(len(vals), 1), 2)


def print_table(results):
    dims_short = {
        "core_skills": "core_skills",
        "relevant_experience": "rel_exp",
        "target_alignment": "tgt_align",
        "seniority_fit": "sen_fit",
        "workplace_fit": "wkpl_fit",
        "requirements_coverage": "req_cov",
    }
    col_w = 9

    header_dims = "  ".join(f"{'MD':>{col_w}} {'JL':>{col_w}} {'Δ':>5}" for _ in DIMS)
    dim_labels = "  ".join(f"{dims_short[d]:>{col_w*2+7}}" for d in DIMS)
    print()
    print(f"{'Job':<45}  {dim_labels}  {'avg_MD':>7} {'avg_JL':>7} {'Δavg':>6}  {'tok_MD':>7} {'tok_JL':>7}")
    print("-" * 200)

    total_md_tok = total_jl_tok = 0
    for r in results:
        label = f"{r['title'][:20]} / {r['source_key'][:20]}"
        md_sc = r["markdown"]["scorecard"]
        jl_sc = r["jsonld"]["scorecard"]
        avg_md = score_avg(md_sc)
        avg_jl = score_avg(jl_sc)
        delta_avg = round(avg_jl - avg_md, 2)
        tok_md = r["markdown"]["prompt_tokens"]
        tok_jl = r["jsonld"]["prompt_tokens"]
        total_md_tok += tok_md
        total_jl_tok += tok_jl

        dim_cells = []
        for d in DIMS:
            s_md = (md_sc.get(d) or {}).get("score", 0)
            s_jl = (jl_sc.get(d) or {}).get("score", 0)
            delta = round(s_jl - s_md, 1)
            sign = "+" if delta > 0 else ""
            dim_cells.append(f"{s_md:>{col_w}.1f} {s_jl:>{col_w}.1f} {sign+str(delta):>5}")
        dims_str = "  ".join(dim_cells)

        sign_avg = "+" if delta_avg > 0 else ""
        print(f"{label:<45}  {dims_str}  {avg_md:>7.2f} {avg_jl:>7.2f} {sign_avg+str(delta_avg):>6}  {tok_md:>7} {tok_jl:>7}")

    print("-" * 200)
    print(f"Total prompt tokens: Markdown={total_md_tok}  JSON-LD={total_jl_tok}  savings={total_md_tok - total_jl_tok} ({round((1 - total_jl_tok/max(total_md_tok,1))*100, 1)}%)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=5, help="Number of jobs to test (default 5)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--profile-dir", default=os.environ.get("CAREER_OPS_DIR", "career-ops"))
    args = parser.parse_args()

    if not API_KEY:
        print("Set NVIDIA_API_KEY", file=sys.stderr)
        sys.exit(1)

    print(f"Loading profile from {args.profile_dir}…")
    profile_data = load_profile(args.profile_dir)
    system_prompt = build_system_prompt(profile_data)
    target_role = _first_target_role(profile_data)
    print(f"Target role: {target_role or '(none found)'}")
    print(f"Model: {args.model}")
    print(f"Picking {args.n} jobs from catalog…")

    jobs = pick_jobs(args.n)
    if not jobs:
        print("No jobs with parsed_jd found in catalog.", file=sys.stderr)
        sys.exit(1)
    print(f"Found {len(jobs)} jobs. Running A/B calls (2 calls per job)…\n")

    results = []
    for i, (provider, sk, jid, record) in enumerate(jobs, 1):
        title = record.get("title") or jid
        print(f"[{i}/{len(jobs)}] {title} / {sk} …", end=" ", flush=True)

        md_text = markdown_format(record)
        jl_text = jsonld_format(record, target_role)

        try:
            md_result, md_ptok, md_ctok = call_model(system_prompt, f"Analyze this job:\n\n{md_text}", args.model)
            time.sleep(0.5)
            jl_result, jl_ptok, jl_ctok = call_model(system_prompt, f"Analyze this job:\n\n{jl_text}", args.model)
            print(f"MD={md_ptok}tok JL={jl_ptok}tok")
            results.append({
                "title": title,
                "source_key": sk,
                "markdown": {"scorecard": md_result.get("scorecard", {}), "prompt_tokens": md_ptok},
                "jsonld": {"scorecard": jl_result.get("scorecard", {}), "prompt_tokens": jl_ptok},
                "markdown_input_chars": len(md_text),
                "jsonld_input_chars": len(jl_text),
            })
        except Exception as e:
            print(f"ERROR: {e}")

    if results:
        print_table(results)

        # Save raw results
        out = BASE_DIR / "benchmark_jsonld_results.json"
        out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nRaw results saved to {out}")


if __name__ == "__main__":
    main()
