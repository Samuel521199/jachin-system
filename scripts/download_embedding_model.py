#!/usr/bin/env python3
"""
Edge Embedding 模型预下载 - L2 记忆向量检索

预下载 sentence-transformers all-MiniLM-L6-v2 (~90MB)，避免 L2 首次使用 edge 模式时卡顿。
"""

from __future__ import annotations

import sys

if sys.platform == "win32":
    try:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass


def main() -> int:
    try:
        from sentence_transformers import SentenceTransformer
        print("[INFO] 正在下载 all-MiniLM-L6-v2 (~90MB)...")
        SentenceTransformer("all-MiniLM-L6-v2")
        print("[OK] Embedding 模型已缓存")
        return 0
    except ImportError:
        print("[错误] 需要 sentence-transformers，请运行: pip install sentence-transformers")
        return 1
    except Exception as e:
        print(f"[WARN] 预下载失败: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
