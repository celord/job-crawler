#!/usr/bin/env python3
"""
Semantic retrieve: given a batch of jobs (as JSONL on stdin or a file) and
target role titles from the user profile, return the top-K job keys
(provider|source_key|job_id) whose titles are closest in embedding space.

Used as a pre-filter before LLM scoring to reduce token cost.
Operates on the batch itself — no pre-built corpus needed.

Falls back gracefully if the ONNX model is missing (exits with code 1,
Node caller catches and skips the filter).

CLI (called by match-run.ts):
  python embed_retrieve.py --jobs-jsonl <path> --top-k 30 --profile-dir career-ops

Testing:
  echo '{"job_id":"1","title":"Senior TPM","provider":"x","source_key":"y"}' | \\
    python embed_retrieve.py --jobs-jsonl - --top-k 5 --profile-dir career-ops
"""
import os
import sys
import json
import argparse
import numpy as np
from pathlib import Path

ONNX_DIR = Path(__file__).resolve().parent / "onnx_minilm"


def load_embedder():
    model_path = ONNX_DIR / "model.onnx"
    tok_path   = ONNX_DIR / "tokenizer.json"
    if not model_path.exists() or not tok_path.exists():
        print(f"[retrieve] ONNX model not found at {ONNX_DIR}", file=sys.stderr)
        return None
    try:
        import onnxruntime as ort
        from tokenizers import Tokenizer
        tok = Tokenizer.from_file(str(tok_path))
        tok.enable_truncation(max_length=64)
        tok.enable_padding()
        sess = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        return tok, sess
    except Exception as e:
        print(f"[retrieve] embedder load error: {e}", file=sys.stderr)
        return None


def embed(tok, sess, texts: list[str]) -> np.ndarray:
    encs = tok.encode_batch([t or "" for t in texts])
    ids  = np.array([e.ids for e in encs], dtype=np.int64)
    am   = np.array([e.attention_mask for e in encs], dtype=np.int64)

    input_names = {i.name for i in sess.get_inputs()}
    feed: dict = {"input_ids": ids, "attention_mask": am}
    if "token_type_ids" in input_names:
        feed["token_type_ids"] = np.zeros_like(ids)

    raw = sess.run(None, feed)[0]  # [B, T, H] or [B, H]

    if raw.ndim == 3:
        mask = am[:, :, np.newaxis].astype(np.float32)
        vecs = (raw * mask).sum(axis=1) / mask.sum(axis=1).clip(min=1e-9)
    else:
        vecs = raw.astype(np.float32)

    norms = np.linalg.norm(vecs, axis=1, keepdims=True).clip(min=1e-9)
    return vecs / norms


def load_target_roles(profile_dir: str) -> list[str]:
    """
    Extract role title strings from profile.yml target_roles.
    Handles both list format and dict format (with primary/archetypes keys).
    """
    try:
        import yaml
        path = Path(profile_dir) / "profile.yml"
        data = yaml.safe_load(path.read_text())
        raw = data.get("target_roles", [])

        roles: list[str] = []

        if isinstance(raw, list):
            # Simple list of strings
            roles = [str(r).strip() for r in raw if str(r).strip()]
        elif isinstance(raw, dict):
            # Dict with keys like "primary" (list of strings) and
            # "archetypes" (list of dicts with "name" key)
            primary = raw.get("primary", [])
            if isinstance(primary, list):
                roles.extend(str(r).strip() for r in primary if str(r).strip())
            archetypes = raw.get("archetypes", [])
            if isinstance(archetypes, list):
                for arch in archetypes:
                    if isinstance(arch, dict) and arch.get("name"):
                        # "Product Manager/owner" → take first variant before "/"
                        name = str(arch["name"]).split("/")[0].strip()
                        if name:
                            roles.append(name)
                    elif isinstance(arch, str):
                        roles.append(arch.strip())

        return roles
    except Exception as e:
        print(f"[retrieve] profile load error: {e}", file=sys.stderr)
        return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs-jsonl", required=True,
                        help="Path to JSONL file of jobs, or - for stdin")
    parser.add_argument("--top-k", type=int, default=30)
    parser.add_argument("--profile-dir",
                        default=os.environ.get("CAREER_OPS_DIR", "career-ops"))
    args = parser.parse_args()

    # Load roles
    roles = load_target_roles(args.profile_dir)
    if not roles:
        print("[retrieve] no target_roles in profile — skipping", file=sys.stderr)
        sys.exit(1)

    # Load jobs
    src = sys.stdin if args.jobs_jsonl == "-" else open(args.jobs_jsonl, encoding="utf-8")
    jobs = []
    for line in src:
        line = line.strip()
        if not line:
            continue
        try:
            j = json.loads(line)
            jobs.append(j)
        except Exception:
            pass
    if args.jobs_jsonl != "-":
        src.close()

    if not jobs:
        sys.exit(1)

    # If batch is already small, no point filtering
    top_k = min(args.top_k, len(jobs))
    if len(jobs) <= top_k:
        for j in jobs:
            print(f"{j['provider']}|{j['source_key']}|{j['job_id']}")
        return

    emb = load_embedder()
    if emb is None:
        sys.exit(1)

    tok, sess = emb

    # Embed roles (max-pool query)
    role_vecs = embed(tok, sess, roles)          # [R, H]

    # Embed job titles
    titles = [j.get("title") or "" for j in jobs]
    job_vecs = embed(tok, sess, titles)          # [N, H]

    # Score: max cosine over all roles per job
    sims = job_vecs @ role_vecs.T               # [N, R]
    scores = sims.max(axis=1)                   # [N]

    # Top-K indices
    top_idx = np.argpartition(scores, -top_k)[-top_k:]
    top_idx = top_idx[np.argsort(scores[top_idx])[::-1]]

    print(f"[retrieve] {len(jobs)} → {top_k} jobs kept | roles: {roles}", file=sys.stderr)

    for i in top_idx:
        j = jobs[i]
        print(f"{j['provider']}|{j['source_key']}|{j['job_id']}")


if __name__ == "__main__":
    main()
