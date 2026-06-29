#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM 意图规划 + VL 候选重排（配合 test_florence2_nl_click.py 的 Florence-2 接地）。"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

_JSON_OBJ_RE = re.compile(r"\{[^{}]*(?:\{[^{}]*}[^{}]*)*\}", re.DOTALL)

_PLANNER_SYSTEM = """你是视觉点击任务的「意图规划器」。用户用自然语言描述要在屏幕截图里点击什么。
你不能输出像素坐标。请把意图拆解成 Florence-2 视觉接地模型能理解的**短英文短语**（每个约 3–12 词），并给出空间提示。

输出必须是单个 JSON 对象（不要 markdown）：
{
  "intent_summary": "一句话中文摘要",
  "florence_phrases": ["phrase1", "phrase2"],
  "spatial_hint": "大致在截图哪个区域（中文）",
  "relative_position": "top-left|top|top-right|left|center|right|bottom-left|bottom|bottom-right|unknown",
  "target_label": "用户要点的精确文字/数字/图标，如 8 或 + 或 Play Now",
  "avoid_labels": ["易混淆项，如相邻键 9、=、-"],
  "verification_criteria": "点击前如何目视确认点对（中文，如：必须是加号+，不能是等号=）",
  "layout_relation": "与邻近控件的空间关系（中文，如：在右侧运算符列、位于等号上方一行）"
}

规则：
- florence_phrases：2–4 条，英文，从具体到略泛化；适合 CAPTION_TO_PHRASE_GROUNDING
- 运算符/数字键：短语里写清符号，如 "plus sign button above equals", "the + operator key"
- 禁止把用户整句原文、或「找到/点击/please click」塞进短语
- verification_criteria / layout_relation 要具体，供后续「点击前验证」与失败重规划使用
"""

_VERIFY_PATCH_SYSTEM = """你是视觉点击任务的「点击前验证器」。你会看到一张**小图**——即系统即将点击位置附近的局部截图。
请判断：若鼠标点在图中央，是否满足用户意图？

只返回 JSON（不要 markdown）：
{
  "match": true,
  "seen_symbol": "你在图中央实际看到的符号/文字/按钮类型",
  "confidence": "high",
  "reason": "一句中文"
}

字段说明：
- match：只有图中央确实是用户要的控件才为 true；若看到的是 avoid 中的混淆项，必须为 false
- confidence：high | medium | low
- 符号类目标（+ - × ÷ = 数字）：必须看清符号本身，不能猜
"""

_REPLAN_SYSTEM = """你是视觉点击任务的「纠错规划器」。上一轮候选都未通过点击前验证。
请阅读失败记录，**换策略**生成新的 grounding 规划（新短语、更准的空间/布局描述），不要重复已失败的 phrase。

输出 JSON 格式与意图规划器相同（所有字段必填，尤其是 target_label / avoid_labels / florence_phrases），并多加：
"adaptation_note": "相对上一轮改了什么"

注意：
- florence_phrases 必须是 2–4 条**英文**短语，禁止中文、禁止用户原话
- 若失败原因是「看到了 = 而不是 +」，新短语应强调 plus above equals、not the equals button 等
"""

_REPLAN_SYSTEM_FULL = _PLANNER_SYSTEM + "\n\n---\n\n" + _REPLAN_SYSTEM

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

_RERANK_SYSTEM = """你是视觉点击任务的「候选判定器」。截图上用**绿色框+编号**标出了 Florence 模型的多个候选。
请根据用户目标，选出**唯一**最该点击的编号。

只返回 JSON（不要 markdown）：
{"chosen_index": 1, "reason": "简短中文理由"}
若无一合适：{"chosen_index": null, "reason": "..."}
chosen_index 为 1-based，对应图上绿色编号。"""


def dashscope_openai_client():
    """OpenAI 兼容客户端（百炼 / DashScope，读 .env）。"""
    _cn = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    _sea = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    r = (os.environ.get("JACHIN_ACTIVE_REGION") or "").strip().upper() or "CN"
    if r == "SEA":
        key = (os.environ.get("DASHSCOPE_API_KEY_SEA") or "").strip()
        base = (os.environ.get("DASHSCOPE_API_BASE_SEA") or "").strip() or _sea
    else:
        key = (os.environ.get("DASHSCOPE_API_KEY_CN") or "").strip()
        base = (os.environ.get("DASHSCOPE_API_BASE_CN") or "").strip() or _cn
    if not key:
        key = (
            (os.environ.get("DASHSCOPE_API_KEY") or "")
            or (os.environ.get("QWEN_API_KEY") or "")
            or (os.environ.get("QWEN_AI_API_KEY") or "")
        ).strip()
    if not base:
        base = (os.environ.get("DASHSCOPE_API_BASE") or "").strip() or _cn
    if not key:
        raise RuntimeError(
            "未配置 LLM：请设置 DASHSCOPE_API_KEY 或 QWEN_API_KEY（.env）"
        )
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError("请安装 openai: pip install openai") from e
    return OpenAI(api_key=key, base_url=base.rstrip("/")), key


def _extract_json_obj(text: str) -> dict[str, Any] | None:
    s = (text or "").strip()
    if not s:
        return None
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    m = _JSON_OBJ_RE.search(s)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


@dataclass
class LlmChatTrace:
    """单次 LLM 调用的可观测追踪（含 thinking / token / 耗时）。"""

    content: str = ""
    reasoning_content: str = ""
    latency_ms: float = 0.0
    model: str = ""
    finish_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    enable_thinking: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _planner_enable_thinking(explicit: bool | None = None) -> bool:
    if explicit is not None:
        return explicit
    v = (os.environ.get("NL_CLICK_PLANNER_ENABLE_THINKING") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _planner_extra_body(model: str, *, enable_thinking: bool) -> dict[str, Any]:
    """qwen3.5-plus 默认 thinking 会显著变慢且可能占满 token；规划 JSON 默认关 thinking。"""
    if enable_thinking:
        return {}
    if "qwen3" in (model or "").lower():
        return {"extra_body": {"enable_thinking": False}}
    return {}


def _usage_to_dict(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    if isinstance(usage, dict):
        return usage
    out: dict[str, Any] = {}
    for key in (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "reasoning_tokens",
        "output_tokens",
        "input_tokens",
    ):
        val = getattr(usage, key, None)
        if val is not None:
            out[key] = val
    return out


def log_planner_trace(stage: str, trace: LlmChatTrace, *, verbose: bool = True) -> None:
    """打印意图规划/纠错 LLM 的详细日志（含 thinking 链）。"""
    print(
        f"[llm][{stage}] model={trace.model!r} latency={trace.latency_ms:.0f}ms "
        f"finish={trace.finish_reason!r} thinking={'on' if trace.enable_thinking else 'off'}",
        flush=True,
    )
    if trace.usage:
        parts = [f"{k}={v}" for k, v in trace.usage.items()]
        print(f"[llm][{stage}] usage: {', '.join(parts)}", flush=True)
    if not verbose:
        preview = (trace.content or trace.reasoning_content or "")[:200]
        if preview:
            print(f"[llm][{stage}] preview: {preview!r}", flush=True)
        return
    if trace.reasoning_content:
        print(f"[llm][{stage}] ----- reasoning / 思考链 -----", flush=True)
        print(trace.reasoning_content, flush=True)
        print(f"[llm][{stage}] ----- end reasoning -----", flush=True)
    else:
        print(f"[llm][{stage}] (无 reasoning_content)", flush=True)
    print(f"[llm][{stage}] ----- content / 正文 -----", flush=True)
    print(trace.content or "(空 content)", flush=True)
    print(f"[llm][{stage}] ----- end content -----", flush=True)


def _llm_chat_with_trace(
    *,
    model: str,
    system: str,
    user: str,
    temperature: float = 0.1,
    json_mode: bool = True,
    max_tokens: int = 2048,
    enable_thinking: bool | None = None,
) -> LlmChatTrace:
    client, _ = dashscope_openai_client()
    thinking = _planner_enable_thinking(enable_thinking)
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    extra = _planner_extra_body(model, enable_thinking=thinking)
    if extra:
        kwargs.update(extra)
    t0 = time.perf_counter()
    resp = client.chat.completions.create(**kwargs)
    latency_ms = (time.perf_counter() - t0) * 1000.0
    choice = resp.choices[0]
    msg = choice.message
    content = (getattr(msg, "content", None) or "").strip()
    reasoning = (getattr(msg, "reasoning_content", None) or "").strip()
    return LlmChatTrace(
        content=content,
        reasoning_content=reasoning,
        latency_ms=latency_ms,
        model=model,
        finish_reason=getattr(choice, "finish_reason", None),
        usage=_usage_to_dict(getattr(resp, "usage", None)),
        enable_thinking=thinking,
    )


def _llm_chat_text(
    *,
    model: str,
    system: str,
    user: str,
    temperature: float = 0.1,
    json_mode: bool = True,
    max_tokens: int = 2048,
    enable_thinking: bool | None = None,
    verbose: bool = False,
    trace_stage: str = "chat",
) -> str:
    trace = _llm_chat_with_trace(
        model=model,
        system=system,
        user=user,
        temperature=temperature,
        json_mode=json_mode,
        max_tokens=max_tokens,
        enable_thinking=enable_thinking,
    )
    if verbose:
        log_planner_trace(trace_stage, trace, verbose=True)
    return trace.content


def _pil_to_data_url(image) -> str:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _vl_chat_json(*, model: str, system: str, user: str, image) -> dict[str, Any] | None:
    client, _ = dashscope_openai_client()
    data_url = _pil_to_data_url(image)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        temperature=0.1,
        max_tokens=512,
    )
    text = (resp.choices[0].message.content or "").strip()
    return _extract_json_obj(text)


@dataclass
class GroundingPlan:
    user_query: str
    intent_summary: str = ""
    florence_phrases: list[str] = field(default_factory=list)
    spatial_hint: str = ""
    relative_position: str = "unknown"
    target_label: str = ""
    avoid_labels: list[str] = field(default_factory=list)
    verification_criteria: str = ""
    layout_relation: str = ""
    adaptation_note: str = ""
    scene_caption: str | None = None
    planner_model: str = ""
    planner_latency_ms: float = 0.0
    llm_trace: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_valid_florence_phrase(phrase: str, *, user_query: str = "") -> bool:
    s = (phrase or "").strip()
    if len(s) < 3 or len(s) > 80:
        return False
    cjk = _CJK_RE.findall(s)
    if cjk and len(cjk) >= 2:
        return False
    uq = (user_query or "").strip()
    if uq and (s == uq or (len(s) >= len(uq) * 0.85 and uq in s)):
        return False
    low = s.lower()
    for bad in ("click", "please click", "help me", "找到", "点击", "帮我"):
        if bad in low or bad in s:
            return False
    return True


def _filter_florence_phrases(phrases: list[str], user_query: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for p in phrases:
        p = str(p).strip()
        if not p or not _is_valid_florence_phrase(p, user_query=user_query):
            continue
        key = p.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _merge_replan_plans(prev: GroundingPlan, new: GroundingPlan) -> GroundingPlan:
    """纠错轮 JSON 缺字段时保留上一轮有效规划，避免空 target / 中文短语直通 Florence。"""
    phrases = _filter_florence_phrases(new.florence_phrases, prev.user_query)
    if not phrases:
        phrases = list(prev.florence_phrases)
        if new.adaptation_note:
            print(
                "[llm][replan] 新短语无效，保留上一轮 Florence 短语",
                flush=True,
            )
    return GroundingPlan(
        user_query=prev.user_query,
        intent_summary=new.intent_summary or prev.intent_summary,
        florence_phrases=phrases[:4] or prev.florence_phrases,
        spatial_hint=new.spatial_hint or prev.spatial_hint,
        relative_position=(
            new.relative_position
            if new.relative_position and new.relative_position != "unknown"
            else prev.relative_position
        ),
        target_label=new.target_label or prev.target_label,
        avoid_labels=new.avoid_labels or list(prev.avoid_labels),
        verification_criteria=new.verification_criteria or prev.verification_criteria,
        layout_relation=new.layout_relation or prev.layout_relation,
        adaptation_note=new.adaptation_note or prev.adaptation_note,
        scene_caption=prev.scene_caption,
        planner_model=new.planner_model or prev.planner_model,
        planner_latency_ms=new.planner_latency_ms,
        llm_trace=new.llm_trace or prev.llm_trace,
    )


@dataclass
class VerifyResult:
    candidate_index: int
    match: bool
    seen_symbol: str = ""
    confidence: str = "low"
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def ok(self) -> bool:
        return self.match and self.confidence in ("high", "medium")


@dataclass
class GroundingCandidate:
    phrase: str
    bbox: list[float]
    cx: float
    cy: float
    label: str
    heuristic_score: float = 0.0
    florence_raw: Any = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("florence_raw", None)
        return d


def _fallback_plan(user_query: str) -> GroundingPlan:
    """无 LLM 时的最小降级：把用户句子里可能的英文/数字抽成短语。"""
    q = user_query.strip()
    phrases: list[str] = []
    # 提取数字/字母作为 target
    m = re.search(r"[0-9A-Za-z]+", q)
    target = m.group(0) if m else ""
    if target:
        phrases = [
            f"calculator button {target}",
            f"the digit {target} key",
            f"button labeled {target}",
        ]
    else:
        phrases = [q[:64] if len(q) <= 64 else q[:64]]
    return GroundingPlan(
        user_query=user_query,
        intent_summary="（无 LLM，直通 Florence）",
        florence_phrases=phrases,
        spatial_hint="",
        relative_position="unknown",
        target_label=target,
        avoid_labels=[],
    )


def plan_grounding_intent(
    user_query: str,
    *,
    image_width: int,
    image_height: int,
    scene_caption: str | None = None,
    llm_model: str,
    planner_verbose: bool = True,
    enable_thinking: bool | None = None,
) -> GroundingPlan:
    caption_block = ""
    if scene_caption:
        caption_block = f"\n\n【Florence 画面描述（供参考，非坐标）】\n{scene_caption[:1200]}\n"

    user_prompt = (
        f"用户原话：{user_query}\n"
        f"截图尺寸：宽 {image_width} px × 高 {image_height} px（坐标系原点在截图左上角）"
        f"{caption_block}\n"
        "请输出 JSON 规划。"
    )
    try:
        trace = _llm_chat_with_trace(
            model=llm_model,
            system=_PLANNER_SYSTEM,
            user=user_prompt,
            enable_thinking=enable_thinking,
        )
    except Exception as e:
        print(f"[WARN] LLM 规划失败 ({e})，降级为简单短语", flush=True)
        return _fallback_plan(user_query)

    if planner_verbose:
        log_planner_trace("plan", trace, verbose=True)

    obj = _extract_json_obj(trace.content) or {}
    if not obj and trace.reasoning_content:
        obj = _extract_json_obj(trace.reasoning_content) or {}
        if obj:
            print("[llm][plan] JSON 从 reasoning_content 回退提取", flush=True)

    if not obj:
        print("[WARN] LLM 规划未返回可解析 JSON，降级为简单短语", flush=True)
        fb = _fallback_plan(user_query)
        fb.llm_trace = trace.to_dict()
        return fb

    phrases = obj.get("florence_phrases") or []
    if isinstance(phrases, str):
        phrases = [phrases]
    phrases = _filter_florence_phrases(
        [str(p).strip() for p in phrases if str(p).strip()],
        user_query,
    )
    if not phrases:
        phrases = _fallback_plan(user_query).florence_phrases
    obj["florence_phrases"] = phrases[:4]

    plan = _plan_from_json_obj(
        obj,
        user_query=user_query,
        scene_caption=scene_caption,
        llm_model=llm_model,
        latency_ms=trace.latency_ms,
    )
    plan.llm_trace = trace.to_dict()
    return plan


def _plan_from_json_obj(
    obj: dict[str, Any],
    *,
    user_query: str,
    scene_caption: str | None,
    llm_model: str,
    latency_ms: float = 0.0,
) -> GroundingPlan:
    phrases = obj.get("florence_phrases") or []
    if isinstance(phrases, str):
        phrases = [phrases]
    phrases = _filter_florence_phrases(
        [str(p).strip() for p in phrases if str(p).strip()],
        user_query,
    )
    avoid = obj.get("avoid_labels") or []
    if isinstance(avoid, str):
        avoid = [avoid]
    if not phrases:
        phrases = _fallback_plan(user_query).florence_phrases
    return GroundingPlan(
        user_query=user_query,
        intent_summary=str(obj.get("intent_summary") or "").strip(),
        florence_phrases=phrases[:4] or _fallback_plan(user_query).florence_phrases,
        spatial_hint=str(obj.get("spatial_hint") or "").strip(),
        relative_position=str(obj.get("relative_position") or "unknown").strip().lower(),
        target_label=str(obj.get("target_label") or "").strip(),
        avoid_labels=[str(x).strip() for x in avoid if str(x).strip()],
        verification_criteria=str(obj.get("verification_criteria") or "").strip(),
        layout_relation=str(obj.get("layout_relation") or "").strip(),
        adaptation_note=str(obj.get("adaptation_note") or "").strip(),
        scene_caption=scene_caption,
        planner_model=llm_model,
        planner_latency_ms=latency_ms,
    )


def replan_after_verification_failures(
    plan: GroundingPlan,
    failures: list[VerifyResult],
    candidates: list[GroundingCandidate],
    *,
    image_width: int,
    image_height: int,
    llm_model: str,
    planner_verbose: bool = True,
    enable_thinking: bool | None = None,
) -> GroundingPlan:
    """验证全失败后，让 LLM 读失败原因并换策略。"""
    lines = []
    for vr in failures:
        c = candidates[vr.candidate_index]
        lines.append(
            f"  候选 #{vr.candidate_index + 1} center=({c.cx:.0f},{c.cy:.0f}) "
            f"phrase={c.phrase!r} → 验证: match={vr.match} seen={vr.seen_symbol!r} "
            f"conf={vr.confidence} 原因={vr.reason}"
        )
    user = (
        f"用户原话：{plan.user_query}\n"
        f"上一轮摘要：{plan.intent_summary}\n"
        f"目标：{plan.target_label!r}  避免：{plan.avoid_labels}\n"
        f"验证标准：{plan.verification_criteria or plan.intent_summary}\n"
        f"布局关系：{plan.layout_relation or plan.spatial_hint}\n"
        f"上一轮 Florence 短语：{plan.florence_phrases}\n"
        f"截图：{image_width}x{image_height}\n\n"
        f"失败记录：\n" + "\n".join(lines) + "\n\n"
        "请输出新的 JSON 规划（英文 florence_phrases，勿重复已失败 phrase）。"
    )
    try:
        trace = _llm_chat_with_trace(
            model=llm_model,
            system=_REPLAN_SYSTEM_FULL,
            user=user,
            enable_thinking=enable_thinking,
        )
    except Exception as e:
        print(f"[WARN] 纠错重规划失败: {e}", flush=True)
        return plan

    if planner_verbose:
        log_planner_trace("replan", trace, verbose=True)

    obj = _extract_json_obj(trace.content) or {}
    if not obj and trace.reasoning_content:
        obj = _extract_json_obj(trace.reasoning_content) or {}
        if obj:
            print("[llm][replan] JSON 从 reasoning_content 回退提取", flush=True)

    if not obj:
        print("[WARN] 纠错重规划未返回 JSON，保留上一轮规划", flush=True)
        return plan

    new_plan = _plan_from_json_obj(
        obj,
        user_query=plan.user_query,
        scene_caption=plan.scene_caption,
        llm_model=llm_model,
        latency_ms=trace.latency_ms,
    )
    new_plan.llm_trace = {"replan": trace.to_dict(), "plan": plan.llm_trace}
    return _merge_replan_plans(plan, new_plan)


def _zone_score(relative_position: str, nx: float, ny: float) -> float:
    """nx, ny in 0..1；与 relative_position 一致则加分。"""
    rp = (relative_position or "unknown").lower()
    zones: dict[str, tuple[float, float, float, float]] = {
        "top-left": (0.0, 0.0, 0.5, 0.5),
        "top": (0.25, 0.0, 0.75, 0.45),
        "top-right": (0.5, 0.0, 1.0, 0.5),
        "left": (0.0, 0.25, 0.45, 0.75),
        "center": (0.25, 0.25, 0.75, 0.75),
        "right": (0.55, 0.25, 1.0, 0.75),
        "bottom-left": (0.0, 0.5, 0.5, 1.0),
        "bottom": (0.25, 0.55, 0.75, 1.0),
        "bottom-right": (0.5, 0.5, 1.0, 1.0),
    }
    if rp not in zones:
        return 0.0
    x1, y1, x2, y2 = zones[rp]
    if x1 <= nx <= x2 and y1 <= ny <= y2:
        return 1.0
    # 软距离：离 zone 中心越近分越高
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    dist = ((nx - cx) ** 2 + (ny - cy) ** 2) ** 0.5
    return max(0.0, 1.0 - dist * 2)


def _label_heuristic(plan: GroundingPlan, label: str) -> float:
    lab = (label or "").strip().lower()
    score = 0.0
    target = (plan.target_label or "").strip().lower()
    if target:
        if lab == target or target in lab.split():
            score += 3.0
        elif target in lab:
            score += 1.5
    for bad in plan.avoid_labels:
        b = bad.strip().lower()
        if b and (lab == b or b in lab.split()):
            score -= 2.5
    return score


def bbox_area(bbox: list[float]) -> float:
    x1, y1, x2, y2 = bbox
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def bbox_area_ratio(bbox: list[float], image_width: int, image_height: int) -> float:
    denom = max(1, image_width * image_height)
    return bbox_area(bbox) / denom


def is_atomic_target(plan: GroundingPlan | None) -> bool:
    """单字符/短标签（数字键、字母键）需小框精点，不能用半屏大框。"""
    if plan is None:
        return False
    t = (plan.target_label or "").strip()
    if not t:
        return False
    if len(t) <= 3 and t.isalnum():
        return True
    return len(t) <= 2


def filter_oversized_candidates(
    candidates: list[GroundingCandidate],
    *,
    image_width: int,
    image_height: int,
    max_area_ratio: float,
) -> list[GroundingCandidate]:
    kept = [
        c
        for c in candidates
        if bbox_area_ratio(c.bbox, image_width, image_height) <= max_area_ratio
    ]
    return kept if kept else candidates


def score_candidates_heuristic(
    plan: GroundingPlan,
    candidates: list[GroundingCandidate],
    *,
    image_width: int,
    image_height: int,
) -> None:
    w = max(1, image_width)
    h = max(1, image_height)
    for c in candidates:
        nx, ny = c.cx / w, c.cy / h
        ratio = bbox_area(c.bbox) / (w * h)
        # 通用：偏好更紧凑的定位框（最终是否点击由 patch 验证决定）
        if ratio > 0.15:
            size_penalty = -1.5
        elif ratio > 0.08:
            size_penalty = -0.5
        elif ratio < 0.04:
            size_penalty = 0.5
        else:
            size_penalty = 0.0
        c.heuristic_score = (
            _zone_score(plan.relative_position, nx, ny)
            + _label_heuristic(plan, c.label)
            + size_penalty
        )


def draw_numbered_candidates(image, candidates: list[GroundingCandidate]):
    from PIL import ImageDraw, ImageFont

    img = image.copy()
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 16)
    except OSError:
        font = ImageFont.load_default()
    for i, c in enumerate(candidates, start=1):
        x1, y1, x2, y2 = c.bbox
        draw.rectangle([x1, y1, x2, y2], outline="#00FF44", width=3)
        tag = f"#{i}"
        draw.rectangle([x1, max(0, y1 - 20), x1 + 28, y1], fill="#00FF44")
        draw.text((x1 + 4, max(0, y1 - 18)), tag, fill="#000000", font=font)
    return img


def rerank_candidates_with_vl(
    plan: GroundingPlan,
    candidates: list[GroundingCandidate],
    annotated_image,
    *,
    vl_model: str,
) -> tuple[int | None, str]:
    """返回 0-based index 或 None。"""
    if len(candidates) <= 1:
        return (0 if candidates else None), "only_one_or_none"

    avoid = "、".join(plan.avoid_labels) if plan.avoid_labels else "（无）"
    lines = []
    for i, c in enumerate(candidates, start=1):
        lines.append(
            f"  #{i} phrase={c.phrase!r} label={c.label!r} "
            f"center=({c.cx:.0f},{c.cy:.0f}) heuristic={c.heuristic_score:.2f}"
        )
    user = (
        f"用户原话：{plan.user_query}\n"
        f"规划摘要：{plan.intent_summary}\n"
        f"目标标签：{plan.target_label or '（未指定）'}\n"
        f"空间提示：{plan.spatial_hint or '（无）'}\n"
        f"应避免：{avoid}\n\n"
        f"候选列表：\n" + "\n".join(lines) + "\n\n"
        "请看图中绿色编号框，选出最该点击的一个。\n"
        "重要：若目标是单个数字/字母，必须选**框最小、只罩住该键**的候选；"
        "不要选罩住整块键盘或半屏的大框（大框中心会偏到相邻键如 9）。"
    )
    try:
        obj = _vl_chat_json(
            model=vl_model,
            system=_RERANK_SYSTEM,
            user=user,
            image=annotated_image,
        )
    except Exception as e:
        return None, f"vl_error:{e}"

    if not obj:
        return None, "vl_no_json"
    idx = obj.get("chosen_index")
    reason = str(obj.get("reason") or "")
    if idx is None:
        return None, reason or "vl_rejected_all"
    try:
        one_based = int(idx)
    except (TypeError, ValueError):
        return None, reason or "vl_bad_index"
    if one_based < 1 or one_based > len(candidates):
        return None, reason or "vl_out_of_range"
    return one_based - 1, reason


def crop_patch_around_point(image, cx: float, cy: float, *, size: int = 128):
    """以点击点为中心裁局部图，供点击前验证（比 Florence 大框更可靠）。"""
    half = max(48, size // 2)
    left = max(0, int(cx) - half)
    top = max(0, int(cy) - half)
    right = min(image.width, int(cx) + half)
    bottom = min(image.height, int(cy) + half)
    if right - left < 32 or bottom - top < 32:
        return image.copy()
    return image.crop((left, top, right, bottom))


def verify_click_patch(
    plan: GroundingPlan,
    patch_image,
    *,
    vl_model: str,
    candidate_index: int,
) -> VerifyResult:
    avoid = "、".join(plan.avoid_labels) if plan.avoid_labels else "（无）"
    user = (
        f"用户原话：{plan.user_query}\n"
        f"目标：{plan.target_label or plan.intent_summary}\n"
        f"验证标准：{plan.verification_criteria or plan.intent_summary}\n"
        f"布局关系：{plan.layout_relation or plan.spatial_hint or '（无）'}\n"
        f"必须避免误点为：{avoid}\n\n"
        "请只看这张局部图中央，判断若在此点击是否满足目标。"
    )
    try:
        obj = _vl_chat_json(
            model=vl_model,
            system=_VERIFY_PATCH_SYSTEM,
            user=user,
            image=patch_image,
        )
    except Exception as e:
        return VerifyResult(
            candidate_index=candidate_index,
            match=False,
            reason=f"verify_error:{e}",
        )
    if not obj:
        return VerifyResult(candidate_index=candidate_index, match=False, reason="verify_no_json")
    conf = str(obj.get("confidence") or "low").strip().lower()
    if conf not in ("high", "medium", "low"):
        conf = "low"
    return VerifyResult(
        candidate_index=candidate_index,
        match=bool(obj.get("match")),
        seen_symbol=str(obj.get("seen_symbol") or ""),
        confidence=conf,
        reason=str(obj.get("reason") or ""),
    )


def select_candidate_by_patch_verification(
    plan: GroundingPlan,
    candidates: list[GroundingCandidate],
    image,
    *,
    vl_model: str,
) -> tuple[int | None, list[VerifyResult]]:
    """按启发式顺序逐个 patch 验证，返回第一个通过的候选。"""
    if not candidates:
        return None, []
    order = sorted(
        range(len(candidates)),
        key=lambda i: candidates[i].heuristic_score,
        reverse=True,
    )
    failures: list[VerifyResult] = []
    for idx in order:
        c = candidates[idx]
        patch = crop_patch_around_point(image, c.cx, c.cy)
        vr = verify_click_patch(plan, patch, vl_model=vl_model, candidate_index=idx)
        failures.append(vr)
        status = "通过" if vr.ok else "拒绝"
        print(
            f"  [verify] 候选 #{idx + 1} center=({c.cx:.0f},{c.cy:.0f}) "
            f"{status} seen={vr.seen_symbol!r} conf={vr.confidence} — {vr.reason}",
            flush=True,
        )
        if vr.ok:
            return idx, failures
    return None, failures
