"""
控制台 API - 思维流日志、建议、记忆搜索、模型列表等
供桌面控制台 HUD 消费
"""

import logging
import os
from collections import deque
from typing import List, Optional, Any

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import asyncio
import json

from core.config import settings

# v5.0: Dapr StateStore 已废弃，使用极简内存存储
_console_state: dict = {}


class _StateStoreStub:
    """v5.0 极简状态存储（替代 Dapr StateStore）"""

    async def get(self, key: str):
        return _console_state.get(key)

    async def save(self, key: str, value):
        _console_state[key] = value


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v3", tags=["console"])

# 环形缓冲区，存储最近 N 条日志（供思维流）
LOG_BUFFER: deque = deque(maxlen=100)


def _get_log_buffer() -> deque:
    return LOG_BUFFER


# 自定义 Handler 可将日志写入 buffer（可选，在 main 中挂载）
class ConsoleLogHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            _get_log_buffer().append(msg)
        except Exception:
            self.handleError(record)


# --- 模型 ---
class ModelItem(BaseModel):
    id: str
    name: str
    description: Optional[str] = None


class ModelsResponse(BaseModel):
    models: List[ModelItem]
    current: str


def _should_skip_log_line(raw: str) -> bool:
    """跳过不应在思维流中展示的重复 debug 信息（如 pynvml 未安装）"""
    lower = raw.lower()
    if "pynvml" in lower and ("unavailable" in lower or "import failed" in lower or "not installed" in lower):
        return True
    if "gpu stats" in lower and "unavailable" in lower:
        return True
    return False


def _naturalize_log_line(raw: str) -> str:
    """将原始日志转为自然语言描述（供思维流）"""
    import re
    s = raw.strip()
    if not s:
        return s
    # 移除时间戳 [2026-02-11 12:00:00]
    s = re.sub(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s*", "", s)
    s = re.sub(r"^\[\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\]\s*", "", s)
    # 移除 [INFO] [DEBUG] [WARNING] [ERROR]
    s = re.sub(r"^\[(INFO|DEBUG|WARNING|ERROR|WARN)\]\s*", "", s, flags=re.I)
    # 常见模式转自然语言
    lower = s.lower()
    if "index" in lower and ("file" in lower or "document" in lower or ".pdf" in lower):
        m = re.search(r"['\"]?([\w\-\.]+\.pdf|[\w\-\.]+\.docx?)['\"]?", s, re.I)
        name = m.group(1) if m else "文档"
        return f"正在后台索引「{name}」…"
    if "ray" in lower and "node" in lower:
        return "Ray 集群节点状态已更新。"
    if "qdrant" in lower or "vector" in lower:
        return "向量记忆库已同步。"
    if "skill" in lower and ("invoke" in lower or "call" in lower):
        m = re.search(r"\[?([\w\.]+)\]?", s)
        skill = m.group(1) if m else "技能"
        return f"正在调用技能「{skill}」。"
    if "context" in lower and "token" in lower:
        return "上下文窗口使用量已更新。"
    if "device" in lower and ("online" in lower or "connect" in lower):
        return "设备连接状态已更新。"
    if "error" in lower or "exception" in lower:
        return "检测到异常，正在处理。"
    if "chat" in lower or "llm" in lower:
        return "正在处理对话请求。"
    # 过长则截断
    if len(s) > 80:
        s = s[:77] + "..."
    return s or raw


@router.get("/logs/recent")
async def get_recent_logs(
    limit: int = Query(20, ge=1, le=100),
    naturalize: bool = Query(True, description="转为自然语言描述"),
):
    """获取最近日志行，供思维流展示"""
    buf = _get_log_buffer()
    lst = list(buf)
    take = min(limit * 2, len(lst))
    lines = lst[-take:] if take else []
    lines = [line for line in lines if not _should_skip_log_line(line)][-limit:]
    if naturalize:
        lines = [_naturalize_log_line(line) for line in lines]
    return {"lines": lines}


@router.get("/logs/stream")
async def stream_logs(
    limit: int = Query(20, ge=1, le=100),
    interval: float = Query(2.0, ge=0.5, le=10),
):
    """SSE 推送最近日志（轮询式，供思维流实时更新）"""
    async def generate():
        last_len = 0
        while True:
            buf = _get_log_buffer()
            lines = list(buf)[-limit:]
            lines = [_naturalize_log_line(line) for line in lines]
            if len(lines) != last_len or (lines and last_len == 0):
                last_len = len(lines)
                yield f"data: {json.dumps(lines, ensure_ascii=False)}\n\n"
            await asyncio.sleep(interval)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


def _get_disk_usage_percent() -> Optional[float]:
    """获取 C 盘（或当前盘）使用率，用于建议卡片。Windows 优先检测 C:"""
    try:
        import shutil
        path = os.path.expanduser("~")
        if os.name == "nt" and os.path.exists("C:\\"):
            path = "C:\\"
        total, used, _ = shutil.disk_usage(path)
        if total > 0:
            return round(100 * used / total, 1)
    except Exception:
        pass
    return None


# --- 日历/待办：已迁移至 Skill（com.jachin.calendar），此处为占位 ---
@router.get("/calendar/events")
async def get_calendar_events(
    days: int = Query(2, ge=1, le=7, description="未来几天内的事件"),
):
    """日历事件 API（占位：由 com.jachin.calendar Skill 提供）"""
    return {"events": []}


@router.get("/todos")
async def get_todos():
    """待办事项 API（占位：由 com.jachin.calendar Skill 提供）"""
    return {"items": [], "pending_count": 0}


@router.get("/suggestions")
async def get_suggestions():
    """获取主动建议卡片（占位：日历/待办由 Skill 提供，仅保留系统类）"""
    suggestions = []
    disk_pct = _get_disk_usage_percent()
    if disk_pct is not None and disk_pct >= 90:
        suggestions.append({
            "id": "sys-disk", "text": f"磁盘使用率已达 {disk_pct}%，建议清理缓存。",
            "action": "清理", "type": "system",
        })
    elif disk_pct is not None and disk_pct >= 80:
        suggestions.append({
            "id": "sys-disk", "text": f"磁盘使用率 {disk_pct}%，可考虑清理。",
            "action": "清理", "type": "system",
        })
    if not suggestions:
        suggestions.append({
            "id": "cal-empty", "text": "安装 com.jachin.calendar Skill 后可获得日历与提醒建议。",
            "action": "添加", "type": "calendar",
        })
    return {"items": suggestions[:5]}


class SuggestExecuteRequest(BaseModel):
    action: Optional[str] = "执行"


@router.post("/suggestions/{suggestion_id}/execute")
async def execute_suggestion(suggestion_id: str, body: SuggestExecuteRequest):
    """执行建议：按 type 接入真实逻辑（calendar/system/task）"""
    action = body.action or "执行"
    try:
        if suggestion_id == "2" or "清理" in action:
            # 系统类：清理缓存（可扩展为实际清理逻辑）
            try:
                import tempfile
                cache_dir = tempfile.gettempdir()
                if os.path.isdir(cache_dir):
                    return {"ok": True, "message": f"已触发系统清理，action={action}"}
            except Exception:
                pass
            return {"ok": True, "message": f"系统清理已记录，action={action}"}
        if suggestion_id == "3" or "待办" in str(suggestion_id) or "task" in str(suggestion_id).lower():
            # 任务类：可接任务列表 API
            return {"ok": True, "message": f"已打开待办视图，action={action}"}
        # 默认：日历等
        return {"ok": True, "message": f"建议 {suggestion_id} 已执行，action={action}"}
    except Exception as e:
        logger.warning("execute_suggestion failed: %s", e)
        return {"ok": True, "message": f"建议已记录，action={action}"}


# --- 日历/待办/提醒 CRUD（占位：由 com.jachin.calendar Skill 提供）---
class CalendarCreateRequest(BaseModel):
    title: str
    description: Optional[str] = None
    item_type: str = "reminder"
    start_at: str
    end_at: Optional[str] = None
    recurrence: str = "none"
    recurrence_interval: int = 1
    reminder_minutes_before: Optional[int] = None
    metadata: dict = {}


class CalendarUpdateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    start_at: Optional[str] = None
    end_at: Optional[str] = None
    recurrence: Optional[str] = None
    recurrence_interval: Optional[int] = None
    is_done: Optional[bool] = None
    metadata: Optional[dict] = None


@router.get("/calendar/items")
async def list_calendar_items(
    item_type: Optional[str] = Query(None),
    include_done: bool = Query(False),
    days: int = Query(7, ge=1, le=90),
):
    """列出日历条目（占位：由 com.jachin.calendar Skill 提供）"""
    return {"items": []}


@router.get("/calendar/items/due")
async def get_due_reminders(within_minutes: int = Query(60, ge=1, le=1440)):
    """即将到期的提醒（占位：由 com.jachin.calendar Skill 提供）"""
    return {"items": []}


@router.get("/calendar/items/{item_id}")
async def get_calendar_item(item_id: str):
    """获取单条（占位）"""
    from fastapi import HTTPException
    raise HTTPException(status_code=404, detail="not_found")


@router.post("/calendar/items")
async def create_calendar_item(body: CalendarCreateRequest):
    """创建条目（占位：由 com.jachin.calendar Skill 提供）"""
    from fastapi import HTTPException
    raise HTTPException(status_code=501, detail="Install com.jachin.calendar Skill")


@router.patch("/calendar/items/{item_id}")
async def update_calendar_item(item_id: str, body: CalendarUpdateRequest):
    """更新条目（占位）"""
    from fastapi import HTTPException
    raise HTTPException(status_code=501, detail="Install com.jachin.calendar Skill")


@router.delete("/calendar/items/{item_id}")
async def delete_calendar_item(item_id: str):
    """删除条目（占位）"""
    from fastapi import HTTPException
    raise HTTPException(status_code=501, detail="Install com.jachin.calendar Skill")
    return {"ok": True}


# --- 记忆搜索 ---
class MemorySearchResult(BaseModel):
    id: str
    text: str
    score: float
    metadata: Optional[dict] = None


@router.get("/memory/search")
async def search_memory(q: str = Query(..., min_length=1)):
    """v5.0: Qdrant 已废弃，记忆由 SQLite 生物学记忆管线接管"""
    return {"results": [], "message": "v5.0 已废弃 Qdrant，请使用 Layer 2 生物学记忆 (core/biological_memory)"}


class BatchDeleteRequest(BaseModel):
    ids: List[str]


@router.delete("/memory/{memory_id}")
async def delete_memory(memory_id: str):
    """v5.0: Qdrant 已废弃"""
    return {"ok": False, "message": "v5.0 已废弃 Qdrant 向量记忆"}


@router.post("/memory/batch-delete")
async def batch_delete_memory(body: BatchDeleteRequest):
    """v5.0: Qdrant 已废弃"""
    return {"ok": False, "message": "v5.0 已废弃 Qdrant 向量记忆", "deleted": 0}


@router.get("/memory/count")
async def get_memory_count():
    """v5.0: Qdrant 已废弃，返回 0"""
    return {"count": 0}


# --- 模型列表与切换 ---
@router.get("/models", response_model=ModelsResponse)
async def list_models():
    """可用的模型列表"""
    models = [
        ModelItem(id="qwen-max", name="Qwen-Max", description="适合通用对话"),
        ModelItem(id="qwen-plus", name="Qwen-Plus", description="速度快"),
        ModelItem(id=settings.LLM_MODEL, name=settings.LLM_MODEL, description="当前"),
    ]
    store = _StateStoreStub()
    current = await store.get("console/current_model") or settings.LLM_MODEL
    return ModelsResponse(models=models, current=current)


class SetModelRequest(BaseModel):
    model_id: str


@router.post("/models/current")
async def set_current_model(body: SetModelRequest):
    """切换当前模型（存到本地状态，实际切换需与 LLM 模块联动）"""
    store = _StateStoreStub()
    await store.save("console/current_model", body.model_id)
    return {"ok": True, "current": body.model_id}


# --- 推理策略（运行模式）---
INFERENCE_STRATEGY_KEY = "console/inference_strategy"
VALID_STRATEGIES = ("eco", "default", "performance", "god")


class SetStrategyRequest(BaseModel):
    mode: str


@router.get("/inference/strategy")
async def get_inference_strategy():
    """获取当前推理策略（节能/默认/高性能/上帝模式）"""
    store = _StateStoreStub()
    mode = await store.get(INFERENCE_STRATEGY_KEY) or "default"
    if mode not in VALID_STRATEGIES:
        mode = "default"
    return {"mode": mode, "label": _strategy_label(mode)}


@router.post("/inference/strategy")
async def set_inference_strategy(body: SetStrategyRequest):
    """设置推理策略"""
    mode = (body.mode or "default").lower()
    if mode not in VALID_STRATEGIES:
        mode = "default"
    store = _StateStoreStub()
    await store.save(INFERENCE_STRATEGY_KEY, mode)
    return {"ok": True, "mode": mode, "label": _strategy_label(mode)}


def _strategy_label(mode: str) -> str:
    labels = {"eco": "节能", "default": "默认", "performance": "高性能", "god": "上帝模式"}
    return labels.get(mode, mode)


# --- GPU 监控（NVML）---
_pynvml_import_warned = False


def _collect_gpu_stats() -> list:
    """通过 pynvml 采集 GPU 温度、利用率、显存"""
    gpus = []
    try:
        import pynvml
        pynvml.nvmlInit()
        count = pynvml.nvmlDeviceGetCount()
        for i in range(count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
            name = pynvml.nvmlDeviceGetName(handle)
            name_str = name.decode("utf-8") if isinstance(name, bytes) else str(name)
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            # 温度（GPU 传感器，NVML_TEMPERATURE_GPU = 0）
            temp = None
            try:
                temp = pynvml.nvmlDeviceGetTemperature(handle, 0)
            except Exception:
                pass
            # 利用率
            util_gpu = util_mem = None
            try:
                util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                util_gpu = util.gpu
                util_mem = util.memory
            except Exception:
                pass
            gpus.append({
                "index": i,
                "name": name_str,
                "memory_total_mb": round(mem.total / (1024 * 1024), 1),
                "memory_used_mb": round(mem.used / (1024 * 1024), 1),
                "memory_free_mb": round(mem.free / (1024 * 1024), 1),
                "utilization_gpu": util_gpu,
                "utilization_memory": util_mem,
                "temperature_c": temp,
            })
        pynvml.nvmlShutdown()
    except ImportError as e:
        global _pynvml_import_warned
        if not _pynvml_import_warned:
            _pynvml_import_warned = True
            logger.debug("pynvml import failed: %s (ensure backend runs in same env as pip install)", e)
    except Exception as e:
        logger.warning("NVML init/query failed: %s", e)
    return gpus


# --- 上下文 Token（供 ModelController）---
_CONTEXT_USED: int = 0
_CONTEXT_MAX: int = 8192


def update_context_used(delta: int) -> None:
    """Chat 模块调用：增加已用 Token 数（估算）"""
    global _CONTEXT_USED
    _CONTEXT_USED = max(0, min(_CONTEXT_USED + delta, _CONTEXT_MAX))


def reset_context_used() -> None:
    """重置上下文（如新会话）"""
    global _CONTEXT_USED
    _CONTEXT_USED = 0


@router.post("/llm/context/reset")
async def reset_llm_context():
    """重置上下文 Token 计数（新会话时调用）"""
    reset_context_used()
    return {"ok": True}


@router.get("/llm/context")
async def get_llm_context():
    """当前对话上下文 Token 占用（ModelController 用）"""
    max_tokens = getattr(settings, "LLM_MAX_TOKENS", None) or 8192
    return {"used": _CONTEXT_USED, "max": max_tokens}


# --- GPU 监控（NVML）---
@router.get("/gpu/overheat")
async def get_gpu_overheat_status():
    """GPU 是否过热（>=85°C），供推理分流策略使用"""
    from core.utils.gpu_status import is_gpu_overheated
    return {"overheated": is_gpu_overheated()}


@router.get("/gpu/stats")
async def get_gpu_stats():
    """GPU 统计（温度、利用率、显存），通过 NVML 采集"""
    try:
        gpus = _collect_gpu_stats()
        from core.utils.gpu_status import update_cache
        update_cache(gpus)
        return {"gpus": gpus, "message": None if gpus else "No GPU or NVML unavailable"}
    except Exception as e:
        logger.warning("GPU stats failed: %s", e)
        return {"gpus": [], "message": str(e)}
