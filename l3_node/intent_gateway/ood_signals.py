"""
§12.4 OOD / UNKNOWN：否决直连 completion；对「高置信键盘游走/重复拉丁/乱码」硬拦截整轮 LLM。
正常一段话式中英技术需求不因「混排」误拦；分类面 OOD 第二路优先用 bundle「当前路由/用户句」而非短记忆拼接全文。
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Tuple

# 可打印、常见中文标点、空白
_PRINTABLE_OK = re.compile(r"[\w\s\u4e00-\u9fff，。！？、；：「」『』（）\[\]【】《》\"'.,;:!?\-+=%/\\]", re.UNICODE)

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# 常见键盘游走 / 无意义拉丁片段（不依赖完整词典）
_KEYBOARD_SPAM_RE = re.compile(
    r"asdfghjkl|qwerty|qweqwe|zxcvbnm|qwer|wqwq|hjkl{2,}|"
    r"asdf{2,}|dfgh{2,}|erty{2,}|uiop|vbnm|zxcv|cxzv|fghj|"
    r"^[\s;:,]*[a-z]{6,}[\s;:,]*$",
    re.I | re.MULTILINE,
)

# 同一短拉丁串重复 2 次及以上（如 qweqwe、asdfasdf）
_REPEATED_LATIN_CHUNK_RE = re.compile(r"([a-z]{2,5})\1+", re.I)

# 长串小写拉丁、几乎无空格（乱敲）
_LONG_LATIN_NO_SPACE_RE = re.compile(r"[a-z]{14,}")

# --- L0.5 技术载荷白名单：运维排查中的 JWT / Base64 / JSON / 堆栈 / K8s 等 ---
_JWT_LIKE_RE = re.compile(
    r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",
    re.I,
)
_HEX_HASH_RE = re.compile(r"\b[a-fA-F0-9]{32,64}\b")
_K8S_OR_RUNTIME_MARKERS_RE = re.compile(
    r"CrashLoopBackOff|Error\s+from\s+server|Unable\s+to\s+connect|"
    r"pod\s+[\"']|namespace\s+|deployment[\./]|daemonset[\./]|"
    r"k8s\.io|kubectl\b|kube-system|containerd|docker://|imagepullbackoff",
    re.I,
)
_ERR_STACK_MARKERS_RE = re.compile(
    r"Traceback\s*\(most\s+recent\s+call\s+last\)|Exception\s+in\s+thread|"
    r"Caused\s+by:\s*|at\s+[\w$.]+\([\w$.]+:\d+\)|\bstderr\b|\bstdout\b",
    re.I,
)
_JSON_OBJECT_HEAD_RE = re.compile(r"\{\s*[\"'][\w.-]+[\"']\s*:")
_DIAGNOSTIC_FRAMING_RE = re.compile(
    r"报错|错误|日志|堆栈|异常|排查|看看|分析|token|jwt|base64|"
    r"接口|请求失败|响应码|trace|curl|http\s*状态|status\s*code|"
    r"无法连接|连接超时|timeout|panic:|fatal\s*:|errno",
    re.I,
)

# 正常需求句（中英混排含技术标识符）：在「无键盘游走/无重复拉丁块」时不判 mixed_injection
_NATURAL_TASK_HINT_RE = re.compile(
    r"请|帮|能否|希望|需要|帮我|写|创建|新建|生成|修改|删除|添加|实现|运行|执行|打印|输出|"
    r"获取|监控|查询|统计|分析|安装|配置|部署|脚本|代码|程序|文件|文件夹|目录|路径|项目|"
    r"告诉|说明|解释|列出|汇总|整理|转换|导出|导入|下载|上传|订阅|取消|开始|停止|"
    r"模型|接口|数据库|表|字段|服务|系统|内存|CPU|网络|磁盘|报错|日志|错误|为什么|怎么|如何|"
    r"工作区|绝对路径|保存|每隔|秒|打印|占用率",
    re.I,
)

# mixed_injection 标签最低分（与 hard_min 解耦：标签要「够毒」才贴 mixed）
_MIXED_INJECTION_LABEL_MIN_SCORE = 0.82


def _latin_letter_ratio(t: str) -> float:
    n = len(t)
    if not n:
        return 0.0
    return sum(1 for c in t if "a" <= c <= "z" or "A" <= c <= "Z") / n


def _natural_zh_instruction_exempt_from_mixed(t: str) -> bool:
    """
    一段话式正常指令：含足够中文与任务语义，且拉丁占比适中、无明确键盘/重复噪声特征。
    用于避免「Python/文件名/API」等合法中英混排被判 mixed_injection。
    """
    s = (t or "").strip()
    if len(s) < 14:
        return False
    if not _CJK_RE.search(s):
        return False
    if _KEYBOARD_SPAM_RE.search(s) or _REPEATED_LATIN_CHUNK_RE.search(s):
        return False
    if _LONG_LATIN_NO_SPACE_RE.search(s):
        return False
    if _repeated_short_token_score(s) >= 0.75:
        return False
    lat_r = _latin_letter_ratio(s)
    if lat_r > 0.40:
        return False
    if len(_CJK_RE.findall(s)) < 5:
        return False
    return bool(_NATURAL_TASK_HINT_RE.search(s))


def _repeated_short_token_score(t: str) -> float:
    """如 qwe 出现两次以上（分词粒度较粗）。"""
    low = t.lower()
    toks = re.findall(r"[a-z]{3,12}(?![a-z])", low)
    if not toks:
        return 0.0
    top = Counter(toks).most_common(1)[0][1]
    if top >= 3:
        return 0.9
    if top >= 2:
        return 0.75
    return 0.0


def surface_ood_class(text: str) -> Tuple[str, float]:
    """
    返回 (label, score)。label 含 normal | ood_gibberish | ood_unknown_short | ood_keyboard_mash | ood_mixed_injection。
    score 越高越异常。
    """
    t = (text or "").strip()
    if not t:
        return "normal", 0.0
    n = len(t)
    if n > 8000:
        t = t[:8000]
        n = len(t)

    # --- 混合注入：仅在高置信「拉丁噪声」特征下贴标；正常一段话技术需求不进入 ---
    has_cjk = bool(_CJK_RE.search(t))
    lat_r = _latin_letter_ratio(t)
    if has_cjk and lat_r >= 0.14 and n >= 16:
        if _natural_zh_instruction_exempt_from_mixed(t):
            pass  # 交由后续键盘/乱码检测；不因「中英混排」单独定罪
        else:
            mixed_score = 0.0
            if _KEYBOARD_SPAM_RE.search(t):
                mixed_score = max(mixed_score, 0.93)
            if _REPEATED_LATIN_CHUNK_RE.search(t):
                mixed_score = max(mixed_score, 0.91)
            mixed_score = max(mixed_score, _repeated_short_token_score(t))
            if _LONG_LATIN_NO_SPACE_RE.search(t):
                mixed_score = max(mixed_score, 0.88)
            if mixed_score >= _MIXED_INJECTION_LABEL_MIN_SCORE:
                return "ood_mixed_injection", mixed_score

    # --- 纯键盘游走 / 明显垃圾拉丁（可无中文）---
    if _KEYBOARD_SPAM_RE.search(t) or _REPEATED_LATIN_CHUNK_RE.search(t):
        return "ood_keyboard_mash", 0.9
    if n >= 10 and lat_r >= 0.85 and not has_cjk:
        if _repeated_short_token_score(t) >= 0.75 or _LONG_LATIN_NO_SPACE_RE.search(t):
            return "ood_keyboard_mash", 0.88

    printable_hits = sum(1 for c in t if _PRINTABLE_OK.match(c))
    ratio_print = printable_hits / max(n, 1)

    if ratio_print < 0.45 and n >= 8:
        return "ood_gibberish", min(1.0, 1.0 - ratio_print + 0.2)

    alnum_cn = sum(1 for c in t if c.isalnum() or "\u4e00" <= c <= "\u9fff")
    if n <= 6 and alnum_cn < 2:
        return "ood_unknown_short", 0.85

    if n >= 12:
        uniq = len(set(t))
        if uniq <= 3:
            return "ood_gibberish", 0.7

    return "normal", 0.0


def text_has_technical_artifact_signature(text: str) -> bool:
    """
    判断文本是否含典型技术载荷（JWT、长 Base64 行、十六进制摘要、K8s/容器、堆栈、JSON 片段）。
    用于 L0.5 在「表面 OOD」命中时降低误杀，不替代语义层。
    """
    t = text or ""
    if len(t) < 16:
        return False
    if _JWT_LIKE_RE.search(t):
        return True
    if _K8S_OR_RUNTIME_MARKERS_RE.search(t) or _ERR_STACK_MARKERS_RE.search(t):
        return True
    if _JSON_OBJECT_HEAD_RE.search(t) and t.count("{") >= 1 and t.count("}") >= 1:
        return True
    if _HEX_HASH_RE.search(t):
        return True
    # 连续长行且主要为 Base64 / Base64URL 字母表
    for line in t.splitlines():
        s = line.strip()
        if len(s) < 48:
            continue
        if re.match(r"^[A-Za-z0-9+/=_-]+$", s) and sum(c in "+/=" for c in s) <= max(4, len(s) // 24):
            return True
    return False


def text_has_ops_diagnostic_framing(text: str) -> bool:
    """用户是否在描述「排查/报错/日志」类运维语境（与 mixed 豁免联用）。"""
    return bool(_DIAGNOSTIC_FRAMING_RE.search(text or ""))


def _apply_technical_exemption_to_hard_block(
    *,
    hard_block: bool,
    reason: str,
    lab: str,
    raw_user_input: str,
    classification_text: str,
    exemption_on: bool,
) -> tuple[bool, str]:
    if not hard_block or not exemption_on:
        return hard_block, reason
    combined = f"{raw_user_input or ''}\n{classification_text or ''}"
    if not text_has_technical_artifact_signature(combined):
        return hard_block, reason
    if lab in ("ood_gibberish", "ood_keyboard_mash"):
        return False, f"technical_exemption_l05:{reason}"
    if lab == "ood_mixed_injection" and text_has_ops_diagnostic_framing(
        raw_user_input or classification_text or ""
    ):
        return False, f"technical_exemption_l05:{reason}"
    return hard_block, reason


def _worst_surface_ood(*texts: str) -> Tuple[str, float, str]:
    """多段文本取最糟 OOD；返回 (label, score, which)。"""
    best_l = "normal"
    best_s = 0.0
    which = ""
    for i, tx in enumerate(texts):
        if not (tx or "").strip():
            continue
        lab, sc = surface_ood_class(tx)
        if sc > best_s or (sc == best_s and lab != "normal"):
            best_s = sc
            best_l = lab
            which = f"arg[{i}]"
    return best_l, best_s, which


@dataclass(frozen=True)
class OodGateResult:
    hard_block_llm: bool
    veto_direct_bypass: bool
    reason: str
    surface_label: str
    surface_score: float
    """与 §9 观测对齐：混合/键盘 OOD 视为稀疏不可信邻域"""
    treat_as_embedding_ood_sparse: bool


def evaluate_gateway_ood_gates(
    *,
    raw_user_input: str,
    classification_text: str,
    bundle_extra: dict | None = None,
) -> OodGateResult:
    """
    raw_user_input：原始用户句（防分类面截断/小模型「抠出」干净句绕过）。
    classification_text：网关分类面全文（兼容旧调用）。
    bundle_extra.ood_classification_surface：若存在非空，则与 raw_user_input 一起做表面 OOD，避免短记忆+用户句拼接误伤。
    """
    try:
        from l3_node.intent_gateway.config import get_intent_gateway_config

        cfg = get_intent_gateway_config()
    except Exception:
        cfg = {}

    be = bundle_extra or {}
    # 避免「短记忆 + --- + 当前句」整段参与 mixed 表面分：优先用 bundle 注入的当前路由/用户面
    _surf2 = be.get("ood_classification_surface")
    if isinstance(_surf2, str) and _surf2.strip():
        _class_for_ood = _surf2.strip()
    else:
        _class_for_ood = (classification_text or "").strip()
    lab, sc, _which = _worst_surface_ood(raw_user_input or "", _class_for_ood)

    mixed_on = bool(cfg.get("ood_mixed_injection_enabled", True))
    hard_on = bool(cfg.get("ood_hard_block_llm_enabled", True))
    try:
        hard_min = float(cfg.get("ood_hard_block_min_score", 0.72))
    except (TypeError, ValueError):
        hard_min = 0.72

    emb_sparse = bool(be.get("embedding_ood_sparse")) and bool(cfg.get("embedding_ood_veto_bypass_enabled", True))

    veto_bypass = False
    try:
        if not bool(cfg.get("ood_veto_direct_bypass_enabled", True)):
            pass
        elif emb_sparse:
            veto_bypass = True
        elif lab != "normal" and sc >= 0.55:
            veto_bypass = True
    except Exception:
        veto_bypass = lab != "normal" and sc >= 0.55

    hard_block = False
    reason = ""
    if hard_on and mixed_on:
        if lab in ("ood_mixed_injection", "ood_keyboard_mash") and sc >= hard_min:
            hard_block = True
            reason = f"{lab}:score={sc:.2f}"
        elif lab == "ood_gibberish" and sc >= max(hard_min, 0.78):
            hard_block = True
            reason = f"{lab}:score={sc:.2f}"

    try:
        _tex_on = bool(cfg.get("ood_technical_exemption_enabled", True))
    except Exception:
        _tex_on = True
    hard_block, reason = _apply_technical_exemption_to_hard_block(
        hard_block=hard_block,
        reason=reason,
        lab=lab,
        raw_user_input=raw_user_input or "",
        classification_text=_class_for_ood,
        exemption_on=_tex_on,
    )

    treat_sparse = emb_sparse or (lab in ("ood_mixed_injection", "ood_keyboard_mash") and sc >= max(0.82, hard_min - 0.06))

    return OodGateResult(
        hard_block_llm=hard_block,
        veto_direct_bypass=veto_bypass,
        reason=reason or (lab if lab != "normal" else ""),
        surface_label=lab,
        surface_score=sc,
        treat_as_embedding_ood_sparse=treat_sparse,
    )


def should_skip_progress_thought_kick(*, raw_user_input: str) -> bool:
    """
    WebSocket 在 run_agent 前会发「已接入任务…可能需数分钟」首包 thought；
    Lark IM 路径无此流式首包，仅收最终 reply。若本句将触发 OOD 硬拦，应跳过该首包，
    使桌面端与 Lark 都只呈现统一拒答，并避免「长任务」误导文案。
    """
    try:
        t = raw_user_input or ""
        og = evaluate_gateway_ood_gates(
            raw_user_input=t,
            classification_text=t,
            bundle_extra=None,
        )
        return bool(og.hard_block_llm)
    except Exception:
        return False


def should_veto_direct_llm_bypass(
    classification_text: str,
    *,
    bundle_extra: dict | None = None,
    raw_user_input: str = "",
) -> bool:
    og = evaluate_gateway_ood_gates(
        raw_user_input=raw_user_input,
        classification_text=classification_text,
        bundle_extra=bundle_extra,
    )
    return og.veto_direct_bypass


def get_ood_hard_block_reply() -> str:
    try:
        from l3_node.intent_gateway.config import get_intent_gateway_config

        m = str(get_intent_gateway_config().get("ood_hard_block_reply_zh") or "").strip()
        if m:
            return m
    except Exception:
        pass
    return (
        "【安全网关】检测到输入中含有键盘乱码、重复无意义片段或与正文混排的异常模式（疑似提示词夹带/分布外攻击）。"
        "已拒绝调用大模型，以避免算力滥用与高置信误解析。请删除乱码后，用单一、清晰的中文或英文重新描述需求。"
    )


def user_input_looks_like_mixed_poison(text: str) -> bool:
    """供 classification_llm 等跳过「抠句」扩写。"""
    lab, sc = surface_ood_class(text or "")
    return lab in ("ood_mixed_injection", "ood_keyboard_mash") and sc >= _MIXED_INJECTION_LABEL_MIN_SCORE
