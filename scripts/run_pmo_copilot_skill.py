#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PMO-Copilot v6：CLI「一句话点火」—— 进程内拉起 L3 引擎并 ``run_agent``。

默认加载 ``skills_repo/pmo-copilot/SKILL.md``（SQLite 提取入库 → db_query 分析 → Lark 推送）。

- **工具白名单 SSOT**：从 SKILL frontmatter（``mcp_tools`` / ``native_tools`` / ``tools[].prefer``）解析。
- **Skill 注入 system**：正文 + persona 经 ``gateway_inject`` 写入 system；user 消息仅为短点火句。
- **信道**：``pmo_copilot_cli``（启用 PMO 宿主守卫）。

用法（仓库根）::

  python scripts/run_pmo_copilot_skill.py
  python scripts/run_pmo_copilot_skill.py --init
  python scripts/run_pmo_copilot_skill.py -m "分支 A：查 DB 生成宏观看板并双群推送"

调试日志：``~/.jachin/jachin_debug/健康skill/pmo_copilot_*.txt``

前置：``.env`` LLM Key；``atom_bi_project_context`` / ``atom_lark_notifier`` MCP 已配置；
``core:db_query`` / ``core:db_write`` 已注册（``l3_node/tools/pmo_db_tools.py``）。
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

DEFAULT_PMO_DB = Path.home() / ".jachin" / "workspace" / "pmo_db.sqlite"
PMO_LARK_PULL_DIR = (Path.home() / ".jachin" / "workspace" / "pmo_lark_pull").resolve()
PMO_STAGING_DIR = (Path.home() / ".jachin" / "workspace" / "pmo_staging").resolve()

PMO_INIT_BATCH_ROWS = 20
PMO_INIT_MAX_ITERATIONS = 270
PMO_BRANCH_A_MAX_ITERATIONS = 28
PMO_BRANCH_A_ITERATIONS_CAP = 64

MESSAGE_BRANCH_A = (
    "请严格按 system 中的 PMO-Copilot v6 SKILL（pmo-copilot-enterprise）执行 **分支 A · 宏观看板**："
    "1) 若 ~/.jachin/workspace/pmo_db.sqlite 尚无数据，先走 **INIT**（fs_write JSON + pmo_import_json）；"
    "2) 否则用 **core:db_query** 按当前 work_cycle 查询四张业务表（≥3 次 SQL）；"
    "3) 分步交叉分析后组装 §11 三表战报；"
    "4) **mcp:atom_lark_notifier** 双群推送（native_table_card: true）；Final Answer ≤3 句。"
    "禁止回退 v5「全量 fs_read 12 张表再分析」旧路径。"
)

MESSAGE_INIT = (
    "请严格按 system 中的 PMO-Copilot v6.1.1 SKILL 执行 **INIT · 微批次 Extract→NDJSON→Python Import**："
    f"1) mcp:atom_bi_project_context 拉 §9 全部 wiki_urls（**仅 1 次**；落盘 SSOT `{PMO_LARK_PULL_DIR}`，"
    "**禁止** output_dir 指向仓库根）；fs_read manifest 建队列（**files[] 用 basename，禁止臆造文件名**）；"
    f"2) **逐张 md**：fs_read(manifest basename) → **每批 {PMO_INIT_BATCH_ROWS} 行** "
    "fs_write `pmo_staging/{view_id}_partN.ndjson` → **立即** pmo_import_json → 再下一批（"
    "**禁止**连写 part1/2/3 再 import）；"
    "3) import 若 partial/parse_warnings：继续下一批，**禁止**二次拉表、禁止 fs_read pmo_db.sqlite；"
    "4) flow_progress_note 对照 **SKILL §附录 A**；pmo_people 仅 id/name/dept/role/is_active；"
    "5) 12 张完成后 **core:pmo_init_gap_report**；对 missing_files 再 extract→import；"
    "6) init_complete 后 Final Answer 含各表 row_count。"
)


def _pmo_iterations_hard_cap(*, init_mode: bool) -> int:
    return PMO_INIT_MAX_ITERATIONS if init_mode else PMO_BRANCH_A_ITERATIONS_CAP


def _default_max_iterations(*, init_mode: bool) -> int:
    raw = (os.environ.get("JACHIN_PMO_MAX_REACT_ITERATIONS") or "").strip()
    cap = _pmo_iterations_hard_cap(init_mode=init_mode)
    if raw.isdigit():
        return max(1, min(int(raw), cap))
    return PMO_INIT_MAX_ITERATIONS if init_mode else PMO_BRANCH_A_MAX_ITERATIONS


def _pmo_debug_log_dir() -> Path:
    """~/.jachin/jachin_debug/健康skill（例：C:\\Users\\Samuel\\.jachin\\jachin_debug\\健康skill）。"""
    return Path.home() / ".jachin" / "jachin_debug" / "健康skill"


def _open_pmo_file_debug(path: Path, *, correlation_id: str, user_msg: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    path.write_text(
        "\n".join(
            [
                "PMO-Copilot v6 / run_pmo_copilot_skill.py — 文件调试日志（以下不写入控制台）",
                f"生成时间: {ts}",
                f"correlation_id: {correlation_id}",
                f"调试文件路径: {path}",
                "",
                "【本文件说明 · v6 DB 架构】",
                "1) 拉表：atom_bi_project_context 的 wiki_urls / files[]。",
                "2) 入库：core:fs_read + core:db_write（提取层）。",
                "3) 分析：core:db_query（分析层）→ 战报 markdown。",
                "4) 推送：atom_lark_notifier 的 status。",
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


def build_pmo_runtime_hints(*, init_mode: bool) -> str:
    """INIT/分支 A 运行时路径 SSOT，避免相对路径落进 workspace 或臆造 md 文件名。"""
    design_doc = (ROOT / "docs" / "architecture" / "PMO_DB_REFACTOR_DESIGN.md").resolve()
    pull_ws = PMO_LARK_PULL_DIR
    staging_ws = PMO_STAGING_DIR
    manifest_ws = pull_ws / "00_SYNC_MANIFEST.json"
    lines = [
        "### PMO 运行时路径（宿主注入 · 必读）",
        "",
        f"- **仓库根（JACHIN_APP_ROOT，开发机）**: `{ROOT}`",
        "- **flow_progress_note SSOT**：**SKILL §附录 A**（已内嵌 system，**禁止** fs_read 外链流程文档）",
        f"- **架构文档（开发机可选 fs_read）**: `{design_doc}`",
        f"- **拉表落盘（唯一 SSOT · INIT 读 md 用此目录）**: `{pull_ws}`",
        f"- **manifest 绝对路径**: `{manifest_ws}`",
        "- **拉表范围**：PMO INIT **仅 SKILL §9 十二 view**（12 个 md + manifest）；MCP 会自动过滤 `tblL2gXBH`/发版记录/平台链接等默认噪声种子",
        f"- **SQLite DB**: `{DEFAULT_PMO_DB}`",
        f"- **staging JSON 目录（fs_write 须写此处）**: `{staging_ws}`",
        "",
        "**fs_read / fs_write 路径（禁止仓库根、禁止 ~/. 字符串未展开时的臆造路径）**：",
        f"- 读 manifest：`{{\"file_path\": \"{manifest_ws}\"}}` 或 `pmo_lark_pull/00_SYNC_MANIFEST.json`",
        f"- 读 md：`{{\"file_path\": \"{pull_ws}\\\\<manifest.files[i] basename>\"}}`（**仅 basename**，勿臆造 `02_产品方任务_…`）",
        f"- 写 staging：`pmo_staging/{{view_id}}_partN.ndjson` 或 `{staging_ws}\\\\…`",
        "",
        "**读 md 纪律**：",
        "1. `atom_bi_project_context` Observation 的 `output_dir` + `files[]` 为唯一 SSOT；",
        "2. `files[]` 在 workspace 模式下为 **basename**；fs_read 用 `output_dir/basename` 或 `{manifest_ws 同目录}/basename`；",
        "3. **禁止**臆造文件名；若不确定，先 fs_read `00_SYNC_MANIFEST.json`；",
        "4. 调试日志里的 `[on_step 截断]` **仅限制日志显示**，不是 md 文件被截断；",
        "5. 打包 L3 无仓库：**勿** fs_read `docs/pmo_bmo_plugin/…`；流程说明已在 Skill 正文。",
    ]
    if init_mode:
        lines.extend(
            [
                "",
                "**INIT · 微批次 Extract → NDJSON → Python Import（严格交替）**：",
                f"- 每张 md 每批：**fs_write partN → 立即 pmo_import_json partN → 再 fs_write partN+1**；",
                f"- **禁止**连续 fs_write 多个 part 后再 import（如 part1+part2+part3 连写）；",
                f"- 每批 **{PMO_INIT_BATCH_ROWS} 行**；同 view 多批 **upsert 叠加**；",
                "- import 返回 partial/parse_warnings 时继续下一批；",
                "- **禁止 INIT 使用 core:db_write**；**禁止** import 失败后二次拉表或 fs_read pmo_db.sqlite；",
                "- 12 张完成后 **pmo_init_gap_report**；对 missing_files 再 extract→import；",
                "- Observation 去重时仍须写 JSON + import，勿 skip；",
                "",
                "**INIT 完成标准**：",
                "- pmo_init_gap_report：`missing_count=0` 且四表 table_totals 均 > 0；",
                "- Final Answer 含各表 row_count。",
            ]
        )
    return "\n".join(lines)


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

    if (args.message or "").strip():
        user_msg = args.message.strip()
    elif args.init:
        user_msg = MESSAGE_INIT
    else:
        user_msg = MESSAGE_BRANCH_A

    skill_version = str(meta.get("version") or "?").strip()
    skill_name = str(meta.get("name") or "pmo-copilot").strip()
    print(
        f"[pmo-copilot] Skill: {skill_name} v{skill_version} @ {skill_path}",
        flush=True,
    )
    print(
        f"[pmo-copilot] 模式: {'INIT 入库' if args.init and not (args.message or '').strip() else '自定义/分支 A'}",
        flush=True,
    )
    db_exists = DEFAULT_PMO_DB.is_file()
    init_mode = bool(args.init and not (args.message or "").strip())
    if init_mode:
        PMO_LARK_PULL_DIR.mkdir(parents=True, exist_ok=True)
        PMO_STAGING_DIR.mkdir(parents=True, exist_ok=True)
        os.environ["JACHIN_PMO_LARK_PULL_DIR"] = str(PMO_LARK_PULL_DIR)
    else:
        os.environ.pop("JACHIN_PMO_LARK_PULL_DIR", None)
    staging_dir = PMO_STAGING_DIR
    print(
        f"[pmo-copilot] DB: {DEFAULT_PMO_DB} ({'已存在' if db_exists else '尚未创建，INIT 或分支 A 会先入库'})",
        flush=True,
    )
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

    tool_ids = {str(t.get("id") or "") for t in tools}
    missing_tools = [t for t in base_allow if t not in tool_ids]
    if missing_tools:
        print(
            "[pmo-copilot] 警告：SKILL 声明但当前引擎未注册的工具（v6 可能无法完整运行）：",
            ", ".join(missing_tools),
            file=sys.stderr,
        )
    print(
        f"[pmo-copilot] 可用工具 ({len(tool_ids)}): {', '.join(sorted(tool_ids)) or '(无)'}",
        flush=True,
    )

    gateway_block = build_gateway_skill_inject(skill_path, meta, skill_body)
    gateway_block = gateway_block + "\n\n" + build_pmo_runtime_hints(
        init_mode=bool(args.init and not (args.message or "").strip())
    )

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
    from l3_node.pmo_copilot_debug_file import sync_pmo_debug_max_iterations

    init_mode = bool(args.init and not (args.message or "").strip())
    mi = max(1, min(int(args.max_iterations), _pmo_iterations_hard_cap(init_mode=init_mode)))
    sync_pmo_debug_max_iterations(mi)
    _pmo_step = _make_pmo_on_step_writer(_debug_path)
    ans = await run_agent(
        user_msg,
        engine,
        max_iterations=mi,
        _allowed_skills_override=allowlist_diag_source,
        _system_prompt_override=full_system,
        gateway_context_bundle=bundle,
        implicit_attribution={
            **implicit,
            "skill_file": str(skill_path),
            "skill_name": str(meta.get("name") or ""),
            "skill_version": str(meta.get("version") or ""),
            "pmo_db_path": str(DEFAULT_PMO_DB),
            "pmo_init_mode": bool(args.init and not (args.message or "").strip()),
            "jachin_app_root": str(ROOT),
        },
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
    ap = argparse.ArgumentParser(
        description="PMO-Copilot v6：加载 skills_repo/pmo-copilot/SKILL.md，注入 system 后 run_agent"
    )
    ap.add_argument(
        "--skill",
        default=str(DEFAULT_SKILL),
        help=f"SKILL.md 路径（默认 {DEFAULT_SKILL}）",
    )
    ap.add_argument("-m", "--message", default="", help="自定义点火句（覆盖 --init / 默认分支 A）")
    ap.add_argument(
        "--init",
        action="store_true",
        help="INIT 全量提取入库（拉表 → fs_read → db_write）；未指定 -m 时使用 INIT 默认句",
    )
    ap.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help=f"ReAct 上限（默认 INIT={PMO_INIT_MAX_ITERATIONS} / 分支 A={PMO_BRANCH_A_MAX_ITERATIONS}，或 env JACHIN_PMO_MAX_REACT_ITERATIONS）",
    )
    args = ap.parse_args()
    if args.max_iterations is None:
        args.max_iterations = _default_max_iterations(init_mode=bool(args.init))
    try:
        return asyncio.run(_async_main(args))
    except KeyboardInterrupt:
        print("\n[pmo-copilot] 已中断", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
