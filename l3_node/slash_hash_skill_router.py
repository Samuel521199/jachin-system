"""
用户消息以 `/#/` 开头时表示**显式按 Skills（SKILL.md）路由**：由模型根据其后自然语言，
在仓库已扫描的技能目录中择优匹配一项并按其 SOP 执行；若无合适匹配则如实说明。

仅做「提示词路由 + 清单注入」，不手写具体业务规则；扫描 `skills_repo/**/SKILL.md`。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SKILL_ROUTE_PREFIX = "/#/"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _skills_repo_root() -> Path:
    return _project_root() / "skills_repo"


def is_slash_hash_skill_invocation(user_text: str) -> bool:
    t = (user_text or "").strip()
    return bool(t.startswith(SKILL_ROUTE_PREFIX))


def extract_skill_route_tail(user_text: str) -> str:
    t = (user_text or "").strip()
    if not t.startswith(SKILL_ROUTE_PREFIX):
        return ""
    return t[len(SKILL_ROUTE_PREFIX) :].strip()


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
        logger.debug("[/#/ SkillRouter] skills_repo 不存在或不可读: %s", repo)
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


def augment_gateway_inject_for_slash_hash_skill(user_input: str, existing_inject: str) -> str:
    """
    若 user_input 以 `/#/` 开头，则在 gateway system inject 末尾追加技能路由指令与目录。
    """

    ui = user_input or ""
    if not is_slash_hash_skill_invocation(ui):
        return existing_inject

    tail = extract_skill_route_tail(ui)
    cats = enumerate_skill_md_catalog()

    logger.info(
        "[/#/ SkillRouter] 启用：tail_len=%d catalog_skills=%d",
        len(tail),
        len(cats),
    )

    inject_parts: list[str] = []

    if not tail:
        inject_parts.append(
            "【/#/ · 技能显式呼叫】用户使用了前缀 `/#/`，但未在其后写明具体诉求。\n"
            "请用 Final Answer **友善追问**：请用户在 `/#/` 后面用一两句话描述想调用的能力或任务目标；"
            "并简述本仓库技能的典型用途（可参考下方清单标题），**不要捏造**已成功执行某 Skill。"
        )
    else:
        inject_parts.append(
            "【/#/ · 技能显式呼叫 · 最高优先级】\n"
            "用户正在通过前缀 `/#/` **明确要求**你从 **Skills（`skills_repo/**/SKILL.md`）** 中择优执行一项能力，而非普通闲聊。\n"
            "**用户紧随 `/#/` 之后的原文（语义匹配的首要依据）**：\n"
            f"「{tail}」\n"
            "你必须完成的步骤：\n"
            "1) 仔细阅读下方清单中每一项的 **id / name / description**，根据与上文原文的**语义相符度**选出**至多一项**最合适的技能；\n"
            "2) 若**没有**任一技能与诉求合理匹配：**不要编造执行结果**，在 Final Answer 中如实说明「当前仓库已扫描的技能中无合适匹配」"
            "，并可按需用 2～5 句话概括清单里各技能的定位，便于用户改述或换前缀重试；\n"
            "3) 若选中了某项：先用 **core:fs_read** 读取该技能的 `SKILL.md` 文件的**绝对路径**（见清单中的 path），再严格按其 SOP 与可用工具链编排 Action直至交付；"
            "**禁止**仅凭记忆臆造 SKILL 正文。\n"
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