"""
Jachin Nexus v8.0 - 虫群大脑 (Cognitive Swarm via LiteLLM) + 流式神经

LiteLLM 万能模型库：model_name 透传，彻底清除 API 差异冗余。
支持 gpt-4o、通义 DashScope 等 LiteLLM 兼容模型。
API Key 优先级：环境变量 > .env > ~/.jachin/nexus_config.json llm_keys
未配置 Key 时默认仍指向 dashscope/qwen3.5-plus（调用将失败直至配置 DASHSCOPE_API_KEY），不再使用已弃用的 Ollama 默认回退。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

# 尽早加载 .env，确保 OPENAI_API_KEY 等被注入（IDE/子进程可能未继承系统环境变量）
# Windows 下 echo > .env 或记事本可能产生 UTF-16，需尝试多种编码；加载成功后转为 UTF-8 供 pydantic-settings 使用
def _load_dotenv_safe() -> None:
    """加载项目根目录 .env，确保 Layer2 daemon 无论从何目录启动都能读到 DASHSCOPE_API_KEY 等"""
    try:
        from dotenv import load_dotenv
        # 优先从 core 的父目录（项目根）加载，再尝试 cwd
        for _p in [Path(__file__).resolve().parent.parent, Path.cwd()]:
            _e = _p / ".env"
            if _e.exists():
                try:
                    load_dotenv(_e, encoding="utf-8")
                except UnicodeDecodeError:
                    load_dotenv(_e, encoding="utf-16")
                    try:
                        _e.write_text(_e.read_text(encoding="utf-16"), encoding="utf-8")
                    except Exception:
                        pass
                break
    except ImportError:
        pass

_load_dotenv_safe()

import litellm
from rich.console import Console

from core.brain.llm.dashscope_regional import (
    get_dashscope_regional_api_base,
    get_dashscope_regional_credentials,
    litellm_apply_dashscope_credentials,
)

logger = logging.getLogger(__name__)
console = Console()
_NEXUS_CONFIG = Path.home() / ".jachin" / "nexus_config.json"

# 阿里百炼 / DashScope：与 DASHSCOPE_API_KEY 共用；推理用 plus、编码用 coder-plus，降级可用 flash
DASHSCOPE_REASONING_MODEL = "dashscope/qwen3.5-plus"
DASHSCOPE_CODER_MODEL = "dashscope/qwen3-coder-plus"
DASHSCOPE_COMPLEX_MODEL = "dashscope/qwen-max"
DASHSCOPE_ECON_FALLBACK_MODEL = "dashscope/qwen3.5-flash"

_IGNITION_EMITTED = False
_IGNITION_CODER_EMITTED = False


def _load_nexus_config() -> dict[str, Any]:
    """读取 ~/.jachin/nexus_config.json，兼容 UTF-8 / UTF-16（Windows 可能产生 UTF-16）"""
    if not _NEXUS_CONFIG.exists():
        return {}
    try:
        return json.loads(_NEXUS_CONFIG.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        try:
            return json.loads(_NEXUS_CONFIG.read_text(encoding="utf-16"))
        except Exception:
            return {}
    except Exception:
        return {}


def _llm_model_from_env_or_settings() -> str:
    """裸模型名或带前缀均可；空则默认 qwen3.5-plus（推理/分析）。"""
    raw = (os.environ.get("LLM_MODEL") or "").strip()
    if not raw:
        try:
            from core.config import settings

            raw = (getattr(settings, "LLM_MODEL", None) or "").strip()
        except ImportError:
            pass
    return raw if raw else "qwen3.5-plus"


def _get_model_name(config: dict[str, Any] | None = None) -> str:
    """
    从配置读取 model_name，透传给 LiteLLM。
    示例：gpt-4o, dashscope/qwen3.5-plus
    已配置百炼/DashScope Key 且未显式写 model_name 时，默认 qwen3.5-plus（推理）；编码见 get_coder_model_litellm_id。
    """
    cfg = config or _load_nexus_config()
    llm_cfg = cfg.get("llm") or {}
    if isinstance(llm_cfg, dict):
        name = (
            llm_cfg.get("model_name")
            or llm_cfg.get("cloud_model")
            or llm_cfg.get("edge_model")
            or cfg.get("model_name")
        )
        if name and str(name).strip():
            return str(name).strip()
    # 当 OPENAI_API_KEY 用于 Qwen 时，默认使用 qwen 模型
    use_for_qwen = os.environ.get("QWEN_USE_OPENAI_KEY", "").lower() in ("1", "true", "yes")
    if not use_for_qwen and isinstance(llm_cfg, dict):
        use_for_qwen = llm_cfg.get("use_openai_key_for_qwen", False)
    if use_for_qwen:
        return _llm_model_from_env_or_settings()
    # 若已配置 DASHSCOPE/QWEN API Key，默认用通义千问（推理默认 qwen3.5-plus），避免回退到未启动的 Ollama
    if os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("QWEN_API_KEY") or os.environ.get("QWEN_AI_API_KEY"):
        return _llm_model_from_env_or_settings()
    try:
        from core.config import settings
        if getattr(settings, "DASHSCOPE_API_KEY", None) or getattr(settings, "QWEN_API_KEY", None):
            return _llm_model_from_env_or_settings()
    except ImportError:
        pass
    # 兜底：检查 nexus_config / credential_loader（可能尚未注入到 os.environ）
    try:
        from core.brain.llm.credential_loader import get_dashscope_key
        if get_dashscope_key():
            return _llm_model_from_env_or_settings()
    except ImportError:
        pass
    llm_keys = (cfg.get("llm_keys") or {}) if isinstance(cfg.get("llm_keys"), dict) else {}
    if llm_keys.get("dashscope"):
        return _llm_model_from_env_or_settings()
    return "gpt-4o-mini"  # LiteLLM 默认兜底


def _get_openai_key_from_sources() -> str | None:
    """多源读取 OpenAI Key：env > credential_loader > nexus_config 直接读取"""
    val = os.environ.get("OPENAI_API_KEY")
    if val and str(val).strip():
        return str(val).strip()
    try:
        from core.brain.llm.credential_loader import get_openai_key
        val = get_openai_key()
        if val:
            return val
    except ImportError:
        pass
    cfg = _load_nexus_config()
    llm_keys = (cfg.get("llm_keys") or {}) if isinstance(cfg.get("llm_keys"), dict) else {}
    val = llm_keys.get("openai")
    if val and str(val).strip():
        return str(val).strip()
    return None


def _has_dashscope_key() -> bool:
    """检测是否已配置通义千问 API Key（env / credential_loader / nexus_config）"""
    if os.environ.get("DASHSCOPE_API_KEY_CN") or os.environ.get("DASHSCOPE_API_KEY_SEA"):
        return True
    if os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("QWEN_API_KEY") or os.environ.get("QWEN_AI_API_KEY"):
        return True
    try:
        from core.config import settings
        if getattr(settings, "DASHSCOPE_API_KEY", None) or getattr(settings, "QWEN_API_KEY", None):
            return True
    except ImportError:
        pass
    try:
        from core.brain.llm.credential_loader import get_dashscope_key
        if get_dashscope_key():
            return True
    except ImportError:
        pass
    cfg = _load_nexus_config()
    llm_keys = (cfg.get("llm_keys") or {}) if isinstance(cfg.get("llm_keys"), dict) else {}
    return bool(llm_keys.get("dashscope"))


def _inject_api_keys() -> None:
    """从多源注入 API Key 到环境变量，供 LiteLLM 读取"""
    val = _get_openai_key_from_sources()
    if val and not os.environ.get("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = val
    try:
        from core.brain.llm.credential_loader import get_dashscope_key
        val = get_dashscope_key()
        if val and not os.environ.get("DASHSCOPE_API_KEY"):
            os.environ["DASHSCOPE_API_KEY"] = str(val).strip()
    except ImportError:
        cfg = _load_nexus_config()
        llm_keys = (cfg.get("llm_keys") or {}) if isinstance(cfg.get("llm_keys"), dict) else {}
        val = llm_keys.get("dashscope")
        if val and not os.environ.get("DASHSCOPE_API_KEY"):
            os.environ["DASHSCOPE_API_KEY"] = str(val).strip()
    # 当 OPENAI_API_KEY 实际存的是阿里云 Qwen 密钥时：注入到 DASHSCOPE，供 qwen/* 模型使用
    if not os.environ.get("DASHSCOPE_API_KEY") and os.environ.get("OPENAI_API_KEY"):
        use_for_qwen = os.environ.get("QWEN_USE_OPENAI_KEY", "").lower() in ("1", "true", "yes")
        if not use_for_qwen:
            cfg = _load_nexus_config()
            llm = (cfg.get("llm") or {}) if isinstance(cfg.get("llm"), dict) else {}
            use_for_qwen = llm.get("use_openai_key_for_qwen", False)
        if use_for_qwen:
            os.environ["DASHSCOPE_API_KEY"] = os.environ["OPENAI_API_KEY"]
    # 区域化 CN/SEA：同步 DASHSCOPE_API_KEY 与 DASHSCOPE_API_BASE，供 LiteLLM 与旧路径读取
    _rk, _rb = get_dashscope_regional_credentials()
    if _rk:
        os.environ["DASHSCOPE_API_KEY"] = str(_rk).strip()
    if _rb:
        os.environ["DASHSCOPE_API_BASE"] = str(_rb).strip()


def _model_needs_key(model: str) -> tuple[bool, str | None]:
    """判断模型是否需要 API Key，返回 (需要, 环境变量名)"""
    m = (model or "").lower()
    if m.startswith("ollama/") or m.startswith("ollama:"):
        return False, None
    if m.startswith("openai/") or m.startswith("gpt-"):
        return True, "OPENAI_API_KEY"
    if m.startswith("qwen/") or m.startswith("dashscope/") or m.startswith("qwen-"):
        return True, "DASHSCOPE_API_KEY"
    # 默认按 OpenAI 处理（gpt-4o-mini 等）
    return True, "OPENAI_API_KEY"


# 百炼控制台偶发「快照」模型 id：带 -YYYY-MM-DD 后缀；稳定版应为无日期名（如 qwen3-max）。
_QWEN_SNAPSHOT_DATE_SUFFIX = re.compile(r"-20\d{2}-\d{2}-\d{2}$")


def _strip_dashscope_qwen_snapshot_date_suffix(model: str) -> str:
    """
    去掉 DashScope/通义模型名末尾的 ``-YYYY-MM-DD`` 快照后缀。

    配置中若误写 ``qwen3-max-2026-01-23``，日志与 LiteLLM 会长期显示快照名；统一剥除后
    与控制台「稳定版」模型 id 对齐（不改变 ollama/*、非 qwen 尾名）。
    """
    m = (model or "").strip()
    if not m:
        return m
    if "/" in m:
        prefix, tail = m.rsplit("/", 1)
        if prefix.lower() not in ("dashscope", "qwen"):
            return m
    else:
        prefix, tail = "", m
    if not tail.lower().startswith("qwen"):
        return m
    new_tail = _QWEN_SNAPSHOT_DATE_SUFFIX.sub("", tail)
    if new_tail == tail:
        return m
    return f"{prefix}/{new_tail}" if prefix else new_tail


def _normalize_model_for_litellm(model: str) -> str:
    """将裸模型名转为 LiteLLM 所需格式，如 qwen-max -> dashscope/qwen-max；并剥除通义快照日期后缀。"""
    m = (model or "").strip()
    if not m:
        return m
    ml = m.lower()
    if ml.startswith("ollama/") or ml.startswith("ollama:"):
        return m
    if ml.startswith("qwen") and not ml.startswith("qwen/") and not ml.startswith("dashscope/"):
        m = f"dashscope/{m}"
    return _strip_dashscope_qwen_snapshot_date_suffix(m)


def get_coder_model_litellm_id(config: dict[str, Any] | None = None) -> str:
    """
    编码/子 Agent coder 角色用模型；与推理模型共用百炼 API Key。
    优先级：nexus llm.coder_model_name → 环境变量 LLM_CODER_MODEL → 默认 qwen3-coder-plus。
    """
    cfg = config or _load_nexus_config()
    llm_cfg = cfg.get("llm") or {}
    if isinstance(llm_cfg, dict):
        for key in ("coder_model_name", "coder_model", "code_model_name"):
            name = llm_cfg.get(key)
            if name and str(name).strip():
                return _normalize_model_for_litellm(str(name).strip())
    env_m = (os.environ.get("LLM_CODER_MODEL") or "").strip()
    if env_m:
        return _normalize_model_for_litellm(env_m)
    try:
        from core.config import settings

        sc = (getattr(settings, "LLM_CODER_MODEL", None) or "").strip()
        if sc:
            return _normalize_model_for_litellm(sc)
    except ImportError:
        pass
    return DASHSCOPE_CODER_MODEL


def get_complex_model_litellm_id(config: dict[str, Any] | None = None) -> str:
    """
    复杂推理用模型（长链路、子 Agent、大上下文等）；与推理/编码共用百炼 Key。
    优先级：nexus llm.complex_model_name → 环境变量 LLM_COMPLEX_MODEL → 默认 qwen-max。
    """
    cfg = config or _load_nexus_config()
    llm_cfg = cfg.get("llm") or {}
    if isinstance(llm_cfg, dict):
        for key in ("complex_model_name", "complex_model", "heavy_model_name"):
            name = llm_cfg.get(key)
            if name and str(name).strip():
                return _normalize_model_for_litellm(str(name).strip())
    env_m = (os.environ.get("LLM_COMPLEX_MODEL") or "").strip()
    if env_m:
        return _normalize_model_for_litellm(env_m)
    try:
        from core.config import settings

        sc = (getattr(settings, "LLM_COMPLEX_MODEL", None) or "").strip()
        if sc:
            return _normalize_model_for_litellm(sc)
    except ImportError:
        pass
    return DASHSCOPE_COMPLEX_MODEL


def _coerce_user_content_to_text(content: Any) -> str:
    """OpenAI 多模态 user content 可能为 str 或 list[dict]；启发式路由只应看文本段。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for p in content:
            if isinstance(p, dict) and p.get("type") == "text":
                parts.append(str(p.get("text") or ""))
        return "\n".join(parts).strip() if parts else ""
    return str(content or "")


def user_message_content_has_openai_image(content: Any) -> bool:
    """
    OpenAI Chat 格式：user.content 为 list 且含 type=image_url 时，需走视觉模型（如 qwen-vl）。
    主推理模型（qwen3.5-plus 等）走 LiteLLM 时可能不消费 image_url，表现为「用户已传图但模型称未见」。
    """
    if not isinstance(content, list):
        return False
    for p in content:
        if not isinstance(p, dict):
            continue
        if p.get("type") == "image_url":
            return True
        # 少数兼容形态
        if p.get("type") in ("input_image", "image"):
            return True
    return False


def l3_react_full_messages_need_vision_model(full_messages: list[dict[str, Any]] | None) -> bool:
    """任一条 user 消息含 OpenAI 图片块则本轮 ReAct 应使用多模态（VL）模型。"""
    if not full_messages:
        return False
    for m in full_messages:
        if (m.get("role") or "").strip() != "user":
            continue
        if user_message_content_has_openai_image(m.get("content")):
            return True
    return False


def litellm_model_supports_openai_multimodal_chat(model: str) -> bool:
    """
    粗略判断 LiteLLM 模型 id 是否可能按 OpenAI Chat 格式消费 ``user.content`` 中的 ``image_url`` 块。

    用于过滤 ``fallback_models``：若含图会话在重试时降级到 ``qwen3.5-flash`` / ``qwen3.5-plus`` 等
    **纯语言**模型，API 仍可能 200，但模型侧会忽略图片，表现为「用户未上传图」。
    """
    m = (model or "").strip().lower()
    if not m:
        return False
    if "embedding" in m or "text-embedding" in m:
        return False
    tail = m.split("/")[-1] if "/" in m else m
    if tail.startswith("gpt-4o"):
        return True
    if "gemini" in m and "embedding" not in m:
        return True
    if "claude-3" in m or "claude-3-" in m:
        return True
    if "qwen-vl" in m or "qwen2-vl" in m or "qwen2.5-vl" in m or "qwen3-vl" in m:
        return True
    # 通义 3.5 主推理模型在兼容模式下按 OpenAI 多模态 Chat 消费 image_url（与 ReAct 多模态路由一致）
    if "qwen3.5-plus" in m:
        return True
    return False


def dashscope_vl_should_omit_openai_tools_for_multimodal(
    *,
    model: str,
    messages: list[dict[str, Any]] | None,
) -> bool:
    """
    DashScope 经 LiteLLM 时，**同一请求**内同时携带 OpenAI ``tools`` 与含 ``image_url`` 的
    ``user`` 多模态块时，上游可能不将图片送入推理，表现为模型否认收到图（仅依据文本与历史摘要）。

    此前仅对「名称像 VL」的模型省略 tools；但 ``dashscope/qwen3.5-plus`` 等在**无 tools** 的直连请求里
    可正常读图（见 ``scripts/test_dashscope_vision_smoke.py``），**带 tools 时仍会丢图**，故改为：
    凡 ``dashscope/*`` 且 ``messages`` 中已有 OpenAI 图片块，即省略 ``tools``（除非环境显式关闭）。

    为 true 时，调用方应**不传** ``tools`` / ``tool_choice``，仅依赖 system 内 ReAct 工具说明输出
    Thought/Action（与 ``JACHIN_REACT_STREAM_DISABLE_TOOLS`` 行为一致）。

    设 ``JACHIN_DASHSCOPE_VL_KEEPS_TOOLS=1`` 可关闭此规避（用于供应商修复后验证）。
    """
    if os.environ.get("JACHIN_DASHSCOPE_VL_KEEPS_TOOLS", "").strip().lower() in ("1", "true", "yes"):
        return False
    ml = (model or "").strip().lower()
    if not ml.startswith("dashscope/"):
        return False
    return l3_react_full_messages_need_vision_model(messages)


def vision_safe_litellm_fallback_models(
    *,
    primary: str,
    base_fallbacks: list[str] | None,
) -> list[str]:
    """
    从引擎原有 fallback 链中只保留多模态可用的模型 id；若链被清空则回退到环境或主 VL，避免注入 flash/plus。
    """
    import os

    primary = (primary or "").strip()
    kept = [str(x).strip() for x in (base_fallbacks or []) if str(x).strip()]
    kept = [x for x in kept if litellm_model_supports_openai_multimodal_chat(x)]
    alt = (os.environ.get("JACHIN_VISION_LITELLM_FALLBACK") or "").strip()
    if alt and litellm_model_supports_openai_multimodal_chat(alt) and alt not in kept:
        kept.append(alt)
    if not kept and primary and litellm_model_supports_openai_multimodal_chat(primary):
        # 与主模型同 id 一条：满足 LiteLLMEngine「非空 fallback」默认值逻辑，且第二路重试仍是 VL
        kept = [primary]
    if not kept:
        # 极端：主模型未被识别为 VL（配置错误）— 仍优于误塞入 flash
        kept = [primary] if primary else []
    seen: set[str] = set()
    out: list[str] = []
    for x in kept:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def l3_react_should_use_complex_model(
    *,
    delegate_depth: int,
    react_iteration: int,
    full_messages: list[dict[str, Any]],
    tools_count: int,
    force_complex: bool = False,
) -> bool:
    """
    L3 ReAct 主循环：是否改用 LLM_COMPLEX_MODEL（qwen-max）。
    可通过 JACHIN_LLM_COMPLEX_DISABLE=1 关闭；阈值见环境变量注释。
    """
    if os.environ.get("JACHIN_LLM_COMPLEX_DISABLE", "").strip().lower() in ("1", "true", "yes"):
        return False
    if force_complex:
        return True
    if delegate_depth and delegate_depth > 0:
        return True
    try:
        min_iter = int(os.environ.get("JACHIN_LLM_COMPLEX_MIN_REACT_ITER", "8"))
    except ValueError:
        min_iter = 8
    if react_iteration >= min_iter:
        return True
    try:
        # L3 常合并 20+ MCP；28 会导致几乎每轮强制 qwen-max，压过主模型与「编程首轮→coder」
        min_tools = int(os.environ.get("JACHIN_LLM_COMPLEX_MIN_TOOLS", "56"))
    except ValueError:
        min_tools = 56
    if tools_count >= min_tools:
        return True
    try:
        min_user_chars = int(os.environ.get("JACHIN_LLM_COMPLEX_MIN_USER_CHARS", "2400"))
    except ValueError:
        min_user_chars = 2400
    for m in reversed(full_messages):
        if (m.get("role") or "").strip() != "user":
            continue
        c = _coerce_user_content_to_text(m.get("content"))
        if len(c) >= min_user_chars:
            return True
        break
    try:
        min_msg_count = int(os.environ.get("JACHIN_LLM_COMPLEX_MIN_MESSAGES", "28"))
    except ValueError:
        min_msg_count = 28
    if len(full_messages) >= min_msg_count:
        return True
    return False


_CODING_INTENT_USER_RE = re.compile(
    r"(?:写|编写|新建|创建).{0,52}(?:脚本|代码|程序|\.py\b|python|typescript|ts\b|javascript|js\b|golang|rust\b)"
    r"|\.py\b|python\s*脚本|(?:shell|bash)\s*脚本|core:fs_write|apply_patch|"
    r"(?:工作区|workspace).{0,44}(?:脚本|文件|文件夹|目录|路径)"
    r"|实现.{0,28}(?:函数|类|模块|接口|功能|程序)"
    r"|(?:打印|监控|获取).{0,24}(?:CPU|内存|占用率|系统)",
    re.I | re.DOTALL,
)


def _last_substantive_user_snippet(full_messages: list[dict[str, Any]]) -> str:
    """跳过 Observation 追问问句，取最近一条像「用户原任务」的 user 文本。"""
    for m in reversed(full_messages or []):
        if (m.get("role") or "").strip() != "user":
            continue
        c = _coerce_user_content_to_text(m.get("content")).strip()
        if len(c) < 14:
            continue
        low = c[:120].lower()
        if low.startswith("observation:") or low.startswith("请根据观察") or low.startswith("这是最后一轮"):
            continue
        return c
    return ""


def should_prime_l3_react_coder_mode(
    *,
    react_iteration: int,
    full_messages: list[dict[str, Any]],
) -> bool:
    """
    ReAct 首轮（及尚未写过工作区时）：用户话明显是编程/脚本/监控类任务时，优先走编码模型。
    与「fs_write/apply_patch 之后才切 coder」互补，避免 tools 数量误触 complex 后整轮用 qwen-max 写代码。
    """
    if int(react_iteration or 0) != 0:
        return False
    snip = _last_substantive_user_snippet(full_messages)
    if len(snip) < 14:
        return False
    return bool(_CODING_INTENT_USER_RE.search(snip))


def _brief_llm_context(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> str:
    """调度可观测性：消息规模、角色计数、最后 user 预览、工具数。"""
    n = len(messages)
    roles: dict[str, int] = {}
    for m in messages:
        r = (m.get("role") or "?").strip()
        roles[r] = roles.get(r, 0) + 1
    last_user = ""
    for m in reversed(messages):
        if (m.get("role") or "").strip() == "user":
            c = _coerce_user_content_to_text(m.get("content"))
            last_user = c[:120].replace("\n", " ")
            break
    tc = len(tools) if tools else 0
    return f"msgs={n} roles={roles} tools={tc} last_user_preview={last_user!r}"


def _pop_call_purpose(kwargs: dict[str, Any], default: str = "cognitive_unspecified") -> str:
    p = kwargs.pop("call_purpose", None)
    if p is None:
        p = kwargs.pop("l3_call_purpose", None)
    s = str(p or "").strip()
    return s if s else default


def sanitize_llm_fallback_models(models: list[str]) -> list[str]:
    """
    将链中的 ollama/* 替换为 DASHSCOPE_REASONING_MODEL（qwen3.5-plus），去重并保持顺序。
    避免本机未启动 Ollama 时长时间 TCP 等待。
    同时对每条应用 ``_normalize_model_for_litellm``（含通义快照 ``-YYYY-MM-DD`` 剥离）。
    """
    out: list[str] = []
    seen: set[str] = set()
    for m in models:
        s = str(m).strip()
        if not s:
            continue
        s = _normalize_model_for_litellm(s)
        sl = s.lower()
        if sl.startswith("ollama/") or sl.startswith("ollama:"):
            s = DASHSCOPE_REASONING_MODEL
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _resolve_litellm_http_timeout_sec(
    call_purpose: str,
    *,
    stream: bool,
    base_timeout: float,
    llm_cfg: dict[str, Any],
) -> float:
    """
    注入 litellm ``timeout``（秒），防止公网/API 静默挂起死等。

    下限策略（可被 ``nexus_config.json`` 与环境变量抬高，不单方面压低用户配置）：
    - **非流式**、非长文专 purpose：至少 **180**
    - **流式**或 ``util_compose_long_document``：至少 **600**（长文另可读 ``JACHIN_LLM_LONG_DOCUMENT_TIMEOUT_SEC``）
    - ``compaction_*``：``llm.compaction_timeout_seconds`` 或至少 **180**
    """
    cp = str(call_purpose)
    t = max(1.0, float(base_timeout))
    if cp.startswith("compaction_"):
        _cts = llm_cfg.get("compaction_timeout_seconds")
        if _cts is not None:
            try:
                return max(float(_cts), t, 180.0)
            except (TypeError, ValueError):
                pass
        return max(t, 180.0)
    long_or_stream = bool(stream) or cp == "util_compose_long_document"
    if long_or_stream:
        _env = (os.environ.get("JACHIN_LLM_LONG_DOCUMENT_TIMEOUT_SEC") or "").strip()
        if _env:
            try:
                return max(t, float(_env))
            except ValueError:
                pass
        _ld = llm_cfg.get("long_document_timeout_seconds")
        if _ld is not None:
            try:
                return max(t, float(_ld))
            except (TypeError, ValueError):
                pass
        return max(t, 600.0)
    return max(t, 180.0)


def _asyncio_wait_slack_for_purpose(call_purpose: str) -> float:
    """
    asyncio.wait_for 相对 litellm timeout 的额外余量（内部重试、网络抖动）。
    长文生成默认加大，避免「API 仍在输出但 wait_for 先掐断」。
    """
    cp = str(call_purpose)
    if cp.startswith("compaction_"):
        return 90.0
    if cp == "util_compose_long_document":
        _s = (os.environ.get("JACHIN_LLM_LONG_DOCUMENT_SLACK_SEC") or "").strip()
        if _s:
            try:
                return max(30.0, float(_s))
            except ValueError:
                pass
        return 180.0
    return 45.0


def _get_retry_config(config: dict[str, Any] | None = None) -> tuple[int, list[str], float]:
    """
    读取重试与降级配置。
    返回 (max_attempts, fallback_models, timeout_seconds)
    有 DASHSCOPE_API_KEY 时默认第二路为低成本 flash；无 Key 时第二路仍为 qwen3.5-plus（将报 Key 错误，不再使用 Ollama）。
    """
    cfg = config or _load_nexus_config()
    llm = (cfg.get("llm") or {}) if isinstance(cfg.get("llm"), dict) else {}
    max_attempts = int(llm.get("max_attempts", 2))
    fallback = llm.get("fallback_models")
    if isinstance(fallback, list):
        fallback_models = [str(m).strip() for m in fallback if m]
    elif isinstance(fallback, str) and fallback.strip():
        fallback_models = [fallback.strip()]
    else:
        env_fallback = os.environ.get("LITELLM_FALLBACK_MODELS", "").strip()
        if env_fallback:
            fallback_models = [m.strip() for m in env_fallback.split(",") if m.strip()]
        elif _has_dashscope_key():
            fallback_models = [DASHSCOPE_ECON_FALLBACK_MODEL]
        else:
            fallback_models = [DASHSCOPE_REASONING_MODEL]
    fallback_models = sanitize_llm_fallback_models(fallback_models)
    if not fallback_models:
        fallback_models = [DASHSCOPE_REASONING_MODEL]
    timeout = float(llm.get("timeout_seconds", 60.0))
    return max_attempts, fallback_models, timeout


def _deep_llm_log(
    *,
    source: str,
    purpose: str,
    phase: str,
    model: str,
    stream: bool,
    t0: float,
    messages: list[dict[str, str]],
    tools: list[dict[str, Any]] | None,
    response_text: str | None = None,
    response_dict_summary: str | None = None,
    error: str | None = None,
) -> None:
    try:
        from core.deep_execution_log import log_llm_completion

        log_llm_completion(
            source=source,
            purpose=purpose,
            phase=phase,
            model=model,
            stream=stream,
            elapsed_ms=(time.perf_counter() - t0) * 1000.0,
            messages=messages,
            tools=tools,
            response_text=response_text,
            response_dict_summary=response_dict_summary,
            error=error,
        )
    except Exception:
        pass


def _resolve_model_with_fallback(model: str) -> str:
    """若云模型无 Key，改为使用 DASHSCOPE_REASONING_MODEL（需配置 DASHSCOPE_API_KEY，不再回退 Ollama）。"""
    needs, env_key = _model_needs_key(model)
    if not needs:
        return model
    if env_key == "OPENAI_API_KEY":
        has_key = bool(_get_openai_key_from_sources())
    else:
        has_key = bool(os.environ.get(env_key))
        if not has_key:
            try:
                from core.brain.llm.credential_loader import get_dashscope_key
                has_key = bool(get_dashscope_key())
            except ImportError:
                cfg = _load_nexus_config()
                has_key = bool((cfg.get("llm_keys") or {}).get("dashscope"))
    if has_key:
        return model
    logger.info(
        "[LiteLLM] %s 未配置，主模型将使用 %s（请配置环境变量或 ~/.jachin/nexus_config.json → llm_keys）。"
        "已弃用 Ollama 默认回退。",
        env_key or "API Key",
        DASHSCOPE_REASONING_MODEL,
    )
    return DASHSCOPE_REASONING_MODEL


class LiteLLMEngine:
    """
    虫群大脑引擎 — 通过 litellm.acompletion 统一接入任意模型。
    不再维护 Qwen/OpenAI/Ollama 分支，model_name 直接透传。
    API Key 从环境变量或 nexus_config.json 注入；无 Key 时默认指向 dashscope/qwen3.5-plus。
    """

    def __init__(self, model_name: str | None = None) -> None:
        _inject_api_keys()
        raw = model_name or _get_model_name()
        resolved = _resolve_model_with_fallback(raw)
        self.model_name = _normalize_model_for_litellm(resolved)
        if self.model_name.lower().startswith(("ollama/", "ollama:")):
            logger.info(
                "[LiteLLM] 配置中的 Ollama 模型已弃用，改为 %s",
                DASHSCOPE_REASONING_MODEL,
            )
            self.model_name = _normalize_model_for_litellm(DASHSCOPE_REASONING_MODEL)

    async def generate_response(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> str | dict[str, Any]:
        """
        调用 litellm.acompletion，返回纯文本或 tool_calls 字典。
        v8.0 神盾：for attempt 重试 + fallback_models 降级灾备。
        """
        call_purpose = _pop_call_purpose(kwargs)
        _inject_api_keys()
        _nexus_cfg = _load_nexus_config()
        max_attempts, fallback_models, timeout = _get_retry_config(_nexus_cfg)
        _llm_cfg = (_nexus_cfg.get("llm") or {}) if isinstance(_nexus_cfg.get("llm"), dict) else {}
        timeout = _resolve_litellm_http_timeout_sec(
            call_purpose, stream=False, base_timeout=timeout, llm_cfg=_llm_cfg
        )
        models_to_try = [self.model_name] + [m for m in fallback_models if m != self.model_name]
        last_error: Exception | None = None

        logger.info(
            "[LLM][调度] purpose=%s action=chat_completion %s",
            call_purpose,
            _brief_llm_context(messages, tools),
        )

        for attempt in range(max_attempts):
            model = _normalize_model_for_litellm(models_to_try[min(attempt, len(models_to_try) - 1)])
            phase = "primary" if attempt == 0 else "fallback_resilience"
            next_if_fail = (
                models_to_try[min(attempt + 1, len(models_to_try) - 1)]
                if attempt + 1 < len(models_to_try)
                else None
            )
            logger.info(
                "[LLM][调度] purpose=%s phase=%s attempt=%d/%d model=%s next_if_fail=%s",
                call_purpose,
                phase,
                attempt + 1,
                max_attempts,
                model,
                next_if_fail or "-",
            )
            try:
                t0 = time.perf_counter()
                kwargs_chat: dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "timeout": timeout,
                }
                if tools:
                    kwargs_chat["tools"] = tools
                # 透传 LiteLLM 兼容参数（如 DashScope extra_body / enable_thinking）
                for _ek, _ev in kwargs.items():
                    if _ev is not None:
                        kwargs_chat[_ek] = _ev

                litellm_apply_dashscope_credentials(model, kwargs_chat)

                # 硬上限：litellm 内部可能对 /chat/completions 重试；长文/compaction 需更大 slack
                _slack = _asyncio_wait_slack_for_purpose(call_purpose)
                _cap = float(timeout) + _slack
                try:
                    response = await asyncio.wait_for(litellm.acompletion(**kwargs_chat), timeout=_cap)
                except asyncio.TimeoutError as te:
                    raise TimeoutError(
                        f"LLM 非流式调用逾时 (>{_cap:.0f}s, purpose={call_purpose}, model={model})"
                    ) from te
                choice = response.choices[0] if response.choices else None
                if not choice:
                    logger.info(
                        "[LLM][调度] purpose=%s result=empty model_used=%s",
                        call_purpose,
                        model,
                    )
                    _deep_llm_log(
                        source="core.llm_provider",
                        purpose=str(call_purpose),
                        phase=phase,
                        model=model,
                        stream=False,
                        t0=t0,
                        messages=messages,
                        tools=tools,
                        response_text="",
                        error=None,
                    )
                    return ""

                msg = choice.message
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    logger.info(
                        "[LLM][调度] purpose=%s result=ok model_used=%s outcome=tool_calls n=%d",
                        call_purpose,
                        model,
                        len(msg.tool_calls),
                    )
                    _names: list[str] = []
                    try:
                        for _tc in msg.tool_calls:
                            _fn = getattr(getattr(_tc, "function", None), "name", None)
                            _names.append(str(_fn or _tc))
                    except Exception:
                        _names = ["(unreadable)"]
                    _deep_llm_log(
                        source="core.llm_provider",
                        purpose=str(call_purpose),
                        phase=phase,
                        model=model,
                        stream=False,
                        t0=t0,
                        messages=messages,
                        tools=tools,
                        response_text=None,
                        response_dict_summary=(
                            f"non-stream tool_calls n={len(msg.tool_calls)} names={_names} "
                            f"assistant_content_preview={(msg.content or '')[:800]!r}"
                        ),
                    )
                    return {"content": msg.content or "", "tool_calls": msg.tool_calls}
                text = (msg.content or "").strip()
                logger.info(
                    "[LLM][调度] purpose=%s result=ok model_used=%s outcome=text chars=%d",
                    call_purpose,
                    model,
                    len(text),
                )
                _deep_llm_log(
                    source="core.llm_provider",
                    purpose=str(call_purpose),
                    phase=phase,
                    model=model,
                    stream=False,
                    t0=t0,
                    messages=messages,
                    tools=tools,
                    response_text=text,
                )
                return text
            except Exception as e:
                last_error = e
                if attempt < max_attempts - 1 and len(models_to_try) > 1:
                    next_model = models_to_try[min(attempt + 1, len(models_to_try) - 1)]
                    logger.info(
                        "[LLM][调度] purpose=%s phase=fallback_chain from=%s to=%s err=%s: %s",
                        call_purpose,
                        model,
                        next_model,
                        type(e).__name__,
                        str(e)[:320],
                    )
                    console.print(
                        f"[yellow][⚠ 降级策略][/yellow] 主模型异常，尝试第 {attempt + 2} 次呼叫备用算力: [cyan]{next_model}[/cyan]"
                    )
                    logger.warning("[LiteLLM] attempt=%s model=%s 失败: %s，降级至 %s", attempt + 1, model, e, next_model)
                else:
                    logger.exception("[LiteLLM] 调用异常 model=%s: %s", model, e)
                    try:
                        _deep_llm_log(
                            source="core.llm_provider",
                            purpose=str(call_purpose),
                            phase=phase,
                            model=model,
                            stream=False,
                            t0=t0,
                            messages=messages,
                            tools=tools,
                            error=f"{type(e).__name__}: {e}"[:8000],
                        )
                    except Exception:
                        pass
                    raise last_error
        raise last_error or RuntimeError("LLM 调用失败")

    async def generate_response_stream(
        self,
        messages: list[dict[str, str]],
        chunk_callback: Callable[[str], Awaitable[None]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> str:
        """
        v8.0 流式神经：stream=True 调用 litellm.acompletion，逐 token 回调并返回完整响应。
        v8.0 神盾：for attempt 重试 + fallback_models 降级灾备。
        """
        call_purpose = _pop_call_purpose(kwargs)
        _inject_api_keys()
        _nexus_cfg = _load_nexus_config()
        max_attempts, fallback_models, timeout = _get_retry_config(_nexus_cfg)
        _llm_cfg = (_nexus_cfg.get("llm") or {}) if isinstance(_nexus_cfg.get("llm"), dict) else {}
        timeout = _resolve_litellm_http_timeout_sec(
            call_purpose, stream=True, base_timeout=timeout, llm_cfg=_llm_cfg
        )
        models_to_try = [self.model_name] + [m for m in fallback_models if m != self.model_name]
        last_error: Exception | None = None

        logger.info(
            "[LLM][调度] purpose=%s action=chat_completion_stream %s",
            call_purpose,
            _brief_llm_context(messages, None),
        )

        for attempt in range(max_attempts):
            model = _normalize_model_for_litellm(models_to_try[min(attempt, len(models_to_try) - 1)])
            phase = "primary" if attempt == 0 else "fallback_resilience"
            next_if_fail = (
                models_to_try[min(attempt + 1, len(models_to_try) - 1)]
                if attempt + 1 < len(models_to_try)
                else None
            )
            logger.info(
                "[LLM][调度] purpose=%s phase=%s attempt=%d/%d model=%s stream=1 next_if_fail=%s",
                call_purpose,
                phase,
                attempt + 1,
                max_attempts,
                model,
                next_if_fail or "-",
            )
            try:
                t0 = time.perf_counter()
                kwargs_chat: dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "stream": True,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "timeout": timeout,
                }

                from core.litellm_stream_hints import merge_dashscope_stream_incremental_hint
                from core.stream_text_delta import StreamDeltaNormalizer

                merge_dashscope_stream_incremental_hint(model, kwargs_chat)
                litellm_apply_dashscope_credentials(model, kwargs_chat)
                # 与 l3_node.llm_client 一致：默认识别增量/累积帧，避免 UI 复读。
                async def _consume_litellm_stream() -> str:
                    response_inner = await litellm.acompletion(**kwargs_chat)
                    _norm = StreamDeltaNormalizer()
                    parts: list[str] = []
                    async for chunk in response_inner:
                        choice = chunk.choices[0] if chunk.choices else None
                        if not choice or not hasattr(choice, "delta"):
                            continue
                        delta = getattr(choice.delta, "content", None) or ""
                        if not delta:
                            continue
                        piece = _norm.feed(delta)
                        if piece:
                            parts.append(piece)
                            if chunk_callback:
                                await chunk_callback(piece)
                    return "".join(parts).strip()

                _cap_total = float(timeout) + float(_asyncio_wait_slack_for_purpose(call_purpose))
                try:
                    out = await asyncio.wait_for(_consume_litellm_stream(), timeout=_cap_total)
                except asyncio.TimeoutError as te:
                    raise TimeoutError(
                        f"LLM 流式整段逾时 (>{_cap_total:.0f}s, litellm_timeout={timeout:.0f}s, "
                        f"purpose={call_purpose}, model={model})"
                    ) from te
                logger.info(
                    "[LLM][调度] purpose=%s result=ok model_used=%s outcome=stream chars=%d",
                    call_purpose,
                    model,
                    len(out),
                )
                _deep_llm_log(
                    source="core.llm_provider",
                    purpose=str(call_purpose),
                    phase=phase,
                    model=model,
                    stream=True,
                    t0=t0,
                    messages=messages,
                    tools=None,
                    response_text=out,
                )
                return out
            except Exception as e:
                last_error = e
                if attempt < max_attempts - 1 and len(models_to_try) > 1:
                    next_model = models_to_try[min(attempt + 1, len(models_to_try) - 1)]
                    logger.info(
                        "[LLM][调度] purpose=%s phase=fallback_chain stream=1 from=%s to=%s err=%s: %s",
                        call_purpose,
                        model,
                        next_model,
                        type(e).__name__,
                        str(e)[:320],
                    )
                    console.print(
                        f"[yellow][⚠ 降级策略][/yellow] 主模型异常，尝试第 {attempt + 2} 次呼叫备用算力: [cyan]{next_model}[/cyan]"
                    )
                    logger.warning("[LiteLLM] 流式 attempt=%s model=%s 失败: %s，降级至 %s", attempt + 1, model, e, next_model)
                else:
                    logger.exception("[LiteLLM] 流式调用异常 model=%s: %s", model, e)
                    try:
                        _deep_llm_log(
                            source="core.llm_provider",
                            purpose=str(call_purpose),
                            phase=phase,
                            model=model,
                            stream=True,
                            t0=t0,
                            messages=messages,
                            tools=None,
                            error=f"{type(e).__name__}: {e}"[:8000],
                        )
                    except Exception:
                        pass
                    raise last_error
        raise last_error or RuntimeError("LLM 流式调用失败")


class CognitiveEngineFactory:
    """
    认知引擎工厂 — 仅读取 model_name，透传 LiteLLM。
    """

    _engine: LiteLLMEngine | None = None
    _coder_engine: LiteLLMEngine | None = None

    @classmethod
    def get_engine(cls, config: dict[str, Any] | None = None) -> LiteLLMEngine:
        """
        获取 LiteLLM 引擎实例。
        config.llm.model_name: 如 gpt-4o, dashscope/qwen3.5-plus
        """
        global _IGNITION_EMITTED
        _inject_api_keys()  # 先注入 key，确保 _get_model_name 能检测到 nexus_config/credential_loader 中的 DASHSCOPE_API_KEY
        model_name = _get_model_name(config)
        engine = LiteLLMEngine(model_name=model_name)
        cls._engine = engine

        if not _IGNITION_EMITTED:
            console.print(
                f"[bold green][Ignition][/bold green] Cognitive Swarm Online. Model: [cyan]{engine.model_name}[/cyan]"
            )
            _IGNITION_EMITTED = True
        return engine

    @classmethod
    def get_coder_engine(cls, config: dict[str, Any] | None = None) -> LiteLLMEngine:
        """
        编码专用引擎（默认 qwen3-coder-plus），与 get_engine 共用 DASHSCOPE_API_KEY。
        """
        global _IGNITION_CODER_EMITTED
        _inject_api_keys()
        raw = get_coder_model_litellm_id(config)
        resolved = _resolve_model_with_fallback(raw)
        engine = LiteLLMEngine(model_name=_normalize_model_for_litellm(resolved))
        cls._coder_engine = engine
        if not _IGNITION_CODER_EMITTED:
            console.print(
                f"[bold green][Ignition][/bold green] Coder model online: [cyan]{engine.model_name}[/cyan]"
            )
            _IGNITION_CODER_EMITTED = True
        return engine
