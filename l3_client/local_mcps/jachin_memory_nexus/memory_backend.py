"""
Memory Nexus 持久化底座：SQLite 单文件 + NumPy 向量相似度 + FastEmbed 本地嵌入。

- 数据库：``~/.jachin/palace_db/memory_nexus.sqlite3``（与旧 Chroma 目录同父级，便于打包分发）
- 表：``drawers``（wing / room / document / embedding BLOB float32 / metadata JSON）
- 语义检索：在候选子集（默认最近 N 条，可环境变量覆盖）上做余弦相似度，返回与旧 API 兼容的 ``distance``（1 - cosine_sim，越小越近）

``CHROMA_USE_HTTP_CLIENT`` 等旧变量已忽略；若需换模型设 ``JACHIN_MEMORY_EMBED_MODEL``（FastEmbed 模型名）。

跨 Agent 共享（AW）：
  设 JACHIN_NEXUS_SHARED_PATH=/shared/path/memory_nexus.sqlite3 后，
  所有 Agent（同机多进程或挂载同一共享文件系统）共用同一 SQLite 数据库，
  实现记忆共享（SQLite WAL 模式保证并发读写安全）。
  未设置时退回 ~/.jachin/palace_db/memory_nexus.sqlite3（原有行为不变）。

向量主导检索（AW）：
  设 JACHIN_NEXUS_VECTOR_LEAD=1 后，检索得分由纯向量余弦相似度主导，
  时间衰减和 Wing 重要性仅作微权调整（各缩小为原权重的 0.3 倍）。
  适合跨 Agent 共享场景——不同 Agent 的记忆时间戳差异大，纯时间衰减会导致偏差。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar

from l3_client.local_mcps.jachin_memory_nexus.wing_registry import (
    normalize_wing,
    wing_half_life_days,
    wing_importance_mult,
)

logger = logging.getLogger(__name__)

_PALACE_ROOT = Path.home() / ".jachin" / "palace_db"
_SQLITE_NAME = "memory_nexus.sqlite3"

# FastEmbed 模型（多语言默认，利于中英文混合桌面）
_DEFAULT_EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

_conn_lock = threading.RLock()
_sqlite_conn: sqlite3.Connection | None = None
_embed_lock = threading.RLock()
_embedder: Any | None = None
_embed_model_name: str | None = None

T = TypeVar("T")


def _db_file() -> Path:
    """返回 SQLite 文件路径。
    若设置了 JACHIN_NEXUS_SHARED_PATH，使用共享路径（跨 Agent 共享记忆，AW）；
    否则使用本地默认路径。
    """
    shared = (os.environ.get("JACHIN_NEXUS_SHARED_PATH") or "").strip()
    if shared:
        p = Path(shared)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    _PALACE_ROOT.mkdir(parents=True, exist_ok=True)
    return _PALACE_ROOT / _SQLITE_NAME


def _invalidate_connection() -> None:
    global _sqlite_conn
    logger.info("[Memory Nexus][SQLite] invalidate：关闭连接并丢弃缓存")
    with _conn_lock:
        if _sqlite_conn is not None:
            try:
                _sqlite_conn.close()
            except Exception:
                pass
            _sqlite_conn = None


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS drawers (
            drawer_id TEXT PRIMARY KEY NOT NULL,
            wing TEXT NOT NULL,
            room TEXT NOT NULL,
            document TEXT NOT NULL,
            embedding BLOB NOT NULL,
            dim INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            extra_meta_json TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_drawers_wing_room_ts ON drawers (wing, room, timestamp DESC)"
    )
    conn.commit()


def _get_connection() -> sqlite3.Connection:
    global _sqlite_conn
    with _conn_lock:
        if _sqlite_conn is not None:
            return _sqlite_conn
        path = _db_file()
        logger.info("[Memory Nexus][SQLite] 打开数据库 path=%s", path)
        _sqlite_conn = sqlite3.connect(str(path), check_same_thread=False, timeout=60.0)
        _sqlite_conn.row_factory = sqlite3.Row
        _init_schema(_sqlite_conn)
        return _sqlite_conn


def _with_db_retry(operation: Callable[[sqlite3.Connection], T], *, op_label: str = "sqlite_op") -> T:
    logger.info("[Memory Nexus][SQLite] 操作「%s」开始", op_label)
    try:
        return operation(_get_connection())
    except sqlite3.OperationalError as first:
        logger.warning(
            "[Memory Nexus][SQLite] 操作「%s」失败，将 invalidate 后重试一次: %s",
            op_label,
            first,
        )
        _invalidate_connection()
        return operation(_get_connection())


def _preload_onnxruntime_for_memory_nexus() -> None:
    """
    FastEmbed 文本路径仍依赖 onnxruntime；在导入 fastembed 子模块前先预检，
    避免根包 ``from fastembed import ...`` 的链式导入掩盖真实死因。

    Windows 常见：缺少 VC++ 运行库、GPU/directml 轮子与 CPU 版冲突、坏缓存。
    """
    try:
        import onnxruntime as _ort  # noqa: F401
    except Exception as e:
        logger.error(
            "[Memory Nexus] onnxruntime 无法加载（FastEmbed 硬依赖）。底层: %s",
            e,
            exc_info=True,
        )
        win_extra = ""
        if sys.platform == "win32":
            win_extra = (
                "\n[Windows 常见修复]\n"
                "1) 安装 Microsoft Visual C++ 2015–2022 Redistributable (x64)：\n"
                "   https://aka.ms/vs/17/release/vc_redist.x64.exe\n"
                "2) 在**启动 L3 的同一 Python** 中重装 CPU 版 onnxruntime，并去掉冲突轮子：\n"
                "   python -m pip uninstall -y onnxruntime-gpu onnxruntime-directml onnxruntime\n"
                "   python -m pip install --no-cache-dir \"onnxruntime>=1.17.3,<1.24\"\n"
                "3) 一键脚本（仓库根）：\n"
                "   powershell -ExecutionPolicy Bypass -File .\\scripts\\repair-onnxruntime-windows.ps1\n"
            )
        raise RuntimeError(
            "Memory Nexus 无法加载 onnxruntime，无法使用 FastEmbed 嵌入。"
            + win_extra
            + f"\n底层异常: {e}"
        ) from e


def _get_embedder() -> Any:
    global _embedder, _embed_model_name
    if (os.environ.get("CHROMA_USE_HTTP_CLIENT", "").strip().lower() in ("1", "true", "yes", "on")):
        logger.debug("[Memory Nexus] 已忽略 CHROMA_USE_HTTP_CLIENT（Nexus 现为 SQLite+FastEmbed）")
    model_name = (os.environ.get("JACHIN_MEMORY_EMBED_MODEL") or _DEFAULT_EMBED_MODEL).strip()
    with _embed_lock:
        if _embedder is not None and _embed_model_name == model_name:
            return _embedder
        _preload_onnxruntime_for_memory_nexus()
        try:
            # 避免 ``from fastembed import TextEmbedding`` 走包根 __init__ 先拉 ImageEmbedding 等无关链
            from fastembed.text.text_embedding import TextEmbedding
        except ImportError as e:
            logger.error(
                "[Memory Nexus] 无法导入 fastembed.text.TextEmbedding（未安装或解释器不一致）。底层: %s",
                str(e),
                exc_info=True,
            )
            raise RuntimeError(
                "Memory Nexus 无法导入 fastembed，底层: "
                + str(e)
                + "；请执行: python -m pip install 'fastembed>=0.4.0' numpy（须与启动 L3 的 python 相同）"
            ) from e

        cache_dir = _PALACE_ROOT / "models"
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(
                "[Memory Nexus] 无法创建 FastEmbed 模型缓存目录 %s，底层: %s",
                cache_dir,
                str(e),
                exc_info=True,
            )
            raise

        logger.info(
            "[Memory Nexus][FastEmbed] 正在初始化 model=%s cache_dir=%s（首次可能下载权重）",
            model_name,
            cache_dir,
        )
        try:
            inst = TextEmbedding(model_name=model_name, cache_dir=str(cache_dir))
        except Exception as e:
            logger.error(
                "[Memory Nexus] FastEmbed 模型加载或下载失败，真实原因: %s",
                str(e),
                exc_info=True,
            )
            raise
        logger.info("[Memory Nexus][FastEmbed] 模型加载成功 model=%s", model_name)
        _embedder = inst
        _embed_model_name = model_name
        return _embedder


def _embed_one(text: str) -> Any:
    """单条文本 → L2 归一化 float32 向量（numpy）。"""
    import numpy as np

    t = (text or "").strip()
    if not t:
        raise ValueError("empty text for embedding")
    model = _get_embedder()
    raw = model.embed([t])
    if isinstance(raw, (list, tuple)):
        emb = raw[0]
    else:
        emb = next(iter(raw))
    v = np.asarray(emb, dtype=np.float32).reshape(-1)
    n = float(np.linalg.norm(v))
    if n > 1e-12:
        v = v / np.float32(n)
    return v


def embed_texts_normalized_list(texts: list[str]) -> list[Any]:
    """
    批量嵌入并逐条 L2 归一化为 float32 向量（list[np.ndarray]）。
    供 Experience RAG 等对多条候选一次性推理；单条失败则整批交由调用方降级。
    """
    import numpy as np

    if not texts:
        return []
    model = _get_embedder()
    clean = [(t or "").strip() or " " for t in texts]
    raw = model.embed(clean)
    rows: list[Any]
    if isinstance(raw, np.ndarray):
        mat = np.asarray(raw, dtype=np.float32)
        if mat.ndim == 1:
            mat = mat.reshape(1, -1)
        rows = [mat[i].reshape(-1) for i in range(mat.shape[0])]
    else:
        rows = []
        for emb in raw:
            v = np.asarray(emb, dtype=np.float32).reshape(-1)
            rows.append(v)
    out: list[Any] = []
    for v in rows:
        n = float(np.linalg.norm(v))
        if n > 1e-12:
            v = v / np.float32(n)
        out.append(v)
    return out


def _embedding_to_blob(vec: Any) -> tuple[bytes, int]:
    import numpy as np

    arr = np.asarray(vec, dtype=np.float32).reshape(-1)
    return arr.tobytes(), int(arr.shape[0])


def _blob_to_vec(blob: bytes, dim: int) -> Any:
    import numpy as np

    return np.frombuffer(blob, dtype=np.float32, count=dim).copy()


def _palace_path() -> Path:
    """兼容旧调用：本地持久化目录（与 Chroma 时代路径一致）。"""
    _PALACE_ROOT.mkdir(parents=True, exist_ok=True)
    return _PALACE_ROOT


def _normalize_extra_meta(extra_meta: dict[str, Any] | None) -> dict[str, Any]:
    if not extra_meta:
        return {}
    out: dict[str, Any] = {}
    for k, v in extra_meta.items():
        key = str(k)[:256]
        if isinstance(v, (str, int, float, bool)):
            out[key] = v
        else:
            out[f"{key}_json"] = json.dumps(v, ensure_ascii=False)[:8192]
    return out


def _parse_ts(meta: dict[str, Any] | None) -> float:
    raw = (meta or {}).get("timestamp") or ""
    if not isinstance(raw, str):
        return 0.0
    s = raw.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return 0.0


def commit_drawer(
    text: str,
    wing: str,
    room: str,
    extra_meta: dict[str, Any] | None = None,
) -> str:
    # AL: 写入时归一化 wing 名，消除旧名/别名不一致
    wing = normalize_wing(wing)
    logger.info(
        "[Memory Nexus] commit_drawer 入口 wing=%s room=%s text_len=%d",
        wing,
        room,
        len(text or ""),
    )
    drawer_id = str(uuid.uuid4())
    ts = datetime.now(timezone.utc).isoformat()
    meta: dict[str, Any] = {
        "wing": wing,
        "room": room,
        "timestamp": ts,
        "drawer_id": drawer_id,
    }
    meta.update(_normalize_extra_meta(extra_meta))
    vec = _embed_one(text)
    blob, dim = _embedding_to_blob(vec)
    extra_json = json.dumps(meta, ensure_ascii=False)

    def _ins(conn: sqlite3.Connection) -> str:
        logger.info(
            "[Memory Nexus][SQLite] commit_drawer INSERT id=%s wing=%s room=%s dim=%d",
            drawer_id,
            wing,
            room,
            dim,
        )
        conn.execute(
            """
            INSERT INTO drawers (drawer_id, wing, room, document, embedding, dim, timestamp, extra_meta_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (drawer_id, str(wing), str(room), text, blob, dim, ts, extra_json),
        )
        conn.commit()
        logger.info(
            "[Memory Nexus] commit_drawer 完成 id=%s wing=%s room=%s chars=%d",
            drawer_id,
            wing,
            room,
            len(text or ""),
        )
        return drawer_id

    return _with_db_retry(_ins, op_label="commit_drawer")


def delete_drawers_in_room(wing: str, room: str) -> None:
    wing_s = str(wing).strip()
    room_s = str(room).strip()
    if not wing_s or not room_s:
        return
    logger.info("[Memory Nexus] delete_drawers_in_room wing=%s room=%s", wing_s, room_s)

    def _del(conn: sqlite3.Connection) -> None:
        conn.execute("DELETE FROM drawers WHERE wing = ? AND room = ?", (wing_s, room_s))
        conn.commit()

    try:
        _with_db_retry(_del, op_label="delete_drawers_in_room")
    except Exception as e:
        logger.warning("[Memory Nexus] delete_drawers_in_room failed: %s", e)
        raise


def upsert_drawer(
    drawer_id: str,
    text: str,
    wing: str,
    room: str,
    extra_meta: dict[str, Any] | None = None,
) -> str:
    # AL: 写入时归一化 wing 名
    wing = normalize_wing(wing)
    did = str(drawer_id).strip()
    if not did:
        raise ValueError("upsert_drawer: empty drawer_id")
    ts = datetime.now(timezone.utc).isoformat()
    meta: dict[str, Any] = {
        "wing": wing,
        "room": room,
        "timestamp": ts,
        "drawer_id": did,
    }
    meta.update(_normalize_extra_meta(extra_meta))
    vec = _embed_one(text)
    blob, dim = _embedding_to_blob(vec)
    extra_json = json.dumps(meta, ensure_ascii=False)

    def _up(conn: sqlite3.Connection) -> str:
        logger.info("[Memory Nexus][SQLite] upsert_drawer id=%s wing=%s room=%s dim=%d", did, wing, room, dim)
        conn.execute(
            """
            INSERT INTO drawers (drawer_id, wing, room, document, embedding, dim, timestamp, extra_meta_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(drawer_id) DO UPDATE SET
                wing=excluded.wing,
                room=excluded.room,
                document=excluded.document,
                embedding=excluded.embedding,
                dim=excluded.dim,
                timestamp=excluded.timestamp,
                extra_meta_json=excluded.extra_meta_json
            """,
            (did, str(wing), str(room), text, blob, dim, ts, extra_json),
        )
        conn.commit()
        return did

    return _with_db_retry(_up, op_label="upsert_drawer")


def recall_room(wing: str, room: str, limit: int = 5) -> dict[str, Any]:
    lim = max(1, min(int(limit), 100))
    fetch_cap = min(500, max(lim * 20, lim))

    def _recall(conn: sqlite3.Connection) -> dict[str, Any]:
        cur = conn.execute(
            """
            SELECT drawer_id, document, extra_meta_json, timestamp
            FROM drawers
            WHERE wing = ? AND room = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (str(wing), str(room), fetch_cap),
        )
        rows = cur.fetchall()
        pairs: list[tuple[float, str, dict[str, Any], str]] = []
        for row in rows:
            rid = str(row["drawer_id"])
            doc = str(row["document"] or "")
            raw_meta = row["extra_meta_json"]
            try:
                m = json.loads(raw_meta) if raw_meta else {}
            except json.JSONDecodeError:
                m = {}
            if not isinstance(m, dict):
                m = {}
            pairs.append((_parse_ts(m), rid, m, doc))
        pairs.sort(key=lambda x: x[0], reverse=True)
        drawers: list[dict[str, Any]] = []
        for _, rid, m, doc in pairs[:lim]:
            drawers.append({"id": rid, "text": doc, "metadata": m})
        return {"ok": True, "wing": wing, "room": room, "count": len(drawers), "drawers": drawers}

    try:
        return _with_db_retry(_recall, op_label="recall_room")
    except Exception as e:
        logger.warning("recall_room failed: %s", e)
        return {"ok": False, "error": repr(e), "drawers": []}


def _deep_search_candidate_cap() -> int:
    raw = (os.environ.get("JACHIN_NEXUS_DEEP_SEARCH_CANDIDATES") or "2500").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 2500
    return max(50, min(n, 50_000))


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 遗忘曲线时间衰减（§4 方案 D）+ Wing 重要性乘数（§4 P0 AI + AL Wing 全量重映射）
#
# AL（Wing 全量重映射）：
#   半衰期和重要性系数统一从 wing_registry.WingMeta 读取，不再硬编码字典；
#   写入时 wing 名通过 normalize_wing() 归一化，消除旧名/别名不一致问题。
#
# 环境变量：
#   JACHIN_NEXUS_TIME_DECAY_WEIGHT  0~1（默认 0.2）；0 = 纯语义，不含时间权重
#   JACHIN_NEXUS_WING_IMPORTANCE_WEIGHT  0~1（默认 0.15）；0 = 关闭乘数调权
#   JACHIN_WING_IMPORTANCE_OVERRIDE  JSON 对象，可覆盖各 Wing 的重要性系数
# ---------------------------------------------------------------------------


def _time_decay_weight() -> float:
    raw = (os.environ.get("JACHIN_NEXUS_TIME_DECAY_WEIGHT") or "0.2").strip()
    try:
        w = float(raw)
    except ValueError:
        w = 0.2
    return max(0.0, min(w, 1.0))


def _wing_importance_weight() -> float:
    """Wing 重要性乘数的强度权重（0 = 完全关闭，1 = 按完整系数调权）。"""
    raw = (os.environ.get("JACHIN_NEXUS_WING_IMPORTANCE_WEIGHT") or "0.15").strip()
    try:
        w = float(raw)
    except ValueError:
        w = 0.15
    return max(0.0, min(w, 1.0))


def _vector_lead_mode() -> bool:
    """向量主导检索模式（AW）：开启后时间/Wing 权重各缩小为 0.3 倍，向量余弦相似度主导。
    适合跨 Agent 共享场景，避免时间戳差异导致检索偏差。
    环境变量：JACHIN_NEXUS_VECTOR_LEAD=1"""
    return (os.environ.get("JACHIN_NEXUS_VECTOR_LEAD") or "").strip().lower() in (
        "1", "true", "yes"
    )


def _compute_wing_importance(wing: str, importance_weight: float) -> float:
    """返回最终乘数：lerp(1.0, wing_mult, importance_weight)。
    wing_mult 来自 wing_registry，支持 JACHIN_WING_IMPORTANCE_OVERRIDE 覆盖。
    """
    if importance_weight <= 0.0:
        return 1.0
    mult = wing_importance_mult(normalize_wing(wing))
    return 1.0 + (mult - 1.0) * importance_weight


def _compute_time_decay(timestamp: float, now: float, wing: str = "") -> float:
    """Ebbinghaus 简化遗忘曲线：decay = 0.5 ^ (age_days / half_life_days)。
    半衰期从 wing_registry 读取（AL 全量重映射后支持 Inbox=7d / Core=180d 等）。
    """
    half_life = wing_half_life_days(normalize_wing(wing))
    age_days = max(0.0, (now - timestamp) / 86400.0)
    return float(0.5 ** (age_days / half_life))


def deep_search(query: str, wing: str | None = None, limit: int = 5) -> dict[str, Any]:
    import numpy as np

    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "empty query", "matches": []}
    n_out = max(1, min(int(limit), 50))
    cap = _deep_search_candidate_cap()
    wing_f = str(wing).strip() if wing else ""

    try:
        qv = _embed_one(q)
    except Exception as e:
        logger.warning("deep_search embed query failed: %s", e, exc_info=True)
        return {"ok": False, "error": repr(e), "matches": []}
    q_dim = int(qv.shape[0])

    tdw = _time_decay_weight()    # 0 = 纯语义；>0 = 融合时间衰减
    wiw = _wing_importance_weight()  # 0 = 不调 Wing 重要性权重
    # 向量主导模式（AW）：跨 Agent 共享时缩小时间/Wing 权重，突出纯语义相似度
    if _vector_lead_mode():
        tdw = tdw * 0.3
        wiw = wiw * 0.3

    def _search(conn: sqlite3.Connection) -> dict[str, Any]:
        if wing_f:
            cur = conn.execute(
                """
                SELECT drawer_id, wing, room, document, embedding, dim, timestamp, extra_meta_json
                FROM drawers
                WHERE wing = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (wing_f, cap),
            )
        else:
            cur = conn.execute(
                """
                SELECT drawer_id, wing, room, document, embedding, dim, timestamp, extra_meta_json
                FROM drawers
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (cap,),
            )
        rows = cur.fetchall()
        if not rows:
            return {"ok": True, "query": q, "wing_filter": wing, "count": 0, "matches": []}

        blobs: list[bytes] = []
        dims: list[int] = []
        metas: list[dict[str, Any]] = []
        ids: list[str] = []
        docs: list[str] = []
        wings: list[str] = []
        timestamps: list[float] = []
        for row in rows:
            d = int(row["dim"])
            if d != q_dim:
                continue
            blobs.append(row["embedding"])
            dims.append(d)
            ids.append(str(row["drawer_id"]))
            docs.append(str(row["document"] or ""))
            wings.append(str(row["wing"] or ""))
            try:
                ts = float(row["timestamp"] or 0.0)
            except (TypeError, ValueError):
                ts = 0.0
            timestamps.append(ts)
            try:
                m = json.loads(row["extra_meta_json"] or "{}")
            except json.JSONDecodeError:
                m = {}
            metas.append(m if isinstance(m, dict) else {})

        if not blobs:
            return {"ok": True, "query": q, "wing_filter": wing, "count": 0, "matches": []}

        mat = np.stack([_blob_to_vec(b, dims[0]) for b in blobs], axis=0)
        sims = mat @ qv.astype(np.float32)

        if tdw > 0.0:
            # 融合时间衰减：final_score = sem*(1-w) + decay*w；score 越高越好
            now_ts = __import__("time").time()
            decays = np.array(
                [_compute_time_decay(timestamps[i], now_ts, wings[i]) for i in range(len(blobs))],
                dtype=np.float64,
            )
            sem_scores = sims.astype(np.float64)
            combined = sem_scores * (1.0 - tdw) + decays * tdw
        else:
            # 纯语义（保持原始行为）
            combined = sims.astype(np.float64)

        # Wing 重要性分级乘数（AI）：对不同 Wing 记忆提权/降权
        if wiw > 0.0:
            importance_mults = np.array(
                [_compute_wing_importance(wings[i], wiw) for i in range(len(blobs))],
                dtype=np.float64,
            )
            combined = combined * importance_mults
            # 限制在 [0, 1]，避免乘数超出 distance 约定范围
            combined = np.clip(combined, 0.0, 1.0)

        # distance 约定：越小越相似（与 Chroma 旧习惯一致）
        dists = 1.0 - combined

        order = np.argsort(dists)[:n_out]

        matches: list[dict[str, Any]] = []
        for i in order:
            idx = int(i)
            matches.append(
                {
                    "id": ids[idx],
                    "text": docs[idx],
                    "distance": float(dists[idx]),
                    "metadata": metas[idx],
                }
            )
        return {"ok": True, "query": q, "wing_filter": wing, "count": len(matches), "matches": matches}

    try:
        return _with_db_retry(_search, op_label="deep_search")
    except Exception as e:
        logger.warning("deep_search failed: %s", e)
        return {"ok": False, "error": repr(e), "matches": []}
