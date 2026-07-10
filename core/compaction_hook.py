"""
Jachin Nexus v8.0 — 神盾 Compaction Hook（上下文时空折叠）

注册到 HOOK_BEFORE_LLM_THINK，当 ctx.messages 超 token 阈值时：
1. 主动记忆刷新（memoryFlush）：静默 LLM 回合，提醒模型将重要信息写入 core_memory
2. 时空折叠：将中间陈旧对话压缩为【历史摘要】，防止 ContextWindowExceededError

参考 OpenClaw memoryFlush 设计。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from rich.console import Console

from core.hooks_pipeline import HOOK_BEFORE_LLM_THINK, PipelineContext, global_hooks
from core.intelligence_workspace import (
    anchors_stale,
    append_compaction_audit,
    append_findings_checkpoint_block,
    emit_intelligence_event,
    findings_has_machine_checkpoint,
    get_jachin_home,
    load_anchor_remediate_mode,
    load_post_compaction_audit_config,
    load_workspace_anchor_paths,
    snapshot_anchor_states,
    touch_stale_workspace_anchors,
)

logger = logging.getLogger(__name__)
console = Console()
_NEXUS_CONFIG = Path.home() / ".jachin" / "nexus_config.json"
_DEFAULT_THRESHOLD = 6000
# 记忆刷新：当 token 超过 threshold - soft_margin 时触发，在压缩前提醒模型写入持久记忆
_MEMORY_FLUSH_SOFT_MARGIN = 4000


def _load_nexus_config() -> dict[str, Any]:
    """读取 ~/.jachin/nexus_config.json"""
    if not _NEXUS_CONFIG.exists():
        return {}
    try:
        return json.loads(_NEXUS_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """极简 token 估算：字符数 / 4（中文约 1.5 字/token，英文约 4 字/token，取折中）"""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        total = 0
        for m in messages:
            content = m.get("content") or ""
            if isinstance(content, str):
                total += len(enc.encode(content))
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        total += len(enc.encode(part.get("text", "")))
        return total
    except ImportError:
        return len(str(messages)) // 4


def _get_compaction_config() -> tuple[int, str]:
    """返回 (threshold, summary_model)"""
    cfg = _load_nexus_config()
    llm = (cfg.get("llm") or {}) if isinstance(cfg.get("llm"), dict) else {}
    threshold = int(llm.get("compaction_threshold", _DEFAULT_THRESHOLD))
    try:
        from core.llm_provider import DASHSCOPE_REASONING_MODEL
    except ImportError:
        DASHSCOPE_REASONING_MODEL = "dashscope/qwen3.5-plus"  # type: ignore[misc]
    explicit_comp = bool(llm.get("compaction_model") and str(llm.get("compaction_model")).strip())
    # 压缩链路统一默认 qwen3.5-plus；已弃用 Ollama
    default_sm = DASHSCOPE_REASONING_MODEL
    if explicit_comp:
        raw = str(llm.get("compaction_model")).strip()
    else:
        raw = str(llm.get("edge_model") or default_sm).strip()
    # 旧版 edge 标签如 "qwen2.5:0.5b" 曾映射到 Ollama，现改为 qwen3.5-plus
    if raw and "/" not in raw and ":" in raw:
        model = DASHSCOPE_REASONING_MODEL
    else:
        model = raw or default_sm
    ml = (model or "").lower()
    if ml.startswith("ollama/") or ml.startswith("ollama:"):
        logger.info("[Compaction] Ollama 已弃用，压缩改用 %s", DASHSCOPE_REASONING_MODEL)
        model = DASHSCOPE_REASONING_MODEL
    return threshold, model


def _get_llm_section() -> dict[str, Any]:
    cfg = _load_nexus_config()
    return (cfg.get("llm") or {}) if isinstance(cfg.get("llm"), dict) else {}


def _get_memory_flush_model() -> str:
    """
    记忆刷新专用模型：默认经济型 flash，避免 qwen3.5-plus 等「思考链」在 JSON 抽取上耗时数十秒、浪费 reasoning_tokens。
    可在 ~/.jachin/nexus_config.json → llm.memory_flush.model 覆盖。
    """
    llm = _get_llm_section()
    mf = (llm.get("memory_flush") or {}) if isinstance(llm.get("memory_flush"), dict) else {}
    raw = str(mf.get("model") or mf.get("memory_flush_model") or "").strip()
    if raw:
        from core.llm_provider import _normalize_model_for_litellm

        return _normalize_model_for_litellm(raw)
    try:
        from core.llm_provider import DASHSCOPE_ECON_FALLBACK_MODEL

        return DASHSCOPE_ECON_FALLBACK_MODEL
    except ImportError:
        return "dashscope/qwen3.5-flash"


def _dashscope_extra_no_thinking(model_name: str) -> dict[str, Any]:
    if (model_name or "").lower().startswith("dashscope/"):
        return {"extra_body": {"enable_thinking": False}}
    return {}


def _get_compaction_context_summary_model() -> str:
    """
    「历史摘要」专用模型：默认经济型 flash，避免 qwen3.5-plus 在大 blob 上动辄 200s+。
    可在 nexus llm.compaction_summary_model / compaction_fast_model 覆盖。
    """
    llm = _get_llm_section()
    for key in ("compaction_summary_model", "compaction_fast_model"):
        raw = str(llm.get(key) or "").strip()
        if raw:
            from core.llm_provider import _normalize_model_for_litellm

            return _normalize_model_for_litellm(raw)
    try:
        from core.llm_provider import DASHSCOPE_ECON_FALLBACK_MODEL

        return DASHSCOPE_ECON_FALLBACK_MODEL
    except ImportError:
        return "dashscope/qwen3.5-flash"


def _get_memory_flush_config() -> tuple[bool, int]:
    """返回 (enabled, soft_threshold)。enabled 时，token 超过 threshold - soft_threshold 即触发刷新。"""
    llm = _get_llm_section()
    mf = (llm.get("memory_flush") or {}) if isinstance(llm.get("memory_flush"), dict) else {}
    enabled = bool(mf.get("enabled", True))
    soft = int(mf.get("soft_threshold", _MEMORY_FLUSH_SOFT_MARGIN))
    return enabled, soft


def _parse_memory_flush_output(text: str) -> list[dict[str, str]]:
    """解析 LLM 记忆刷新输出，提取 tag + content 列表。支持 JSON 数组或 NO_REPLY。"""
    text = (text or "").strip()
    if not text or "NO_REPLY" in text.upper():
        return []
    m = re.search(r"\[[\s\S]*?\]", text)
    if m:
        try:
            arr = json.loads(m.group())
            if isinstance(arr, list):
                out = []
                for item in arr:
                    if isinstance(item, dict):
                        tag = (item.get("tag") or "").strip()
                        content = (item.get("content") or str(item.get("content", ""))).strip()
                        if tag and content:
                            out.append({"tag": tag, "content": content})
                return out
        except json.JSONDecodeError:
            pass
    return []


async def _run_memory_flush(messages: list[dict[str, Any]], summary_model: str) -> int:
    """
    主动记忆刷新：静默 LLM 回合，提醒模型将重要信息写入 core_memory。
    返回写入的条数。
    summary_model 参数保留兼容；实际模型见 _get_memory_flush_model()。
    """
    from core.biological_memory import add_core_memory

    content_blob = "\n\n".join(
        f"{m.get('role', '')}: {(m.get('content') or '')[:300]}"
        for m in messages[-12:]  # 最近 12 条
    )
    prompt = """【系统】会话即将压缩，请将以下对话中值得永久记住的重要信息写入记忆。
可写入：用户偏好、决策、关键事实、配置、习惯等。
输出格式：仅输出 JSON 数组，每项 {"tag": "xxx", "content": "xxx"}。tag 示例：preference、user_habit、config_hint。
若无重要内容，请仅回复 NO_REPLY。

对话内容：
"""
    prompt += content_blob + "\n\n请输出 JSON 或 NO_REPLY："

    try:
        from core.llm_provider import LiteLLMEngine

        mf_model = _get_memory_flush_model()
        logger.info(
            "[Compaction] memory_flush 使用模型 %s（nexus llm.memory_flush.model 可覆盖；默认 flash；compaction 主模型=%s）",
            mf_model,
            summary_model,
        )
        console.print(
            "[bold cyan][Compaction][/bold cyan] "
            "会话将压缩：正在调用 LLM 做「记忆刷新」(memory_flush)，通常数秒～十余秒，请勿误以为卡住。"
        )
        engine = LiteLLMEngine(model_name=mf_model)
        result = await engine.generate_response(
            [{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=512,
            call_purpose="compaction_memory_flush",
            **_dashscope_extra_no_thinking(mf_model),
        )
        if isinstance(result, dict):
            result = result.get("content", "") or ""
        parsed = _parse_memory_flush_output(str(result or ""))
        for p in parsed:
            add_core_memory(
                tag=p.get("tag", "memory_flush"),
                content=p.get("content", ""),
                source_summary="主动记忆刷新",
            )
        if parsed:
            console.print(f"[bold blue][🛡️ 神盾][/bold blue] 记忆刷新：已写入 {len(parsed)} 条核心记忆")
            logger.info("[Compaction] memory_flush 写入 %d 条", len(parsed))
        return len(parsed)
    except Exception as e:
        logger.warning("[Compaction] memory_flush 失败，跳过: %s", e)
        return 0


def _silent_anchor_file_round_enabled() -> bool:
    llm = _get_llm_section()
    mf = llm.get("memory_flush") if isinstance(llm.get("memory_flush"), dict) else {}
    return bool(mf.get("silent_anchor_file_round"))


def _parse_anchor_file_updates(text: str) -> list[dict[str, str]]:
    raw = (text or "").strip()
    if not raw:
        return []
    if raw.startswith("```"):
        lines = raw.split("\n")
        if len(lines) >= 2 and lines[-1].strip().startswith("```"):
            raw = "\n".join(lines[1:-1]).strip()
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return []
    try:
        obj = json.loads(m.group())
    except json.JSONDecodeError:
        return []
    if not isinstance(obj, dict):
        return []
    arr = obj.get("updates")
    if not isinstance(arr, list):
        return []
    out: list[dict[str, str]] = []
    for it in arr:
        if not isinstance(it, dict):
            continue
        rp = (it.get("rel_path") or it.get("path") or "").strip().replace("\\", "/").lstrip("/")
        content = it.get("content")
        if not rp or content is None:
            continue
        out.append({"rel_path": rp, "content": str(content)})
    return out


async def _run_silent_anchor_file_round(
    messages: list[dict[str, Any]],
    summary_model: str,
    stale_abs_paths: list[str],
) -> int:
    """
    OpenClaw 风格：专用静默回合仅生成锚点文件全文并落盘（白名单路径）。
    需 nexus llm.memory_flush.silent_anchor_file_round=true。
    """
    root = get_jachin_home().resolve()
    allowed: set[str] = set()
    for sp in stale_abs_paths:
        try:
            p = Path(sp).resolve()
            rel = p.relative_to(root)
            allowed.add(str(rel).replace("\\", "/"))
        except (ValueError, OSError):
            continue
    if not allowed:
        return 0
    blob = "\n\n".join(
        f"{m.get('role', '')}: {(m.get('content') or '')[:400]}"
        for m in messages[-14:]
    )
    alist = sorted(allowed)
    prompt = f"""你是 **静默锚点落盘** 回合：只能将对话中应持久化的内容写入文件。
允许更新的路径（相对于用户 ~/.jachin/ 的 POSIX 路径，必须完全匹配之一）：
{json.dumps(alist, ensure_ascii=False)}

规则：
1) 输出 **仅有** 一个 JSON 对象，形如 {{"updates":[{{"rel_path":"...","content":"文件全文"}}]}}。
2) rel_path 必须从上述列表中原样选取；不得添加其它路径。
3) 若某文件本轮不需要改动，不要出现在 updates 里。
4) content 为写入后的完整文件内容（UTF-8 文本）。
5) 若无任何文件需要写入，输出 {{"updates":[]}}。

对话片段：
{blob}

请仅输出 JSON："""
    try:
        from core.llm_provider import LiteLLMEngine

        engine = LiteLLMEngine(model_name=summary_model)
        result = await engine.generate_response(
            [{"role": "user", "content": prompt}],
            temperature=0.15,
            max_tokens=4096,
            call_purpose="compaction_silent_anchor_file_round",
        )
        if isinstance(result, dict):
            result = result.get("content", "") or ""
        updates = _parse_anchor_file_updates(str(result or ""))
    except Exception as e:
        logger.warning("[Compaction] silent_anchor_file_round LLM 失败: %s", e)
        return 0

    wrote = 0
    for u in updates:
        rp = u["rel_path"]
        if rp not in allowed:
            logger.info("[Compaction] silent_anchor 跳过非白名单路径: %s", rp)
            continue
        target = (root / rp).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_text(u["content"], encoding="utf-8")
            wrote += 1
            logger.info("[Compaction] silent_anchor 已写入 %s", rp)
        except OSError as e:
            logger.warning("[Compaction] silent_anchor 写入失败 %s: %s", rp, e)
    if wrote:
        emit_intelligence_event("silent_anchor_file_round", {"wrote": wrote, "paths": alist})
        console.print(f"[bold blue][🛡️ 神盾][/bold blue] 静默锚点回合：已写入 {wrote} 个文件")
    return wrote


async def _run_anchor_focused_second_flush(
    messages: list[dict[str, Any]],
    summary_model: str,
    stale_paths: list[str],
) -> int:
    """锚点仍未动时第二轮记忆刷新（强调落盘路径，仍写入 core_memory）。"""
    from core.biological_memory import add_core_memory

    names = "; ".join(Path(s).name for s in stale_paths[:12])
    content_blob = "\n\n".join(
        f"{m.get('role', '')}: {(m.get('content') or '')[:300]}"
        for m in messages[-12:]
    )
    prompt = f"""【系统】以下工作区锚点文件在上一轮刷新后仍未检测到变更：{names}。
请再次从对话中提取须持久化的要点，输出 JSON 数组 [{{"tag":"...","content":"..."}}]；
并建议用户或后续 Agent 用 core:fs_write 更新对应路径（路径相对 ~/.jachin/）。
若无新要点请仅回复 NO_REPLY。

对话摘要：
{content_blob}

请输出 JSON 或 NO_REPLY："""
    try:
        from core.llm_provider import LiteLLMEngine

        mf_model = _get_memory_flush_model()
        engine = LiteLLMEngine(model_name=mf_model)
        result = await engine.generate_response(
            [{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=512,
            call_purpose="compaction_anchor_second_flush",
            **_dashscope_extra_no_thinking(mf_model),
        )
        if isinstance(result, dict):
            result = result.get("content", "") or ""
        parsed = _parse_memory_flush_output(str(result or ""))
        for p in parsed:
            add_core_memory(
                tag=p.get("tag", "memory_flush_anchor2"),
                content=p.get("content", ""),
                source_summary="锚点二次记忆刷新",
            )
        if parsed:
            logger.info("[Compaction] anchor_second_flush 写入 %d 条", len(parsed))
        return len(parsed)
    except Exception as e:
        logger.warning("[Compaction] anchor_second_flush 失败: %s", e)
        return 0


async def _generate_summary(middle_messages: list[dict[str, str]], summary_model: str) -> str:
    """异步调用 LLM 生成历史摘要（模型优先 compaction_summary_model / flash，与 memory_flush 所用 summary_model 解耦）。"""
    from core.llm_provider import LiteLLMEngine

    summary_llm = _get_compaction_context_summary_model()
    engine = LiteLLMEngine(model_name=summary_llm)
    logger.info(
        "[Compaction] context_summary 使用模型 %s（配置项 llm.compaction_summary_model；memory_flush 独立见 llm.memory_flush.model）",
        summary_llm,
    )
    content_blob = "\n\n".join(
        f"{m.get('role', '')}: {m.get('content', '')}"[:500]
        for m in middle_messages[:20]  # 最多取 20 条
    )
    summary_prompt = f"""将以下对话压缩为一段极度精简的【历史摘要】，保留关键事实、决策和用户偏好。不超过 200 字。

对话内容：
{content_blob}

历史摘要："""
    logger.info(
        "[Compaction] 开始生成「历史摘要」：中间片段约 %d 条，purpose=compaction_context_summary（接下来会看到 LiteLLM POST，属正常）",
        len(middle_messages),
    )
    console.print(
        "[bold cyan][Compaction][/bold cyan] "
        "正在调用 LLM 生成「历史摘要」(compaction_context_summary)，请勿误以为进程卡住。"
    )
    summary = await engine.generate_response(
        [{"role": "user", "content": summary_prompt}],
        temperature=0.3,
        max_tokens=256,
        call_purpose="compaction_context_summary",
    )
    if isinstance(summary, dict):
        summary = summary.get("content", "") or ""
    out = (summary or "").strip() or "[对话已压缩]"
    logger.info(
        "[Compaction] 「历史摘要」生成完成，长度=%d 字符",
        len(out),
    )
    return out


async def compaction_before_llm_think(ctx: PipelineContext) -> None:
    """
    神盾 Compaction：超载时先触发主动记忆刷新，再折叠中间消息为历史摘要。
    供 core agent_loop 与 L3 hooks 共用（duck-typed ctx：messages + metadata）。
    """
    messages = ctx.messages
    if not messages or len(messages) < 4:
        return

    threshold, summary_model = _get_compaction_config()
    # tiktoken 在大 messages 上会长时间占用事件循环，易拖慢 Lark WS 协议层 pong → 1011 断连
    estimated = await asyncio.to_thread(_estimate_tokens, messages)
    if estimated <= threshold:
        return

    logger.info(
        "[Compaction] token 估算=%s > 阈值=%s，进入折叠（可选 memory_flush → LLM 历史摘要）；日志中出现 compaction_* POST 属正常，非卡死",
        estimated,
        threshold,
    )
    console.print(
        f"[bold cyan][Compaction][/bold cyan] "
        f"上下文较长（估算≈{estimated} tokens > {threshold}），正在压缩管线中…"
    )

    llm_cfg = _get_llm_section()
    anchor_paths = load_workspace_anchor_paths(llm_cfg)
    post_audit_enabled, post_remediation = load_post_compaction_audit_config(llm_cfg)
    write_ck = True
    pca = llm_cfg.get("post_compaction_audit")
    if isinstance(pca, dict) and pca.get("write_checkpoint_on_fold") is False:
        write_ck = False

    if ctx.metadata.get("_compaction_started_ts") is None:
        ctx.metadata["_compaction_started_ts"] = time.time()

    # 主动记忆刷新：token 接近上限时，提醒模型写入持久记忆（OpenClaw memoryFlush 风格）
    flush_enabled, soft_margin = _get_memory_flush_config()
    if flush_enabled and estimated >= (threshold - soft_margin):
        if not ctx.metadata.get("_memory_flush_done"):
            before_anchors = snapshot_anchor_states(anchor_paths) if anchor_paths else {}
            await _run_memory_flush(messages, summary_model)
            ctx.metadata["_memory_flush_done"] = True
            # 阶段 A：锚点校验 + 自动导出 MEMORY.md（若配置要求）
            if anchor_paths:
                mem_md = get_jachin_home() / "memory" / "MEMORY.md"
                if mem_md in anchor_paths or str(mem_md) in {str(p) for p in anchor_paths}:
                    try:
                        from core.biological_memory import export_core_memory_to_markdown

                        export_core_memory_to_markdown()
                    except Exception as e:
                        logger.debug("[Compaction] export MEMORY.md 跳过: %s", e)
                after_anchors = snapshot_anchor_states(anchor_paths)
                stale = anchors_stale(before_anchors, after_anchors)
                ar_mode = load_anchor_remediate_mode(llm_cfg)
                if stale and ar_mode == "second_llm":
                    await _run_anchor_focused_second_flush(messages, summary_model, stale)
                    if mem_md in anchor_paths or str(mem_md) in {str(p) for p in anchor_paths}:
                        try:
                            from core.biological_memory import export_core_memory_to_markdown

                            export_core_memory_to_markdown()
                        except Exception:
                            pass
                    after_anchors = snapshot_anchor_states(anchor_paths)
                    stale = anchors_stale(before_anchors, after_anchors)
                if stale and _silent_anchor_file_round_enabled():
                    await _run_silent_anchor_file_round(messages, summary_model, stale)
                    after_anchors = snapshot_anchor_states(anchor_paths)
                    stale = anchors_stale(before_anchors, after_anchors)
                if stale and ar_mode == "touch_workspace_anchors":
                    touched = touch_stale_workspace_anchors(stale)
                    if touched:
                        emit_intelligence_event("anchor_touch_remediate", {"touched": touched})
                    after_anchors = snapshot_anchor_states(anchor_paths)
                    stale = anchors_stale(before_anchors, after_anchors)
                if stale:
                    append_compaction_audit(
                        {
                            "kind": "workspace_anchor_stale_after_flush",
                            "stale": stale,
                            "run_id": getattr(ctx, "run_id", "") or ctx.metadata.get("run_id", ""),
                            "remediate_mode": ar_mode,
                        },
                    )
                    emit_intelligence_event("anchor_stale", {"paths": stale})
                    if post_remediation == "clarification":
                        try:
                            from l3_node.intelligence_p1 import enqueue_clarification

                            enqueue_clarification(
                                "记忆刷新后以下工作区锚点文件未更新，请检查或手动维护："
                                + "; ".join(Path(s).name for s in stale[:8]),
                            )
                        except Exception as e:
                            logger.debug("[Compaction] clarification 入队跳过: %s", e)
                else:
                    append_compaction_audit({"kind": "workspace_anchor_ok_after_flush", "count": len(anchor_paths)})

    # 保留：第一条 system + 最后 2 轮 (user + assistant)
    first_system: list[dict[str, str]] = []
    last_rounds: list[dict[str, str]] = []
    middle: list[dict[str, str]] = []

    for m in messages:
        role = (m.get("role") or "").strip().lower()
        if role == "system" and not first_system:
            first_system.append(m)
        else:
            middle.append(m)

    # 从 middle 尾部取出最后 2 轮（4 条：user, assistant, user, assistant）
    if len(middle) > 4:
        last_rounds = middle[-4:]
        middle = middle[:-4]
    else:
        last_rounds = middle
        middle = []

    if not middle:
        return

    # 折叠前快照，供 post_audit memory_flush_retry（clear 后 ctx.messages 已缩短）
    _pre_fold_messages = [dict(x) for x in messages]

    try:
        summary_text = await _generate_summary(middle, summary_model)
        try:
            from l3_node.task_planning import progress_has_open_checkboxes

            if progress_has_open_checkboxes():
                summary_text += (
                    "\n\n【续跑提示】progress.md 含未完成项，新会话请结合 task_plan.md / findings.md 继续。"
                )
        except ImportError:
            pass
        summary_msg: dict[str, str] = {
            "role": "system",
            "content": f"【历史摘要】{summary_text}",
        }
        new_messages = first_system + [summary_msg] + last_rounds
        ctx.messages.clear()
        ctx.messages.extend(new_messages)
        new_est = await asyncio.to_thread(_estimate_tokens, ctx.messages)
        console.print(
            f"[bold blue][🛡️ 神盾][/bold blue] 上下文超载 ({estimated} tokens)，"
            f"已触发时空折叠，压缩至 {new_est} tokens。"
        )
        logger.info(
            "[Compaction] %s -> %s tokens, memory_flush/compaction_model=%s context_summary_llm=%s",
            estimated,
            new_est,
            summary_model,
            _get_compaction_context_summary_model(),
        )
        emit_intelligence_event(
            "compaction_fold",
            {
                "tokens_before": estimated,
                "tokens_after": new_est,
                "run_id": getattr(ctx, "run_id", "") or ctx.metadata.get("run_id", ""),
            },
        )
        if write_ck:
            append_findings_checkpoint_block(summary_text[:1200], source="compaction")
            try:
                from l3_node.local_memory import add_local_memory

                add_local_memory(
                    "task_checkpoint",
                    (summary_text or "")[:500],
                    source="compaction_hook",
                )
            except Exception as e:
                logger.debug(
                    "[Compaction] Memory Nexus checkpoint 跳过，底层: %s",
                    e,
                    exc_info=True,
                )
        if post_audit_enabled:
            audit_ok = True
            if write_ck:
                audit_ok = findings_has_machine_checkpoint()
            if not audit_ok:
                append_compaction_audit(
                    {
                        "kind": "post_compaction_audit_failed",
                        "remediation": post_remediation,
                        "run_id": getattr(ctx, "run_id", "") or ctx.metadata.get("run_id", ""),
                    },
                )
                emit_intelligence_event("post_compaction_audit_failed", {})
                if post_remediation == "clarification":
                    try:
                        from l3_node.intelligence_p1 import enqueue_clarification

                        enqueue_clarification(
                            "上下文压缩后审计未通过：findings.md 中缺少 MACHINE_CHECKPOINT 块，请检查记忆与任务续跑上下文。",
                        )
                    except Exception as e:
                        logger.debug("[Compaction] post_audit clarification 跳过: %s", e)
                elif post_remediation == "memory_flush_retry":
                    try:
                        await _run_memory_flush(_pre_fold_messages, summary_model)
                        append_compaction_audit({"kind": "post_compaction_memory_flush_retry", "run_id": getattr(ctx, "run_id", "")})
                    except Exception as e:
                        logger.debug("[Compaction] post_audit memory_flush_retry 跳过: %s", e)
    except Exception as e:
        logger.warning("[Compaction] 折叠失败，跳过: %s", e)
        # 不抛出，避免影响主流程；仅记录警告


async def run_pre_reset_memory_flush() -> int:
    """
    智能化 P0：Pre-reset / Pre-new 记忆刷新。
    会话重置前，从 short_term 提取近期对话，执行记忆刷新，避免遗忘。
    供 /llm/context/reset、/new 等触发。
    """
    flush_enabled, _ = _get_memory_flush_config()
    if not flush_enabled:
        return 0
    try:
        from core.biological_memory import get_short_term_for_dream

        logs = get_short_term_for_dream(limit=30)
        if len(logs) < 3:
            return 0
        # 转换为 messages 格式
        messages = []
        for log in logs:
            role = log.get("role", "user")
            content = (log.get("content") or "").strip()
            if content:
                messages.append({"role": role, "content": content})
        if not messages:
            return 0
        _, summary_model = _get_compaction_config()
        return await _run_memory_flush(messages, summary_model)
    except Exception as e:
        logger.debug("[Compaction] pre_reset_flush 跳过: %s", e)
        return 0


def register_compaction_hook() -> None:
    """注册 Compaction 到 HOOK_BEFORE_LLM_THINK（core 全局 hooks）"""
    global_hooks.register(HOOK_BEFORE_LLM_THINK, compaction_before_llm_think)


# 模块加载时自动注册
register_compaction_hook()
