#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
云端 VLM 视觉感知代理（Gemini 或阿里百炼 Qwen-VL，OpenAI 兼容 HTTP）。

职责边界：
  - 允许：认牌、识别 UI 阶段（特征提取）
  - 禁止：出牌决策（由 tongits_rule_bot.TongitsDecisionEngine 负责）

环境变量：
  TONGITS_VLM_PROVIDER=gemini|qwen（默认：有 GEMINI_API_KEY 且模型名含 gemini 时用 gemini）
  GEMINI_API_KEY / GOOGLE_API_KEY + GEMINI_OPENAI_BASE_URL（Gemini OpenAI 兼容端点）
  DASHSCOPE_API_KEY / OPENAI_API_KEY + OPENAI_BASE_URL（百炼 Qwen-VL）
  TONGITS_VLM_MODEL（gemini 默认 gemini-2.0-flash；qwen 默认 qwen3.5-flash）
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("vision_proxy_qwen")

VALID_SUITS = frozenset({"S", "H", "C", "D"})
VALID_RANKS = frozenset(
    {"A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"}
)

# 非标准点数 → 规范点数（全项目统一：A 不用 1，J/Q/K 不用 11/12/13）
RANK_CANONICAL_MAP: dict[str, str] = {
    "1": "A",
    "T": "10",
    "11": "J",
    "12": "Q",
    "13": "K",
}

CANONICAL_LABEL_FORMAT = (
    "【标签格式】统一为「花色+点数」：S/H/C/D + A/2-10/J/Q/K，"
    "例如 HA、S10、CJ、DK；"
    "A 必须写 HA/SA/CA/DA（禁止 H1/S1）；"
    "J/Q/K 必须写 HJ/HQ/HK 等（禁止 11/12/13）。"
)

SINGLE_CARD_PROMPT = (
    "这是一张扑克牌局部的截图。"
    "请仅输出它的花色字母与点数（格式如 H9、S7、CQ、HA），"
    "不要输出 Markdown、不要解释、不要空格与标点。"
    + CANONICAL_LABEL_FORMAT
)

ZONE_MELDS_PROMPT = (
    "这是一张 Tongits 扑克游戏中「{zone_desc}」区域的截图。"
    "请识别画面中所有**已经亮出、正面朝上**的扑克牌（含顺子/刻子等叠放明牌）。"
    "花色：S=黑桃、H=红桃、C=梅花、D=方块；点数：A,2,3,4,5,6,7,8,9,10,J,Q,K。"
    "若没有任何明牌，只输出空数组 []。"
    "你只能输出一个纯 JSON 数组，例如：\n"
    '[{{"suit":"H","rank":"9"}},{{"suit":"C","rank":"10"}}]\n'
    "严禁输出 Markdown、解释文字或其它键名。"
)

FULLSCREEN_BOARD_PROMPT = (
    "这是一张 Tongits 扑克游戏的**全屏截图**（含浏览器 UI；坐标原点在图像左上角，单位像素）。\n"
    "请识别下列五个战区内所有**正面朝上、清晰可见**的扑克牌，并给出每张牌**中心点**坐标 (x,y)：\n"
    "1. player_hand — 屏幕底部**本玩家**横向手牌（全部手牌）\n"
    "2. my_melds — 下方 Drop/Fight/Group/Dump 按钮上方、**我方已亮出**的明牌\n"
    "3. opponent_left — **左侧对手**已亮的明牌\n"
    "4. opponent_right — **右侧对手**已亮的明牌\n"
    "5. center_discard — **中央弃牌堆最顶上一张**（仅 1 张；若无则 []）\n"
    "花色 S/H/C/D；点数 A,2,3,4,5,6,7,8,9,10,J,Q,K。标签如 S7、H9、HA。\n"
    + CANONICAL_LABEL_FORMAT
    + "\n"
    "只输出一个 JSON 对象，键名必须为上述英文 zone 名，例如：\n"
    '{"player_hand":[{"label":"S7","x":962,"y":871}],"my_melds":[],"'
    '"opponent_left":[],"opponent_right":[],"center_discard":[{"label":"S2","x":978,"y":344}]}\n'
    "严禁 Markdown、解释文字或其它键名。"
)

FULLSCREEN_BOARD_ZONE_KEYS: tuple[str, ...] = (
    "player_hand",
    "my_melds",
    "opponent_left",
    "opponent_right",
    "center_discard",
)

FULLSCREEN_BOARD_COMPACT_PROMPT = (
    "这是一张 Tongits 扑克游戏的全屏截图。\n"
    "请识别下列五个战区内所有**正面朝上、清晰可见**的扑克牌（仅牌面标签，不要坐标）：\n"
    "1. player_hand — 屏幕底部**本玩家**手牌，按从左到右顺序列出\n"
    "2. my_melds — Drop/Fight/Group/Dump 按钮上方**我方已亮**明牌\n"
    "3. opponent_left — **左侧对手**已亮明牌\n"
    "4. opponent_right — **右侧对手**已亮明牌\n"
    "5. center_discard — **中央弃牌堆最顶上一张**（0 或 1 张）\n"
    "【空区规则】my_melds / opponent_left / opponent_right / center_discard "
    "若无任何正面朝上的牌（仅有空白、牌背、UI 数字），必须输出 []。\n"
    "【误识禁令】禁止把牌堆剩余张数数字（如 15）当成牌面；不存在 S15 等非法标签。\n"
    "花色 S/H/C/D；点数 A,2-10,J,Q,K。每张牌一个字符串如 \"S7\"、\"HA\"。\n"
    + CANONICAL_LABEL_FORMAT
    + "\n"
    "只输出 JSON 对象，值为字符串数组，例如：\n"
    '{"player_hand":["D7","C7","H7"],"my_melds":["S3"],"opponent_left":["S8","S9"],'
    '"opponent_right":[],"center_discard":["D5"]}\n'
    "严禁 Markdown 与解释文字。"
)

LOWER_BOARD_COMPACT_PROMPT = (
    "这是一张 Tongits 游戏截图的**下半区**（含手牌、我方明牌、中央弃牌）。\n"
    "请识别下列三个区域内所有**正面朝上**的扑克牌（仅标签，不要坐标）：\n"
    "1. player_hand — **本玩家手牌**，按从左到右顺序\n"
    "2. my_melds — **我方已亮**明牌\n"
    "3. center_discard — **弃牌堆最顶上一张**（0 或 1 张）\n"
    "花色 S/H/C/D；点数 A,2-10,J,Q,K。标签如 \"S7\"、\"HA\"。\n"
    + CANONICAL_LABEL_FORMAT
    + "\n"
    "只输出 JSON 对象：\n"
    '{"player_hand":["D7","C7"],"my_melds":["S3"],"center_discard":["D5"]}\n'
    "严禁 Markdown 与解释。"
)

LOWER_BOARD_ZONE_KEYS: tuple[str, ...] = (
    "player_hand",
    "my_melds",
    "center_discard",
)

ZONE_LABELS_COMPACT_PROMPT = (
    "这是一张 Tongits 扑克游戏中「{zone_desc}」区域的截图。\n"
    "请列出该区域内所有**正面朝上、清晰可见**的扑克牌标签（S/H/C/D + 点数，如 S7、HA）。\n"
    "按从左到右顺序。\n"
    "{empty_rules}"
    + CANONICAL_LABEL_FORMAT
    + "\n"
    "你只能输出一个 JSON 字符串数组，例如：[\"S8\",\"S9\",\"HA\"]\n"
    "严禁 Markdown 与解释文字。"
)

ZONE_LABELS_EMPTY_RULES = (
    "【空区规则】若本区域内**没有任何正面朝上的扑克牌**（仅有空白、灰色槽、UI 按钮/文字、"
    "或**牌背**），**必须**只输出 []。\n"
    "【误识禁令】**禁止**把牌堆上的**剩余张数数字**（如 15、14）或 TONGITS 牌背图案当成牌面；"
    "点数只能是 A,2,3,4,5,6,7,8,9,10,J,Q,K，不存在 S15 等非法标签。\n"
)

ZONE_LABELS_HAND_RULES = (
    "【手牌规则】只识别本玩家**正面持牌**；按视觉从左到右、从上到下顺序列出。\n"
)

CARDS_PROMPT = (
    "这是一张扑克牌游戏截图。画面下方是玩家的手牌，每张牌上都有一个红色的数字 ID。\n"
    "请识别出玩家手里**每一张牌**的 ID、花色（S黑桃/H红桃/C梅花/D方块）和点数（A,2-10,J,Q,K）。\n"
    "你只能输出一个纯 JSON 数组，例如：\n"
    '[{"id": 18, "suit": "H", "rank": "9"}, {"id": 19, "suit": "H", "rank": "10"}]\n'
    "严禁输出任何其他解释性文字。"
)

BUTTONS_PROMPT = (
    "这是 Tongits 游戏截图，红色数字为 OmniParser element_id。\n"
    "请识别以下**按钮/可点击区域**各自对应的 id（整数）：\n"
    "deck（中央牌堆摸牌）、drop、fight、group、dump、special（吃牌/特殊）。\n"
    "只输出 JSON 对象，例如：\n"
    '{"deck": 20, "drop": 27, "fight": 28, "group": 29, "dump": 30, "special": 24}\n'
    "找不到的键可省略。严禁 markdown 与解释文字。"
)

PHASE_PROMPT = (
    "这是一张 Tongits 扑克游戏截图（带红色 OmniParser 元素 ID）。\n"
    "请仅判断**当前回合 UI 状态**，不要建议点击什么按钮。\n"
    "turn_phase 取值：fight_offer | draw | meld | dump | idle\n"
    "你只能输出一个 JSON 对象，例如：\n"
    '{"turn_phase": "draw", "can_fight": false, "can_chow": false, '
    '"can_group": true, "can_drop": false, "scatter_points": 18}\n'
    "严禁输出 markdown 或解释文字。"
)


def strip_markdown_json(text: str, *, prefer_object: bool = False) -> str:
    """清洗 VLM 输出中的 ```json ... ``` 或夹杂说明文字。"""
    raw = (text or "").strip()
    if prefer_object and raw.startswith("{") and "}" in raw:
        return raw[: raw.rfind("}") + 1]
    if not prefer_object and raw.startswith("[") and "]" in raw:
        return raw[: raw.rfind("]") + 1]
    if "```" in raw:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.I)
        if m:
            return strip_markdown_json(m.group(1).strip(), prefer_object=prefer_object)
    patterns = (r"\{[\s\S]*\}", r"\[[\s\S]*\]") if prefer_object else (
        r"\[[\s\S]*\]",
        r"\{[\s\S]*\}",
    )
    for pat in patterns:
        m = re.search(pat, raw)
        if m:
            return m.group(0).strip()
    return raw


def _extract_json_object(text: str) -> dict[str, Any]:
    """从 VLM 回复中提取 JSON 对象（优先 {…}，避免误匹配内层数组）。"""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("VLM 返回为空")
    if raw.startswith("{") and "}" in raw:
        try:
            obj = json.loads(raw[: raw.rfind("}") + 1])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    if "```" in raw:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.I)
        if m:
            return _extract_json_object(m.group(1).strip())
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        obj = json.loads(m.group(0))
        if isinstance(obj, dict):
            return obj
    raise ValueError(f"无法解析 JSON 对象: {raw[:160]!r}")


def _extract_json_array(text: str) -> list[Any]:
    """从 VLM 回复中提取 JSON 数组（优先 […]）。"""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("VLM 返回为空")
    if raw.startswith("[") and "]" in raw:
        try:
            arr = json.loads(raw[: raw.rfind("]") + 1])
            if isinstance(arr, list):
                return arr
        except json.JSONDecodeError:
            pass
    if "```" in raw:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.I)
        if m:
            return _extract_json_array(m.group(1).strip())
    m = re.search(r"\[[\s\S]*\]", raw)
    if m:
        arr = json.loads(m.group(0))
        if isinstance(arr, list):
            return arr
    raise ValueError(f"无法解析 JSON 数组: {raw[:160]!r}")


def hydrate_dashscope_env() -> None:
    if (os.environ.get("OPENAI_API_KEY") or "").strip():
        return
    cn = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    sea = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    region = (os.environ.get("JACHIN_ACTIVE_REGION") or "CN").strip().upper()
    if region == "SEA":
        key = (
            os.environ.get("DASHSCOPE_API_KEY_SEA")
            or os.environ.get("DASHSCOPE_API_KEY")
            or ""
        ).strip()
        base = (
            os.environ.get("DASHSCOPE_API_BASE_SEA")
            or os.environ.get("DASHSCOPE_API_BASE")
            or sea
        ).strip()
    else:
        key = (
            os.environ.get("DASHSCOPE_API_KEY_CN")
            or os.environ.get("DASHSCOPE_API_KEY")
            or ""
        ).strip()
        base = (
            os.environ.get("DASHSCOPE_API_BASE_CN")
            or os.environ.get("DASHSCOPE_API_BASE")
            or cn
        ).strip()
    if not key:
        key = (os.environ.get("QWEN_API_KEY") or "").strip()
    if key:
        os.environ["OPENAI_API_KEY"] = key
        if base and not (os.environ.get("OPENAI_BASE_URL") or "").strip():
            os.environ["OPENAI_BASE_URL"] = base.rstrip("/")


_GEMINI_OPENAI_BASE_DEFAULT = (
    "https://generativelanguage.googleapis.com/v1beta/openai/"
)


def _gemini_api_key() -> str:
    return (
        os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
    ).strip()


def vlm_provider() -> str:
    """当前 Tongits/脚本 VLM 后端：gemini 或 qwen。"""
    explicit = (os.environ.get("TONGITS_VLM_PROVIDER") or "").strip().lower()
    if explicit in ("gemini", "google"):
        return "gemini"
    if explicit in ("qwen", "dashscope", "aliyun", "bailian"):
        return "qwen"
    model_hint = (os.environ.get("TONGITS_VLM_MODEL") or "").strip().lower()
    if _gemini_api_key() and ("gemini" in model_hint or not model_hint):
        return "gemini"
    return "qwen"


def default_vlm_model() -> str:
    if vlm_provider() == "gemini":
        raw = (os.environ.get("TONGITS_VLM_MODEL") or "gemini-2.0-flash").strip()
    else:
        raw = (os.environ.get("TONGITS_VLM_MODEL") or "qwen3.5-flash").strip()
    if raw.lower().startswith("dashscope/"):
        return raw.split("/", 1)[1].strip()
    if raw.lower().startswith("gemini/"):
        return raw.split("/", 1)[1].strip()
    return raw


def _openai_client(*, max_retries: int | None = None):
    from openai import OpenAI

    kwargs: dict[str, Any] = {}
    if max_retries is not None:
        kwargs["max_retries"] = max_retries

    if vlm_provider() == "gemini":
        api_key = _gemini_api_key()
        if not api_key:
            raise RuntimeError("未配置 GEMINI_API_KEY / GOOGLE_API_KEY，无法调用 Gemini VLM")
        base = (
            os.environ.get("GEMINI_OPENAI_BASE_URL")
            or os.environ.get("GOOGLE_OPENAI_BASE_URL")
            or _GEMINI_OPENAI_BASE_DEFAULT
        ).strip().rstrip("/")
        return OpenAI(api_key=api_key, base_url=base, **kwargs)

    hydrate_dashscope_env()
    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError(
            "未配置 DASHSCOPE_API_KEY / OPENAI_API_KEY，无法调用百炼 VLM"
        )
    base = (os.environ.get("OPENAI_BASE_URL") or "").strip() or None
    return OpenAI(api_key=api_key, base_url=base, **kwargs)


def _image_message_part(image_path: str) -> dict[str, Any]:
    p = Path(image_path)
    if not p.is_file():
        raise FileNotFoundError(f"截图不存在: {image_path}")
    mime = "image/jpeg" if p.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def _vlm_timeout_sec() -> float:
    try:
        return float((os.environ.get("TONGITS_VLM_TIMEOUT") or "25").strip())
    except ValueError:
        return 25.0


def vlm_zone_timeout_sec(zone_key: str = "") -> float:
    """qwen_full 分区超时：手牌默认 25s；明牌/对手/弃牌默认 10s。"""
    if zone_key == "player_hand":
        try:
            raw = (
                os.environ.get("TONGITS_VLM_HAND_TIMEOUT")
                or os.environ.get("TONGITS_VLM_TIMEOUT")
                or "12"
            )
            return float(raw.strip())
        except ValueError:
            return 12.0
    if zone_key:
        try:
            return float(
                (os.environ.get("TONGITS_VLM_LABEL_ZONE_TIMEOUT") or "10").strip()
            )
        except ValueError:
            return 10.0
    return _vlm_timeout_sec()


def _vlm_chat(
    prompt: str,
    image_path: str,
    *,
    model: str,
    max_tokens: int = 1024,
    timeout_sec: float | None = None,
    max_retries: int | None = None,
) -> str:
    client = _openai_client(max_retries=max_retries)
    timeout = timeout_sec if timeout_sec is not None else _vlm_timeout_sec()
    provider = vlm_provider()
    logger.info(
        "[vlm] 请求 provider=%s model=%s image=%s timeout=%.0fs retries=%s",
        provider,
        model,
        image_path,
        timeout,
        "default" if max_retries is None else max_retries,
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    _image_message_part(image_path),
                ],
            }
        ],
        temperature=0.05,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    content = (resp.choices[0].message.content or "").strip()
    logger.info("[vlm] 原始回复(len=%d): %s", len(content), content[:1200])
    return content


_RANK_PATTERN = r"10|11|12|13|A|J|Q|K|T|[2-9]|1"
_LABEL_PARSE_RE = re.compile(
    rf"^([SHCD])({_RANK_PATTERN})$",
    re.IGNORECASE,
)
_LABEL_PARSE_ALT_RE = re.compile(
    rf"^({_RANK_PATTERN})([SHCD])$",
    re.IGNORECASE,
)
# 兼容旧引用
_LABEL_RE = _LABEL_PARSE_RE


def _canonical_rank(rank: str) -> str | None:
    r = (rank or "").strip().upper()
    r = RANK_CANONICAL_MAP.get(r, r)
    return r if r in VALID_RANKS else None


def _parse_label_parts(raw: str) -> tuple[str, str] | None:
    """从已清洗字符串解析 (suit, canonical_rank)。"""
    m = _LABEL_PARSE_RE.match(raw)
    if m:
        suit = m.group(1).upper()
        rank = _canonical_rank(m.group(2))
        return (suit, rank) if rank else None
    m_alt = _LABEL_PARSE_ALT_RE.match(raw)
    if m_alt:
        rank = _canonical_rank(m_alt.group(1))
        suit = m_alt.group(2).upper()
        return (suit, rank) if rank else None
    return None


def canonical_card_label(text: str) -> str | None:
    """规范牌面标签，如 H1→HA、ST→S10；失败返回 None。"""
    parsed = parse_card_label(text)
    return parsed[0] if parsed else None


def parse_card_label(text: str) -> tuple[str, str, str] | None:
    """解析 VLM/YOLO 输出为 (label, suit, rank)，如 H9、HA。"""
    raw = (text or "").strip().upper()
    raw = re.sub(r"[^SHCD0-9AJQKT]", "", raw)
    parts = _parse_label_parts(raw)
    if not parts:
        return None
    suit, rank = parts
    if suit not in VALID_SUITS:
        return None
    label = f"{suit}{rank}"
    return label, suit, rank


def zone_labels_compact_prompt(*, zone_desc: str, zone_key: str = "") -> str:
    """按战区生成紧凑认牌 prompt（空区/手牌附加规则）。"""
    if zone_key == "player_hand":
        empty_rules = ZONE_LABELS_HAND_RULES
    else:
        empty_rules = ZONE_LABELS_EMPTY_RULES
    return ZONE_LABELS_COMPACT_PROMPT.format(zone_desc=zone_desc, empty_rules=empty_rules)


def filter_valid_card_labels(labels: list[str]) -> list[str]:
    """丢弃非法 VLM 标签（如 S15、牌堆数字误识）。"""
    out: list[str] = []
    for raw in labels:
        parsed = parse_card_label(raw)
        if parsed:
            out.append(parsed[0])
        elif (raw or "").strip():
            logger.warning("[vlm] 丢弃非法牌标签: %r", raw)
    return out


def parse_zone_melds_json(text: str) -> list[dict[str, str]]:
    """
    解析战区 VLM 输出的明牌 JSON 数组。

    支持 [{"suit":"H","rank":"9"}, ...] 或 ["H9","C10", ...]。
    """
    raw = strip_markdown_json(text)
    if not raw or raw in ("[]", "null"):
        return []
    data = _extract_json_array(text)
    if not isinstance(data, list):
        raise ValueError(f"须为 JSON 数组，收到: {type(data).__name__}")

    out: list[dict[str, str]] = []
    for i, row in enumerate(data):
        if isinstance(row, str):
            parsed = parse_card_label(row)
            if parsed:
                label, suit, rank = parsed
                out.append({"suit": suit, "rank": rank, "label": label})
            continue
        if not isinstance(row, dict):
            raise ValueError(f"cards[{i}] 须为对象或字符串")
        suit = str(row.get("suit", "")).strip().upper()
        rank = str(row.get("rank", "")).strip().upper()
        if suit not in VALID_SUITS:
            raise ValueError(f"cards[{i}] suit 非法: {suit}")
        if rank not in VALID_RANKS:
            raise ValueError(f"cards[{i}] rank 非法: {rank}")
        out.append({"suit": suit, "rank": rank, "label": f"{suit}{rank}"})
    return out


def analyze_zone_melds_with_qwen(
    image_path: str,
    *,
    zone_desc: str = "对手明牌区",
    model: str | None = None,
    max_retries: int = 2,
) -> list[dict[str, str]]:
    """
    战区裁图 → 一次 VLM 列出该区域内全部明牌（无 element id）。

    Returns:
        [{"suit":"H","rank":"9","label":"H9"}, ...]；失败返回 []。
    """
    del max_retries  # 同图重复请求无意义，仅调用一次 VLM
    model = model or default_vlm_model()
    prompt = ZONE_MELDS_PROMPT.format(zone_desc=zone_desc)
    try:
        text = _vlm_chat(
            prompt,
            image_path,
            model=model,
            max_tokens=512,
        )
        cards = parse_zone_melds_json(text)
        logger.info(
            "[vlm] 战区认牌成功 zone=%s count=%d cards=%s",
            zone_desc,
            len(cards),
            [c.get("label") for c in cards],
        )
        return cards
    except (ValueError, json.JSONDecodeError, KeyError, TypeError) as e:
        logger.error("[vlm] 战区认牌解析失败 zone=%s: %s", zone_desc, e)
    except Exception as e:
        logger.error("[vlm] 战区认牌失败 zone=%s: %s", zone_desc, e)
    return []


def _parse_vlm_card_label(row: dict[str, Any]) -> str:
    label = str(row.get("label", "")).strip().upper()
    if label:
        parsed = parse_card_label(label)
        if parsed:
            return parsed[0]
    suit = str(row.get("suit", "")).strip().upper()
    rank = RANK_CANONICAL_MAP.get(
        str(row.get("rank", "")).strip().upper(),
        str(row.get("rank", "")).strip().upper(),
    )
    if suit in VALID_SUITS and rank in VALID_RANKS:
        return f"{suit}{rank}"
    raise ValueError(f"无法解析牌标签: {row!r}")


def _parse_vlm_xy(row: dict[str, Any]) -> tuple[int, int]:
    if "x" in row and "y" in row:
        return int(round(float(row["x"]))), int(round(float(row["y"])))
    if "center_x" in row and "center_y" in row:
        return int(round(float(row["center_x"]))), int(round(float(row["center_y"])))
    pt = row.get("center") or row.get("xy") or row.get("pos")
    if isinstance(pt, (list, tuple)) and len(pt) >= 2:
        return int(round(float(pt[0]))), int(round(float(pt[1])))
    raise ValueError(f"缺少坐标 x,y: {row!r}")


def _parse_vlm_zone_cards(raw_cards: Any, zone_key: str) -> list[dict[str, Any]]:
    if raw_cards is None:
        return []
    if not isinstance(raw_cards, list):
        raise ValueError(f"{zone_key} 须为数组，收到: {type(raw_cards).__name__}")
    out: list[dict[str, Any]] = []
    for i, row in enumerate(raw_cards):
        if isinstance(row, str):
            parsed = parse_card_label(row)
            if not parsed:
                raise ValueError(f"{zone_key}[{i}] 无法解析: {row!r}")
            label, suit, rank = parsed
            out.append({"label": label, "suit": suit, "rank": rank, "x": 0, "y": 0})
            continue
        if not isinstance(row, dict):
            raise ValueError(f"{zone_key}[{i}] 须为对象或字符串")
        label = _parse_vlm_card_label(row)
        try:
            x, y = _parse_vlm_xy(row)
        except ValueError:
            x, y = 0, 0
        parsed = parse_card_label(label)
        if parsed:
            label, suit, rank = parsed
        else:
            suit, rank = label[:1], label[1:]
        out.append({"label": label, "suit": suit, "rank": rank, "x": x, "y": y})
    return out


def parse_fullscreen_board_json(text: str) -> dict[str, list[dict[str, Any]]]:
    """解析全屏五战区 VLM JSON。"""
    data = _extract_json_object(text)
    if not data:
        raise ValueError("VLM 返回为空")

    board: dict[str, list[dict[str, Any]]] = {}
    for zone_key in FULLSCREEN_BOARD_ZONE_KEYS:
        board[zone_key] = _parse_vlm_zone_cards(data.get(zone_key), zone_key)
    return board


def _normalize_card_label(raw: str) -> str | None:
    parsed = parse_card_label(raw)
    return parsed[0] if parsed else None


def _parse_zone_label_list(raw: Any, zone_key: str) -> list[str]:
    """紧凑格式：战区 → ["S7","H9",...]"""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{zone_key} 须为字符串数组，收到: {type(raw).__name__}")
    labels: list[str] = []
    for i, item in enumerate(raw):
        if isinstance(item, str):
            label = _normalize_card_label(item)
            if not label and (item or "").strip():
                logger.warning("[vlm] 丢弃非法牌标签 zone=%s: %r", zone_key, item)
        elif isinstance(item, dict):
            label = _parse_vlm_card_label(item)
        else:
            raise ValueError(f"{zone_key}[{i}] 须为字符串或对象")
        if label:
            labels.append(label)
    return labels


def parse_fullscreen_board_compact_json(text: str) -> dict[str, list[str]]:
    """解析全屏紧凑 JSON（五战区仅牌面标签，无坐标）。"""
    data = _extract_json_object(text)
    return {
        zone_key: _parse_zone_label_list(data.get(zone_key), zone_key)
        for zone_key in FULLSCREEN_BOARD_ZONE_KEYS
    }


def parse_lower_board_compact_json(text: str) -> dict[str, list[str]]:
    """解析下半区紧凑 JSON（手牌 / 我方明牌 / 弃牌顶）。"""
    data = _extract_json_object(text)
    return {
        zone_key: _parse_zone_label_list(data.get(zone_key), zone_key)
        for zone_key in LOWER_BOARD_ZONE_KEYS
    }


def parse_zone_labels_compact_json(text: str) -> list[str]:
    """解析战区紧凑字符串数组 JSON。"""
    data = _extract_json_array(text)
    labels: list[str] = []
    for i, item in enumerate(data):
        if isinstance(item, str):
            label = _normalize_card_label(item)
            if not label and (item or "").strip():
                logger.warning("[vlm] 丢弃非法牌标签: %r", item)
        elif isinstance(item, dict):
            label = _parse_vlm_card_label(item)
        else:
            raise ValueError(f"labels[{i}] 须为字符串或对象")
        if label:
            labels.append(label)
    return labels


def analyze_lower_board_compact_with_qwen(
    image_path: str,
    *,
    model: str | None = None,
) -> dict[str, list[str]]:
    """下半区裁图 → 一次 VLM（手牌+明牌+弃牌顶，仅标签）。"""
    model = model or default_vlm_model()
    try:
        text = _vlm_chat(
            LOWER_BOARD_COMPACT_PROMPT,
            image_path,
            model=model,
            max_tokens=384,
        )
        board = parse_lower_board_compact_json(text)
        logger.info(
            "[vlm] 下半区紧凑认牌 hand=%d melds=%d discard=%d",
            len(board.get("player_hand", [])),
            len(board.get("my_melds", [])),
            len(board.get("center_discard", [])),
        )
        return board
    except (ValueError, json.JSONDecodeError, KeyError, TypeError) as e:
        logger.error("[vlm] 下半区紧凑认牌解析失败: %s", e)
    except Exception as e:
        logger.error("[vlm] 下半区紧凑认牌失败: %s", e)
    return {z: [] for z in LOWER_BOARD_ZONE_KEYS}


def analyze_zone_labels_compact_with_qwen(
    image_path: str,
    *,
    zone_desc: str = "对手明牌区",
    zone_key: str = "",
    model: str | None = None,
    timeout_sec: float | None = None,
    no_retry: bool | None = None,
) -> list[str]:
    """战区裁图 → 一次 VLM，返回标签字符串数组。"""
    model = model or default_vlm_model()
    prompt = zone_labels_compact_prompt(zone_desc=zone_desc, zone_key=zone_key)
    timeout = (
        timeout_sec
        if timeout_sec is not None
        else vlm_zone_timeout_sec(zone_key)
    )
    # 标签区默认不重试；player_hand 保持历史行为（可重试），除非显式指定 no_retry。
    retry_off = (zone_key != "player_hand") if no_retry is None else bool(no_retry)
    try:
        text = _vlm_chat(
            prompt,
            image_path,
            model=model,
            max_tokens=256,
            timeout_sec=timeout,
            max_retries=0 if retry_off else None,
        )
        labels = filter_valid_card_labels(parse_zone_labels_compact_json(text))
        logger.info(
            "[vlm] 战区紧凑认牌成功 zone=%s count=%d labels=%s",
            zone_desc,
            len(labels),
            labels,
        )
        return labels
    except (ValueError, json.JSONDecodeError, KeyError, TypeError) as e:
        logger.error("[vlm] 战区紧凑认牌解析失败 zone=%s: %s", zone_desc, e)
    except Exception as e:
        logger.warning(
            "[vlm] 战区紧凑认牌失败 zone=%s timeout=%.0fs: %s",
            zone_desc,
            timeout,
            e,
        )
    return []


def analyze_duel_point_with_qwen(
    image_path: str,
    *,
    model: str | None = None,
    timeout_sec: float = 1.4,
) -> int | None:
    """
    读取决斗弹窗中间 POINT 数值（仅返回整数）。
    """
    model = model or default_vlm_model()
    prompt = (
        "这是 Tongits 决斗弹窗中间的 POINT 徽标小图。"
        "请只输出当前点数的十进制整数（例如 8、15、34）。"
        "不要输出任何解释、文字、JSON 或标点。"
    )
    try:
        text = _vlm_chat(
            prompt,
            image_path,
            model=model,
            max_tokens=16,
            timeout_sec=max(0.8, float(timeout_sec)),
            max_retries=0,
        )
        m = re.search(r"\d{1,3}", text or "")
        if not m:
            return None
        val = int(m.group(0))
        if val < 0 or val > 200:
            return None
        logger.info("[vlm] 决斗点数识别成功: %d", val)
        return val
    except Exception as e:
        logger.warning("[vlm] 决斗点数识别失败: %s", e)
        return None


def analyze_waiting_overlay_type_with_qwen(
    image_path: str,
    *,
    model: str | None = None,
    timeout_sec: float = 1.6,
) -> str:
    """
    识别等待态覆盖层类型：settlement | duel | none。
    """
    model = model or default_vlm_model()
    prompt = (
        "这是 Tongits 游戏等待态覆盖层截图（通常在下半屏）。"
        "请只输出一个小写词：settlement 或 duel 或 none。\n"
        "判定规则：\n"
        "- settlement：出现 CONTINUE + DETAILS 结算页按钮，通常有胜负结算面板。\n"
        "- duel：出现 CHALLENGE + FOLD 决斗按钮，中间有 POINT。\n"
        "- none：以上都不是。\n"
        "严禁输出解释、标点、JSON。"
    )
    try:
        text = _vlm_chat(
            prompt,
            image_path,
            model=model,
            max_tokens=16,
            timeout_sec=max(0.8, float(timeout_sec)),
            max_retries=0,
        )
        raw = (text or "").strip().lower()
        if "settlement" in raw:
            return "settlement"
        if "duel" in raw:
            return "duel"
        return "none"
    except Exception as e:
        logger.warning("[vlm] 等待态覆盖层识别失败: %s", e)
        return "none"


def analyze_coin_amount_with_qwen(
    image_path: str,
    *,
    model: str | None = None,
    timeout_sec: float = 1.6,
) -> str | None:
    """
    读取金币文本（如 57.39K / 41.40M / 66960），仅返回该值字符串。
    """
    model = model or default_vlm_model()
    prompt = (
        "这是 Tongits 界面中“金币数值”小区域截图。"
        "请只输出金币数字本身，格式仅允许：整数或小数，后缀可选 K/M/B（例如 57.39K、41.40M、66960）。\n"
        "若看不到明确金币数值，只输出 NONE。\n"
        "严禁输出解释、标点、JSON。"
    )
    try:
        text = _vlm_chat(
            prompt,
            image_path,
            model=model,
            max_tokens=16,
            timeout_sec=max(0.8, float(timeout_sec)),
            max_retries=0,
        )
        raw = (text or "").strip().upper()
        if not raw or "NONE" in raw:
            return None
        m = re.search(r"(\d+(?:\.\d+)?\s*[KMB]?)", raw)
        if not m:
            return None
        coin = m.group(1).replace(" ", "")
        logger.info("[vlm] 金币识别成功: %s", coin)
        return coin
    except Exception as e:
        logger.warning("[vlm] 金币识别失败: %s", e)
        return None


def analyze_settlement_deltas_with_qwen(
    image_path: str,
    *,
    model: str | None = None,
    timeout_sec: float = 2.8,
) -> dict[str, Any] | None:
    """
    读取结算面板中的本局输赢（优先识别我方 +3000/-1500）与对手输赢。

    Returns:
        {
          "my_delta": 3000,
          "opponents": [{"name": "Jholee", "delta": -1500}, ...]
        }
    """
    model = model or default_vlm_model()
    prompt = (
        "这是 Tongits 的结算面板截图。"
        "请提取本局金币变动，输出 JSON 对象，格式：\n"
        '{"my_delta": 3000, "opponents": [{"name":"Jholee","delta":-1500},{"name":"Regndo","delta":-1500}]}\n'
        "规则：\n"
        "- delta 必须是整数（可正可负），不带 + 号与逗号。\n"
        "- 未识别到我方变动时 my_delta 设为 null。\n"
        '- 未识别到对手时 opponents 设为 []。\n'
        "严禁输出解释文字或 Markdown。"
    )
    try:
        text = _vlm_chat(
            prompt,
            image_path,
            model=model,
            max_tokens=180,
            timeout_sec=max(0.8, float(timeout_sec)),
            max_retries=0,
        )
        obj = _extract_json_object(text)
        my_delta_raw = obj.get("my_delta")
        my_delta: int | None = None
        if isinstance(my_delta_raw, (int, float)):
            my_delta = int(round(float(my_delta_raw)))
        elif isinstance(my_delta_raw, str):
            m = re.search(r"[+-]?\d{1,8}", my_delta_raw.replace(",", ""))
            if m:
                my_delta = int(m.group(0))

        opps_out: list[dict[str, Any]] = []
        opps_raw = obj.get("opponents")
        if isinstance(opps_raw, list):
            for item in opps_raw:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                d_raw = item.get("delta")
                delta: int | None = None
                if isinstance(d_raw, (int, float)):
                    delta = int(round(float(d_raw)))
                elif isinstance(d_raw, str):
                    m = re.search(r"[+-]?\d{1,8}", d_raw.replace(",", ""))
                    if m:
                        delta = int(m.group(0))
                if delta is None:
                    continue
                opps_out.append({"name": name, "delta": delta})

        if my_delta is None and not opps_out:
            return None
        logger.info(
            "[vlm] 结算面板输赢识别成功: my_delta=%s opponents=%d",
            my_delta if my_delta is not None else "-",
            len(opps_out),
        )
        return {"my_delta": my_delta, "opponents": opps_out}
    except Exception as e:
        logger.warning("[vlm] 结算面板输赢识别失败: %s", e)
        return None


def analyze_signed_delta_with_qwen(
    image_path: str,
    *,
    model: str | None = None,
    timeout_sec: float = 3.0,
) -> int | None:
    """
    读取一个“带符号金币增减”小区域截图（例如 +1500 / -500）。
    专用于“我方座位旁的本局增减数字”，区域已裁剪得很紧，只期望一个数字。
    返回带符号整数；读不到返回 None。
    """
    model = model or default_vlm_model()
    prompt = (
        "这是 Tongits 结算页里“我的本局金币增减”小区域截图，"
        "通常是一个带正负号的数字（赢为正、输为负，例如 +1500、-500）。\n"
        "请只输出这个带符号整数本身（必须带 + 或 - 号，不带逗号、不带货币符号）。\n"
        "若区域内看不到明确的增减数字，只输出 NONE。\n"
        "严禁输出解释、JSON 或其它文字。"
    )
    try:
        text = _vlm_chat(
            prompt,
            image_path,
            model=model,
            max_tokens=16,
            timeout_sec=max(0.8, float(timeout_sec)),
            max_retries=0,
        )
        raw = (text or "").strip().upper()
        if not raw or "NONE" in raw:
            return None
        m = re.search(r"([+-])\s*(\d[\d,]{0,8})", raw)
        if not m:
            return None
        try:
            val = int(m.group(2).replace(",", ""))
        except ValueError:
            return None
        signed = val if m.group(1) == "+" else -val
        logger.info("[vlm] 我方增减识别成功: %+d", signed)
        return signed
    except Exception as e:
        logger.warning("[vlm] 我方增减识别失败: %s", e)
        return None


def analyze_fullscreen_board_compact_with_qwen(
    image_path: str,
    *,
    model: str | None = None,
    max_retries: int = 2,
) -> dict[str, list[str]]:
    """
    全屏截图 → 一次 VLM 紧凑认牌（五战区仅标签，无坐标）。

    Returns:
        {zone_key: ["S7", "H9", ...], ...}
    """
    del max_retries  # 同图不重复打 VLM
    model = model or default_vlm_model()
    try:
        text = _vlm_chat(
            FULLSCREEN_BOARD_COMPACT_PROMPT,
            image_path,
            model=model,
            max_tokens=512,
        )
        board = parse_fullscreen_board_compact_json(text)
        total = sum(len(v) for v in board.values())
        logger.info(
            "[vlm] 全屏紧凑认牌成功 total=%d hand=%d left=%d right=%d melds=%d discard=%d",
            total,
            len(board.get("player_hand", [])),
            len(board.get("opponent_left", [])),
            len(board.get("opponent_right", [])),
            len(board.get("my_melds", [])),
            len(board.get("center_discard", [])),
        )
        return board
    except (ValueError, json.JSONDecodeError, KeyError, TypeError) as e:
        logger.error("[vlm] 全屏紧凑认牌解析失败: %s", e)
    except Exception as e:
        logger.error("[vlm] 全屏紧凑认牌失败: %s", e)
    return {z: [] for z in FULLSCREEN_BOARD_ZONE_KEYS}


def analyze_fullscreen_board_with_qwen(
    image_path: str,
    *,
    model: str | None = None,
    max_retries: int = 2,
) -> dict[str, list[dict[str, Any]]]:
    """
    全屏截图 → 一次 VLM 识别五战区全部明牌与手牌（含坐标）。

    Returns:
        {zone_key: [{"label":"S7","suit":"S","rank":"7","x":962,"y":871}, ...], ...}
    """
    model = model or default_vlm_model()
    last_err = ""
    for attempt in range(1, max_retries + 1):
        try:
            text = _vlm_chat(
                FULLSCREEN_BOARD_PROMPT,
                image_path,
                model=model,
                max_tokens=2048,
            )
            board = parse_fullscreen_board_json(text)
            total = sum(len(v) for v in board.values())
            logger.info(
                "[vlm] 全屏认牌成功 total=%d hand=%d left=%d right=%d melds=%d discard=%d",
                total,
                len(board.get("player_hand", [])),
                len(board.get("opponent_left", [])),
                len(board.get("opponent_right", [])),
                len(board.get("my_melds", [])),
                len(board.get("center_discard", [])),
            )
            return board
        except (ValueError, json.JSONDecodeError, KeyError, TypeError) as e:
            last_err = str(e)
            logger.warning(
                "[vlm] 全屏认牌解析失败 %d/%d: %s",
                attempt,
                max_retries,
                e,
            )
        except Exception as e:
            last_err = repr(e)
            logger.warning(
                "[vlm] 全屏认牌失败 %d/%d: %s",
                attempt,
                max_retries,
                e,
            )
    logger.error("[vlm] analyze_fullscreen_board 放弃: %s", last_err)
    return {z: [] for z in FULLSCREEN_BOARD_ZONE_KEYS}


def analyze_single_card_with_qwen(
    crop_image_path: str,
    *,
    model: str | None = None,
    max_retries: int = 2,
) -> tuple[str, str, str] | None:
    """
    单张手牌裁剪图 → VLM 认知（仅感知）。

    Returns:
        (label, suit, rank) 如 ("H9","H","9")；失败返回 None
    """
    model = model or default_vlm_model()
    last_err = ""
    for attempt in range(1, max_retries + 1):
        try:
            text = _vlm_chat(
                SINGLE_CARD_PROMPT,
                crop_image_path,
                model=model,
                max_tokens=32,
            )
            parsed = parse_card_label(text)
            if parsed:
                return parsed
            last_err = f"无法解析标签: {text!r}"
        except Exception as e:
            last_err = repr(e)
            logger.warning(
                "[vlm] 单牌认知失败 %d/%d: %s",
                attempt,
                max_retries,
                e,
            )
    logger.error("[vlm] analyze_single_card 放弃: %s", last_err)
    return None


def parse_cards_json(text: str) -> list[dict[str, Any]]:
    raw = strip_markdown_json(text)
    if not raw:
        raise ValueError("VLM 返回为空")
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError(f"须为 JSON 数组，收到: {type(data).__name__}")
    out: list[dict[str, Any]] = []
    for i, row in enumerate(data):
        if not isinstance(row, dict):
            raise ValueError(f"cards[{i}] 须为对象")
        eid = int(row["id"])
        suit = str(row["suit"]).strip().upper()
        rank = str(row["rank"]).strip().upper()
        if suit not in VALID_SUITS:
            raise ValueError(f"cards[{i}] suit 非法: {suit}")
        if rank not in VALID_RANKS:
            raise ValueError(f"cards[{i}] rank 非法: {rank}")
        out.append({"id": eid, "suit": suit, "rank": rank})
    return out


def parse_phase_json(text: str) -> dict[str, Any]:
    raw = strip_markdown_json(text)
    if not raw:
        raise ValueError("VLM phase 返回为空")
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError(f"phase 须为 JSON 对象，收到: {type(obj).__name__}")
    return obj


def analyze_cards_with_qwen(
    image_path: str,
    elements_dict: dict[int, dict[str, int]] | None = None,
    *,
    model: str | None = None,
    max_retries: int = 3,
) -> list[dict[str, Any]]:
    """
    调用百炼 VLM 识别玩家手牌（仅特征，无决策）。

    Args:
        image_path: OmniParser 标注图路径（红框 ID）
        elements_dict: 可选，仅用于日志对照，不参与模型路由

    Returns:
        [{"id": 18, "suit": "H", "rank": "9"}, ...]
    """
    model = model or default_vlm_model()
    hint = ""
    if elements_dict:
        hint = f"\n（参考：当前 OmniParser 共 {len(elements_dict)} 个 UI 元素 ID）"
    prompt = CARDS_PROMPT + hint
    last_err = ""
    for attempt in range(1, max_retries + 1):
        try:
            text = _vlm_chat(prompt, image_path, model=model)
            cards = parse_cards_json(text)
            logger.info("[vlm] 认牌成功 %d 张: %s", len(cards), cards)
            return cards
        except (ValueError, json.JSONDecodeError, KeyError, TypeError) as e:
            last_err = str(e)
            logger.warning(
                "[vlm] 认牌解析失败 尝试 %d/%d: %s",
                attempt,
                max_retries,
                e,
            )
    raise RuntimeError(f"analyze_cards_with_qwen 失败: {last_err}")


def analyze_game_phase_with_qwen(
    image_path: str,
    *,
    model: str | None = None,
    max_retries: int = 2,
) -> dict[str, Any]:
    """识别回合阶段 UI 状态（感知层，非决策）。"""
    model = model or default_vlm_model()
    last_err = ""
    for attempt in range(1, max_retries + 1):
        try:
            text = _vlm_chat(PHASE_PROMPT, image_path, model=model, max_tokens=256)
            phase = parse_phase_json(text)
            logger.info("[vlm] UI 阶段: %s", phase)
            return phase
        except (ValueError, json.JSONDecodeError) as e:
            last_err = str(e)
            logger.warning("[vlm] phase 解析失败 %d/%d: %s", attempt, max_retries, e)
    raise RuntimeError(f"analyze_game_phase_with_qwen 失败: {last_err}")


# ---------------------------------------------------------------------------
# 手牌特征 → cv_state_dict（供规则引擎，纯本地逻辑）
# ---------------------------------------------------------------------------

_RANK_VALUE: dict[str, int] = {
    "A": 1,
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
    "6": 6,
    "7": 7,
    "8": 8,
    "9": 9,
    "10": 10,
    "J": 11,
    "Q": 12,
    "K": 13,
}

_SCATTER_POINTS: dict[str, int] = {
    "A": 1,
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
    "6": 6,
    "7": 7,
    "8": 8,
    "9": 9,
    "10": 10,
    "J": 10,
    "Q": 10,
    "K": 10,
}


def _rank_numeric(rank: str) -> int:
    return _RANK_VALUE[rank.upper()]


def _has_three_of_kind(cards: list[dict[str, Any]]) -> bool:
    counts: dict[int, int] = {}
    for c in cards:
        r = _rank_numeric(str(c["rank"]))
        counts[r] = counts.get(r, 0) + 1
        if counts[r] >= 3:
            return True
    return False


def _has_straight_flush(cards: list[dict[str, Any]], *, min_len: int = 3) -> bool:
    by_suit: dict[str, list[int]] = {}
    for c in cards:
        suit = str(c["suit"]).upper()
        by_suit.setdefault(suit, []).append(_rank_numeric(str(c["rank"])))
    for ranks in by_suit.values():
        uniq = sorted(set(ranks))
        run = 1
        for i in range(1, len(uniq)):
            if uniq[i] == uniq[i - 1] + 1:
                run += 1
                if run >= min_len:
                    return True
            else:
                run = 1
    return False


def scatter_points_from_cards(cards: list[dict[str, Any]]) -> int:
    return sum(_SCATTER_POINTS.get(str(c["rank"]).upper(), 10) for c in cards)


def analyze_buttons_with_qwen(
    image_path: str,
    *,
    model: str | None = None,
    max_retries: int = 2,
) -> dict[str, int]:
    """VLM 感知层：动作名 → 当前帧 element_id（不参与出牌决策）。"""
    model = model or default_vlm_model()
    last_err = ""
    for attempt in range(1, max_retries + 1):
        try:
            text = _vlm_chat(BUTTONS_PROMPT, image_path, model=model, max_tokens=256)
            raw = strip_markdown_json(text)
            obj = json.loads(raw)
            if not isinstance(obj, dict):
                raise ValueError("buttons 须为 JSON 对象")
            out: dict[str, int] = {}
            for k, v in obj.items():
                key = str(k).strip().lower()
                if key in ("deck", "drop", "fight", "group", "dump", "special"):
                    out[key] = int(v)
            logger.info("[vlm] 按钮映射: %s", out)
            return out
        except (ValueError, json.JSONDecodeError, TypeError) as e:
            last_err = str(e)
            logger.warning("[vlm] buttons 解析失败 %d/%d: %s", attempt, max_retries, e)
    raise RuntimeError(f"analyze_buttons_with_qwen 失败: {last_err}")


def cards_to_cv_state_dict(
    cards: list[dict[str, Any]],
    *,
    phase_overlay: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    将 VLM 认牌结果 + 可选 UI 阶段覆盖，组装为规则引擎可消费的 cv_state_dict。
    """
    can_group = _has_three_of_kind(cards) or _has_straight_flush(cards)
    scatter = scatter_points_from_cards(cards)
    state: dict[str, Any] = {
        "hand_cards": cards,
        "scatter_points": scatter,
        "can_group": can_group,
        "can_drop": False,
        "can_chow": False,
        "can_fight": False,
        "should_fight": False,
        "turn_phase": "draw",
    }
    if phase_overlay:
        for k in (
            "turn_phase",
            "can_fight",
            "should_fight",
            "can_chow",
            "can_group",
            "can_drop",
            "scatter_points",
        ):
            if k in phase_overlay:
                state[k] = phase_overlay[k]
    if state["turn_phase"] == "meld" and state.get("can_group"):
        state["can_drop"] = state.get("can_drop") or True
    if scatter <= 5:
        state["can_fight"] = state.get("can_fight", True)
        state["should_fight"] = state.get("should_fight", False)
    logger.info(
        "[cv] 本地推导: scatter=%s can_group=%s turn_phase=%s",
        state["scatter_points"],
        state["can_group"],
        state["turn_phase"],
    )
    return state
