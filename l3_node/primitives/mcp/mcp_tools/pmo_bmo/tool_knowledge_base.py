"""
PMO 知识库分块与向量化 — mcp:atom_pmo_knowledge_base

从同步目录读取 Markdown，按段落分块（可限制单块最大字符），可选调用可插拔 Embedder 生成向量，
将每块写入 corpus 目录下的 .md（含 YAML frontmatter）并写入 manifest.jsonl 摘要。

配置: config/mcps/atom_pmo_knowledge_base/config.yaml

注意：向量维度依赖 ~/.jachin/nexus_config.json 中 embedding 配置；失败时仍写入文本块，仅无向量字段。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_root = Path(__file__).resolve().parents[4]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

DEFAULT_SOURCE_REL = "docs/pmo_bmo_plugin/synced"
DEFAULT_CORPUS_REL = "docs/pmo_bmo_plugin/corpus"


def _split_chunks(text: str, max_chars: int, overlap: int) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    parts = re.split(r"\n{2,}", text)
    chunks: list[str] = []
    buf = ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(buf) + len(p) + 2 <= max_chars:
            buf = f"{buf}\n\n{p}" if buf else p
        else:
            if buf:
                chunks.append(buf)
            if len(p) <= max_chars:
                buf = p
            else:
                for i in range(0, len(p), max(1, max_chars - overlap)):
                    chunks.append(p[i : i + max_chars])
                buf = ""
    if buf:
        chunks.append(buf)
    return chunks


async def _embed_one(text: str) -> list[float] | None:
    try:
        from core.embedding import get_embedder

        embedder = get_embedder(None)
        return await embedder.embed_text(text[:8000])
    except Exception as e:
        logger.debug("[pmo_knowledge_base] embed skip: %s", e)
        return None


def run_pmo_knowledge_base(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    operation:
      - ingest（默认）：扫描 source_dir 下 .md，输出到 corpus_dir
    """
    from l3_node.paths import get_app_root
    from l3_node.jachin_config import load_mcp_config

    args = dict(arguments or {})
    op = (args.pop("operation", None) or "ingest").strip().lower()
    root = get_app_root()
    cfg = load_mcp_config("atom_pmo_knowledge_base", project_root=root)
    cfg.update({k: v for k, v in args.items() if v is not None})

    if op != "ingest":
        return {"status": "error", "error": f"未知 operation: {op}（当前仅支持 ingest）"}

    source_rel = (cfg.get("source_dir_relative") or DEFAULT_SOURCE_REL).strip() or DEFAULT_SOURCE_REL
    corpus_rel = (cfg.get("corpus_dir_relative") or DEFAULT_CORPUS_REL).strip() or DEFAULT_CORPUS_REL
    max_chars = int(cfg.get("chunk_max_chars") or 2000)
    overlap = int(cfg.get("chunk_overlap") or 200)
    do_embed = cfg.get("embed", True)
    if str(do_embed).lower() in ("0", "false", "no"):
        do_embed = False
    else:
        do_embed = bool(do_embed)

    source_dir = (root / source_rel).resolve()
    corpus_dir = (root / corpus_rel).resolve()
    corpus_dir.mkdir(parents=True, exist_ok=True)
    chunks_sub = corpus_dir / "chunks"
    chunks_sub.mkdir(parents=True, exist_ok=True)

    md_files = sorted(source_dir.rglob("*.md")) if source_dir.is_dir() else []
    # 跳过 manifest 类
    md_files = [p for p in md_files if p.name != "00_SYNC_MANIFEST.json" and not p.name.endswith("_MANIFEST.json")]

    manifest_path = corpus_dir / "ingest_manifest.jsonl"
    rows_out: list[dict[str, Any]] = []
    total_chunks = 0

    for src in md_files:
        try:
            raw = src.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            rows_out.append({"source": str(src.relative_to(root)), "error": str(e)})
            continue
        # 去掉常见 YAML frontmatter（若有）
        body = raw
        if raw.startswith("---"):
            end = raw.find("\n---", 3)
            if end > 0:
                body = raw[end + 4 :].lstrip()
        rel = str(src.relative_to(root))
        chunks = _split_chunks(body, max_chars=max_chars, overlap=overlap)
        for i, ch in enumerate(chunks):
            total_chunks += 1
            h = hashlib.sha256(f"{rel}:{i}:{ch[:200]}".encode()).hexdigest()[:16]
            fname = f"{total_chunks:05d}_{h}.md"
            fpath = chunks_sub / fname
            emb_preview: list[float] | None = None
            emb_dim: int | None = None
            if do_embed:
                try:
                    vec = asyncio.run(_embed_one(ch))
                    if vec is not None:
                        emb_dim = len(vec)
                        emb_preview = [round(x, 6) for x in vec[:8]]
                except Exception:
                    emb_preview = None
                    emb_dim = None
            front = {
                "source_md": rel,
                "chunk_index": i,
                "chunk_id": h,
                "ingested_at": datetime.now(timezone.utc).isoformat(),
                "char_len": len(ch),
                "embedding_preview": emb_preview,
                "embedding_dim": emb_dim,
            }
            fpath.write_text(
                "---\n"
                + json.dumps(front, ensure_ascii=False, indent=2)
                + "\n---\n\n"
                + ch,
                encoding="utf-8",
            )
            row = {
                "chunk_file": str(fpath.relative_to(root)),
                "source_md": rel,
                "chunk_index": i,
                "has_embedding_preview": emb_preview is not None,
            }
            rows_out.append(row)

    manifest_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows_out) + ("\n" if rows_out else ""),
        encoding="utf-8",
    )

    return {
        "status": "success",
        "msg": f"ingest 完成：源文件 {len(md_files)} 个，块 {total_chunks} 个，输出 {corpus_dir}",
        "source_dir": str(source_dir),
        "corpus_dir": str(corpus_dir),
        "manifest": str(manifest_path.relative_to(root)),
        "files_scanned": len(md_files),
        "chunks_written": total_chunks,
    }


def atom_pmo_knowledge_base(config: dict[str, Any] | None = None) -> dict[str, Any]:
    return run_pmo_knowledge_base(config)
