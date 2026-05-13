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

每次运行会在 ``%USERPROFILE%\\.jachin\\jachin_debug\\健康skill\\pmo_copilot_YYYYMMDD_HHMMSS.txt``
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
    "请严格按系统提示中的 PMO-Copilot SKILL：按「分支 A / 定时宏观看板」拉取 §1.1 全部种子链接并汇总"
    "（含开发表 tblfK9… 的多个 view，须一次或分批 atom_bi_project_context 全覆盖）；"
    "若需推送则用 mcp:atom_lark_notifier 发 Markdown。"
)


def _pmo_debug_log_dir() -> Path:
    """~/.jachin/jachin_debug/健康skill（例：C:\\Users\\Samuel\\.jachin\\jachin_debug\\健康skill）。"""
    return Path.home() / ".jachin" / "jachin_debug" / "健康skill"


def _open_pmo_file_debug(path: Path, *, correlation_id: str, user_msg: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    path.write_text(
        "\n".join(
            [
                "PMO-Copilot / run_pmo_copilot_skill.py — 文件调试日志（以下不写入控制台）",
                f"生成时间: {ts}",
                f"correlation_id: {correlation_id}",
                f"调试文件路径: {path}",
                "",
                "【本文件说明】",
                "1) 抓取哪个地址：见各轮「ACTION 完整输入」中解析出的 wiki_urls；若无则可能走 MCP 内置默认 URL。",
                "2) 执行流程：下方「on_step」时间线 + L3 写入的 ACTION / OBSERVATION 大块。",
                "3) 如何综合成看板：对照各轮 thought 与 bi_project 的 OBSERVATION（manifest / 路径 / 摘要）。",
                "4) 是否真实发 Lark：见 atom_lark_notifier 的 ACTION 摘要与 OBSERVATION 的 status。",
                "",
                "【用户输入】",
                user_msg,
                "",
                "=" * 72,
                "",
            ]
        ),
        encoding="utf-8",
    )


def _make_pmo_on_step_writer(debug_path: Path):
    def _on_step(step_type: str, content: str, run_id: str) -> None:
        try:
            clip = (content or "").strip()
            lim = 1200
            if len(clip) > lim:
                clip = clip[:lim] + f"\n... [on_step 截断，总长度 {len(content)}]"
            with open(debug_path, "a", encoding="utf-8") as f:
                f.write(f"\n>>> on_step [{step_type}] run_id={run_id[:12]}…\n{clip}\n")
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

    user_msg = (args.message or "").strip() or DEFAULT_MESSAGE

    _debug_dir = _pmo_debug_log_dir()
    _file_ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    _debug_path = _debug_dir / f"pmo_copilot_{_file_ts}.txt"
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
    _open_pmo_file_debug(_debug_path, correlation_id=correlation_id, user_msg=user_msg)
    implicit = {"channel": "pmo_copilot_cli", "source": "run_pmo_copilot_skill.py"}
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

    mi = max(1, min(int(args.max_iterations), 64))
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
    try:
        with open(_debug_path, "a", encoding="utf-8") as _df:
            _df.write(
                "\n"
                + "=" * 72
                + "\n### 本轮 Final Answer（控制台也会打印）\n\n"
                + (ans or "").strip()
                + "\n"
            )
    except OSError:
        pass

    print("\n--- Final Answer ---\n", (ans or "").strip())
    await asyncio.sleep(0.5)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="PMO-Copilot：Skill YAML 驱动白名单；SKILL 注入 system，不长驻 user 消息")
    ap.add_argument("--skill", default=str(DEFAULT_SKILL), help="SKILL.md 路径")
    ap.add_argument("-m", "--message", default="", help="等同聊天框输入的一句话")
    ap.add_argument("--max-iterations", type=int, default=32, help="ReAct 上限（1～64）")
    args = ap.parse_args()
    try:
        return asyncio.run(_async_main(args))
    except KeyboardInterrupt:
        print("\n[pmo-copilot] 已中断", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
