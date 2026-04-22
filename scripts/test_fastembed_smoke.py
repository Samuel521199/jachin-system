#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FastEmbed 冒烟测试：与 Memory Nexus 相同的 onnx 预检 + ``fastembed.text.text_embedding`` 导入路径。

用法（须与启动 L3 的 Python 一致）:
  python scripts/test_fastembed_smoke.py
  python scripts/test_fastembed_smoke.py --model "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

环境变量:
  JACHIN_MEMORY_EMBED_MODEL  覆盖默认模型名（与 l3_client/.../memory_backend 一致）

成功: 退出码 0；失败: 非 0 并打印异常栈。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _print_ok(msg: str) -> None:
    print(f"[OK] {msg}")


def _print_fail(msg: str) -> None:
    print(f"[FAIL] {msg}", file=sys.stderr)


def _cosine(a, b) -> float:
    import numpy as np

    x = np.asarray(a, dtype=np.float64).ravel()
    y = np.asarray(b, dtype=np.float64).ravel()
    nx = float(np.linalg.norm(x))
    ny = float(np.linalg.norm(y))
    if nx < 1e-12 or ny < 1e-12:
        return 0.0
    return float(np.dot(x, y) / (nx * ny))


def main() -> int:
    parser = argparse.ArgumentParser(description="FastEmbed / onnxruntime smoke test")
    parser.add_argument(
        "--model",
        default="",
        help="FastEmbed model_name (default: env JACHIN_MEMORY_EMBED_MODEL or multilingual MiniLM)",
    )
    args = parser.parse_args()

    default_model = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    model_name = (args.model or os.environ.get("JACHIN_MEMORY_EMBED_MODEL") or default_model).strip()

    print("=== FastEmbed smoke test ===")
    print(f"sys.executable = {sys.executable}")
    print(f"model_name     = {model_name}")

    # 1) onnxruntime（与 memory_backend 一致）
    try:
        import onnxruntime as ort

        _print_ok(f"onnxruntime import OK, version={ort.__version__}")
    except Exception as e:
        _print_fail(f"onnxruntime: {e}")
        raise

    # 2) FastEmbed 子模块导入（避免包根 __init__ 抢先拉 image）
    try:
        from fastembed.text.text_embedding import TextEmbedding
    except Exception as e:
        _print_fail(f"import TextEmbedding: {e}")
        raise

    cache_dir = Path.home() / ".jachin" / "palace_db" / "models"
    cache_dir.mkdir(parents=True, exist_ok=True)
    print(f"cache_dir      = {cache_dir}")

    # 3) 加载模型（首次会下载）
    try:
        print("[..] loading TextEmbedding (first run may download weights)...")
        model = TextEmbedding(model_name=model_name, cache_dir=str(cache_dir))
        _print_ok("TextEmbedding constructed")
    except Exception as e:
        _print_fail(f"TextEmbedding load: {e}")
        raise

    def embed_one(text: str):
        raw = model.embed([text])
        if isinstance(raw, (list, tuple)):
            emb = raw[0]
        else:
            emb = next(iter(raw))
        import numpy as np

        return np.asarray(emb, dtype=np.float32).ravel()

    # 4) 单条嵌入：维度、有限性
    try:
        v0 = embed_one("hello world")
    except Exception as e:
        _print_fail(f"embed: {e}")
        raise

    import numpy as np

    dim = int(v0.shape[0])
    if dim < 8:
        _print_fail(f"embedding dim too small: {dim}")
        return 2
    if not np.all(np.isfinite(v0)):
        _print_fail("embedding has non-finite values")
        return 3
    _print_ok(f"single embed dim={dim}, finite=True")

    # 5) 语义相似度：相近句应比无关句更相似（多语言模型对中英文各测一对）
    a1 = embed_one("日本东京是首都圈的核心城市")
    a2 = embed_one("东京位于日本关东地方")
    b1 = embed_one("量子纠缠与贝尔不等式")
    s_close = _cosine(a1, a2)
    s_far = _cosine(a1, b1)
    print(f"cosine(close_paraphrase_zh) = {s_close:.4f}")
    print(f"cosine(far_topic_zh)        = {s_far:.4f}")
    if s_close <= s_far + 0.02:
        _print_fail("expected paraphrase pair more similar than unrelated (threshold +0.02 slack)")
        return 4
    _print_ok("semantic sanity: close > far")

    c1 = embed_one("The cat sleeps on the sofa.")
    c2 = embed_one("A cat is sleeping on the couch.")
    d1 = embed_one("Stock market volatility increased today.")
    s_en_close = _cosine(c1, c2)
    s_en_far = _cosine(c1, d1)
    print(f"cosine(close_paraphrase_en) = {s_en_close:.4f}")
    print(f"cosine(far_topic_en)        = {s_en_far:.4f}")
    if s_en_close <= s_en_far + 0.02:
        _print_fail("English paraphrase pair not more similar than unrelated")
        return 5
    _print_ok("English semantic sanity: close > far")

    # 6) 小批量（与 Nexus 批处理习惯接近）
    try:
        texts = ["alpha", "beta", "gamma"]
        raw = model.embed(texts)
        rows = list(raw) if not isinstance(raw, (list, tuple)) else raw
        if len(rows) != len(texts):
            _print_fail(f"batch embed count {len(rows)} != {len(texts)}")
            return 6
        _print_ok(f"batch embed count={len(texts)}")
    except Exception as e:
        _print_fail(f"batch embed: {e}")
        raise

    print("=== all checks passed ===")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        raise SystemExit(130)
    except Exception:
        import traceback

        traceback.print_exc()
        raise SystemExit(1)
