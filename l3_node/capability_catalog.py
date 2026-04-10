"""
L3 能力总目录：与业务域解耦。

- 核心切片：`docs/L3_CAPABILITY_CATALOG.md` 中 PROMPT_INJECT_CORE
- 各域切片：`docs/capability_domains/<域>.md`，在 DOMAIN_REGISTRY 注册 tool id markers 与锚点名

新增 MCP/Skill 域：加 md 文件 + 在 DOMAIN_REGISTRY 追加一条即可，勿改总目录主体结构。
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CapabilityDomainSpec:
    """单域注册项（与招聘等业务解耦，可任意追加）。"""

    domain_id: str
    tool_markers: tuple[str, ...]
    """工具 id 中出现任一字串即视为该域可用。"""
    doc_relpath: str
    """相对 docs/ 的路径，如 capability_domains/hr_recruitment.md"""
    inject_anchor: str
    """锚点中间名，如 RECRUITMENT → PROMPT_INJECT_RECRUITMENT_START/END"""
    fallback: str


def _inject_tags(anchor: str) -> tuple[str, str]:
    a = anchor.strip().upper()
    return (
        f"<!-- PROMPT_INJECT_{a}_START -->",
        f"<!-- PROMPT_INJECT_{a}_END -->",
    )


_RECRUITMENT_FALLBACK = """【域：招聘】若可用工具中含 atom_post_job_boss、hr_scheduler_send_confirm_prompt、add_automated_recruitment_task、stop_automated_recruitment、atom_greet_recommend_boss、atom_lark_chat、hr_analyze_resume 等，则招聘能力已就绪；请用 MCP 落实意图。飞书极短指令可能已被 lark_workflow_command_interceptor 处理；WebSocket/HTTP 仍应调工具。详细步骤见注入的 SKILL.md（若有）。"""

_OFFICE_PPT_FALLBACK = """【域：PPTX】若可用工具中含 create_presentation、save_presentation 等（id 多为 mcp: 前缀），则本机 PowerPoint MCP 已连接：必须用 ReAct 调用这些工具完成 PPT，禁止谎称无法连接 MCP 或只给替代 Python 脚本。用 presentation_id 串联步骤；save 时使用绝对路径（Windows 勿用未展开的 ~）。"""

_CORE_FALLBACK = """你是 Jachin L3 执行节点助手：仅使用「可用工具」列表中出现的 MCP/技能；短指令可能由代码硬路径处理，长对话与控制台仍应通过工具落实意图。若下文含「域」摘要，仅在与该域相关的用户意图时使用对应工具。"""


# 新域在此追加；与 docs/capability_domains/ 及 md 内锚点保持一致
DOMAIN_REGISTRY: tuple[CapabilityDomainSpec, ...] = (
    CapabilityDomainSpec(
        domain_id="hr_recruitment",
        tool_markers=(
            "atom_post_job_boss",
            "hr_scheduler_send_confirm_prompt",
            "add_automated_recruitment_task",
            "stop_automated_recruitment",
            "atom_greet_recommend_boss",
            "atom_lark_chat",
            "hr_analyze_resume",
            "atom_inbox_harvester",
            "atom_lark_bitable",
            "atom_lark_send_message",
        ),
        doc_relpath="capability_domains/hr_recruitment.md",
        inject_anchor="RECRUITMENT",
        fallback=_RECRUITMENT_FALLBACK.strip(),
    ),
    CapabilityDomainSpec(
        domain_id="office_powerpoint_mcp",
        tool_markers=(
            "create_presentation",
            "save_presentation",
            "add_slide",
            "apply_professional_design",
            "create_presentation_from_template",
        ),
        doc_relpath="capability_domains/office_powerpoint_mcp.md",
        inject_anchor="OFFICE_POWERPOINT",
        fallback=_OFFICE_PPT_FALLBACK.strip(),
    ),
)


def _project_roots_for_docs() -> list[Path]:
    here = Path(__file__).resolve()
    proj = here.parent.parent
    out = [proj]
    try:
        from l3_node.paths import get_app_root

        app = get_app_root()
        if app and app.resolve() != proj.resolve():
            out.insert(0, app.resolve())
    except Exception:
        pass
    return out


def _docs_dirs() -> list[Path]:
    """
    解析顺序：PyInstaller 解压目录下的 docs/（与 build_l3_sidecar --add-data 一致）→
    get_app_root()/docs（便携包）→ 开发仓库根/docs。
    保证任意机器上打包后的 L3 仍能读到总目录与各域切片。
    """
    raw: list[Path] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            raw.append(Path(meipass) / "docs")
    for r in _project_roots_for_docs():
        raw.append(r / "docs")
    seen: set[str] = set()
    out: list[Path] = []
    for p in raw:
        try:
            key = str(p.resolve())
        except OSError:
            key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _extract_between(raw: str, start_tag: str, end_tag: str) -> str | None:
    if start_tag not in raw or end_tag not in raw:
        return None
    try:
        a = raw.index(start_tag) + len(start_tag)
        b = raw.index(end_tag, a)
        chunk = raw[a:b].strip()
        return chunk if chunk else None
    except ValueError:
        return None


def load_core_catalog_prompt_block() -> str:
    """总目录核心注入（与具体域无关）。"""
    start, end = _inject_tags("CORE")
    for ddir in _docs_dirs():
        p = ddir / "L3_CAPABILITY_CATALOG.md"
        if not p.is_file():
            continue
        try:
            raw = p.read_text(encoding="utf-8")
        except OSError as e:
            logger.debug("[capability_catalog] 读取 core %s: %s", p, e)
            continue
        got = _extract_between(raw, start, end)
        if got:
            return got
    logger.warning("[capability_catalog] 未找到 CORE 注入锚点，使用兜底")
    return _CORE_FALLBACK.strip()


def _load_domain_inject(spec: CapabilityDomainSpec) -> str:
    start, end = _inject_tags(spec.inject_anchor)
    for ddir in _docs_dirs():
        p = ddir / spec.doc_relpath
        if not p.is_file():
            continue
        try:
            raw = p.read_text(encoding="utf-8")
        except OSError as e:
            logger.debug("[capability_catalog] 读取域 %s: %s", p, e)
            continue
        got = _extract_between(raw, start, end)
        if got:
            return got
    logger.warning("[capability_catalog] 域 %s 缺少注入锚点，使用兜底", spec.domain_id)
    return spec.fallback.strip()


def _tools_blob(tools: list[dict] | None) -> str:
    if not tools:
        return ""
    return " ".join(str(t.get("id") or "").lower() for t in tools)


def iter_matching_domain_specs(tools: list[dict] | None) -> list[CapabilityDomainSpec]:
    blob = _tools_blob(tools)
    if not blob:
        return []
    out: list[CapabilityDomainSpec] = []
    for spec in DOMAIN_REGISTRY:
        if any(m in blob for m in spec.tool_markers):
            out.append(spec)
    return out


def build_capability_prompt_inject_for_tools(tools: list[dict] | None) -> str:
    """
    拼接：核心总目录 + 当前工具命中的各域摘要。
    与具体业务解耦；新域仅扩展 DOMAIN_REGISTRY 与 docs/capability_domains/。
    """
    parts: list[str] = [load_core_catalog_prompt_block().strip()]
    seen: set[str] = set()
    for spec in iter_matching_domain_specs(tools):
        if spec.domain_id in seen:
            continue
        seen.add(spec.domain_id)
        parts.append(_load_domain_inject(spec).strip())
    return "\n\n---\n\n".join(p for p in parts if p)


def tools_include_recruitment(tools: list[dict] | None) -> bool:
    """是否应注入 HR 招聘 SKILL.md（与域摘要分离，仅招聘 SOP 需要）。"""
    blob = _tools_blob(tools)
    if not blob:
        return False
    for spec in DOMAIN_REGISTRY:
        if spec.domain_id != "hr_recruitment":
            continue
        return any(m in blob for m in spec.tool_markers)
    return False
