"""
Jachin Nexus v8.0 - 全域向量路由引擎 (Semantic Router)

可插拔双引擎：Cloud (OpenAI) / Edge (ONNX Local)
基于 LanceDB，将自然语言意图转换为 Embedding，余弦相似度检索最匹配的技能。
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console

from core.embedding import BaseEmbedder, get_embedder

logger = logging.getLogger(__name__)
console = Console()

# 项目根
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SKILLS_REPO = _PROJECT_ROOT / "skills_repo"


def _parse_skill_frontmatter(skill_path: Path) -> dict[str, Any]:
    """解析 SKILL.md 的 YAML Frontmatter，提取 name、description"""
    try:
        text = skill_path.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
        if not match:
            return {}
        data = yaml.safe_load(match.group(1)) or {}
        return {"name": data.get("name", ""), "description": data.get("description", "")}
    except Exception as e:
        logger.debug("解析 SKILL.md 失败 %s: %s", skill_path, e)
        return {}


class SemanticRouter:
    """
    全域向量路由：意图 -> Embedding (可插拔) -> 余弦相似度 -> 最匹配技能
    """

    def __init__(
        self,
        skills_repo: Path | None = None,
        db_path: Path | None = None,
        embedder: BaseEmbedder | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.skills_repo = Path(skills_repo) if skills_repo else _SKILLS_REPO
        self.db_path = db_path or (Path.home() / ".jachin" / "vector_db")
        self._embedder = embedder or get_embedder(config)
        self._table = None

        # 启动时打印当前引擎模式
        engine_name = "🛡️ Edge (ONNX Local)" if "ONNX" in type(self._embedder).__name__ else "☁️ Cloud (OpenAI)"
        console.print(f"[bold green][INFO] Vector Router initialized. Engine: {engine_name}[/bold green]")

        # 维度校验：若 LanceDB 表维度与当前 Embedder 不符，drop 并重建，实现无感切换
        self._ensure_table_dimension_match()

    async def _get_embedding_async(self, text: str) -> list[float]:
        """异步调用 embedder"""
        return await self._embedder.embed_text(text)

    def _get_embedding(self, text: str) -> list[float]:
        """同步包装：在无事件循环或独立线程中运行异步 embed_text"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._embedder.embed_text(text))
        # 已有运行中的 loop，使用 run_in_executor 在线程中执行
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, self._embedder.embed_text(text))
            return future.result()

    def match_local_skill(
        self,
        intent: str,
        threshold: float = 0.75,
    ) -> dict[str, Any] | None:
        """同步版本：在独立 loop 中运行"""
        return asyncio.run(self.match_local_skill_async(intent, threshold))

    async def match_local_skill_async(
        self,
        intent: str,
        threshold: float = 0.75,
    ) -> dict[str, Any] | None:
        """
        将自然语言意图匹配到本地最相似的技能。

        Args:
            intent: 用户意图，如「帮我审查这个 PR」
            threshold: 相似度阈值，低于则返回 None

        Returns:
            {"skill_id": str, "path": str, "description": str, "score": float} 或 None
        """
        console.print(f"[cyan]🔮 Semantic Router[/cyan] 意图: [yellow]{intent}[/yellow]")
        emb = await self._get_embedding_async(intent)
        if not emb:
            console.print("[red]⚠ Embedding 生成失败，跳过向量检索[/red]")
            return None

        try:
            import lancedb
            db = lancedb.connect(str(self.db_path))
            if "skills" not in db.table_names():
                console.print("[yellow]⚠ skills 向量表未初始化，返回 None[/yellow]")
                return None
            tbl = db.open_table("skills")
            results = tbl.search(emb).limit(1).to_list()
            if not results:
                return None
            r = results[0]
            # L2 距离 → 余弦相似度（sentence-transformers 输出已归一化）: cos_sim = 1 - L2^2/2
            dist = r.get("_distance", 1.0)
            score = max(0.0, 1.0 - (dist * dist) / 2.0)
            if score < threshold:
                console.print(f"[dim]相似度 {score:.2f} < {threshold}，未命中[/dim]")
                return None
            console.print(f"[green]✓ 命中技能: {r.get('skill_id', '?')} (score={score:.2f})[/green]")
            return {
                "skill_id": r.get("skill_id", ""),
                "path": r.get("path", ""),
                "description": r.get("description", ""),
                "score": score,
            }
        except ImportError:
            console.print("[red]⚠ lancedb 未安装，跳过向量检索[/red]")
            return None
        except Exception as e:
            logger.exception("match_local_skill 异常: %s", e)
            return None

    def _ensure_table_dimension_match(self) -> None:
        """
        校验 LanceDB skills 表向量维度与当前 Embedder 是否一致。
        若不一致（如用户切换 Cloud/Edge 模式），drop 表并触发全量重建，实现无感切换。
        """
        try:
            import lancedb
            db = lancedb.connect(str(self.db_path))
            if "skills" not in db.table_names():
                # 首次运行：表不存在，直接全量建索引
                self.reindex_all_skills()
                return
            tbl = db.open_table("skills")
            # 取首行获取向量维度（兼容 to_pandas / to_list）
            rows = None
            if hasattr(tbl, "to_pandas"):
                try:
                    df = tbl.to_pandas()
                    rows = df.head(1) if not df.empty else None
                except Exception:
                    pass
            if rows is None and hasattr(tbl, "to_list"):
                try:
                    lst = tbl.to_list()
                    rows = [lst[0]] if lst else []
                except Exception:
                    rows = []
            if not rows or (hasattr(rows, "empty") and rows.empty):
                return
            first = rows.iloc[0] if hasattr(rows, "iloc") else (rows[0] if isinstance(rows, list) else None)
            if first is None:
                return
            vec = first.get("vector") if isinstance(first, dict) else getattr(first, "vector", None)
            if vec is None:
                return
            stored_dim = len(vec) if hasattr(vec, "__len__") else 0
            expected_dim = self._embedder.dimension
            if stored_dim != expected_dim:
                console.print(
                    f"[yellow]⚠ 向量维度不匹配 (表={stored_dim} vs 引擎={expected_dim})，"
                    "drop 表并重建索引[/yellow]"
                )
                db.drop_table("skills")
                self.reindex_all_skills()
        except ImportError:
            pass
        except Exception as e:
            logger.warning("维度校验异常: %s", e)

    def reindex_all_skills(self) -> None:
        """全量重建 skills 向量表：扫描 skills_repo/**/SKILL.md，逐个 index_skill"""
        skills = list(self.skills_repo.rglob("SKILL.md"))
        if not skills:
            console.print("[dim]skills_repo 下无 SKILL.md，跳过 reindex[/dim]")
            return
        count = 0
        for p in skills:
            meta = _parse_skill_frontmatter(p)
            name = meta.get("name") or p.parent.name
            desc = meta.get("description") or ""
            if not name:
                continue
            path_str = str(p.resolve())
            self.index_skill(skill_id=name, path=path_str, description=desc)
            count += 1
        console.print(f"[green]✓ reindex_all_skills 完成，共 {count} 个技能[/green]")

    def index_skill(self, skill_id: str, path: str, description: str) -> None:
        """将技能索引到向量表。"""
        emb = self._get_embedding(f"{skill_id} {description}")
        if not emb:
            return
        try:
            import lancedb
            db = lancedb.connect(str(self.db_path))
            if "skills" in db.table_names():
                tbl = db.open_table("skills")
                tbl.add([{"id": skill_id, "skill_id": skill_id, "path": path, "description": description, "vector": emb}])
            else:
                db.create_table("skills", data=[{"id": skill_id, "skill_id": skill_id, "path": path, "description": description, "vector": emb}])
        except Exception as e:
            logger.warning("index_skill 失败: %s", e)


# 云端未命中时返回的 HITL 状态
HITL_REQUIRED = "HITL_REQUIRED"


def require_hitl_for_cloud_skill(skill_name: str, fee: str = "") -> dict[str, Any]:
    """
    当 vector_router 本地未命中、需向云端请求时，返回 HITL 状态。
    调用方必须挂起，等待 Layer 3 或 IM 的确认，严禁静默下载执行。
    """
    console.print("[bold red]⚠ HITL_REQUIRED[/bold red] 云端技能需人工授权:")
    console.print(f"  技能: [yellow]{skill_name}[/yellow]  费用: [yellow]{fee or '待确认'}[/yellow]")
    console.print("[red]严禁静默下载执行未知云端逻辑！等待确认...[/red]")
    return {
        "status": HITL_REQUIRED,
        "skill_name": skill_name,
        "fee": fee,
        "message": "请通过桌面弹窗或 IM 确认后重试",
    }
