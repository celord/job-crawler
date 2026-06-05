#!/usr/bin/env python3
"""
Build (or update) the title embedding corpus used for semantic retrieve.

Reads all job titles from catalog.sqlite, embeds them with the local ONNX
MiniLM model, and writes:
  <state_dir>/title_corpus.npy   float16 [N, 384] — unit-norm embeddings
  <state_dir>/title_ids.json     list of "provider|source_key|job_id" strings

Incremental: titles already in the corpus are skipped.

Usage (inside Docker):
  docker compose run --rm matcher python /app/build_title_corpus.py

Environment:
  CATALOG_DB   path to catalog.sqlite  (default: /app/state/catalog.sqlite)
  STATE_DIR    output directory         (default: directory of CATALOG_DB)
"""
import os
import sys
import json
import sqlite3
import numpy as np
from pathlib import Path

CATALOG_DB  = os.environ.get("CATALOG_DB", "/app/state/catalog.sqlite")
STATE_DIR   = Path(os.environ.get("STATE_DIR", str(Path(CATALOG_DB).parent)))
ONNX_DIR    = Path(__file__).resolve().parent / "onnx_minilm"
CORPUS_PATH = STATE_DIR / "title_corpus.npy"
IDS_PATH    = STATE_DIR / "title_ids.json"
BATCH_SIZE  = 512


def load_embedder():
    try:
        import onnxruntime as ort
        from tokenizers import Tokenizer
    except ImportError:
        print("❌ onnxruntime and tokenizers are required. Rebuild the matcher image.")
        sys.exit(1)

    model_path = ONNX_DIR / "model.onnx"
    tok_path   = ONNX_DIR / "tokenizer.json"
    if not model_path.exists() or not tok_path.exists():
        print(f"❌ ONNX model not found at {ONNX_DIR}. Run export_onnx.py first.")
        sys.exit(1)

    tok = Tokenizer.from_file(str(tok_path))
    tok.enable_truncation(max_length=64)
    tok.enable_padding()

    sess = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    print(f"  ONNX providers: {sess.get_providers()}")
    return tok, sess


def embed_batch(tok, sess, texts):
    encs = tok.encode_batch([t or "" for t in texts])
    ids = np.array([e.ids for e in encs], dtype=np.int64)
    am  = np.array([e.attention_mask for e in encs], dtype=np.int64)

    input_names = {i.name for i in sess.get_inputs()}
    feed: dict = {"input_ids": ids, "attention_mask": am}
    if "token_type_ids" in input_names:
        feed["token_type_ids"] = np.zeros_like(ids)

    raw = sess.run(None, feed)[0]  # [B, T, H] or [B, H]

    if raw.ndim == 3:
        # Token-level — mean pool weighted by attention mask
        mask = am[:, :, np.newaxis].astype(np.float32)
        summed = (raw * mask).sum(axis=1)
        counts = mask.sum(axis=1).clip(min=1e-9)
        vecs = summed / counts
    else:
        vecs = raw.astype(np.float32)

    norms = np.linalg.norm(vecs, axis=1, keepdims=True).clip(min=1e-9)
    return (vecs / norms).astype(np.float16)


def main():
    print(f"[corpus] CATALOG_DB  = {CATALOG_DB}")
    print(f"[corpus] STATE_DIR   = {STATE_DIR}")
    print(f"[corpus] ONNX_DIR    = {ONNX_DIR}")

    if not Path(CATALOG_DB).exists():
        print(f"❌ DB not found: {CATALOG_DB}")
        sys.exit(1)

    # Load existing corpus
    existing_ids: list[str] = []
    existing_vecs: list[np.ndarray] = []
    if CORPUS_PATH.exists() and IDS_PATH.exists():
        existing_ids = json.loads(IDS_PATH.read_text())
        existing_vecs_arr = np.load(str(CORPUS_PATH))
        existing_vecs = [existing_vecs_arr]
        print(f"[corpus] Existing corpus: {len(existing_ids)} entries")
    else:
        print("[corpus] No existing corpus — building from scratch")

    existing_set = set(existing_ids)

    # Fetch all jobs from DB
    conn = sqlite3.connect(CATALOG_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT provider, source_key, job_id, title FROM catalog_jobs WHERE title IS NOT NULL"
    ).fetchall()
    conn.close()

    print(f"[corpus] Jobs in DB with titles: {len(rows)}")

    new_jobs = [(r["provider"], r["source_key"], r["job_id"], r["title"])
                for r in rows
                if f"{r['provider']}|{r['source_key']}|{r['job_id']}" not in existing_set]

    print(f"[corpus] New jobs to embed: {len(new_jobs)}")
    if not new_jobs:
        print("[corpus] Nothing to do.")
        return

    print("[corpus] Loading ONNX embedder...")
    tok, sess = load_embedder()

    new_ids = []
    new_vecs_chunks = []

    for i in range(0, len(new_jobs), BATCH_SIZE):
        batch = new_jobs[i:i + BATCH_SIZE]
        titles = [j[3] for j in batch]
        vecs = embed_batch(tok, sess, titles)
        new_ids.extend(f"{j[0]}|{j[1]}|{j[2]}" for j in batch)
        new_vecs_chunks.append(vecs)
        done = min(i + BATCH_SIZE, len(new_jobs))
        print(f"  {done}/{len(new_jobs)} embedded...", end="\r")

    print()

    # Merge with existing
    all_ids = existing_ids + new_ids
    all_vecs_parts = existing_vecs + new_vecs_chunks
    all_vecs = np.vstack(all_vecs_parts).astype(np.float16)

    np.save(str(CORPUS_PATH), all_vecs)
    IDS_PATH.write_text(json.dumps(all_ids))

    print(f"[corpus] Saved {len(all_ids)} entries")
    print(f"  {CORPUS_PATH}  ({CORPUS_PATH.stat().st_size // 1024} KB)")
    print(f"  {IDS_PATH}")


if __name__ == "__main__":
    main()
