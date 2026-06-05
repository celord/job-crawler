#!/usr/bin/env python3
"""
Semantic retrieve: given target role titles from the user profile, return the
top-K job keys (provider|source_key|job_id) whose titles are closest in
embedding space.

Used as a pre-filter before LLM scoring to reduce token cost ~90%.

Falls back gracefully if the corpus or ONNX model is missing (no crash,
returns None so callers keep their existing behavior).

Can also be run as a CLI for testing:
  python embed_retrieve.py "Technical Program Manager" --top-k 10
"""
import os
import sys
import json
import argparse
import numpy as np
from pathlib import Path

CATALOG_DB  = os.environ.get("CATALOG_DB", "/app/state/catalog.sqlite")
STATE_DIR   = Path(os.environ.get("STATE_DIR", str(Path(CATALOG_DB).parent)))
ONNX_DIR    = Path(__file__).resolve().parent / "onnx_minilm"
CORPUS_PATH = STATE_DIR / "title_corpus.npy"
IDS_PATH    = STATE_DIR / "title_ids.json"

# Module-level cache — loaded once per process
_corpus: np.ndarray | None = None
_ids: list[str] | None = None
_embedder = None


def _load_corpus():
    global _corpus, _ids
    if _corpus is not None:
        return True
    if not CORPUS_PATH.exists() or not IDS_PATH.exists():
        return False
    try:
        _ids = json.loads(IDS_PATH.read_text())
        arr  = np.load(str(CORPUS_PATH))
        # Dequantize float16 → float32 for dot product
        _corpus = arr.astype(np.float32)
        return True
    except Exception as e:
        print(f"[retrieve] corpus load error: {e}", file=sys.stderr)
        return False


def _load_embedder():
    global _embedder
    if _embedder is not None:
        return _embedder
    model_path = ONNX_DIR / "model.onnx"
    tok_path   = ONNX_DIR / "tokenizer.json"
    if not model_path.exists() or not tok_path.exists():
        return None
    try:
        import onnxruntime as ort
        from tokenizers import Tokenizer
        tok = Tokenizer.from_file(str(tok_path))
        tok.enable_truncation(max_length=64)
        tok.enable_padding()
        sess = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        _embedder = (tok, sess)
        return _embedder
    except Exception as e:
        print(f"[retrieve] embedder load error: {e}", file=sys.stderr)
        return None


def _mean_pool(token_vecs: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
    """Mean-pool token embeddings weighted by attention mask, then L2-normalize."""
    mask = attention_mask[:, :, np.newaxis].astype(np.float32)  # [B, T, 1]
    summed = (token_vecs * mask).sum(axis=1)                     # [B, H]
    counts = mask.sum(axis=1).clip(min=1e-9)                     # [B, 1]
    mean = summed / counts                                        # [B, H]
    norms = np.linalg.norm(mean, axis=1, keepdims=True).clip(min=1e-9)
    return (mean / norms).astype(np.float32)


def _embed_texts(texts: list[str]) -> np.ndarray | None:
    emb = _load_embedder()
    if emb is None:
        return None
    tok, sess = emb
    try:
        encs = tok.encode_batch([t or "" for t in texts])
        ids = np.array([e.ids for e in encs], dtype=np.int64)
        am  = np.array([e.attention_mask for e in encs], dtype=np.int64)

        input_names = {i.name for i in sess.get_inputs()}
        feed: dict = {"input_ids": ids, "attention_mask": am}
        if "token_type_ids" in input_names:
            feed["token_type_ids"] = np.zeros_like(ids)

        raw = sess.run(None, feed)[0]  # [B, T, H] or [B, H]

        if raw.ndim == 3:
            # Token-level output — apply mean pooling
            return _mean_pool(raw, am)
        else:
            # Already sentence-level — just normalize
            norms = np.linalg.norm(raw, axis=1, keepdims=True).clip(min=1e-9)
            return (raw / norms).astype(np.float32)
    except Exception as e:
        print(f"[retrieve] embed error: {e}", file=sys.stderr)
        return None


def retrieve(roles: list[str], top_k: int = 30) -> list[str] | None:
    """
    Return top_k job keys most semantically similar to any of the given roles.
    Returns None if corpus or embedder is unavailable (caller falls through).

    Max-pool over roles: score(job) = max cosine(job, role_i) for all roles.
    This avoids dilution when roles are diverse.
    """
    if not roles:
        return None
    if not _load_corpus():
        return None

    role_vecs = _embed_texts(roles)
    if role_vecs is None:
        return None

    # _corpus: [N, 384] float32 unit-norm
    # role_vecs: [R, 384] float32 unit-norm
    # sims: [N, R] — cosine = dot product on unit vectors
    sims = _corpus @ role_vecs.T          # [N, R]
    best = sims.max(axis=1)               # [N] — max-pool over roles

    top_k = min(top_k, len(_ids))
    top_idx = np.argpartition(best, -top_k)[-top_k:]
    top_idx = top_idx[np.argsort(best[top_idx])[::-1]]

    return [_ids[i] for i in top_idx]


def load_target_roles(profile_dir: str) -> list[str]:
    """
    Read target_roles from profile.yml.
    Falls back to empty list on any error (retrieve returns None → no filter).
    """
    try:
        import yaml
        profile_path = Path(profile_dir) / "profile.yml"
        data = yaml.safe_load(profile_path.read_text())
        roles = data.get("target_roles", [])
        if isinstance(roles, list):
            return [str(r).strip() for r in roles if str(r).strip()]
        return []
    except Exception:
        return []


def is_available() -> bool:
    """True if both corpus and ONNX model are present and loadable."""
    return _load_corpus() and _load_embedder() is not None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Semantic job title retrieve")
    parser.add_argument("roles", nargs="*", help="Target role titles (omit to use --from-profile)")
    parser.add_argument("--top-k", type=int, default=30)
    parser.add_argument("--from-profile", action="store_true", help="Read roles from profile.yml")
    parser.add_argument("--profile-dir", default=os.environ.get("CAREER_OPS_DIR", "career-ops"),
                        help="Profile directory containing profile.yml")
    args = parser.parse_args()

    if args.from_profile or not args.roles:
        roles = load_target_roles(args.profile_dir)
        if not roles:
            print("❌ No target_roles found in profile.yml", file=sys.stderr)
            sys.exit(1)
        print(f"Target roles from profile: {roles}", file=sys.stderr)
    else:
        roles = args.roles

    result = retrieve(roles, top_k=args.top_k)
    if result is None:
        print("❌ Corpus or ONNX model not available. Run build_title_corpus.py first.", file=sys.stderr)
        sys.exit(1)

    # Print only the keys, one per line (stdout) — parsed by the Node caller
    for key in result:
        print(key)
