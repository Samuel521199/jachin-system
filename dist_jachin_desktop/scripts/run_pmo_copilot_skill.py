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
      默认 **全流程 · 多 Agent 方案 B**：库未就绪或**今日尚未拉表**才 INIT/拉盘，再 FanOut→Publish

  python scripts/run_pmo_copilot_skill.py --analysis-only
      **仅分析 · 多 Agent**（库须已就绪；与无参命令的分析阶段相同）

  python scripts/run_pmo_copilot_skill.py --single-agent
      全流程但分析阶段回退 **单 Agent** ReAct（§1.2.1 七步，不含 Worker B/C FanOut）

  python scripts/run_pmo_copilot_skill.py --analysis-only --single-agent
      仅分析 · 单 Agent（库须已就绪）

  python scripts/run_pmo_copilot_skill.py --init
      仅 INIT 入库

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

_SIDECAR_EXE_NAMES = (
    "l3_node-x86_64-pc-windows-msvc.exe",
    "l3_node-aarch64-pc-windows-msvc.exe",
)


def _install_root_has_l3_source(root: Path) -> bool:
    return (root / "l3_node" / "__main__.py").is_file()


def _try_reexec_l3_sidecar(root: Path) -> None:
    """
    安装包仅有 scripts/skills_repo、无 l3_node 源码时，禁止用本机 python 直跑。
    改由 bin/l3_node-*.exe --run-pmo-copilot 在同一目录执行（侧车内已打包 l3_node）。
    """
    if getattr(sys, "frozen", False) or _install_root_has_l3_source(root):
        return
    try:
        import l3_node  # noqa: F401

        return
    except ImportError:
        pass
    for name in _SIDECAR_EXE_NAMES:
        exe = root / "bin" / name
        if not exe.is_file():
            continue
        try:
            if exe.stat().st_size < 64 * 1024:
                continue
        except OSError:
            continue
        import subprocess

        try:
            from l3_node.pmo_copilot_env import apply_pmo_copilot_console_quiet_defaults

            apply_pmo_copilot_console_quiet_defaults()
        except Exception:
            os.environ.setdefault("JACHIN_L3_DEEP_LOG", "0")
            os.environ.setdefault("LOG_LEVEL", "WARNING")
            os.environ.setdefault("JACHIN_LOG_LEVEL", "WARNING")
        env = os.environ.copy()
        env.setdefault("JACHIN_APP_ROOT", str(root))
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("PYTHONUNBUFFERED", "1")
        fwd = [a for a in sys.argv[1:] if a != "--run-pmo-copilot"]
        print(
            f"[pmo-copilot] 安装目录无 l3_node 源码，改由侧车执行: {exe.name}",
            flush=True,
        )
        raise SystemExit(
            subprocess.call([str(exe), "--run-pmo-copilot", *fwd], cwd=str(root), env=env)
        )
    print(
        "[pmo-copilot] 错误: 安装包环境不能用本机 python 直接运行本脚本（缺少 l3_node 模块）。\n"
        f"  请确认存在: {root / 'bin' / _SIDECAR_EXE_NAMES[0]}\n"
        "  或在仓库根目录用: python scripts/run_pmo_copilot_skill.py",
        file=sys.stderr,
    )
    raise SystemExit(1)


_env_root = (os.environ.get("JACHIN_APP_ROOT") or "").strip()
ROOT = Path(_env_root).resolve() if _env_root else Path(__file__).resolve().parent.parent
_try_reexec_l3_sidecar(ROOT)
if _install_root_has_l3_source(ROOT):
    sys.path.insert(0, str(ROOT))

# 开发机 PMO：在 import l3_node 子模块前标记子进程，避免误清常驻 L3 日志 / 抢 l3.lock
try:
    from l3_node.pmo_copilot_env import apply_pmo_copilot_console_quiet_defaults

    apply_pmo_copilot_console_quiet_defaults()
except Exception:
    os.environ.setdefault("JACHIN_PMO_COPILOT_RUN", "1")
    os.environ.setdefault("JACHIN_L3_CONSOLE", "0")

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", encoding="utf-8")
except Exception:
    pass

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

def _default_pmo_skill_path() -> Path:
    try:
        from l3_node.pmo_skill_paths import resolve_pmo_skill_md

        found = resolve_pmo_skill_md()
        if found is not None:
            return found
    except Exception:
        pass
    return ROOT / "skills_repo" / "pmo-copilot" / "SKILL.md"


DEFAULT_SKILL = _default_pmo_skill_path()

DEFAULT_MESSAGE = (
    "请严格按 PMO-Copilot SKILL v7 **分支 A · 单 Agent 回退路径**："
    "若 pmo_raw_records 未就绪则先 INIT；"
    "然后按 **§1.2.1 七步框架** 做 core:db_query 交叉分析，"
    "组装 §1.4 三表后 **双群** mcp:atom_lark_notifier。"
    "（默认 CLI 无 --single-agent 时已走多 Agent §1.2.2 Worker B/C，无需本提示。）"
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
    "请严格按 PMO-Copilot SKILL v7 **分支 A · 仅分析 · 单 Agent 回退**："
    "pmo_raw_records 镜像库已就绪。"
    "**禁止** mcp:atom_bi_project_context、core:fs_read、core:pmo_mirror_import、core:db_write。"
    "（默认无 --single-agent 时已走多 Agent §1.2.2；本路径为单 Agent 回退。）"
    "严格按 **§1.2.1 七步框架** 顺序执行 core:db_query（≤10 次），"
    "并尽量覆盖 §1.2.2 产品/开发/美术视图与字段；禁止捏造 null 字段。"
    "每步 Thought 写「本步产出」并**边查边填**三表 GFM 草稿行。"
    "Version Goal 全空时 📦 表仍须 GFM 占位行（⚠️ 原表字段全空）。"
    "组装 §1.4 三表后 **双群** mcp:atom_lark_notifier（native_table_card:true）。"
    "Final Answer 仅在双群 notifier 均 success 后 ≤3 句确认；禁止声称已推送。"
)


def _use_multi_agent_path(args: argparse.Namespace) -> bool:
    """默认分支 A 分析走多 Agent（§1.2.2 Worker B/C）；仅 --single-agent 回退单 Agent。"""
    if getattr(args, "init", False):
        return False
    return not getattr(args, "single_agent", False)


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
    raw_skill = (args.skill or "").strip()
    default_str = str(_default_pmo_skill_path())
    if not raw_skill or raw_skill == default_str or raw_skill == str(DEFAULT_SKILL):
        try:
            from l3_node.pmo_skill_paths import resolve_pmo_skill_md

            skill_path = resolve_pmo_skill_md() or Path(default_str).expanduser().resolve()
        except Exception:
            skill_path = Path(default_str).expanduser().resolve()
    else:
        try:
            from l3_node.pmo_skill_paths import resolve_pmo_skill_md

            skill_path = resolve_pmo_skill_md(explicit=raw_skill) or Path(raw_skill).expanduser().resolve()
        except Exception:
            skill_path = Path(raw_skill).expanduser().resolve()
    if not skill_path.is_file():
        print(f"[pmo-copilot] SKILL 不存在: {skill_path}", file=sys.stderr)
        print(
            "[pmo-copilot] 请确认以下任一路径存在 SKILL.md："
            "安装目录/skills_repo/pmo-copilot/、"
            "~/.jachin/l3_skill_cache/pmo-copilot/（L2 同步后）",
            file=sys.stderr,
        )
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
            print("  或运行无参全流程（库未就绪时会先 INIT）: python scripts/run_pmo_copilot_skill.py", file=sys.stderr)
            return 2
        allow_lower = {t.lower() for t in base_allow}
        for required in ("core:db_query",):
            if required not in allow_lower:
                base_allow.append(required)
        user_msg = (args.message or "").strip() or ANALYSIS_ONLY_MESSAGE
    elif getattr(args, "init", False):
        user_msg = (args.message or "").strip() or INIT_MESSAGE
    else:
        user_msg = (args.message or "").strip() or DEFAULT_MESSAGE

    use_multi = _use_multi_agent_path(args)

    if args.analysis_only:
        print(f"[pmo-copilot] 模式: 仅分析 · DB: {db_path}", flush=True)
        if use_multi:
            print("[pmo-copilot] 编排: 多 Agent 方案 B（§1.2.2 Worker B/C，与无参命令分析阶段一致）", flush=True)
        else:
            print("[pmo-copilot] 编排: 单 Agent 回退（--single-agent · §1.2.1）", flush=True)
    elif getattr(args, "init", False):
        print("[pmo-copilot] 模式: INIT（拉表 + mirror_import）", flush=True)
    elif use_multi:
        print("[pmo-copilot] 模式: 全流程 · 多 Agent 方案 B（库未就绪则先 INIT）", flush=True)
    else:
        print("[pmo-copilot] 模式: 全流程 · 单 Agent 回退（--single-agent）", flush=True)

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
        if use_multi:
            from l3_node.tools.pmo_db_tools import pmo_mirror_db_ready

            just_init = False
            if not args.analysis_only and not getattr(args, "init", False):
                if not pmo_mirror_db_ready():
                    print("[pmo-copilot] 镜像库未就绪，先执行 INIT…", flush=True)
                    init_rc = await _async_main_init_direct(
                        args,
                        _debug_path,
                        (args.message or "").strip() or INIT_MESSAGE,
                    )
                    if init_rc != 0:
                        return init_rc
                    if not pmo_mirror_db_ready():
                        print("[pmo-copilot] INIT 完成但 pmo_raw_records 仍不可用", file=sys.stderr)
                        return 1
                    print("[pmo-copilot] INIT 完成，继续多 Agent 分析…", flush=True)
                    just_init = True
            if args.analysis_only and getattr(args, "refresh_pull", False):
                print("[pmo-copilot] --refresh-pull：拉表写 md + mirror_import …", flush=True)
                init_rc = await _async_main_init_direct(
                    args,
                    _debug_path,
                    (args.message or "").strip() or INIT_MESSAGE,
                )
                if init_rc != 0:
                    return init_rc
                just_init = True
            return await _async_main_multi_agent(
                args,
                _debug_path,
                base_allow,
                skill_path,
                meta,
                skill_body,
                skip_pull_refresh=just_init,
            )
        if getattr(args, "init", False) and not (args.message or "").strip():
            return await _async_main_init_direct(args, _debug_path, user_msg)
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


async def _async_main_init_direct(
    args: argparse.Namespace,
    _debug_path: Path,
    user_msg: str,
) -> int:
    """INIT 确定性路径：Python 直调拉表 + mirror_import，不启动 ReAct。"""
    import asyncio
    import uuid

    from l3_node.pmo_copilot_debug_file import (
        append_pmo_debug_status,
        bootstrap_pmo_debug_main_agent,
        finalize_pmo_debug_log,
        init_pmo_debug_session,
    )
    from l3_node.pmo_init_runner import format_pmo_init_direct_summary, run_pmo_init_direct

    correlation_id = str(uuid.uuid4())
    init_pmo_debug_session(
        log_path=_debug_path,
        user_message=user_msg,
        correlation_id=correlation_id,
        max_iterations=1,
        mode_hint="init",
    )
    bootstrap_pmo_debug_main_agent(
        mode_hint="init",
        task_preview=user_msg[:200],
        max_iterations=1,
    )
    append_pmo_debug_status("⏳ INIT：开始拉表（sync_bi_project_context）…")
    print("[pmo-copilot] INIT：确定性路径（零 ReAct）— 拉表 + mirror_import …", flush=True)

    result = await asyncio.to_thread(run_pmo_init_direct)
    summary = format_pmo_init_direct_summary(result)
    append_pmo_debug_status(summary)
    print(f"[pmo-copilot] {summary.replace(chr(10), ' | ')}", flush=True)

    if str(result.get("status") or "").lower() != "ok":
        finalize_pmo_debug_log(str(result.get("message") or "INIT 失败"), aborted=True)
        return 1

    finalize_pmo_debug_log(str(result.get("message") or "INIT 完成"))
    print("\n--- INIT 完成 ---\n", str(result.get("message") or "").strip())
    await asyncio.sleep(0.3)
    return 0


async def _async_main_inner(
    args: argparse.Namespace,
    user_msg: str,
    _debug_path: Path,
    base_allow: list[str],
    skill_path: Path,
    meta: dict[str, Any],
    skill_body: str,
    *,
    force_mode_hint: str | None = None,
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

    mode_hint = force_mode_hint or "full"
    if force_mode_hint is None:
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
    if force_mode_hint == "init" or getattr(args, "init", False):
        implicit["pmo_init"] = True
    elif getattr(args, "analysis_only", False):
        implicit["pmo_analysis_only"] = True
        implicit["pmo_db_ready"] = True
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
        from l3_node.standalone_engine import create_engine_standalone

        engine = create_engine_standalone()
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


async def _async_main_multi_agent(
    args: argparse.Namespace,
    _debug_path: Path,
    base_allow: list[str],
    skill_path: Path,
    meta: dict[str, Any],
    skill_body: str,
    *,
    skip_pull_refresh: bool = False,
) -> int:
    """方案 B：FanOut 捞数 → Pipeline 审计 → run_agent 排版双群推送。"""
    from l3_node.intent_gateway.bundle import build_gateway_bundle
    from l3_node.primitives.tools.tool_pool import (
        assemble_tool_pool,
        expand_allowed_skills_with_implicit_sqlite_read,
        expand_allowed_skills_with_local_mcp,
    )
    from l3_node.pmo_copilot_debug_file import (
        append_pmo_debug_agent_begin,
        append_pmo_debug_phase_begin,
        append_pmo_debug_status,
        finalize_pmo_debug_log,
        init_pmo_debug_session,
        set_ma_debug_context,
        sync_pmo_debug_max_iterations,
    )
    from l3_node.pmo_multi_agent_orchestrator import (
        build_pmo_multi_agent_implicit_attribution,
        build_publisher_user_message,
        run_pmo_multi_agent_workflow,
    )

    correlation_id = str(uuid.uuid4())
    mi_phase3 = max(8, min(int(args.max_iterations), 24))
    sync_pmo_debug_max_iterations(mi_phase3)
    init_pmo_debug_session(
        log_path=_debug_path,
        user_message="[multi-agent] FanOut → Publish",
        correlation_id=correlation_id,
        max_iterations=mi_phase3,
        mode_hint="multi-agent",
    )

    print("[pmo-copilot] 模式: 多 Agent 方案 B（FanOut → Publish，已跳过交叉审计）", flush=True)

    allowlist_diag_source = list(base_allow)
    allow_lower = {t.lower() for t in base_allow}
    for _tid in (
        "core:db_query",
        "core:pmo_sprint_epic_report",
        "core:pmo_resolve_sprint",
        "core:pmo_release_epic_mapping",
    ):
        if _tid not in allow_lower:
            allowlist_diag_source.append(_tid)

    log = logging.getLogger("run_pmo_copilot_skill")

    try:
        from l3_node.standalone_engine import create_engine_standalone

        engine = create_engine_standalone()
    except Exception as e:
        print(f"[pmo-copilot] 引擎初始化失败: {e}", file=sys.stderr)
        return 2

    def _on_status(msg: str) -> None:
        append_pmo_debug_status(msg)
        print(f"[pmo-copilot] {msg}", flush=True)

    if getattr(args, "refresh_pull", False):
        refresh_pull = True
        pull_reason = "--refresh-pull 强制拉表"
    elif getattr(args, "no_refresh_pull", False) or skip_pull_refresh:
        refresh_pull = False
        pull_reason = "CLI 指定跳过拉表" if skip_pull_refresh else "--no-refresh-pull"
    else:
        from l3_node.pmo_init_runner import pmo_resolve_refresh_pull_markdown

        refresh_pull, pull_reason = pmo_resolve_refresh_pull_markdown()
    print(f"[pmo-copilot] 阶段零拉表: {'执行' if refresh_pull else '跳过'} — {pull_reason}", flush=True)
    workflow = await run_pmo_multi_agent_workflow(
        engine,
        parent_allowed_skills=allowlist_diag_source,
        on_status=_on_status,
        refresh_pull_markdown=refresh_pull,
    )

    if workflow.status == "failed":
        finalize_pmo_debug_log(workflow.format_summary())
        print(f"\n[pmo-copilot] 多 Agent 失败: {workflow.errors}", file=sys.stderr)
        return 1

    publisher_msg = build_publisher_user_message(workflow)
    try:
        from l3_node.pmo_worker_result_backfill import parse_worker_final_json
        from l3_node.tools.pmo_macro_dashboard import set_pmo_worker_d_push_cache

        wd_seed = parse_worker_final_json(workflow.worker_d) if workflow.worker_d else None
        set_pmo_worker_d_push_cache(wd_seed)
    except Exception:
        pass
    implicit = build_pmo_multi_agent_implicit_attribution()
    implicit["source"] = "run_pmo_copilot_skill.py"
    implicit["pmo_publisher_tool_lock"] = True

    bundle = build_gateway_bundle(
        user_input=publisher_msg,
        short_memory_context="",
        correlation_id=correlation_id,
        implicit_attribution=implicit,
    )

    expanded = expand_allowed_skills_with_implicit_sqlite_read(list(base_allow))
    expanded = expand_allowed_skills_with_local_mcp(expanded)
    publisher_allow: list[str] = []
    seen_pub: set[str] = set()
    for tid in list(expanded) + list(base_allow):
        low = tid.lower()
        if not (
            "pmo_macro_dashboard" in low
            or "atom_lark_notifier" in low
            or "lark_notifier" in low
        ):
            continue
        if low not in seen_pub:
            seen_pub.add(low)
            publisher_allow.append(tid)
    if not any("pmo_macro_dashboard" in t.lower() for t in publisher_allow):
        for tid in ("core:pmo_macro_dashboard_push", "core:pmo_macro_dashboard_preview"):
            if tid.lower() not in seen_pub:
                seen_pub.add(tid.lower())
                publisher_allow.insert(0, tid)
    if not publisher_allow:
        print(
            "[pmo-copilot] 未找到 macro_dashboard_push 或 atom_lark_notifier，无法阶段三推送",
            file=sys.stderr,
        )
        return 2

    tools = await assemble_tool_pool(
        allowed_skills=publisher_allow,
        gateway_bundle=bundle,
        logger=log,
        allowlist_diag_source=publisher_allow,
    )

    gateway_block = build_gateway_skill_inject(skill_path, meta, skill_body)
    from l3_node.pmo_report_format import PMO_DEMAND_TABLE_PUBLISHER_SPEC

    publisher_inject = (
        gateway_block
        + "\n\n### 多 Agent 阶段三（Publisher）\n"
        "**宏观看板（默认）**：优先 `Action: core:pmo_macro_dashboard_push` + `Action Input: {}`。\n"
        "  工具内完成 B/C 预取 + Worker D 📦 发版 Epic 映射、五列📊+三列👥、polish、主群+监控群双推；"
        "成功则 Final Answer 引用 message_id，**禁止**再调 notifier。\n"
        "  仅预览：`core:pmo_macro_dashboard_preview`。工具失败时 **一次** 回退下方手工路径。\n"
        "⛔ 禁止 core:db_query / mirror_import / bi_project_context。\n"
        "**兜底路径**（特殊版式或 push 失败）：mcp:atom_lark_notifier ×2。\n"
        "⛔ **禁止** `webhook_url`（PMO 用应用机器人 IM API，不用群 Webhook）。\n"
        "双群兜底推送须 **显式** `chat_id` + `native_table_card: true`：\n"
        "  ① 主群 chat_id = 环境变量 PMO_PRIMARY_CHAT_ID（留空则用飞书触发群 oc_…）\n"
        "  ② 监控群 chat_id = 环境变量 PMO_MONITOR_CHAT_ID（未配置则单群推送）\n"
        "须将三表 GFM **全文** 写入 markdown_content（勿放代码围栏内）。\n"
        + PMO_DEMAND_TABLE_PUBLISHER_SPEC
        + "\n"
    )

    from l3_node.agent_core import _build_system_prompt, run_agent
    from l3_node.routing.output_format_signals import analyze_output_format_signals

    fmt_sig = analyze_output_format_signals(publisher_msg)
    full_system = await _build_system_prompt(
        tools=tools,
        allow_delegate=False,
        prompt_cycle=None,
        recruitment_longform=False,
        hr_domain_prompt_active=False,
        prompt_style="slim_user_led" if fmt_sig.slim_system_prompt() else "full",
        pure_json_contract=False,
        gateway_inject=publisher_inject,
        safety_lock_user_text=publisher_msg,
        chief_advisor_mode=False,
        environment_report_block="",
        semantic_layer=None,
        experience_few_shots="",
        realtime_web_grounding_block="",
        domain_experts=None,
    )

    _on_status("阶段三：Publisher 排版发报（仅 Lark）…")
    append_pmo_debug_phase_begin(
        3,
        "排版发报 · Publisher",
        detail="run_agent：优先 macro_dashboard_push，兜底 GFM+notifier 双群",
    )
    set_ma_debug_context(
        phase=3,
        phase_label="排版发报",
        agent_label="Publisher",
        role_label="主编排 Agent · 仅 Lark",
        task_preview="将阶段一 JSON 填入战报（macro_dashboard_push 或三表 GFM），双群推送",
        max_iterations=mi_phase3,
    )
    append_pmo_debug_agent_begin(
        agent_label="Publisher",
        role_label="主编排 Agent · 仅 Lark",
        task_preview="三表 GFM 排版 + atom_lark_notifier 双群推送",
        max_iterations=mi_phase3,
    )
    _pmo_step = _make_pmo_on_step_writer(_debug_path)
    ans = await run_agent(
        publisher_msg,
        engine,
        max_iterations=mi_phase3,
        _allowed_skills_override=publisher_allow,
        _system_prompt_override=full_system,
        gateway_context_bundle=bundle,
        implicit_attribution=implicit,
        on_step=_pmo_step,
    )
    try:
        from l3_node.pmo_copilot_debug_file import append_pmo_debug_agent_finish

        append_pmo_debug_agent_finish(
            agent_label="Publisher",
            ok=bool((ans or "").strip()),
            result_preview=str(ans or "")[:300],
        )
    except Exception:
        pass
    finalize_pmo_debug_log(ans or workflow.format_summary())
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
        help="跳过拉表/入库；分析阶段与无参命令相同（默认多 Agent §1.2.2，须库已就绪）",
    )
    ap.add_argument(
        "--multi-agent",
        action="store_true",
        default=False,
        help="显式启用多 Agent（默认：非 --single-agent 且非 --init 时已启用，通常无需指定）",
    )
    ap.add_argument(
        "--single-agent",
        action="store_true",
        help="分析阶段回退单 Agent ReAct（§1.2.1）；无参命令与 --analysis-only 默认均为多 Agent",
    )
    ap.add_argument(
        "--init",
        action="store_true",
        help="仅 INIT：拉表 + core:pmo_mirror_import 镜像入库",
    )
    ap.add_argument(
        "--no-refresh-pull",
        action="store_true",
        help="多 Agent 前强制跳过拉表（默认仅当今日未拉盘/库空时才拉）",
    )
    ap.add_argument(
        "--refresh-pull",
        action="store_true",
        help="强制重新拉表+mirror_import（默认今日已入库则跳过）",
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
