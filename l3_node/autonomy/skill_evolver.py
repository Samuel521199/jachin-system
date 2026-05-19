"""
Skill 自动进化引擎（AY）— 完整版

支持两种 Skill 来源：
  local         — 仓库内 skills_repo/{name}/SKILL.md（本地开发）
  l1_subscribed — 从 L1 平台订阅并下载到 ~/.jachin/skills/{plugin_id}/SKILL.md

两条触发路径：
  主动路径（proactive）：意图连续成功 N 次 → awareness_loop 触发
  修复路径（healing）  ：Level 3 自愈诊断成功 → healer 预存进化候选
                         → 下次该意图成功时立即消费候选

L1 订阅 Skill 的生命周期：
  L1 下载 → mark_skill_origin() 记录 upstream_version
           → 正常使用 → 自动进化（本地修改）
           → L1 发布新版 → skill_sync_guard 拦截
             → 若有本地进化：smart merge（保留 Rules/Persona，接受上游 Frontmatter）
             → 记录 divergence；不强制覆盖

每个 Skill 目录下创建 .skill_evo_manifest.json 存储 per-skill 状态。

环境变量
--------
JACHIN_SKILL_EVOLVE_ENABLE=1              开启（默认关）
JACHIN_SKILL_EVOLVE_MIN_SUCCESSES=3       主动路径触发阈值（默认 3）
JACHIN_SKILL_EVOLVE_MAX_PATCH_RATIO=0.3   最大改动比例（默认 30%）
JACHIN_SKILL_EVOLVE_SNAPSHOT_DIR=         快照根目录（默认 ~/.jachin/workspace/skill_snapshots/）
JACHIN_SKILL_EVOLVE_LOG=                  进化日志（默认 ~/.jachin/workspace/skill_evolution.jsonl）
JACHIN_SKILL_EVOLVE_MODEL=                LLM 模型（默认 LLM_MODEL）
JACHIN_SKILL_EVOLVE_DRY_RUN=1             演练模式
JACHIN_SKILL_L1_CACHE=                    L1 订阅 Skill 根目录（默认 ~/.jachin/skills/）

P3 — 多 Skill 协同进化（仅一跳，禁止沿链递归）
JACHIN_SKILL_COEVOLVE_ENABLE=1            主技能 applied 后向 frontmatter evolution_peers 传播（默认关）
JACHIN_SKILL_COEVOLVE_MAX_PEERS=5         单次最多触发的 peer 数（默认 5）
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

SkillOrigin = Literal["local", "l1_subscribed", "unknown"]


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

def evolve_enabled() -> bool:
    return (os.environ.get("JACHIN_SKILL_EVOLVE_ENABLE") or "").strip().lower() in (
        "1", "true", "yes"
    )


def _min_successes() -> int:
    try:
        return max(1, int(os.environ.get("JACHIN_SKILL_EVOLVE_MIN_SUCCESSES") or "3"))
    except ValueError:
        return 3


def _max_patch_ratio() -> float:
    try:
        return max(0.05, min(0.9, float(os.environ.get("JACHIN_SKILL_EVOLVE_MAX_PATCH_RATIO") or "0.3")))
    except ValueError:
        return 0.3


def _snapshot_root() -> Path:
    custom = (os.environ.get("JACHIN_SKILL_EVOLVE_SNAPSHOT_DIR") or "").strip()
    if custom:
        return Path(custom)
    return Path(os.environ.get("JACHIN_HOME") or Path.home() / ".jachin").expanduser() / "workspace" / "skill_snapshots"


def _evolution_log_path() -> Path:
    custom = (os.environ.get("JACHIN_SKILL_EVOLVE_LOG") or "").strip()
    if custom:
        return Path(custom)
    return Path(os.environ.get("JACHIN_HOME") or Path.home() / ".jachin").expanduser() / "workspace" / "skill_evolution.jsonl"


def _evolve_model() -> str:
    return (
        os.environ.get("JACHIN_SKILL_EVOLVE_MODEL")
        or os.environ.get("LLM_MODEL")
        or "qwen-plus"
    ).strip()


def _dry_run() -> bool:
    return (os.environ.get("JACHIN_SKILL_EVOLVE_DRY_RUN") or "").strip().lower() in ("1", "true", "yes")


def _coevolve_enabled() -> bool:
    return (os.environ.get("JACHIN_SKILL_COEVOLVE_ENABLE") or "").strip().lower() in (
        "1", "true", "yes",
    )


def _coevolve_max_peers() -> int:
    try:
        return max(1, min(20, int(os.environ.get("JACHIN_SKILL_COEVOLVE_MAX_PEERS") or "5")))
    except ValueError:
        return 5


def _l1_skill_root() -> Path:
    custom = (os.environ.get("JACHIN_SKILL_L1_CACHE") or "").strip()
    if custom:
        return Path(custom)
    return Path(os.environ.get("JACHIN_HOME") or Path.home() / ".jachin").expanduser() / "skills"


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class SkillEvolutionManifest:
    """per-skill 进化状态（存储在 SKILL.md 同目录的 .skill_evo_manifest.json）。"""
    skill_name: str
    skill_path: str
    origin: SkillOrigin = "unknown"
    upstream_skill_id: str = ""        # L1 plugin_id
    upstream_version: str = ""         # 最后一次同步时的 L1 版本号
    upstream_last_synced: float = 0.0  # 最后同步时间戳
    local_version: str = ""            # 当前本地版本（含进化）
    local_evolution_count: int = 0
    last_evolved_at: float = 0.0
    diverged_from_upstream: bool = False
    # healer 预存的进化候选（下次成功时消费）
    pending_evolution: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SkillEvolutionManifest":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class SkillEvolutionRecord:
    """进化日志条目（写入 JSONL）。"""
    evolution_id: str
    skill_name: str
    skill_path: str
    status: str                 # applied / rejected / dry_run / error / staged
    trigger: str                # proactive / healing / manual
    change_summary: str
    change_ratio: float
    original_hash: str
    new_hash: str
    snapshot_path: str
    evidence_count: int
    confidence: float
    model: str
    origin: SkillOrigin = "unknown"
    upstream_version: str = ""
    timestamp: float = field(default_factory=time.time)
    error: str = ""
    # P3：协同进化时记录源技能名（主技能触发 peer 写盘时非空）
    co_evolve_from: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Skill 路径发现（本地 + L1 订阅）
# ---------------------------------------------------------------------------

def _skill_search_roots() -> list[Path]:
    """返回所有需要搜索 SKILL.md 的根目录（按优先级排列）。"""
    roots: list[Path] = []

    # 1. L1 订阅 Skill 目录（优先，可能有更新版本）
    l1 = _l1_skill_root()
    if l1.is_dir():
        roots.append(l1)

    # 2. 仓库内 skills_repo（本地开发）
    repo_candidates = [
        Path(__file__).parent.parent.parent / "skills_repo",
        Path(os.environ.get("JACHIN_HOME") or Path.home() / ".jachin").expanduser() / "skills_repo",
    ]
    for c in repo_candidates:
        if c.is_dir() and c not in roots:
            roots.append(c)

    return roots


def find_skill_md_path(skill_name: str) -> Path | None:
    """
    在所有 Skill 根目录下查找 SKILL.md，返回第一个命中。
    支持：精确目录名匹配、末段模糊匹配、plugin_id 反向域名匹配。
    """
    seen: set[str] = set()

    def _yield_candidates(base: Path) -> list[Path]:
        candidates: list[Path] = []
        # 精确匹配
        direct = base / skill_name / "SKILL.md"
        if direct.is_file():
            candidates.append(direct)
        # 模糊：末段目录名包含 skill_name
        try:
            for p in base.rglob("SKILL.md"):
                if skill_name.lower() in p.parent.name.lower():
                    candidates.append(p)
        except PermissionError:
            pass
        return candidates

    for root in _skill_search_roots():
        for p in _yield_candidates(root):
            k = str(p.resolve())
            if k not in seen:
                seen.add(k)
                return p
    return None


# ---------------------------------------------------------------------------
# Manifest I/O
# ---------------------------------------------------------------------------

def _manifest_path(skill_path: Path) -> Path:
    return skill_path.parent / ".skill_evo_manifest.json"


def load_manifest(skill_path: Path, skill_name: str = "") -> SkillEvolutionManifest:
    """加载 per-skill 进化清单；不存在时返回空白 manifest。"""
    mp = _manifest_path(skill_path)
    if mp.is_file():
        try:
            d = json.loads(mp.read_text(encoding="utf-8"))
            return SkillEvolutionManifest.from_dict(d)
        except Exception as e:
            logger.debug("[SkillEvolver] load manifest failed: %s", e)
    return SkillEvolutionManifest(
        skill_name=skill_name or skill_path.parent.name,
        skill_path=str(skill_path),
    )


def save_manifest(manifest: SkillEvolutionManifest) -> None:
    mp = Path(manifest.skill_path).parent / ".skill_evo_manifest.json"
    try:
        mp.write_text(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("[SkillEvolver] save manifest failed: %s", e)


def mark_skill_origin(
    skill_name: str,
    skill_path: Path,
    origin: SkillOrigin,
    upstream_skill_id: str = "",
    upstream_version: str = "",
) -> None:
    """
    L1 Skill 安装/同步后调用，记录来源信息。
    由 l3_node/mcp_sync.py 或类似同步模块在下载后调用。
    """
    manifest = load_manifest(skill_path, skill_name)
    manifest.origin = origin
    manifest.upstream_skill_id = upstream_skill_id or skill_name
    if upstream_version:
        manifest.upstream_version = upstream_version
        manifest.upstream_last_synced = time.time()
    if not manifest.local_version:
        manifest.local_version = upstream_version or "1.0.0"
    save_manifest(manifest)
    logger.info(
        "[SkillEvolver] marked origin skill=%s origin=%s upstream_version=%s",
        skill_name, origin, upstream_version,
    )


# ---------------------------------------------------------------------------
# SKILL.md 解析与 frontmatter 处理
# ---------------------------------------------------------------------------

def _split_frontmatter(content: str) -> tuple[str, str]:
    content = content or ""
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end != -1:
            fm = content[:end + 4]
            body = content[end + 4:].lstrip("\n")
            return fm, body
    return "", content


def _bump_version(frontmatter: str) -> str:
    def _increment(m: re.Match) -> str:
        parts = m.group(1).strip('"\'').split(".")
        if len(parts) == 3:
            try:
                parts[2] = str(int(parts[2]) + 1)
            except ValueError:
                parts.append("1")
        else:
            parts = parts + ["1"] if len(parts) < 3 else parts
        return f'version: "{".".join(parts)}"'
    return re.sub(r'version:\s*["\']?([^"\'\n]+)["\']?', _increment, frontmatter)


def _extract_local_version(frontmatter: str) -> str:
    m = re.search(r'^version:\s*["\']?([^"\'\n]+)["\']?', frontmatter, re.MULTILINE)
    return m.group(1).strip().strip('"\'') if m else "1.0.0"


def _parse_evolution_peers_from_content(content: str) -> list[str]:
    """从进化**前**的 SKILL.md 读出 evolution_peers（或 co_evolve_peers），用于 P3 一跳传播。"""
    fm, _ = _split_frontmatter(content)
    if not fm:
        return []
    inner = fm.strip()
    if inner.startswith("---"):
        inner = inner[3:].lstrip("\n")
    if inner.rstrip().endswith("---"):
        inner = inner.rstrip()[:-3].rstrip()
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return []
    try:
        data = yaml.safe_load(inner) or {}
    except Exception:
        return []
    raw = data.get("evolution_peers")
    if raw is None:
        raw = data.get("co_evolve_peers")
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[str] = []
    for x in raw:
        s = str(x).strip()
        if s:
            out.append(s)
    return out


def _normalize_evolution_peers(peers: list[str], primary: str, max_n: int) -> list[str]:
    seen: set[str] = set()
    primary_l = (primary or "").strip().lower()
    out: list[str] = []
    for p in peers:
        pl = p.strip()
        if not pl or pl.lower() == primary_l:
            continue
        key = pl.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(pl)
        if len(out) >= max_n:
            break
    return out


def _compute_change_ratio(original: str, evolved: str) -> float:
    if not original:
        return 1.0
    orig_lines = set(original.splitlines())
    new_lines = set(evolved.splitlines())
    added = new_lines - orig_lines
    removed = orig_lines - new_lines
    changed_chars = sum(len(l) for l in added | removed)
    return min(1.0, changed_chars / max(1, len(original)))


# ---------------------------------------------------------------------------
# LLM 进化 patch 生成
# ---------------------------------------------------------------------------

_EVOLVE_SYSTEM = """你是 Jachin OS 的 Skill 进化助手。
你的任务是根据「成功执行记录」，最小化地改进一份 SKILL.md 文件，
使该 Skill 在未来能更准确地执行类似任务。

改进规则：
1. 只修改正文 Markdown 部分（Persona / Rules / Examples / Limitations 段落）
2. 绝对不改变 YAML Frontmatter 的 name/mcp_tools/tools 字段结构
3. 改动量应最小（只添加/修改最关键的规则或说明，不做全文重写）
4. 若原文有 # Rules 段，将新规则追加到该段；若没有则在末尾新增 ## 补充经验 段
5. 每条新增规则前加 `- ` 列表项，简洁清晰（≤50字/条）
6. 不添加任何注释说明你做了什么，直接输出完整的修改后 SKILL.md 内容

输出格式：直接输出完整的 SKILL.md 内容，不要代码块标记。"""

_HEAL_EVOLVE_SYSTEM = """你是 Jachin OS 的 Skill 进化助手（修复模式）。
系统检测到某个 Skill 反复出错，并已找到有效的修复路径。
请根据「失败模式描述」和「成功修复路径」，最小化地更新 SKILL.md，
加入能防止该错误再次发生的防御性规则。

改进规则：
1. 仅在正文 # Rules 或 ## 注意事项 段落中追加 1-3 条防错规则
2. 规则需具体（描述具体的错误场景和正确做法），避免宽泛警告
3. 不改 YAML Frontmatter 的结构字段，version 由系统自动递增
4. 直接输出完整的修改后 SKILL.md 内容，不要解释。"""

_COEVOLVE_SYSTEM = """你是 Jachin OS 的 Skill 协同进化助手（P3）。
**另一份** Skill 刚根据运行证据完成进化；你的任务是在**当前** SKILL.md 中判断是否存在**可迁移**的通用经验，
若有则**最小化**写入正文（优先 # Rules），若无则保持正文基本不变。

规则：
1. 只修改正文 Markdown（Persona / Rules / Examples / Limitations）；不得改 name、mcp_tools、tools 等 Frontmatter 结构
2. 仅当教训与当前 Skill 的职责明显相关时才追加规则（每条 ≤50 字，`- ` 列表项）；**不要**照搬无关域的具体业务步骤
3. 若完全无关或无可迁移点，输出与输入**实质相同**的 SKILL.md（除系统可能统一 bump 的 version 外尽量不引入新臆造规则）
4. 直接输出完整的 SKILL.md，不要解释或代码块标记。"""


async def _call_llm_evolve(
    original_content: str,
    evidence_summary: str,
    mode: Literal["proactive", "healing", "co_evolve"] = "proactive",
) -> str:
    try:
        from l3_node.llm_client import LiteLLMEngine
    except ImportError:
        raise RuntimeError("LiteLLMEngine unavailable")

    if mode == "healing":
        system = _HEAL_EVOLVE_SYSTEM
        ev_label = "失败模式 + 成功修复路径"
    elif mode == "co_evolve":
        system = _COEVOLVE_SYSTEM
        ev_label = "协同进化 — 源技能变更与证据摘要"
    else:
        system = _EVOLVE_SYSTEM
        ev_label = "成功执行记录（关键发现）"
    user_msg = (
        f"以下是当前 SKILL.md 内容：\n\n```\n{original_content}\n```\n\n"
        f"{ev_label}：\n\n{evidence_summary}\n\n"
        "请生成改进后的 SKILL.md（直接输出完整内容，不要解释）："
    )
    engine = LiteLLMEngine(model=_evolve_model())
    response = await engine.generate_response(
        messages=[{"role": "user", "content": user_msg}],
        system_prompt=system,
        temperature=0.1,
        max_tokens=2000,
    )
    return str(response or "").strip()


def _summarize_evidence(evidence: list[dict[str, Any]], max_chars: int = 1200) -> str:
    lines: list[str] = []
    for i, rec in enumerate(evidence[:6]):
        intent = str(rec.get("intent") or rec.get("user_intent") or rec.get("query") or "").strip()[:80]
        outcome = str(rec.get("outcome") or rec.get("document") or "").strip()[:200]
        tools = rec.get("tools_used") or []
        if not tools and rec.get("executed_tool"):
            tools = [rec["executed_tool"]]
        tools_str = ", ".join(str(t) for t in tools[:5]) if tools else ""
        line = f"{i+1}. 意图: {intent}"
        if outcome:
            line += f" | 结果: {outcome}"
        if tools_str:
            line += f" | 工具: {tools_str}"
        lines.append(line)
    return "\n".join(lines)[:max_chars]


def _summarize_healing_evidence(
    failure_desc: str,
    last_error: str,
    success_hits: list[dict[str, Any]],
    max_chars: int = 1200,
) -> str:
    lines = [
        f"【失败模式】{failure_desc[:200]}",
        f"【最后错误】{last_error[:300]}",
        "",
        "【成功修复路径（来自 Experience RAG）】",
    ]
    for i, rec in enumerate(success_hits[:4]):
        intent = str(rec.get("user_intent") or rec.get("intent") or "").strip()[:80]
        tool = str(rec.get("executed_tool") or "").strip()
        payload = rec.get("action_payload") or {}
        payload_preview = str(payload)[:200]
        lines.append(f"{i+1}. 意图: {intent}")
        if tool:
            lines.append(f"   工具: {tool}  参数: {payload_preview}")
    return "\n".join(lines)[:max_chars]


# ---------------------------------------------------------------------------
# 验证
# ---------------------------------------------------------------------------

def _validate_candidate(original: str, evolved: str) -> tuple[bool, str]:
    if not evolved or len(evolved) < 20:
        return False, "进化后内容过短或为空"

    ratio = _compute_change_ratio(original, evolved)
    if ratio > _max_patch_ratio():
        return False, f"改动比例 {ratio:.1%} 超过阈值 {_max_patch_ratio():.0%}"

    orig_fm, _ = _split_frontmatter(original)
    new_fm, _ = _split_frontmatter(evolved)

    def _extract_name(fm: str) -> str:
        m = re.search(r"^name:\s*(.+)$", fm, re.MULTILINE)
        return m.group(1).strip().strip('"\'') if m else ""

    if orig_fm and new_fm:
        if _extract_name(orig_fm) and _extract_name(orig_fm) != _extract_name(new_fm):
            return False, f"Skill name 被篡改"

    dangerous = ["rm -rf", "os.system", "subprocess", "exec(", "eval("]
    for d in dangerous:
        if d in evolved and d not in original:
            return False, f"进化内容含危险模式: {d!r}"

    return True, "ok"


# ---------------------------------------------------------------------------
# 快照 & 日志
# ---------------------------------------------------------------------------

def _snapshot_skill(skill_name: str, content: str, evolution_id: str, label: str = "") -> str:
    snap_dir = _snapshot_root() / skill_name
    snap_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    fname = f"{ts}_{evolution_id[:8]}"
    if label:
        fname += f"_{label}"
    snap_path = snap_dir / f"{fname}.md"
    snap_path.write_text(content, encoding="utf-8")
    return str(snap_path)


def save_upstream_snapshot(skill_name: str, content: str, version: str) -> str:
    """L1 同步时保存上游原始版本快照（供 smart merge 使用）。"""
    snap_dir = _snapshot_root() / skill_name / "upstream"
    snap_dir.mkdir(parents=True, exist_ok=True)
    safe_ver = re.sub(r"[^\w.\-]", "_", version)
    snap_path = snap_dir / f"{safe_ver}.md"
    snap_path.write_text(content, encoding="utf-8")
    return str(snap_path)


def load_upstream_snapshot(skill_name: str, version: str) -> str | None:
    """加载指定上游版本快照，用于 3-way merge base。"""
    safe_ver = re.sub(r"[^\w.\-]", "_", version)
    snap_path = _snapshot_root() / skill_name / "upstream" / f"{safe_ver}.md"
    if snap_path.is_file():
        return snap_path.read_text(encoding="utf-8")
    return None


def _append_evolution_log(record: SkillEvolutionRecord) -> None:
    log_path = _evolution_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("[SkillEvolver] append log failed: %s", e)


def _generate_change_summary(original: str, evolved: str) -> str:
    orig_lines = set(original.splitlines())
    new_lines = evolved.splitlines()
    added = [l.strip() for l in new_lines if l.strip() and l not in orig_lines]
    summary = "；".join(added[:3])
    return summary[:200] or "内容微调"


# ---------------------------------------------------------------------------
# 预存进化候选（healing 路径）
# ---------------------------------------------------------------------------

def stage_evolution_candidate(
    skill_name: str,
    failure_desc: str,
    last_error: str,
    success_hits: list[dict[str, Any]],
) -> bool:
    """
    Level 3 自愈成功后调用：将 RAG 证据预存到 manifest 的 pending_evolution 字段。
    下次该意图执行成功时，awareness_loop 会调用 consume_staged_evolution 立即应用。
    返回 True 表示预存成功。
    """
    if not evolve_enabled():
        return False
    skill_path = find_skill_md_path(skill_name)
    if skill_path is None:
        return False

    manifest = load_manifest(skill_path, skill_name)
    manifest.pending_evolution = {
        "failure_desc": failure_desc[:500],
        "last_error": last_error[:300],
        "success_hits": success_hits[:4],
        "staged_at": time.time(),
        "mode": "healing",
    }
    save_manifest(manifest)
    logger.info("[SkillEvolver] staged healing evolution for skill=%s", skill_name)
    return True


async def consume_staged_evolution(skill_name: str) -> SkillEvolutionRecord | None:
    """
    意图成功后消费预存的进化候选（healing 路径）。
    若无候选则返回 None；消费后清除 pending_evolution。
    """
    if not evolve_enabled():
        return None
    skill_path = find_skill_md_path(skill_name)
    if skill_path is None:
        return None

    manifest = load_manifest(skill_path, skill_name)
    pending = manifest.pending_evolution
    if not pending:
        return None

    # 候选超过 24h 视为过期，自动清除
    staged_at = pending.get("staged_at", 0)
    if time.time() - staged_at > 86400:
        manifest.pending_evolution = None
        save_manifest(manifest)
        logger.info("[SkillEvolver] staged evolution expired for skill=%s", skill_name)
        return None

    logger.info("[SkillEvolver] consuming staged healing evolution skill=%s", skill_name)

    # 构造 healing 证据摘要
    evidence_summary = _summarize_healing_evidence(
        failure_desc=pending.get("failure_desc", ""),
        last_error=pending.get("last_error", ""),
        success_hits=pending.get("success_hits", []),
    )

    record = await _apply_evolution(
        skill_name=skill_name,
        skill_path=skill_path,
        manifest=manifest,
        evidence_summary=evidence_summary,
        evidence=pending.get("success_hits", []),
        trigger="healing",
        mode="healing",
    )

    # 消费后清除候选（无论成功失败）
    manifest.pending_evolution = None
    save_manifest(manifest)
    return record


# ---------------------------------------------------------------------------
# 核心进化执行逻辑
# ---------------------------------------------------------------------------

def _build_co_evolve_peer_payload(
    primary_skill_name: str,
    change_summary: str,
    evidence_summary: str,
) -> tuple[list[dict[str, Any]], str]:
    """P3：构造 peer 侧的 synthetic evidence 与 LLM 摘要。"""
    peer_summary = (
        f"【源技能】{primary_skill_name}\n"
        f"【源侧变更摘要】{change_summary}\n"
        f"【源侧运行证据摘要】\n{evidence_summary[:1200]}"
    )
    syn = [{
        "intent": f"[P3 协同进化] 来源技能 {primary_skill_name}",
        "outcome": f"源变更：{change_summary[:400]} | 证据摘录：{evidence_summary[:500]}",
        "tools_used": [],
    }]
    return syn, peer_summary


async def _propagate_co_evolve_to_peers(
    primary_skill_name: str,
    original_primary_content: str,
    change_summary: str,
    evidence_summary: str,
) -> None:
    """
    主技能已成功写盘后：按**进化前** frontmatter 的 evolution_peers 对 peer 各做一次 co_evolve（仅一跳，不再递归）。
    """
    if not _coevolve_enabled():
        return
    raw = _parse_evolution_peers_from_content(original_primary_content)
    peers = _normalize_evolution_peers(raw, primary_skill_name, _coevolve_max_peers())
    if not peers:
        return
    syn_evidence, peer_llm_summary = _build_co_evolve_peer_payload(
        primary_skill_name, change_summary, evidence_summary,
    )
    logger.info(
        "[SkillEvolver][P3] co-evolve from=%s peers=%s",
        primary_skill_name, peers,
    )
    for peer_id in peers:
        peer_path = find_skill_md_path(peer_id)
        if peer_path is None:
            logger.info(
                "[SkillEvolver][P3] peer SKILL.md not found peer=%s from=%s",
                peer_id, primary_skill_name,
            )
            continue
        try:
            peer_manifest = load_manifest(peer_path, peer_id)
            await _apply_evolution(
                skill_name=peer_id,
                skill_path=peer_path,
                manifest=peer_manifest,
                evidence_summary=peer_llm_summary,
                evidence=syn_evidence,
                trigger="co_evolve",
                mode="co_evolve",
                propagate_co_evolve=False,
                co_evolve_from=primary_skill_name,
            )
        except Exception as e:
            logger.warning(
                "[SkillEvolver][P3] co-evolve peer=%s from=%s failed: %s",
                peer_id, primary_skill_name, e,
            )


async def _apply_evolution(
    skill_name: str,
    skill_path: Path,
    manifest: SkillEvolutionManifest,
    evidence_summary: str,
    evidence: list[dict[str, Any]],
    trigger: str,
    mode: Literal["proactive", "healing", "co_evolve"] = "proactive",
    *,
    propagate_co_evolve: bool = True,
    co_evolve_from: str = "",
) -> SkillEvolutionRecord:
    """内部：读取 SKILL.md → LLM 生成 patch → 验证 → 写入 → 更新 manifest → 写日志。"""
    if co_evolve_from:
        propagate_co_evolve = False
    evo_id = str(uuid.uuid4())

    try:
        original = skill_path.read_text(encoding="utf-8")
    except Exception as e:
        return _error_record(
            evo_id, skill_name, skill_path, trigger, manifest.origin, str(e),
            co_evolve_from=co_evolve_from,
        )

    # LLM 生成
    try:
        proposed = await _call_llm_evolve(original, evidence_summary, mode=mode)
    except Exception as e:
        record = _error_record(
            evo_id, skill_name, skill_path, trigger, manifest.origin, f"LLM failed: {e}",
            co_evolve_from=co_evolve_from,
        )
        _append_evolution_log(record)
        return record

    # Frontmatter 处理
    orig_fm, orig_body = _split_frontmatter(original)
    new_fm, new_body = _split_frontmatter(proposed)
    if orig_fm and new_fm:
        bumped_fm = _bump_version(new_fm)
        proposed = bumped_fm + "\n\n" + new_body
    elif orig_fm and not new_fm:
        bumped_fm = _bump_version(orig_fm)
        proposed = bumped_fm + "\n\n" + proposed.lstrip()

    change_ratio = _compute_change_ratio(original, proposed)
    ok, reason = _validate_candidate(original, proposed)
    orig_hash = hashlib.sha256(original.encode()).hexdigest()[:16]
    new_hash = hashlib.sha256(proposed.encode()).hexdigest()[:16]
    change_summary = _generate_change_summary(original, proposed)

    if not ok:
        record = SkillEvolutionRecord(
            evolution_id=evo_id, skill_name=skill_name, skill_path=str(skill_path),
            status="rejected", trigger=trigger, change_summary=change_summary,
            change_ratio=change_ratio, original_hash=orig_hash, new_hash=new_hash,
            snapshot_path="", evidence_count=len(evidence), confidence=0.6,
            model=_evolve_model(), origin=manifest.origin,
            upstream_version=manifest.upstream_version, error=reason,
            co_evolve_from=co_evolve_from,
        )
        _append_evolution_log(record)
        return record

    if _dry_run():
        record = SkillEvolutionRecord(
            evolution_id=evo_id, skill_name=skill_name, skill_path=str(skill_path),
            status="dry_run", trigger=trigger, change_summary=change_summary,
            change_ratio=change_ratio, original_hash=orig_hash, new_hash=new_hash,
            snapshot_path="", evidence_count=len(evidence), confidence=0.85,
            model=_evolve_model(), origin=manifest.origin,
            upstream_version=manifest.upstream_version,
            co_evolve_from=co_evolve_from,
        )
        _append_evolution_log(record)
        logger.info("[SkillEvolver][DryRun] skill=%s ratio=%.1f%% trigger=%s", skill_name, change_ratio * 100, trigger)
        return record

    # 备份 → 写入
    try:
        snap_path = _snapshot_skill(skill_name, original, evo_id, label=trigger)
        skill_path.write_text(proposed, encoding="utf-8")
    except Exception as e:
        record = _error_record(
            evo_id, skill_name, skill_path, trigger, manifest.origin, f"write failed: {e}",
            co_evolve_from=co_evolve_from,
        )
        _append_evolution_log(record)
        return record

    # 更新 manifest
    new_fm_after, _ = _split_frontmatter(proposed)
    manifest.local_version = _extract_local_version(new_fm_after) if new_fm_after else manifest.local_version
    manifest.local_evolution_count += 1
    manifest.last_evolved_at = time.time()
    if manifest.origin == "l1_subscribed" and manifest.upstream_version:
        manifest.diverged_from_upstream = True
    save_manifest(manifest)

    try:
        from l3_node.skill_md_hot_reload import notify_skill_md_changed_from_disk_write

        notify_skill_md_changed_from_disk_write(skill_path)
    except Exception:
        pass

    record = SkillEvolutionRecord(
        evolution_id=evo_id, skill_name=skill_name, skill_path=str(skill_path),
        status="applied", trigger=trigger, change_summary=change_summary,
        change_ratio=change_ratio, original_hash=orig_hash, new_hash=new_hash,
        snapshot_path=snap_path, evidence_count=len(evidence), confidence=0.85,
        model=_evolve_model(), origin=manifest.origin,
        upstream_version=manifest.upstream_version,
        co_evolve_from=co_evolve_from,
    )
    _append_evolution_log(record)
    logger.info(
        "[SkillEvolver] applied skill=%s trigger=%s ratio=%.1f%% evo_count=%d snap=%s",
        skill_name, trigger, change_ratio * 100, manifest.local_evolution_count, snap_path,
    )
    if propagate_co_evolve and not co_evolve_from and mode != "co_evolve":
        try:
            await _propagate_co_evolve_to_peers(
                primary_skill_name=skill_name,
                original_primary_content=original,
                change_summary=change_summary,
                evidence_summary=evidence_summary,
            )
        except Exception as e:
            logger.warning("[SkillEvolver][P3] co-evolve propagation failed: %s", e)
    return record


def _error_record(
    evo_id: str, skill_name: str, skill_path: Path, trigger: str,
    origin: SkillOrigin, error: str,
    *,
    co_evolve_from: str = "",
) -> SkillEvolutionRecord:
    return SkillEvolutionRecord(
        evolution_id=evo_id, skill_name=skill_name, skill_path=str(skill_path),
        status="error", trigger=trigger, change_summary="", change_ratio=0.0,
        original_hash="", new_hash="", snapshot_path="", evidence_count=0,
        confidence=0.0, model=_evolve_model(), origin=origin, error=error,
        co_evolve_from=co_evolve_from,
    )


# ---------------------------------------------------------------------------
# 公开主入口
# ---------------------------------------------------------------------------

async def analyze_and_evolve_skill(
    skill_name: str,
    evidence: list[dict[str, Any]],
    trigger: str = "proactive",
) -> SkillEvolutionRecord | None:
    """
    主动路径：基于 Experience RAG 成功记录进化 SKILL.md。
    """
    if not evolve_enabled() or not evidence:
        return None

    skill_path = find_skill_md_path(skill_name)
    if skill_path is None:
        logger.info("[SkillEvolver] SKILL.md not found for skill=%s", skill_name)
        return None

    manifest = load_manifest(skill_path, skill_name)
    evidence_summary = _summarize_evidence(evidence)
    return await _apply_evolution(
        skill_name=skill_name,
        skill_path=skill_path,
        manifest=manifest,
        evidence_summary=evidence_summary,
        evidence=evidence,
        trigger=trigger,
        mode="proactive",
    )


async def run_skill_evolution_if_ready(
    skill_name: str,
    consecutive_successes: int,
    last_experience_records: list[dict[str, Any]],
    trigger_reason: str = "",
) -> SkillEvolutionRecord | None:
    """主动路径触发器：连续成功 N 次后尝试进化（awareness_loop 调用）。"""
    if not evolve_enabled():
        return None
    # 优先消费 staged（healing）候选
    staged = await consume_staged_evolution(skill_name)
    if staged is not None:
        return staged
    # 其次按阈值触发主动路径
    if consecutive_successes < _min_successes():
        return None
    if not last_experience_records:
        return None
    logger.info("[SkillEvolver] proactive evolution triggered skill=%s successes=%d", skill_name, consecutive_successes)
    return await analyze_and_evolve_skill(
        skill_name=skill_name,
        evidence=last_experience_records,
        trigger=trigger_reason or f"连续成功 {consecutive_successes} 次",
    )


# ---------------------------------------------------------------------------
# 诊断 / 统计接口
# ---------------------------------------------------------------------------

def list_evolution_history(skill_name: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    log_path = _evolution_log_path()
    if not log_path.is_file():
        return []
    results: list[dict[str, Any]] = []
    try:
        with log_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if skill_name and rec.get("skill_name") != skill_name:
                    continue
                results.append(rec)
    except Exception:
        pass
    results.sort(key=lambda r: r.get("timestamp", 0), reverse=True)
    return results[:limit]


def get_evolution_stats() -> dict[str, Any]:
    if not evolve_enabled():
        return {"enabled": False}
    history = list_evolution_history(limit=200)
    applied = [r for r in history if r.get("status") == "applied"]
    rejected = [r for r in history if r.get("status") == "rejected"]
    skills_evolved = list({r["skill_name"] for r in applied})
    # 找出有 pending_evolution 的技能
    pending_count = 0
    for root in _skill_search_roots():
        for mp in root.rglob(".skill_evo_manifest.json"):
            try:
                d = json.loads(mp.read_text(encoding="utf-8"))
                if d.get("pending_evolution"):
                    pending_count += 1
            except Exception:
                pass
    return {
        "enabled": True,
        "dry_run": _dry_run(),
        "coevolve_enabled": _coevolve_enabled(),
        "coevolve_max_peers": _coevolve_max_peers(),
        "min_successes": _min_successes(),
        "max_patch_ratio": _max_patch_ratio(),
        "total_evolutions": len(applied),
        "total_rejected": len(rejected),
        "evolved_skills": skills_evolved,
        "pending_staged_evolutions": pending_count,
        "latest_evolution": applied[0] if applied else None,
        "log_path": str(_evolution_log_path()),
        "snapshot_dir": str(_snapshot_root()),
        "l1_skill_root": str(_l1_skill_root()),
    }


def list_diverged_skills() -> list[dict[str, Any]]:
    """列出所有从上游版本分叉（有本地进化）的 L1 订阅 Skill。"""
    diverged: list[dict[str, Any]] = []
    for root in _skill_search_roots():
        for mp in root.rglob(".skill_evo_manifest.json"):
            try:
                d = json.loads(mp.read_text(encoding="utf-8"))
                if d.get("diverged_from_upstream") and d.get("origin") == "l1_subscribed":
                    diverged.append({
                        "skill_name": d.get("skill_name", ""),
                        "upstream_version": d.get("upstream_version", ""),
                        "local_version": d.get("local_version", ""),
                        "evolution_count": d.get("local_evolution_count", 0),
                        "last_evolved_at": d.get("last_evolved_at", 0),
                        "skill_path": d.get("skill_path", ""),
                    })
            except Exception:
                pass
    return diverged
