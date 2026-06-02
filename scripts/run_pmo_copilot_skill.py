#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PMO-Copilot：CLI「一句话点火」—— 进程内拉起 L3 LiteLLM 引擎并 ``run_agent``。

与设计约束对齐（相对早期草稿）：
- **工具白名单 SSOT**：仅从 ``SKILL.md`` 的 YAML frontmatter（``mcp_tools`` / ``native_tools`` / ``tools[].prefer``）解析，
  不在此脚本硬编码 MCP 列表。
- **Skill 不进用户消息**：SKILL 正文与 persona 通过 ``l3_node.agent_core._build_system_prompt`` 的 ``gateway_inject``
  写入 **system**，``user_input`` 仅为短用户句（避免 ReAct 每轮重复携带长篇 SKILL）。
- **stdio MCP**：不引入自定义环境变量开关；是否合并本地 MCP 由 ``~/.jachin/nexus_config.json`` /
  ``JACHIN_MERGE_LOCAL_MCP_INTO_TOOL_POOL`` 等与主路径一致。

用法（仓库根）::

  python scripts/run_pmo_copilot_skill.py
  python scripts/run_pmo_copilot_skill.py -m "执行分支 A：定时宏观看板……"
  python scripts/run_pmo_copilot_skill.py --analysis-only

每次运行会在 ``%USERPROFILE%\\.jachin\\jachin_debug\\健康skill\\``
新建 **独立** 文件 ``pmo_copilot_YYYYMMDD_HHMMSS_mmm_xxxxxxxx.txt``（毫秒 + 短 UUID，不覆盖同名旧文件），
写入 **仅落盘** 的调试摘要（抓取 URL、ReAct 步骤、Observation 节选、是否调 Lark）；不在控制台打印该内容。

前置：``.env`` 中 LLM Key；飞书播报依赖 ``atom_lark_notifier`` 的 MCP 配置（``config/mcps/atom_lark_notifier/config.yaml`` 或 ``~/.jachin/config/...``），与 ``python -m l3_node`` 一致——**无需在本脚本写 Lark 变量**。
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import logging
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any

_env_root = (os.environ.get("JACHIN_APP_ROOT") or "").strip()
ROOT = Path(_env_root).resolve() if _env_root else Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", encoding="utf-8")
except Exception:
    pass

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

DEFAULT_SKILL = ROOT / "skills_repo" / "pmo-copilot" / "SKILL.md"

DEFAULT_MESSAGE = (
    "请严格按 PMO-Copilot SKILL v7 **分支 A**："
    "若 pmo_raw_records 未就绪则先 INIT（mcp:atom_bi_project_context 拉 §1.1 全部 12 视图 + "
    "core:pmo_mirror_import 一次性镜像入库）；"
    "然后用 core:db_query 对 pmo_raw_records 做交叉分析与大颗粒度探针，"
    "组装 §1.4 三表后 **双群** mcp:atom_lark_notifier。"
    "禁止 core:fs_read 读 md；禁止 core:pmo_import_json。"
)

INIT_MESSAGE = (
    "请严格按 PMO-Copilot SKILL v7 **INIT 分支**："
    "调用 mcp:atom_bi_project_context 拉取 §1.1 全部 12 个 wiki_urls；"
    "然后 **仅一次** 调用 core:pmo_mirror_import 完成镜像入库。"
    "禁止 core:fs_read、core:pmo_import_json、core:db_write 循环。"
    "Final Answer 仅在 mirror_import 返回 ok 且 total_records>0 后简短确认。"
)

ANALYSIS_ONLY_MESSAGE = (
    "请严格按 PMO-Copilot SKILL v7 **分支 A · 仅分析模式**："
    "pmo_raw_records 镜像库已就绪。"
    "**禁止** mcp:atom_bi_project_context、core:fs_read、core:pmo_mirror_import、core:db_write。"
    "严格按 **§1.2.1 七步框架** 顺序执行 core:db_query（≤10 次）："
    "Step1 地图(record_count+columns_json) → Step2 样本(vewpI8lyYw+vewCz1FFJi) → "
    "Step3 人员(vewCz1FFJi·**1次**明细SQL：person+task+status+sprint+due 同查，禁止只查en_name) → "
    "Step4 Epic(**仅**vewpI8lyYw·父记录[0].text IS NULL，**禁止**在vewCz1FFJi用此条件) → "
    "Step5 状态+Sprint(Sprint用$.Sprint，禁止[0].text) → Step6 跨视图矛盾(6a+6b两步，禁止JOIN) → Step7 Version Goal。"
    "每步 Thought 写「本步产出」并**边查边填**三表 GFM 草稿行（禁止写「待填充」）。"
    "Version Goal 全空时 📦 表仍须 GFM 占位行（⚠️ 原表字段全空）。"
    "第11–13轮组 §1.4 三表并做推送前自检，第14–15轮 **双群** mcp:atom_lark_notifier（须 native_table_card:true）。"
    "若 pmo_premature_notifier_blocked(reason=markdown_incomplete) 且探针已完成，**只改 markdown_content**，禁止重跑 Step1–7。"
    "Thought 里的三表草稿须全文写入 atom_lark_notifier 的 markdown_content 字段。"
    "Final Answer 仅在双群 notifier 均 success 后 ≤3 句确认；禁止声称已推送。"
)


def _pmo_debug_log_dir() -> Path:
    """~/.jachin/jachin_debug/健康skill（例：C:\\Users\\Samuel\\.jachin\\jachin_debug\\健康skill）。"""
    return Path.home() / ".jachin" / "jachin_debug" / "健康skill"


def _make_pmo_on_step_writer(debug_path: Path):
    def _on_step(step_type: str, content: str, run_id: str) -> None:
        try:
            from l3_node.pmo_copilot_debug_file import append_pmo_debug_status

            st = (step_type or "").strip().lower()
            clip = (content or "").strip()
            if st in ("status", "gateway", "progress", "info") or clip.startswith("{"):
                append_pmo_debug_status(clip)
                return
            if st in ("thought", "action", "observation"):
                return
            if len(clip) > 400:
                clip = clip[:400] + "…"
            append_pmo_debug_status(f"[{step_type}] {clip}")
        except OSError:
            pass

    return _on_step


def parse_skill_md(raw: str) -> tuple[dict[str, Any], str]:
    """拆分 YAML frontmatter 与 Markdown 正文。"""
    text = raw.lstrip("\ufeff")
    if yaml is None:
        print("[pmo-copilot] 需要 PyYAML（pip install pyyaml）解析 SKILL frontmatter", file=sys.stderr)
        return {}, text.strip()
    m = re.match(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n(.*)$", text, re.DOTALL)
    if not m:
        return {}, text.strip()
    meta = yaml.safe_load(m.group(1))
    if not isinstance(meta, dict):
        meta = {}
    body = (m.group(2) or "").strip()
    return meta, body


def allowed_tools_from_skill_meta(meta: dict[str, Any]) -> list[str]:
    """从 SKILL frontmatter 收集工具 id（单一事实来源），去重保留顺序。"""
    ids: list[str] = []
    for key in ("mcp_tools", "native_tools"):
        for x in meta.get(key) or []:
            if isinstance(x, str) and x.strip():
                ids.append(x.strip())
    for row in meta.get("tools") or []:
        if isinstance(row, dict):
            pref = row.get("prefer") or row.get("prefer_tool")
            if isinstance(pref, str) and pref.strip():
                ids.append(pref.strip())
    seen: set[str] = set()
    out: list[str] = []
    for t in ids:
        low = t.lower()
        if low not in seen:
            seen.add(low)
            out.append(t)
    return out


def build_gateway_skill_inject(skill_path: Path, meta: dict[str, Any], body: str) -> str:
    name = str(meta.get("name") or "pmo-copilot").strip()
    persona = str(meta.get("persona") or "").strip()
    parts = [f"【声明式技能 · {name}】\nskill_file: {skill_path}"]
    if persona:
        parts.append("### Persona（YAML frontmatter）\n\n" + persona)
    parts.append("### SKILL 指令正文（Markdown）\n\n" + body)
    return "\n\n".join(parts)


async def _async_main(args: argparse.Namespace) -> int:
    skill_path = Path(args.skill).expanduser().resolve()
    if not skill_path.is_file():
        print(f"[pmo-copilot] SKILL 不存在: {skill_path}", file=sys.stderr)
        return 2

    try:
        skill_raw = skill_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"[pmo-copilot] 读取 SKILL 失败: {e}", file=sys.stderr)
        return 2

    meta, skill_body = parse_skill_md(skill_raw)
    base_allow = allowed_tools_from_skill_meta(meta)
    if not base_allow:
        print(
            "[pmo-copilot] SKILL.md frontmatter 未声明可用工具 "
            "（需要 mcp_tools / native_tools 或 tools[].prefer）",
            file=sys.stderr,
        )
        return 2

    if args.analysis_only:
        from l3_node.tools.pmo_db_tools import get_pmo_db_path, pmo_mirror_db_ready

        db_path = get_pmo_db_path()
        if not db_path.is_file() or not pmo_mirror_db_ready():
            print(
                f"[pmo-copilot] --analysis-only 需要已有镜像数据（pmo_raw_records）: {db_path}",
                file=sys.stderr,
            )
            print("  请先运行: python scripts/run_pmo_copilot_skill.py --init", file=sys.stderr)
            return 2
        allow_lower = {t.lower() for t in base_allow}
        for required in ("core:db_query",):
            if required not in allow_lower:
                base_allow.append(required)
        user_msg = (args.message or "").strip() or ANALYSIS_ONLY_MESSAGE
    elif getattr(args, "init", False):
        allow_lower = {t.lower() for t in base_allow}
        for required in ("core:pmo_mirror_import",):
            if required not in allow_lower:
                base_allow.append(required)
        user_msg = (args.message or "").strip() or INIT_MESSAGE
    else:
        user_msg = (args.message or "").strip() or DEFAULT_MESSAGE

    if args.analysis_only:
        print(f"[pmo-copilot] 模式: 仅分析（db_query）· DB: {db_path}", flush=True)
    elif getattr(args, "init", False):
        print("[pmo-copilot] 模式: INIT（拉表 + mirror_import）", flush=True)

    print("[pmo-copilot] 正在启动（引擎初始化可能需要数十秒）…", flush=True)

    _debug_dir = _pmo_debug_log_dir()
    _now = datetime.datetime.now()
    _stamp = (
        _now.strftime("%Y%m%d_%H%M%S")
        + f"_{_now.microsecond // 1000:03d}"
        + f"_{uuid.uuid4().hex[:8]}"
    )
    _debug_path = _debug_dir / f"pmo_copilot_{_stamp}.txt"
    print(f"[pmo-copilot] 详细调试日志: {_debug_path}", flush=True)
    _prev_pmo_log_env = os.environ.get("JACHIN_PMO_COPILOT_DEBUG_LOG")
    os.environ["JACHIN_PMO_COPILOT_DEBUG_LOG"] = str(_debug_path.resolve())

    try:
        return await _async_main_inner(
            args,
            user_msg,
            _debug_path,
            base_allow,
            skill_path,
            meta,
            skill_body,
        )
    finally:
        if _prev_pmo_log_env is not None:
            os.environ["JACHIN_PMO_COPILOT_DEBUG_LOG"] = _prev_pmo_log_env
        else:
            os.environ.pop("JACHIN_PMO_COPILOT_DEBUG_LOG", None)


async def _async_main_inner(
    args: argparse.Namespace,
    user_msg: str,
    _debug_path: Path,
    base_allow: list[str],
    skill_path: Path,
    meta: dict[str, Any],
    skill_body: str,
) -> int:
    from l3_node.intent_gateway.bundle import build_gateway_bundle
    from l3_node.primitives.tools.tool_pool import (
        assemble_tool_pool,
        expand_allowed_skills_with_implicit_sqlite_read,
        expand_allowed_skills_with_local_mcp,
    )

    correlation_id = str(uuid.uuid4())
    mi = max(1, min(int(args.max_iterations), 64))
    from l3_node.pmo_copilot_debug_file import (
        finalize_pmo_debug_log,
        init_pmo_debug_session,
        sync_pmo_debug_max_iterations,
    )

    mode_hint = ""
    if getattr(args, "analysis_only", False):
        mode_hint = "analysis-only"
    elif getattr(args, "init", False):
        mode_hint = "init"
    init_pmo_debug_session(
        log_path=_debug_path,
        user_message=user_msg,
        correlation_id=correlation_id,
        max_iterations=mi,
        mode_hint=mode_hint,
    )
    sync_pmo_debug_max_iterations(mi)
    implicit: dict[str, Any] = {"channel": "pmo_copilot_cli", "source": "run_pmo_copilot_skill.py"}
    if getattr(args, "analysis_only", False):
        implicit["pmo_analysis_only"] = True
        implicit["pmo_db_ready"] = True
    elif getattr(args, "init", False):
        implicit["pmo_init"] = True
    else:
        from l3_node.tools.pmo_db_tools import pmo_mirror_db_ready

        if pmo_mirror_db_ready():
            implicit["pmo_db_ready"] = True
    log = logging.getLogger("run_pmo_copilot_skill")
    bundle = build_gateway_bundle(
        user_input=user_msg,
        short_memory_context="",
        correlation_id=correlation_id,
        implicit_attribution=implicit,
    )

    try:
        from l3_node.intent_gateway.gateway_pipeline import apply_gateway_ingress_pipeline

        await apply_gateway_ingress_pipeline(
            bundle,
            user_msg,
            [],
            on_step=None,
            run_id=correlation_id,
            workspace_dir="",
        )
    except Exception as e:
        log.warning("[pmo-copilot] gateway ingress pipeline 跳过: %s", e)

    allowlist_diag_source = list(base_allow)
    expanded = expand_allowed_skills_with_implicit_sqlite_read(list(base_allow))
    expanded = expand_allowed_skills_with_local_mcp(expanded)

    tools = await assemble_tool_pool(
        allowed_skills=expanded,
        gateway_bundle=bundle,
        logger=log,
        allowlist_diag_source=allowlist_diag_source,
    )

    gateway_block = build_gateway_skill_inject(skill_path, meta, skill_body)

    from l3_node.agent_core import _build_system_prompt
    from l3_node.routing.output_format_signals import analyze_output_format_signals

    fmt_sig = analyze_output_format_signals(user_msg)
    prompt_style = "slim_user_led" if fmt_sig.slim_system_prompt() else "full"
    pure_json = bool(fmt_sig.prefer_json_object or fmt_sig.json_relaxed)

    full_system = await _build_system_prompt(
        tools=tools,
        allow_delegate=True,
        prompt_cycle=None,
        recruitment_longform=False,
        hr_domain_prompt_active=False,
        prompt_style=prompt_style,
        pure_json_contract=pure_json,
        gateway_inject=gateway_block,
        safety_lock_user_text=user_msg,
        chief_advisor_mode=False,
        environment_report_block="",
        semantic_layer=None,
        experience_few_shots="",
        realtime_web_grounding_block="",
        domain_experts=None,
    )

    try:
        from l3_node.__main__ import _create_engine_standalone

        engine = _create_engine_standalone()
    except Exception as e:
        print(
            f"[pmo-copilot] 引擎初始化失败: {e}\n"
            "  请检查 .env 中 DASHSCOPE_API_KEY / OPENAI_API_KEY 等。",
            file=sys.stderr,
        )
        return 2

    from l3_node.agent_core import run_agent

    _pmo_step = _make_pmo_on_step_writer(_debug_path)
    ans = await run_agent(
        user_msg,
        engine,
        max_iterations=mi,
        _allowed_skills_override=allowlist_diag_source,
        _system_prompt_override=full_system,
        gateway_context_bundle=bundle,
        implicit_attribution=implicit,
        on_step=_pmo_step,
    )
    finalize_pmo_debug_log(ans or "")

    print("\n--- Final Answer ---\n", (ans or "").strip())
    await asyncio.sleep(0.5)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="PMO-Copilot：Skill YAML 驱动白名单；SKILL 注入 system，不长驻 user 消息")
    ap.add_argument("--skill", default=str(DEFAULT_SKILL), help="SKILL.md 路径")
    ap.add_argument("-m", "--message", default="", help="等同聊天框输入的一句话")
    ap.add_argument(
        "--analysis-only",
        action="store_true",
        help="跳过拉表/入库；基于 pmo_raw_records 仅 db_query 分析并推送",
    )
    ap.add_argument(
        "--init",
        action="store_true",
        help="仅 INIT：拉表 + core:pmo_mirror_import 镜像入库",
    )
    ap.add_argument("--max-iterations", type=int, default=32, help="ReAct 上限（1～64）")
    args = ap.parse_args()
    try:
        return asyncio.run(_async_main(args))
    except KeyboardInterrupt:
        print("\n[pmo-copilot] 已中断", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
