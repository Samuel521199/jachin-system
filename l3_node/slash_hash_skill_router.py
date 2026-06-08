"""
用户消息以 ``#*#``（或兼容别名 ``/#/``）开头时表示**显式按 Skills（SKILL.md）路由**。

- **PMO（pmo-copilot）**：飞书 IM 侧由 ``try_hash_star_skill_lark_intercept`` 直接走
  ``pmo_lark_trigger._run_pmo_skill_coro``（``pmo_copilot_cli`` 信道 + 完整 SKILL 注入 +
  ``atom_lark_notifier`` 卡片推送），避免普通 ``run_agent`` 把战报 Markdown 当纯文本回会话。
- **其它 Skill**：仍走 ``run_agent`` + 本模块的 gateway inject（模型读 SKILL.md 后编排工具）。
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# 主前缀（用户约定）；``/#/`` 为早期别名
SKILL_ROUTE_PREFIXES: tuple[str, ...] = ("#*#", "/#/")
SKILL_ROUTE_PREFIX = SKILL_ROUTE_PREFIXES[0]

PMO_SKILL_ID = "pmo-copilot"

_PMO_TAIL_HINT_RE = re.compile(
    r"pmo|项目管理|项目情况|项目进展|项目进度|项目怎么样|项目进行|管理情况|"
    r"看板|战报|宏观看板|需求进度|人员任务|产研|发版|sprint|敏捷|汇报",
    re.I,
)

_PMO_ANOMALY_TAIL_RE = re.compile(
    r"预警|异常|阻塞|逾期|空载|巡检|分支\s*b|change[\s\-]?alert|变更预警",
    re.I,
)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _skills_repo_root() -> Path:
    return _project_root() / "skills_repo"


def _matched_prefix(user_text: str) -> str | None:
    t = (user_text or "").strip()
    for p in SKILL_ROUTE_PREFIXES:
        if t.startswith(p):
            return p
    return None


def is_slash_hash_skill_invocation(user_text: str) -> bool:
    return _matched_prefix(user_text) is not None


def extract_skill_route_tail(user_text: str) -> str:
    pfx = _matched_prefix(user_text)
    if not pfx:
        return ""
    return (user_text or "").strip()[len(pfx) :].strip()


def _split_yaml_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    """返回 (meta_dict, markdown_body)。"""

    text = raw or ""
    if not text.lstrip().startswith("---"):
        return {}, text

    match = re.match(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n", text, flags=re.DOTALL)
    if not match:
        return {}, text

    body = text[match.end() :]
    blob = match.group(1)
    meta: dict[str, Any] = {}

    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(blob)
        if isinstance(loaded, dict):
            meta = loaded
            return meta, body
    except Exception:
        pass

    for line in blob.splitlines():
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        meta[key.strip()] = val.strip().strip('"').strip("'")

    return meta, body


def enumerate_skill_md_catalog() -> list[dict[str, Any]]:
    """扫描 skills_repo 下 SKILL.md；每项含 skill_id（相对目录）、name、description、skill_md_absolute_path。"""
    repo = _skills_repo_root()
    if not repo.is_dir():
        logger.debug("[#*# SkillRouter] skills_repo 不存在或不可读: %s", repo)
        return []

    skip_parts = frozenset({".git", "node_modules", "__pycache__", ".cursor"})
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []

    for md in sorted(repo.rglob("SKILL.md")):
        if skip_parts.intersection(md.parts):
            continue
        try:
            rel_parent = md.parent.relative_to(repo)
        except ValueError:
            continue

        skill_id = rel_parent.as_posix()
        if skill_id in seen:
            continue
        seen.add(skill_id)

        try:
            raw = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        meta, body = _split_yaml_frontmatter(raw)
        name = str(meta.get("name") or skill_id).strip()
        description = str(meta.get("description") or "").strip()
        if not description:
            snippet = body.strip().replace("\n", " ").replace("\r", " ")
            description = snippet[:260] + ("..." if len(snippet) > 260 else "")

        rows.append(
            {
                "skill_id": skill_id,
                "name": name,
                "description": description,
                "skill_md_absolute_path": str(md.resolve()),
            }
        )

    return rows


def _catalog_match_score(tail: str, row: dict[str, Any]) -> int:
    """越大越匹配；0 表示无显著匹配。"""
    blob = " ".join(
        [
            str(row.get("skill_id") or ""),
            str(row.get("name") or ""),
            str(row.get("description") or ""),
        ]
    ).lower()
    t = (tail or "").lower()
    score = 0
    for token in re.split(r"[\s，,、。！？!?；;：:]+", t):
        token = token.strip()
        if len(token) < 2:
            continue
        if token in blob:
            score += 3
        elif any(token in part for part in blob.split()):
            score += 1
    sid = str(row.get("skill_id") or "").lower()
    if "pmo" in t and "pmo" in sid:
        score += 8
    if "项目管理" in t or "项目进度" in t or "项目情况" in t:
        if "pmo" in sid:
            score += 10
    return score


def resolve_skill_id_from_tail(tail: str, *, catalog: list[dict[str, Any]] | None = None) -> str | None:
    """
    根据 ``#*#`` 后自然语言解析目标 skill_id（相对 skills_repo）。
    未指明子能力时 PMO 默认主 Skill（宏观看板战报）。
    """
    t = (tail or "").strip()
    if not t:
        return None

    cats = catalog if catalog is not None else enumerate_skill_md_catalog()
    if not cats:
        if _PMO_TAIL_HINT_RE.search(t):
            return PMO_SKILL_ID
        return None

    best_id: str | None = None
    best_score = 0
    for row in cats:
        sc = _catalog_match_score(t, row)
        if sc > best_score:
            best_score = sc
            best_id = str(row.get("skill_id") or "")

    if best_score >= 3 and best_id:
        return best_id

    if _PMO_TAIL_HINT_RE.search(t):
        return PMO_SKILL_ID

    return None


def resolve_pmo_action_key(tail: str) -> str:
    """PMO 子分支：默认分支 A 宏观看板；含预警语义时走分支 B。"""
    if _PMO_ANOMALY_TAIL_RE.search(tail or ""):
        return "anomaly"
    return "full_board"


def build_pmo_user_message_for_tail(tail: str, action_key: str) -> str:
    """把用户 ``#*#`` 后的追问并入 PMO 执行句，并强调飞书卡片推送。"""
    from l3_node.pmo_lark_trigger import _ACTION_MESSAGES

    base = _ACTION_MESSAGES.get(action_key) or _ACTION_MESSAGES["full_board"]
    t = (tail or "").strip()
    if not t:
        return base
    return (
        f"{base}\n\n"
        f"【用户通过 #*# 显式触发 · 追问】{t}\n"
        "交付须通过 **mcp:atom_lark_notifier** 推送飞书 **消息卡片**（原生表格 + 翻页），"
        "**禁止**仅在 Final Answer 里贴整段 Markdown 战报代替推送；"
        "**禁止**在 Final Answer 向用户提及「监控群」或任何 oc_ chat_id。"
    )


def _skill_route_prefix_label() -> str:
    return " / ".join(f"`{p}`" for p in SKILL_ROUTE_PREFIXES)


def augment_gateway_inject_for_slash_hash_skill(user_input: str, existing_inject: str) -> str:
    """
    若 user_input 以 ``#*#`` / ``/#/`` 开头，则在 gateway system inject 末尾追加技能路由指令与目录。
    （PMO 已在 IM dispatcher 侧硬路由时，本注入主要服务非 PMO Skill 或终端路径。）
    """

    ui = user_input or ""
    if not is_slash_hash_skill_invocation(ui):
        return existing_inject

    tail = extract_skill_route_tail(ui)
    cats = enumerate_skill_md_catalog()
    pfx_label = _skill_route_prefix_label()

    logger.info(
        "[#*# SkillRouter] 启用：tail_len=%d catalog_skills=%d",
        len(tail),
        len(cats),
    )

    inject_parts: list[str] = []

    if not tail:
        inject_parts.append(
            f"【#*# · 技能显式呼叫】用户使用了前缀 {pfx_label}，但未在其后写明具体诉求。\n"
            f"请用 Final Answer **友善追问**：请用户在 {pfx_label} 后面用一两句话描述想调用的能力或任务目标；"
            "并简述本仓库技能的典型用途（可参考下方清单标题），**不要捏造**已成功执行某 Skill。"
        )
    else:
        inject_parts.append(
            f"【#*# · 技能显式呼叫 · 最高优先级】\n"
            f"用户正在通过前缀 {pfx_label} **明确要求**你从 **Skills（`skills_repo/**/SKILL.md`）** 中择优执行一项能力，而非普通闲聊。\n"
            f"**用户紧随前缀之后的原文（语义匹配的首要依据）**：\n"
            f"「{tail}」\n"
            "你必须完成的步骤：\n"
            "1) 仔细阅读下方清单中每一项的 **id / name / description**，根据与上文原文的**语义相符度**选出**至多一项**最合适的技能；\n"
            "2) 若**没有**任一技能与诉求合理匹配：**不要编造执行结果**，在 Final Answer 中如实说明「当前仓库已扫描的技能中无合适匹配」"
            "，并可按需用 2～5 句话概括清单里各技能的定位，便于用户改述或换前缀重试；\n"
            "3) 若选中了某项：先用 **core:fs_read** 读取该技能的 `SKILL.md` 文件的**绝对路径**（见清单中的 path），再严格按其 SOP 与可用工具链编排 Action直至交付；"
            "**禁止**仅凭记忆臆造 SKILL 正文。\n"
            "4) 若选中 **pmo-copilot** 且任务为宏观看板/项目进度：**必须**调用 **mcp:atom_lark_notifier**（或 **core:pmo_macro_dashboard_push**）推送飞书卡片，"
            "**禁止**把三表 Markdown 整段写在 Final Answer 里冒充已推送。\n"
        )

    if cats:
        catalog_chunks: list[str] = []
        for c in cats:
            cid = str(c["skill_id"])
            nm = str(c["name"]).replace("`", "")
            dc = str(c["description"]).replace("\n", " ").strip()
            if len(dc) > 340:
                dc = dc[:340] + "…"
            pth = str(c["skill_md_absolute_path"])
            catalog_chunks.append(
                f"- **id=`{cid}`** · *{nm}*\n"
                f"  - description: {dc}\n"
                f"  - SKILL.md: `{pth}`"
            )
        inject_parts.append("**本机已扫描的 SKILL.md 清单**\n\n" + "\n".join(catalog_chunks))
    else:
        inject_parts.append(
            "（扫描 `skills_repo` 后**未发现任何** `SKILL.md`：请如实告知用户仓库侧暂无可注册的声明式 Skill 文件，"
            "**不要**假装已执行某技能。）"
        )

    block = "\n\n".join(inject_parts)

    eg = (existing_inject or "").strip()
    if eg:
        return f"{eg}\n\n────\n{block}"
    return block


def try_hash_star_skill_lark_intercept(
    text: str,
    chat_id: str,
    send_reply_fn: Callable[[str, str], bool],
    engine: Any,
    loop: asyncio.AbstractEventLoop,
    session_messages: list[dict[str, Any]],
) -> str | None:
    """
    飞书 IM：``#*#`` / ``/#/`` 显式 Skill 触发。

    - 解析为 **pmo-copilot** 时：走完整 PMO Skill 管线（与 ``/pmo`` 精确触发相同），战报以飞书卡片送达。
    - 其它 Skill：返回 ``None``，由 dispatcher 继续 ``run_agent`` + inject。
    """
    norm = (text or "").strip()
    if not is_slash_hash_skill_invocation(norm):
        return None

    tail = extract_skill_route_tail(norm)
    skill_id = resolve_skill_id_from_tail(tail)
    if skill_id != PMO_SKILL_ID:
        logger.info(
            "[#*# SkillRouter] 非 PMO 或未匹配 skill_id=%s，交 run_agent inject 路径",
            skill_id or "(none)",
        )
        return None

    action_key = resolve_pmo_action_key(tail)
    user_msg = build_pmo_user_message_for_tail(tail, action_key)
    cid = (chat_id or "").strip()

    logger.info(
        "[#*# SkillRouter] PMO 硬路由 action=%s chat_id=%s tail=%r",
        action_key,
        cid[:24] if cid else "",
        tail[:60],
    )

    ack = {
        "full_board": "⏳ 已通过 #*# 启动 PMO 宏观看板，正在查询并生成战报，约需 1-3 分钟；**卡片将直接推送到本群**…",
        "anomaly": "⏳ 已通过 #*# 启动 PMO 异常巡检，约需 1-2 分钟…",
    }.get(action_key, "⏳ PMO 任务已启动，请稍候…")

    if cid:
        send_reply_fn(cid, ack)

    try:
        from l3_node.pmo_lark_trigger import run_pmo_heavy_task_from_lark_sync

        return run_pmo_heavy_task_from_lark_sync(
            action_key,
            user_msg,
            engine,
            session_messages,
            cid,
            loop,
            trigger_source="hash_star_skill_router",
        )
    except Exception as e:
        logger.exception("[#*# SkillRouter] PMO 执行失败: %s", e)
        return f"⚠️ PMO 任务执行出错：{e}"
