"""
Jachin Nexus V2 - L2 梦境扩展 (基于 LanceDB)

打通 dream_weaver 与 L2 记忆：聚类、LLM 融合、冲突消解、记忆升维。
- 聚类：向量距离近 + 时间戳相近的记忆簇
- 融合：LLM 去冗余、冲突保留最新时间戳、输出连贯长期记忆
- 升维：新记忆存为 long_term，旧碎片物理删除
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from core.db.l2_memory_lancedb import (
    _cosine_similarity,
    _run_embed_sync,
    delete_memories_by_ids,
    get_short_term_memories,
    insert_long_term_memory,
)
from core.embedding import BaseEmbedder, get_embedder

logger = logging.getLogger(__name__)

_CLUSTER_SIM_THRESHOLD = 0.85  # 向量相似度阈值，高于此视为同簇
_CLUSTER_TIME_WINDOW = 86400 * 7  # 时间窗口 7 天（秒），同簇内时间戳需在此范围内
_WEAVE_THRESHOLD = 3  # 至少 3 条碎片才触发融合

_L2_DREAM_SYSTEM_PROMPT = """你是一个 Jachin 边缘智能体的「长期记忆整理器」。请分析以下记忆碎片，完成以下任务：

1. **去除冗余**：将语义相同或高度重叠的内容合并，避免重复表述。
2. **冲突消解**：若存在逻辑冲突（如「喜欢茶」与「只喝咖啡」），保留**最新时间戳**对应的结论，并标注已按时间优先消解；若冲突仍无法自动消解、必须由用户确认，在正文之后**另起一行**输出：`CLARIFICATION: <一句中文说明>`。
3. **提炼核心**：输出一段连贯、高密度的长期记忆，可直接用于后续检索与推理。
4. **偏好**（可选）：若可提取稳定偏好，在全文**最后另起一行**输出：`PREFERENCE_JSON: {"键":"值"}`（单行合法 JSON 对象）。
5. **用户修正（P2）**：若碎片含「【用户修正】【p2】」或「【用户修正】」，表示用户在纠正助手；融合时必须优先采纳其「期望」语义，不得忽略。

每条碎片格式为：`[timestamp=xxx] content`，timestamp 为 Unix 秒，越大越新。

输出格式：先输出一段自然段正文；如需澄清或偏好行，严格按上述前缀另起行追加。不要输出 JSON 数组或 Markdown 列表作为正文。"""


def _cluster_memories(
    memories: list[dict[str, Any]],
    embedder: BaseEmbedder,
    sim_threshold: float = _CLUSTER_SIM_THRESHOLD,
    time_window: float = _CLUSTER_TIME_WINDOW,
) -> list[list[dict[str, Any]]]:
    """
    语义聚类：向量距离极近 + 时间戳相近的记忆归为一簇。
    返回簇列表，每簇为一个 memory 列表。
    """
    if not memories:
        return []

    # 确保每条记忆有向量
    for m in memories:
        if "vector" not in m or not m.get("vector"):
            text = (m.get("text") or "").strip()
            if text:
                vec = _run_embed_sync(embedder, text)
                if vec:
                    m["vector"] = vec

    valid = [m for m in memories if m.get("vector")]
    if not valid:
        return []

    clusters: list[list[dict[str, Any]]] = []
    used = set()

    for i, m in enumerate(valid):
        if i in used:
            continue
        cluster = [m]
        used.add(i)
        ts_i = float(m.get("timestamp", 0))
        vec_i = m.get("vector", [])

        for j, n in enumerate(valid):
            if j in used or j <= i:
                continue
            ts_j = float(n.get("timestamp", 0))
            if abs(ts_i - ts_j) > time_window:
                continue
            vec_j = n.get("vector", [])
            sim = _cosine_similarity(vec_i, vec_j)
            if sim >= sim_threshold:
                cluster.append(n)
                used.add(j)

        clusters.append(cluster)

    return clusters


def _build_cluster_text(cluster: list[dict[str, Any]]) -> str:
    """将簇内记忆格式化为 LLM 输入：`[timestamp=xxx] content`（修正碎片优先）"""

    def _is_corr(m: dict[str, Any]) -> bool:
        tx = str(m.get("text", ""))
        return "【用户修正】【p2】" in tx or "【用户修正】" in tx

    lines = []
    for m in sorted(
        cluster,
        key=lambda x: (0 if _is_corr(x) else 1, float(x.get("timestamp", 0))),
    ):
        ts = m.get("timestamp", 0)
        text = (m.get("text") or "").strip()
        if text:
            lines.append(f"[timestamp={ts}] {text}")
    return "\n".join(lines)


async def _call_llm_for_fusion(cluster_text: str) -> str:
    """调用 LLM 执行记忆融合，返回一段连贯的长期记忆文本。"""
    try:
        from core.llm_provider import CognitiveEngineFactory
        engine = CognitiveEngineFactory.get_engine()
        messages = [
            {"role": "system", "content": _L2_DREAM_SYSTEM_PROMPT},
            {"role": "user", "content": f"记忆碎片：\n\n{cluster_text}\n\n请进行融合，输出一段连贯的长期记忆。"},
        ]
        result = await engine.generate_response(messages, temperature=0.3, max_tokens=1024)
        if isinstance(result, dict):
            result = result.get("content", "") or ""
        return (result or "").strip()
    except Exception as e:
        logger.warning("[L2DreamWeaver] LLM 调用失败: %s", e)
        return ""


async def weave_dreams_for_sub_account(sub_account_id: str) -> int:
    """
    对指定子账号执行梦境优化：聚类 → LLM 融合 → 升维 long_term → 删除旧碎片。

    Returns:
        新写入的长期记忆条数
    """
    try:
        embedder = get_embedder()
    except Exception as e:
        logger.warning("[L2DreamWeaver] Embedder 初始化失败: %s", e)
        return 0

    fragments = get_short_term_memories(sub_account_id, limit=100)
    if len(fragments) < _WEAVE_THRESHOLD:
        logger.debug("[L2DreamWeaver] 碎片数量 %d < 阈值 %d，跳过", len(fragments), _WEAVE_THRESHOLD)
        return 0

    clusters = _cluster_memories(fragments, embedder)
    if not clusters:
        return 0

    inserted = 0
    ids_to_delete: list[str] = []

    for cluster in clusters:
        if len(cluster) < _WEAVE_THRESHOLD:
            continue
        cluster_text = _build_cluster_text(cluster)
        if not cluster_text.strip():
            continue

        fused_text = await _call_llm_for_fusion(cluster_text)
        if not fused_text or len(fused_text) < 10:
            continue

        try:
            from l3_node.intelligence_p1 import ingest_dream_auxiliary_lines, strip_dream_auxiliary_lines

            ingest_dream_auxiliary_lines(fused_text)
            fused_text = strip_dream_auxiliary_lines(fused_text)
        except ImportError:
            pass

        node_ids = list({m.get("node_id", "") for m in cluster if m.get("node_id")})
        node_id = node_ids[0] if node_ids else ""

        if not fused_text or len(fused_text) < 10:
            continue

        if insert_long_term_memory(sub_account_id, fused_text, node_id):
            inserted += 1
            ids_to_delete.extend(m["id"] for m in cluster)

    if ids_to_delete:
        deleted = delete_memories_by_ids(ids_to_delete)
        logger.info("[L2DreamWeaver] sub=%s 融合完成：%d 条长期记忆，删除 %d 条碎片", sub_account_id[:8], inserted, deleted)

    return inserted


def trigger_weave_async(sub_account_id: str) -> None:
    """异步触发子账号的梦境优化，不阻塞调用方。"""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(weave_dreams_for_sub_account(sub_account_id))
    except RuntimeError:
        asyncio.run(weave_dreams_for_sub_account(sub_account_id))
