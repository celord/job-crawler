#!/usr/bin/env python3
"""
Download the pre-exported ONNX version of msmarco-MiniLM-L-12-v3 from HuggingFace Hub.
No torch required — uses the optimum-community export.

Outputs:
  matcher/onnx_minilm/model.onnx
  matcher/onnx_minilm/tokenizer.json
  matcher/onnx_minilm/tokenizer_config.json  (and other tokenizer files)

Run once:
  docker compose run --rm matcher python /app/export_onnx.py
"""
import os
import sys
from pathlib import Path

MODEL_REPO = "sentence-transformers/msmarco-MiniLM-L12-v3"
OUT_DIR = Path(__file__).resolve().parent / "onnx_minilm"

# model.onnx lives in the onnx/ subfolder of this repo
ONNX_FILES = {
    "onnx/model.onnx": "model.onnx",          # src_path → local_name
    "tokenizer.json": "tokenizer.json",
    "tokenizer_config.json": "tokenizer_config.json",
    "special_tokens_map.json": "special_tokens_map.json",
    "vocab.txt": "vocab.txt",
    "1_Pooling/config.json": "1_Pooling/config.json",
}

def main():
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print("❌ huggingface-hub not installed. Add it to requirements.txt and rebuild.")
        sys.exit(1)

    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "1_Pooling").mkdir(exist_ok=True)
    print(f"Downloading ONNX model from {MODEL_REPO} → {OUT_DIR}")

    for src_filename, local_name in ONNX_FILES.items():
        dest = OUT_DIR / local_name
        if dest.exists():
            print(f"  ✓ {local_name} (already present)")
            continue
        try:
            path = hf_hub_download(repo_id=MODEL_REPO, filename=src_filename, local_dir=str(OUT_DIR))
            # hf_hub_download preserves the src path structure — move to flat local_name if needed
            downloaded = OUT_DIR / src_filename
            if downloaded.exists() and downloaded != dest:
                dest.parent.mkdir(parents=True, exist_ok=True)
                downloaded.rename(dest)
            print(f"  ↓ {src_filename} → {dest}")
        except Exception as e:
            print(f"  ⚠ {src_filename}: {e} (skipping)")

    model_path = OUT_DIR / "model.onnx"
    tok_path = OUT_DIR / "tokenizer.json"

    if not model_path.exists():
        print(f"❌ model.onnx not found at {model_path}")
        sys.exit(1)
    if not tok_path.exists():
        print(f"❌ tokenizer.json not found at {tok_path}")
        sys.exit(1)

    # Quick sanity check
    print("\nRunning inference sanity check...")
    try:
        import onnxruntime as ort
        import numpy as np
        from tokenizers import Tokenizer

        tok = Tokenizer.from_file(str(tok_path))
        tok.enable_truncation(max_length=64)
        tok.enable_padding()

        titles = ["Senior Technical Program Manager", "Fry Cook"]
        encs = tok.encode_batch(titles)
        ids = np.array([e.ids for e in encs], dtype=np.int64)
        am  = np.array([e.attention_mask for e in encs], dtype=np.int64)

        sess = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        input_names = {i.name for i in sess.get_inputs()}
        feed = {"input_ids": ids, "attention_mask": am}
        if "token_type_ids" in input_names:
            feed["token_type_ids"] = np.zeros_like(ids)
        raw = sess.run(None, feed)[0]
        if raw.ndim == 3:
            mask = am[:, :, np.newaxis].astype(np.float32)
            vecs = (raw * mask).sum(axis=1) / mask.sum(axis=1).clip(min=1e-9)
        else:
            vecs = raw
        vecs = vecs / np.linalg.norm(vecs, axis=1, keepdims=True).clip(min=1e-9)
        cos = float((vecs[0] * vecs[1]).sum())
        print(f"  TPM vs Fry Cook cosine: {cos:.3f}  (expected < 0.5)")
        print("✅ ONNX model OK")
    except Exception as e:
        print(f"⚠ Sanity check failed: {e}")

if __name__ == "__main__":
    main()
