"""
PMO/BMO 主技能 — Pipeline A

0. （可选）operation=export_pmo_tables：六张 K11 多维表 → JSON ~/.jachin/client_volumes/PMO/raw、
   MD docs/pmo_bmo_plugin/raw、DuckDB ~/.jachin/client_volumes/PMO/duckdb/pmo.duckdb
1. mcp:atom_pmo_lark_doc sync — Wiki 全量同步 → project_progress_daily/ 等
2. mcp:atom_pmo_knowledge_base — 分块 ingest → corpus/

**大需求对齐（默认脚本生成 MD，Agent 可润色）**

- `get_pmo_big_requirement_alignment_task_spec()`：任务元数据与 `agent_instructions`。
- `get_pmo_dashboard_three_cards_task_spec()`：三张仪表盘飞书卡片（需求战报/资源负荷/版本发布）**UI/UX 强规范**，与 `tool_data_visualizer.send_pmo_three_dashboard_cards` 的 JSON 构建一致。
- `build_pmo_big_requirement_alignment_context()`：raw 路径、三表 JSON 就绪检查、输出路径。
- `run_pmo_big_requirement_alignment_task()`：可选先 `export_pmo_tables`，再调用 `write_pmo_big_requirement_alignment_markdown_from_raw` 写入 `PMO_大需求对齐.md`（不新增 MCP）。

**按人统计任务（产品/开发/美术 + 干系人表，脚本写 `PMO_人员任务统计.md`）**

- `get_pmo_person_task_stats_task_spec()` / `build_pmo_person_task_stats_context()` / `run_pmo_person_task_stats_task()`
- CLI：`python -m l3_node.primitives.skills.pmo_bmo.main_skill person-stats`

**大需求 ↔ 执行人员（按 dev_by_assignee / art_by_designer 视图 NL 匹配，脚本写 `PMO_需求人员参与明细.md`）**

- `write_pmo_requirement_participants_markdown_from_raw()`：读取 coarse + `dev_tasks_by_assignee` + `art_tasks_by_designer`，将每人任务归到对应大需求下并估算完成度。
- `person-stats` / `full` 第三步在 raw 齐全时会 **额外尝试** 生成该文件；独立跑：`python -m l3_node.primitives.skills.pmo_bmo.main_skill req-participants`

**领导视图与周负荷摘要（脚本写 `PMO_领导视图与周负荷摘要.md`，供飞书卡片文案与多维表提纯对齐）**

- `write_pmo_leadership_weekly_brief_markdown_from_raw()`：汇总本周周负荷、细需求全表（按优先级）、大需求主线（可选 coarse）、Sprint→需求、产品责任人→细需求、可粘贴 lark_md 摘录块。
- 在 `person-stats` / `full` 第三步中，在三张主 MD 之后 **自动生成**（依赖 fine + dev_core + art；coarse 缺失时跳过主线小节）。

**业务一条龙（①六表拉取 → ②③生成 docs/pmo_bmo_plugin/output → ④仪表盘提纯 CSV + 可选 Lark）**

- `run_pmo_full_business_pipeline()`；CLI：`python -m l3_node.primitives.skills.pmo_bmo.main_skill full`（可加 `--skip-output-docs`：仅 ①+④，不跑 ②③）
- **仅根据已有 raw 生成 output 文档（与拉表解耦）**：`run_pmo_output_docs_from_raw()`；CLI：`output-docs`

配置: config/skills/com.jachin.pmo.bmo/pmo_bmo.yaml（lark、pipeline.export_scheduled_tables）。
凭证写在 pmo_bmo.yaml 的 lark 即可；atom_pmo_lark_doc 会优先 MCP/环境变量，缺失或为 ${...} 占位时回退读该 YAML。

大需求表（req_march_coarse）为云文档内表格时：优先 **pipeline.pmo_export.docx_document_ids.req_march_coarse**，其次环境变量 **PMO_REQ_MARCH_COARSE_DOCX_ID**，最后使用内置默认 **PMO_DEFAULT_REQ_MARCH_COARSE_DOCX_ID**（K11「需求表3月」云文档 token，可被前两步覆盖）。

单独测「只抓六表」：在项目根执行
  python -m l3_node.primitives.skills.pmo_bmo.main_skill
对齐任务单（导出 + 打印 JSON 上下文）：  python -m l3_node.primitives.skills.pmo_bmo.main_skill align
人员任务统计任务单：  python -m l3_node.primitives.skills.pmo_bmo.main_skill person-stats
  大需求执行人员明细（需 coarse + 按人/按设计人 三 JSON）：python -m l3_node.primitives.skills.pmo_bmo.main_skill req-participants
**业务一条龙（推荐）**：按顺序执行 ①六表拉取 → ②大需求进度任务单 → ③按人任务分配任务单
  python -m l3_node.primitives.skills.pmo_bmo.main_skill full
  仅生成 docs/pmo_bmo_plugin/output（依赖已有 ~/.jachin/.../PMO/raw，不拉表）：python -m l3_node.primitives.skills.pmo_bmo.main_skill output-docs
  一条龙但跳过文档环节（只拉表+仪表盘）：python -m l3_node.primitives.skills.pmo_bmo.main_skill full --skip-output-docs
  仪表盘提纯 CSV（~/.jachin/.../PMO/output）并可选同步 Lark 多维表；同步成功后可选发卡片：若 **pmo_dashboard_three_cards.enabled** 则只发三张新仪表盘卡；否则可按 **pmo_battle_report_card** 发旧版 K11 单卡（图表数据默认从 Lark 多维表读取）：
  python -m l3_node.primitives.skills.pmo_bmo.main_skill push-dashboard
  仅写 CSV、不同步：python -m l3_node.primitives.skills.pmo_bmo.main_skill push-dashboard --no-sync
  不发 VChart 战报卡片：python -m l3_node.primitives.skills.pmo_bmo.main_skill push-dashboard --no-battle-report
  已有提纯 CSV、只跑「同步多维表 + 战报卡片」（不写 CSV）：python -m l3_node.primitives.skills.pmo_bmo.main_skill push-dashboard --sync-only
  多维表已同步好、只发战报消息（不写 CSV、不同步表）：python -m l3_node.primitives.skills.pmo_bmo.main_skill battle-report
    （pmo_dashboard_three_cards.enabled=true 时发三张新卡；否则发旧版 K11 单卡）
  只发三张仪表盘卡片（需求战报 VChart + 资源负荷表 + 版本发布，全读多维表）：python -m l3_node.primitives.skills.pmo_bmo.main_skill three-dashboard-cards
"""
from __future__ import annotations

import contextlib
import csv
from difflib import SequenceMatcher
import json
import logging
import math
import os
import re
import sys
import threading
import time
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# PMO 技能诊断日志目录（Windows 盘符路径；不存在则自动创建）
PMO_SKILL_LOG_DIR = Path(r"D:\zzz\PMO\运行日志")

_pmo_skill_file_handler: logging.Handler | None = None
_pmo_skill_log_file: Path | None = None
_pmo_cli_stderr_handler: logging.Handler | None = None


def _redact_for_log(obj: Any) -> Any:
    """日志中遮蔽密钥类字段。"""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if lk in ("app_secret", "secret", "password", "authorization") and isinstance(v, str) and v:
                out[k] = "***"
            else:
                out[k] = _redact_for_log(v)
        return out
    if isinstance(obj, list):
        return [_redact_for_log(x) for x in obj]
    return obj


def _pmo_skill_yaml_resolved_path(project_root: Path) -> str | None:
    for p in (
        project_root / "config" / "skills" / "com.jachin.pmo.bmo" / "pmo_bmo.yaml",
        Path.home() / ".jachin" / "config" / "skills" / "com.jachin.pmo.bmo" / "pmo_bmo.yaml",
    ):
        if p.is_file():
            return str(p.resolve())
    return None


def _configure_pmo_cli_stdio_line_buffering() -> None:
    """尽量将 stdout/stderr 设为行缓冲，减少「结束时才一次性刷出」。"""
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(line_buffering=True)
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(line_buffering=True)
    except Exception:
        pass


def _ensure_pmo_cli_stderr_streaming() -> None:
    """
    CLI 模式下将 INFO+ 日志实时写入 stderr（每条 emit 后 flush）。
    仅挂文件 Handler 时，终端几乎无输出，看起来像结束时才涌出。
    """
    global _pmo_cli_stderr_handler
    if _pmo_cli_stderr_handler is not None:
        return

    class _FlushingStderrHandler(logging.StreamHandler):
        def emit(self, record: logging.LogRecord) -> None:
            super().emit(record)
            self.flush()

    _pmo_cli_stderr_handler = _FlushingStderrHandler(sys.stderr)
    _pmo_cli_stderr_handler.setLevel(logging.INFO)
    _pmo_cli_stderr_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    for name in (
        "pmo_bmo_skill",
        __name__,
        "l3_node.primitives.mcp.mcp_tools.pmo_bmo.tool_pmo_bitable_export",
        "l3_node.primitives.mcp.mcp_tools.pmo_bmo.tool_lark_doc",
    ):
        logging.getLogger(name).addHandler(_pmo_cli_stderr_handler)


@contextlib.contextmanager
def _pmo_heartbeat_while(label: str, interval_sec: float = 15.0):
    """
    阻塞型 Lark 调用（如 export_pmo_tables 内 docx 逐格拉取）可能长时间无日志；
    在独立线程中按间隔向 stderr + pmo_bmo_skill 打心跳，避免「卡住十分钟」的观感。
    """
    stop = threading.Event()
    t0 = time.monotonic()

    def _loop() -> None:
        n = 0
        while not stop.wait(timeout=interval_sec):
            n += 1
            elapsed = int(time.monotonic() - t0)
            msg = (
                f"【进度】{label} 仍在执行，已等待约 {elapsed}s（第 {n} 次心跳；"
                "大文档表格需逐格请求 Lark API，请见 tool_pmo_bitable_export 的单元格进度）"
            )
            logging.getLogger("pmo_bmo_skill").info(msg)
            try:
                print(msg, file=sys.stderr, flush=True)
            except Exception:
                pass

    th = threading.Thread(target=_loop, name="pmo_heartbeat", daemon=True)
    th.start()
    try:
        yield
    finally:
        stop.set()
        th.join(timeout=2.0)


def _ensure_pmo_skill_file_logging() -> Path:
    """为本进程挂载一份 UTF-8 文件日志（只挂一次）。"""
    global _pmo_skill_file_handler, _pmo_skill_log_file
    if _pmo_skill_file_handler is not None and _pmo_skill_log_file is not None:
        return _pmo_skill_log_file

    PMO_SKILL_LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    _pmo_skill_log_file = PMO_SKILL_LOG_DIR / f"pmo_bmo_skill_{ts}.log"
    _pmo_skill_file_handler = logging.FileHandler(_pmo_skill_log_file, encoding="utf-8")
    _pmo_skill_file_handler.setLevel(logging.DEBUG)
    _pmo_skill_file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    for name in (
        "pmo_bmo_skill",
        __name__,
        "l3_node.primitives.mcp.mcp_tools.pmo_bmo.tool_pmo_bitable_export",
        "l3_node.primitives.mcp.mcp_tools.pmo_bmo.tool_lark_doc",
    ):
        lg = logging.getLogger(name)
        lg.setLevel(logging.DEBUG)
        lg.addHandler(_pmo_skill_file_handler)
    logging.getLogger("pmo_bmo_skill").debug(
        "已挂载 FileHandler 到 loggers: pmo_bmo_skill, %s, tool_pmo_bitable_export, tool_lark_doc",
        __name__,
    )
    return _pmo_skill_log_file


def _log_pmo_skill_banner(log_file: Path, title: str, **ctx: Any) -> None:
    lg = logging.getLogger("pmo_bmo_skill")
    lg.info("======== %s ========", title)
    lg.info("log_file=%s", log_file)
    lg.info("cwd=%s", os.getcwd())
    lg.info("python=%s", sys.version.replace("\n", " "))
    lg.info("executable=%s", sys.executable)
    if "__main__" in sys.modules:
        lg.info("main_module=%s", getattr(sys.modules["__main__"], "__file__", ""))
    for k in sorted(ctx.keys()):
        lg.info("%s=%s", k, ctx[k])


def _log_pmo_skill_json(lg: logging.Logger, label: str, payload: Any) -> None:
    try:
        lg.info("%s\n%s", label, json.dumps(_redact_for_log(payload), ensure_ascii=False, indent=2))
    except Exception as e:
        lg.warning("%s (json 序列化失败: %s) raw=%r", label, e, payload)


def _log_pmo_skill_cli_entry(argv: list[str], log_path: Path) -> str:
    """
    记录 CLI 完整 argv 与路由结果。
    默认无参数 = 仅六表导出，不会进入 align / person-stats，故日志中不会出现那两类任务单函数名。
    """
    lg = logging.getLogger("pmo_bmo_skill")
    lg.info("======== PMO skill CLI 路由诊断 ========")
    lg.info("PMO_SKILL_LOG_DIR=%s", PMO_SKILL_LOG_DIR.resolve())
    lg.info("当前日志文件=%s", log_path.resolve())
    lg.info("sys.argv=%s", argv)
    lg.info("len(sys.argv)=%s", len(argv))
    a1 = (argv[1] if len(argv) > 1 else "").strip()
    a1l = a1.lower()
    if a1l in ("full", "all", "pipeline", "pmo-full"):
        mode = "full"
        _rest = [x.strip().lower() for x in argv[2:]]
        _skip_od = "--skip-output-docs" in _rest
        lg.info(
            "路由模式=【full】→ 将调用 run_pmo_full_business_pipeline(skip_output_docs=%s)："
            "[1]六表拉取 → [2+3]output 文档（run_pmo_output_docs_from_raw，可跳过）→ [4]仪表盘提纯+Lark",
            _skip_od,
        )
    elif a1l in ("output-docs", "pmo-output-docs", "gen-output-md", "output-md"):
        mode = "output_docs"
        lg.info(
            "路由模式=【output_docs】→ 将调用 run_pmo_output_docs_from_raw()："
            "仅根据已有 PMO/raw 生成 docs/pmo_bmo_plugin/output（不拉表、不写 CSV、不发 Lark）"
        )
    elif a1l in ("align", "big-align", "pmo-align"):
        mode = "align"
        lg.info("路由模式=【align】→ 将调用 run_pmo_big_requirement_alignment_task()（大需求对齐任务单）")
    elif a1l in ("person-stats", "by-person", "pmo-person"):
        mode = "person_stats"
        lg.info("路由模式=【person_stats】→ 将调用 run_pmo_person_task_stats_task()（按人任务统计任务单）")
    elif a1l in ("req-participants", "req-people", "pmo-req-participants"):
        mode = "req_participants"
        lg.info(
            "路由模式=【req_participants】→ 将调用 run_pmo_requirement_participants_report_task()（大需求↔执行人明细 MD）"
        )
    elif a1l in ("push-dashboard", "dashboard-push", "pmo-push"):
        mode = "push_dashboard"
        _rest = [x.strip().lower() for x in argv[2:]]
        _sync_only = "--sync-only" in _rest or "--skip-write-csv" in _rest
        lg.info(
            "路由模式=【push_dashboard】→ 将调用 run_pmo_dashboard_push()（%s）",
            "已有 CSV：仅同步 Lark + 可选战报卡片" if _sync_only else "提纯 CSV → PMO/output，可选同步 Lark",
        )
    elif a1l in ("battle-report", "k11-card", "pmo-battle-report", "send-battle-report"):
        mode = "battle_report"
        lg.info(
            "路由模式=【battle_report】→ 将调用 run_pmo_battle_report_card_only()："
            "若 pmo_dashboard_three_cards.enabled 则三张新卡，否则旧版 K11 单卡（不写 CSV、不同步表）"
        )
    elif a1l in ("three-dashboard-cards", "pmo-three-cards", "dashboard-three-cards"):
        mode = "three_dashboard_cards"
        lg.info(
            "路由模式=【three_dashboard_cards】→ 将调用 run_pmo_three_dashboard_cards_only()："
            "连发三张仪表盘卡片（读多维表；不写 CSV、不同步表）"
        )
    elif a1 == "":
        mode = "export_only"
        lg.info("路由模式=【export_only】→ 将调用 run_pmo_export_scheduled_tables_only()（仅六表导出）")
        lg.info(
            "重要: 默认命令【不会】执行大需求对齐或人员统计；"
            "这两项是独立子命令，仅在 align / person-stats 时进入对应函数并打任务单日志。"
        )
    else:
        mode = "export_only_fallback"
        lg.warning("路由模式=【export_only】首参 argv[1]=%r 未识别，仍只执行六表导出", a1)

    lg.info(
        "子命令速查: 无参=仅导出 | full=业务一条龙(①→②③→④) | output-docs=仅生成 output MD(依赖已有 raw) | "
        "align=大需求进度任务单 | person-stats=按人分配任务单 | req-participants=大需求执行人明细 MD | "
        "push-dashboard=仪表盘 CSV+Lark | battle-report=仅战报卡片(读多维表→图→发群) | "
        "three-dashboard-cards=三张仪表盘卡片(需求战报+资源负荷+版本发布)"
    )
    lg.info(
        "任务单说明: person-stats / full 第三步会尝试自动生成 `PMO_人员任务统计.md`（raw 规则汇总）。"
        "大需求对齐 `PMO_大需求对齐.md` 由 `write_pmo_big_requirement_alignment_markdown_from_raw` 自动生成（align 子命令）。"
    )
    return mode


def _log_pmo_skill_export_only_scope(slg: logging.Logger) -> None:
    """标明「仅导出」与其它 skill 能力的关系，避免日志误解。"""
    slg.info("---------- 本函数范围：run_pmo_export_scheduled_tables_only ----------")
    slg.info("【会执行】run_pmo_lark_doc(operation=export_pmo_tables) → 六表 JSON/MD/DuckDB")
    slg.info("【不会执行】run_pmo_big_requirement_alignment_task（大需求对齐任务单）")
    slg.info("【不会执行】run_pmo_person_task_stats_task（按人任务统计任务单）")
    slg.info("【不会执行】run_pmo_knowledge_sync（Wiki sync + knowledge_base ingest）")
    slg.info(
        "若需要上述任务单日志: python -m l3_node.primitives.skills.pmo_bmo.main_skill align "
        "或 python -m l3_node.primitives.skills.pmo_bmo.main_skill person-stats"
    )


_PMO_INTENT_PATTERN = re.compile(
    r"PMO|BMO|项目总监|知识库同步|拉取\s*(PRD|需求)|同步\s*Wiki|pmo\s*sync",
    re.IGNORECASE,
)


def is_pmo_bmo_intent(text: str) -> bool:
    """判断是否触发 PMO 知识库同步类意图（供 Agent 预检）。"""
    if not text or not isinstance(text, str):
        return False
    return bool(_PMO_INTENT_PATTERN.search(text.strip()))


# 与 tool_pmo_bitable_export.PMO_SCHEDULED_BITABLES 中 slug 一致（用于 raw 完整性检查）
PMO_SCHEDULED_EXPORT_SLUGS: tuple[str, ...] = (
    "req_march_fine",
    "req_march_coarse",
    "dev_tasks_view_core",
    "dev_tasks_by_assignee",
    "art_tasks_completed",
    "art_tasks_by_designer",
)


def pmo_client_raw_snapshot_complete(raw_dir: Path, snap: str) -> bool:
    """``~/.jachin/.../PMO/raw`` 下是否存在 ``{snap}_<slug>.json`` 六张齐全。"""
    s = (snap or "").strip()[:10]
    if len(s) != 10:
        return False
    for slug in PMO_SCHEDULED_EXPORT_SLUGS:
        if not (raw_dir / f"{s}_{slug}.json").is_file():
            return False
    return True


def ensure_pmo_raw_for_monitoring(
    project_root: Path | None,
    snapshot_date: str,
    *,
    extra_export: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    供 monitoring_skill 等调用：先检查本机 PMO raw（默认 ``~/.jachin/client_volumes/PMO/raw``）
    是否已有本次 ``snapshot_date`` 的完整六表；否则调用 ``run_pmo_export_scheduled_tables_only`` 拉取。

    - 目录不存在 → 导出
    - 六表任一缺失 → 导出
    - 六表齐全但某 JSON 顶层 ``snapshot_date`` 与目标日不一致（或无法解析）→ 导出
    - 否则跳过导出
    """
    import json as _json

    from l3_node.paths import get_app_root
    from l3_node.primitives.mcp.mcp_tools.pmo_bmo.paths import get_pmo_raw_dir

    root = project_root or get_app_root()
    snap = (snapshot_date or "").strip()[:10]
    raw_dir = get_pmo_raw_dir()

    detail: list[str] = []
    need_export = False
    if not raw_dir.exists():
        need_export = True
        detail.append("raw 目录不存在")
    elif not pmo_client_raw_snapshot_complete(raw_dir, snap):
        need_export = True
        detail.append(f"缺少日期 {snap} 的完整六表 JSON")
    else:
        bad = False
        for slug in PMO_SCHEDULED_EXPORT_SLUGS:
            p = raw_dir / f"{snap}_{slug}.json"
            try:
                data = _json.loads(p.read_text(encoding="utf-8"))
                sd = (data.get("snapshot_date") or "").strip()[:10]
                if sd and sd != snap:
                    bad = True
                    detail.append(f"{p.name} 内 snapshot_date={sd!r} 与目标 {snap!r} 不一致")
                    break
            except Exception as e:
                bad = True
                detail.append(f"{p.name} 读取/校验失败: {e}")
                break
        if bad:
            need_export = True
        else:
            detail.append("已存在当日完整六表且 snapshot_date 一致，跳过导出")

    out: dict[str, Any] = {
        "raw_dir": str(raw_dir.resolve()),
        "snapshot_date": snap,
        "need_export": need_export,
        "detail": detail,
        "export_result": None,
    }
    if need_export:
        ex = dict(extra_export or {})
        ex["snapshot_date"] = snap
        out["export_result"] = run_pmo_export_scheduled_tables_only(
            root, extra=ex, log_export_scope_notice=False
        )
    return out


def run_pmo_export_scheduled_tables_only(
    project_root: Path | None = None,
    extra: dict[str, Any] | None = None,
    *,
    log_export_scope_notice: bool = True,
) -> dict[str, Any]:
    """
    仅导出六张 PMO 多维表（读 pmo_bmo.yaml），不跑 Wiki 全文 sync、不做 knowledge ingest。
    L3 Agent 可只调本函数做「抓表」验证；凭证来自 YAML 的 lark（或由 extra / MCP 回退逻辑补全）。

    log_export_scope_notice: 为 False 时不写「本函数不会执行 align/person-stats」段（供对齐/人员任务单从内部调用导出时避免重复误导）。
    """
    from l3_node.paths import get_app_root
    from l3_node.primitives.mcp.mcp_tools.pmo_bmo.tool_lark_doc import run_pmo_lark_doc

    root = project_root or get_app_root()
    log_path = _ensure_pmo_skill_file_logging()
    slg = logging.getLogger("pmo_bmo_skill")
    _log_pmo_skill_banner(
        log_path,
        "run_pmo_export_scheduled_tables_only",
        project_root=str(root.resolve()),
        skill_yaml_path=_pmo_skill_yaml_resolved_path(root) or "(未找到 pmo_bmo.yaml)",
        nested_skip_scope_log=not log_export_scope_notice,
    )
    if log_export_scope_notice:
        _log_pmo_skill_export_only_scope(slg)
    else:
        slg.info(
            "（嵌套调用）已跳过「仅导出范围」横幅；外层应为 align 或 person-stats 任务单。"
        )

    cfg = _load_skill_yaml(root)
    pipeline = cfg.get("pipeline") or {}
    lk = cfg.get("lark") or {}
    slg.info(
        "已加载 skill 配置: pipeline.keys=%s lark.keys=%s",
        list(pipeline.keys()) if isinstance(pipeline, dict) else pipeline,
        list(lk.keys()) if isinstance(lk, dict) else lk,
    )
    _log_pmo_skill_json(slg, "skill 配置(脱敏)", _redact_for_log(cfg))

    export_args: dict[str, Any] = {"operation": "export_pmo_tables"}
    if isinstance(pipeline.get("pmo_export"), dict):
        export_args.update(pipeline["pmo_export"])
    psd = pipeline.get("snapshot_date")
    if psd:
        export_args["snapshot_date"] = str(psd).strip()[:10]
    if isinstance(lk, dict):
        for k in ("app_id", "app_secret", "lark_use_feishu"):
            if k in lk and lk[k] is not None and str(lk[k]).strip() != "":
                export_args[k] = lk[k]
    if isinstance(pipeline.get("lark_doc"), dict):
        for k in (
            "app_id",
            "app_secret",
            "lark_use_feishu",
            "max_export_records",
            "json_raw_dir",
            "md_raw_rel",
            "duckdb_path",
            "snapshot_date",
        ):
            if k in pipeline["lark_doc"] and pipeline["lark_doc"][k] is not None:
                export_args[k] = pipeline["lark_doc"][k]
    if extra:
        for k in (
            "snapshot_date",
            "max_export_records",
            "json_raw_dir",
            "md_raw_rel",
            "duckdb_path",
            "app_id",
            "app_secret",
            "lark_use_feishu",
            "docx_document_ids",
        ):
            if extra.get(k) is not None:
                export_args[k] = extra[k]

    _apply_pmo_req_march_coarse_docx_env(export_args)

    _log_pmo_skill_json(slg, "即将调用 run_pmo_lark_doc(export_pmo_tables) 合并后参数", export_args)
    slg.info(
        "说明: python -m l3_node.primitives.skills.pmo_bmo.main_skill 会执行本函数；"
        "凭证来自上列参数，atom_pmo_lark_doc 内还会合并 MCP 配置并对凭证做技能 YAML 回退。"
    )

    try:
        with _pmo_heartbeat_while("export_pmo_tables（六表 JSON→raw）"):
            result = run_pmo_lark_doc(export_args)
    except Exception:
        slg.error("run_pmo_lark_doc 抛出异常:\n%s", traceback.format_exc())
        raise
    _log_pmo_skill_json(slg, "run_pmo_lark_doc 返回(完整)", result)
    slg.info(
        "导出摘要 status=%s tables_ok=%s errors条数=%s warnings=%s",
        result.get("status"),
        result.get("tables_ok"),
        len(result.get("errors") or []),
        result.get("warnings"),
    )
    slg.info("---------- run_pmo_export_scheduled_tables_only 结束 ----------")
    if log_export_scope_notice:
        slg.info(
            "提示: 大需求对齐任务单=子命令 align；按人任务统计任务单=子命令 person-stats；"
            "与本次仅导出使用同一日志文件（本进程内）。"
        )
    return result


def _load_skill_yaml(project_root: Path) -> dict[str, Any]:
    import yaml

    candidates = [
        project_root / "config" / "skills" / "com.jachin.pmo.bmo" / "pmo_bmo.yaml",
        Path.home() / ".jachin" / "config" / "skills" / "com.jachin.pmo.bmo" / "pmo_bmo.yaml",
    ]
    for p in candidates:
        if p.is_file():
            try:
                return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except Exception as e:
                logger.warning("[pmo_bmo] 读取配置失败 %s: %s", p, e)
    return {}


# K11「需求表3月」云文档：浏览器打开文档后 URL 为 .../docx/<token>/...
# 与 Wiki 侧栏内嵌表格块 docx_table_block_id 配套使用；其它租户请在 YAML 或 PMO_REQ_MARCH_COARSE_DOCX_ID 覆盖。
PMO_DEFAULT_REQ_MARCH_COARSE_DOCX_ID = "ZcpedCREaoNrQUxvM7EluZGugWg"


def _apply_pmo_req_march_coarse_docx_env(export_args: dict[str, Any]) -> None:
    """
    合并 docx_document_ids.req_march_coarse：YAML 已有值不变；否则环境变量；再否则内置默认 PMO_DEFAULT_REQ_MARCH_COARSE_DOCX_ID。
    """
    d = export_args.get("docx_document_ids")
    if not isinstance(d, dict):
        d = {}
    else:
        d = dict(d)
    cur = (d.get("req_march_coarse") or "").strip()
    if cur:
        export_args["docx_document_ids"] = d
        return
    eid = (os.environ.get("PMO_REQ_MARCH_COARSE_DOCX_ID") or "").strip()
    if eid:
        d["req_march_coarse"] = eid
        export_args["docx_document_ids"] = d
        return
    d["req_march_coarse"] = PMO_DEFAULT_REQ_MARCH_COARSE_DOCX_ID
    export_args["docx_document_ids"] = d


# --- 大需求对齐：脚本写 MD + 可选 Agent 润色 ---

PMO_PROCESS_FLOW_DOC_REL = "docs/pmo_bmo_plugin/04_PROCESS_FLOW_AND_OUTPUT_SPEC.md"
PMO_RAW_REL = "docs/pmo_bmo_plugin/raw"
PMO_OUTPUT_REL = "docs/pmo_bmo_plugin/output"
# 大需求对齐：固定单文件，每次运行覆盖（正文内仍带 snapshot 日期便于追溯）
PMO_BIG_ALIGN_OUTPUT_BASENAME = "PMO_大需求对齐.md"
# 按人任务统计：固定单文件，每次运行覆盖
PMO_PERSON_STATS_OUTPUT_BASENAME = "PMO_人员任务统计.md"
# 大需求 ↔ 执行人员（按 assignee/designer 视图匹配）：固定单文件，每次运行覆盖
PMO_REQ_PARTICIPANTS_OUTPUT_BASENAME = "PMO_需求人员参与明细.md"
# 领导视图 + 周负荷总览（与仪表盘卡片、提纯 CSV 字段语义对齐）
PMO_LEADERSHIP_BRIEF_OUTPUT_BASENAME = "PMO_领导视图与周负荷摘要.md"
# 领导摘要中「细需求」明细表最大行数（全量条数在表头说明）
PMO_LEADERSHIP_BRIEF_FINE_CAP = 450


def pmo_repo_raw_md_path(raw_dir: Path, slug: str) -> Path:
    """
    仓库 ``docs/pmo_bmo_plugin/raw`` 下与六表导出一致的 Markdown 路径：``{slug}.md``（无日期前缀，每轮抓取覆盖）。
    原始 JSON 仍在 ``~/.jachin/client_volumes/PMO/raw/{date}_{slug}.json``。
    """
    return raw_dir / f"{slug}.md"

# 与 tool_pmo_bitable_export.PMO_SCHEDULED_BITABLES 及业务 Wiki 一致（供 Agent 对照）
PMO_BIG_ALIGN_WIKI: dict[str, str] = {
    "product_fine": "https://ssgkm409t6q5.sg.larksuite.com/wiki/ZItbw4omRi6Sbsksb6jlwYq8gYq?table=tblozlbpzHlL8m8m&view=vew8TxMcSh",
    "dev_core": "https://ssgkm409t6q5.sg.larksuite.com/wiki/GdQ7wTgSRiZ0olkXrNGlFcz0gad?table=tblhJN0G2EhRNwjZ&view=vewpI8lyYw",
    "art_completed": "https://ssgkm409t6q5.sg.larksuite.com/wiki/DiSnwVB1OiDvPWkk0W9lzx6AgLd?table=tblDw87UlhddFIoY&view=vew5taB9H1",
    # 大需求主线：req_march_coarse 为 Wiki 内云文档表格块（docx_table + block id）；document_id 见 pipeline.pmo_export.docx_document_ids
    "master_coarse": "https://ssgkm409t6q5.sg.larksuite.com/wiki/ZItbw4omRi6Sbsksb6jlwYq8gYq?table=ldxvjdZfkv69GwsB",
}

# 导出文件名 {date}_<slug>.*
PMO_BIG_ALIGN_SLUGS: dict[str, str] = {
    "master_coarse": "req_march_coarse",
    "product_fine": "req_march_fine",
    "dev_core": "dev_tasks_view_core",
    "art_completed": "art_tasks_completed",
}

# 大需求对齐报告仅依赖以下三份 JSON（产品细表不纳入匹配与生成）
PMO_BIG_ALIGN_DATA_SLUGS: tuple[str, ...] = (
    "req_march_coarse",
    "dev_tasks_view_core",
    "art_tasks_completed",
)

# 3月需求大表（docx 导出）列7～列11 ↔ 实施阶段；与 req_march_coarse.json 字段名一致
PMO_COARSE_DOCX_MILESTONE_KEYS: tuple[tuple[str, str], ...] = (
    ("列7", "立项评审"),
    ("列8", "需求评审"),
    ("列9", "测试验收"),
    ("列10", "发布评审"),
    ("列11", "生产发布"),
)

PMO_MATCH_MIN_SCORE = 0.30
PMO_MATCH_MAX_TASKS_PER_REQ = 48


def _coarse_docx_milestone_filled(val: Any) -> bool:
    """docx 表「实施阶段」单元格：有实质日期/节点则计为已填；排除占位与「日常」。"""
    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _cell_to_text

    t = _cell_to_text(val).strip()
    if not t or t in ("-", "—", "－", "日常", "N/A", "n/a"):
        return False
    if _pmo_milestone_filled(val):
        return True
    # 短文本节点如 03.09、02.26
    if len(t) <= 16 and any(c.isdigit() for c in t):
        return True
    return bool(t)


def _pmo_nl_match_score(needle: str, haystack: str) -> float:
    """大需求锚点文本 vs 任务拼接文本：子串加分 + SequenceMatcher。"""
    a = (needle or "").strip().lower()
    b = (haystack or "").strip().lower()
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return max(0.75, SequenceMatcher(None, a, b).ratio())
    return SequenceMatcher(None, a, b).ratio()


def _pmo_infer_stage_label_from_coarse_milestones(
    filled: list[tuple[str, bool]],
) -> tuple[str, str]:
    """
    对照 04_PROCESS_FLOW_AND_OUTPUT_SPEC §1.6：根据已填里程碑从左到右推断主阶段与一句说明。
    返回 (阶段标签, 说明)
    """
    last: str | None = None
    last_idx = -1
    for i, (k, ok) in enumerate(filled):
        if ok:
            last = k
            last_idx = i
    if not last:
        return ("待启动 / 未录入里程碑", "主线表「实施阶段」五项均无有效日期节点")
    narrative = f"已录入节点截至「{last}」（从左至右第 {last_idx + 1}/5 列）"
    if last == "立项评审":
        return ("立项与需求准备", narrative)
    if last == "需求评审":
        return ("需求评审通过后 → 开发准备 / 美术预排", narrative)
    if last == "测试验收":
        return ("提测与验收", narrative)
    if last == "发布评审":
        return ("发布评审", narrative)
    if last == "生产发布":
        return ("发布上线后（可进入复盘）", narrative)
    return ("执行中", narrative)


def _pmo_dev_parent_chain_titles(
    rid: str,
    rid_to_parent: dict[str, str | None],
    rid_to_task: dict[str, str],
    max_hops: int = 12,
) -> list[str]:
    """自当前记录向上收集父任务标题（根在前）。"""
    titles: list[str] = []
    cur: str | None = rid
    seen: set[str] = set()
    hops = 0
    while cur and cur not in seen and hops < max_hops:
        seen.add(cur)
        p = rid_to_parent.get(cur)
        if not p:
            break
        pt = (rid_to_task.get(p) or "").strip()
        if pt:
            titles.append(pt)
        cur = p
        hops += 1
    return titles


def _pmo_build_dev_match_blob(
    fld: dict[str, Any],
    rid: str,
    rid_to_parent: dict[str, str | None],
    rid_to_task: dict[str, str],
) -> str:
    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _cell_to_text

    parts: list[str] = []
    t = _cell_to_text(fld.get("任务")).strip()
    if t:
        parts.append(t)
    for pt in _pmo_dev_parent_chain_titles(rid, rid_to_parent, rid_to_task):
        parts.append(pt)
    sp = _cell_to_text(fld.get("Sprint")).strip()
    if sp:
        parts.append(sp)
    risk = _cell_to_text(fld.get("风险/问题/说明")).strip()
    if risk and len(risk) < 200:
        parts.append(risk)
    return " ".join(parts)


def _pmo_build_art_match_blob(fld: dict[str, Any]) -> str:
    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _cell_to_text

    title_key = "任务（交互动画注意问题请标注）"
    parts: list[str] = []
    t = _cell_to_text(fld.get(title_key) or fld.get("任务")).strip()
    if t:
        parts.append(t)
    for k in ("父记录", "Parent items", "父级"):
        p = _cell_to_text(fld.get(k)).strip()
        if p:
            parts.append(p)
            break
    sp = _cell_to_text(fld.get("Sprint")).strip()
    if sp:
        parts.append(sp)
    return " ".join(parts)


def _pmo_parse_coarse_requirements_rows(
    coarse_recs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """解析 docx 表记录：跳过表头行；列2 非空为一条大需求；列1 空行继承上一需求类型。"""
    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _cell_to_text

    out: list[dict[str, Any]] = []
    current_cat = ""
    for r in coarse_recs:
        rid = str(r.get("record_id") or "")
        if rid in ("docx_r0", "docx_r1"):
            continue
        fld = r.get("fields") or {}
        c1 = _cell_to_text(fld.get("列1")).strip()
        c2 = _cell_to_text(fld.get("列2")).strip()
        if not c2:
            continue
        if c1:
            current_cat = c1
        ms: dict[str, Any] = {}
        filled_flags: list[tuple[str, bool]] = []
        for col, label in PMO_COARSE_DOCX_MILESTONE_KEYS:
            v = fld.get(col)
            ok = _coarse_docx_milestone_filled(v)
            ms[label] = v
            filled_flags.append((label, ok))
        n_ok = sum(1 for _, ok in filled_flags if ok)
        stage_label, stage_note = _pmo_infer_stage_label_from_coarse_milestones(filled_flags)
        out.append(
            {
                "record_id": rid,
                "category": current_cat,
                "title": c2,
                "owner": _cell_to_text(fld.get("列3")).strip(),
                "priority": _cell_to_text(fld.get("列4")).strip(),
                "analysis_report": _cell_to_text(fld.get("列5")).strip(),
                "prd_doc": _cell_to_text(fld.get("列6")).strip(),
                "milestones": ms,
                "milestone_filled_flags": filled_flags,
                "milestone_filled_count": n_ok,
                "milestone_pct": (100.0 * n_ok / len(PMO_COARSE_DOCX_MILESTONE_KEYS)) if PMO_COARSE_DOCX_MILESTONE_KEYS else 0.0,
                "followup": _cell_to_text(fld.get("列12")).strip(),
                "workload": _cell_to_text(fld.get("列13")).strip(),
                "comm_out": _cell_to_text(fld.get("列14")).strip(),
                "status_note": _cell_to_text(fld.get("列15")).strip(),
                "flow_stage_label": stage_label,
                "flow_stage_note": stage_note,
            }
        )
    return out


def write_pmo_big_requirement_alignment_markdown_from_raw(
    project_root: Path, snapshot_date: str
) -> Path:
    """
    以 **req_march_coarse** 每行「列2·需求内容」为唯一分类锚点，将 **开发 / 美术** 小任务经 NL 相似度匹配归类；
    依据主线表「实施阶段」五列推断流程阶段与完成度；输出 ``docs/pmo_bmo_plugin/output/PMO_大需求对齐.md``（覆盖）。
    不调用 LLM；**不**纳入产品细表（req_march_fine）。
    """
    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _cell_to_text
    from l3_node.primitives.mcp.mcp_tools.pmo_bmo.paths import get_pmo_raw_dir

    snap = snapshot_date.strip()[:10]
    json_dir = get_pmo_raw_dir()
    paths = {
        "coarse": json_dir / f"{snap}_req_march_coarse.json",
        "dev": json_dir / f"{snap}_dev_tasks_view_core.json",
        "art": json_dir / f"{snap}_art_tasks_completed.json",
    }
    for k, p in paths.items():
        if not p.is_file():
            raise FileNotFoundError(f"缺少大需求对齐 raw JSON ({k}): {p}")

    coarse_doc = json.loads(paths["coarse"].read_text(encoding="utf-8"))
    dev_doc = json.loads(paths["dev"].read_text(encoding="utf-8"))
    art_doc = json.loads(paths["art"].read_text(encoding="utf-8"))

    coarse_list = _pmo_parse_coarse_requirements_rows(coarse_doc.get("records") or [])
    if not coarse_list:
        raise ValueError("req_march_coarse 无有效需求行（列2 为空或仅表头）")

    ref_day = date.today()
    week_start, week_end = _pmo_report_week_bounds(ref_day)

    needles = [f"{c['category']} {c['title']}".strip() for c in coarse_list]

    rid_to_parent: dict[str, str | None] = {}
    rid_to_task: dict[str, str] = {}
    for r in dev_doc.get("records") or []:
        rid = str(r.get("record_id") or r.get("id") or "")
        if not rid:
            continue
        fld = r.get("fields") or {}
        rid_to_task[rid] = _cell_to_text(fld.get("任务")).strip()
        rid_to_parent[rid] = _pmo_dev_parent_record_id(fld)

    dev_by_req: list[list[dict[str, Any]]] = [[] for _ in coarse_list]
    dev_unmatched: list[dict[str, Any]] = []
    for r in dev_doc.get("records") or []:
        fld = r.get("fields") or {}
        rid = str(r.get("record_id") or r.get("id") or "")
        if not _pmo_dev_art_interval_overlaps_week(fld, week_start, week_end):
            continue
        blob = _pmo_build_dev_match_blob(fld, rid, rid_to_parent, rid_to_task)
        if not blob.strip():
            continue
        best_i = 0
        best_s = 0.0
        for i, nd in enumerate(needles):
            s = _pmo_nl_match_score(nd, blob)
            if s > best_s:
                best_s = s
                best_i = i
        row_out = {
            "来源": "开发",
            "任务摘要": _md_cell(fld.get("任务")),
            "匹配分": f"{best_s:.2f}",
            "状态": _md_cell(fld.get("状态")),
            "进度": _md_cell(fld.get("进度")),
            "Sprint": _md_cell(fld.get("Sprint")),
            "起止": f"{_fmt_bitable_ts(fld.get('开始日期'))} → {_fmt_bitable_ts(fld.get('交付日期'))}",
            "record_id": rid,
        }
        if best_s >= PMO_MATCH_MIN_SCORE:
            if len(dev_by_req[best_i]) < PMO_MATCH_MAX_TASKS_PER_REQ:
                dev_by_req[best_i].append(row_out)
        else:
            dev_unmatched.append(row_out)

    art_by_req: list[list[dict[str, Any]]] = [[] for _ in coarse_list]
    art_unmatched: list[dict[str, Any]] = []
    art_title_key = "任务（交互动画注意问题请标注）"
    for r in art_doc.get("records") or []:
        fld = r.get("fields") or {}
        rid = str(r.get("record_id") or r.get("id") or "")
        if not _pmo_dev_art_interval_overlaps_week(fld, week_start, week_end):
            continue
        blob = _pmo_build_art_match_blob(fld)
        title = _cell_to_text(fld.get(art_title_key) or fld.get("任务")).strip()
        if not title and not blob.strip():
            continue
        best_i = 0
        best_s = 0.0
        for i, nd in enumerate(needles):
            s = _pmo_nl_match_score(nd, blob or title)
            if s > best_s:
                best_s = s
                best_i = i
        row_out = {
            "来源": "美术",
            "任务摘要": _md_cell(fld.get(art_title_key) or fld.get("任务")),
            "匹配分": f"{best_s:.2f}",
            "状态": "—",
            "进度": _md_cell(fld.get("进度")),
            "Sprint": _md_cell(fld.get("Sprint")),
            "起止": f"{_fmt_bitable_ts(fld.get('开始日期'))} → {_fmt_bitable_ts(fld.get('交付日期'))}",
            "record_id": rid,
        }
        if best_s >= PMO_MATCH_MIN_SCORE:
            if len(art_by_req[best_i]) < PMO_MATCH_MAX_TASKS_PER_REQ:
                art_by_req[best_i].append(row_out)
        else:
            art_unmatched.append(row_out)

    out_dir = project_root / PMO_OUTPUT_REL
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / PMO_BIG_ALIGN_OUTPUT_BASENAME

    lines: list[str] = [
        f"# PMO 大需求对齐报告（{snap} · 本周 {week_start.isoformat()}～{week_end.isoformat()}）",
        "",
        f"> **输出说明**：仓库内固定文件 `docs/pmo_bmo_plugin/output/{PMO_BIG_ALIGN_OUTPUT_BASENAME}`，每次运行 **覆盖**；raw 快照 **snapshot_date={snap}**；**本周范围** 以生成脚本时的系统日期 **{ref_day.isoformat()}** 所在自然周（周一至周日）为准。",
        f"> **关联**：若已运行 `person-stats` 或 `full`，同目录另有 `{PMO_LEADERSHIP_BRIEF_OUTPUT_BASENAME}`（领导视图与周负荷总览，供卡片与提纯对齐）。",
        "",
        "## 数据源与方法",
        "",
        "- **流程依据**：`docs/pmo_bmo_plugin/04_PROCESS_FLOW_AND_OUTPUT_SPEC.md`（§1.6 期初—季度复盘与任务表关系）。",
        f"- **大需求分类标准**：`{snap}_req_march_coarse.json`（云文档「3月需求大表」）中 **每行「列2·需求内容」** 一条主线；**列1·需求类型** 空行继承上一行的类型。",
        f"- **开发任务**：`{snap}_dev_tasks_view_core.json`（字段：任务、父记录、Sprint、状态、进度、日期…）。",
        f"- **美术任务**：`{snap}_art_tasks_completed.json`（设计任务表完成视图）。",
        "- **产品细表**：当前不纳入匹配与汇总（无数据或不在本次计算范围）。",
        f"- **本周筛选**：仅保留 **开始日期/交付日期** 与 **{week_start.isoformat()}～{week_end.isoformat()}** 有交集的开发、美术子任务；下列大需求节仅在 **本周有这样子任务** 或 **实施阶段里程碑文本中出现本周内日期** 时输出。",
        f"- **匹配规则**：将「需求类型 + 需求内容」与「任务标题 + 父级标题链 + Sprint（+ 简述）」做 **子串加分 + difflib 序列相似度**；阈值 **≥ {PMO_MATCH_MIN_SCORE:.2f}** 归入该大需求，否则列入附录「弱匹配/未归类」。**不等于业务终审**，仅供 PMO 追踪。",
        f"- **阶段与完成度**：主线表「实施阶段」五列（立项评审→生产发布）有有效节点则计进度 **n/5**；并映射到规范 §1.6 的阶段标签。",
        "",
    ]

    tbl_headers = ["来源", "任务摘要", "匹配分", "状态", "进度", "Sprint", "起止", "record_id"]

    def _emit_subtask_table(rows: list[dict[str, Any]]) -> None:
        if not rows:
            lines.append("（本大需求下无达到匹配阈值的开发/美术子任务；可能仍在其它史诗或未录入。）")
            lines.append("")
            return
        lines.append("| " + " | ".join(tbl_headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(tbl_headers)) + " |")
        for row in rows:
            lines.append("| " + " | ".join(_md_cell(row.get(h, "")) for h in tbl_headers) + " |")
        lines.append("")

    for ci, c in enumerate(coarse_list):
        dev_t = dev_by_req[ci] if ci < len(dev_by_req) else []
        art_t = art_by_req[ci] if ci < len(art_by_req) else []
        combined = sorted(dev_t + art_t, key=lambda x: float(x.get("匹配分", "0") or 0), reverse=True)
        if not combined and not _pmo_coarse_milestone_touches_week(c, week_start, week_end, ref_day.year):
            continue

        cat_disp = c["category"] or "—"
        ar = (c["analysis_report"] or "").strip() or "—"
        pr = (c["prd_doc"] or "").strip() or "—"
        lines.append(f"## {c['title']}")
        lines.append("")
        lines.append(f"- **需求类型**：{cat_disp}　**负责人**：{_md_cell(c['owner'])}　**优先级**：{_md_cell(c['priority'])}")
        if c["status_note"]:
            lines.append(f"- **状态说明（列15）**：{_md_cell(c['status_note'])}")
        lines.append(f"- **需求整理分析**：分析、调研报告：{_md_cell(ar)}；需求文档：{_md_cell(pr)}")
        lines.append(
            f"- **后续 / 工作量**：{_md_cell(c['followup'])} / {_md_cell(c['workload'])}（沟通 / 输出：{_md_cell(c['comm_out'])}）"
        )
        lines.append("")

        lines.append("### 主线里程碑（实施阶段 · 与需求大表列对齐）")
        lines.append("")
        ms_row = "| " + " | ".join(k for _, k in PMO_COARSE_DOCX_MILESTONE_KEYS) + " |"
        lines.append(ms_row)
        lines.append("| " + " | ".join(["---"] * len(PMO_COARSE_DOCX_MILESTONE_KEYS)) + " |")
        cells = []
        for col, lab in PMO_COARSE_DOCX_MILESTONE_KEYS:
            cells.append(_md_cell(c["milestones"].get(lab)))
        lines.append("| " + " | ".join(cells) + " |")
        lines.append("")

        lines.append("### 流程阶段推断（对照规范 §1.6）")
        lines.append("")
        lines.append(f"- **推断阶段**：**{c['flow_stage_label']}**")
        lines.append(f"- **说明**：{c['flow_stage_note']}")
        lines.append(
            f"- **主线里程碑完成度**：**{c['milestone_filled_count']}/5**（约 **{c['milestone_pct']:.0f}%** 节点已填日期/交付点）"
        )
        # 子任务时间跨度
        dates: list[str] = []
        for row in combined:
            span = row.get("起止") or ""
            if "→" in span:
                a, b = [x.strip() for x in span.split("→", 1)]
                if a:
                    dates.append(a)
                if b:
                    dates.append(b)
        if dates:
            dsorted = sorted({d for d in dates if d})
            if dsorted:
                lines.append(
                    f"- **已匹配子任务时间跨度**（开发/美术起止合并）：**{dsorted[0]}** → **{dsorted[-1]}**（取极值，非计划里程碑）"
                )
        lines.append("")

        lines.append("### 纳入的开发 / 美术小任务")
        lines.append("")
        _emit_subtask_table(combined)

    lines.append("## 附录 A · 弱匹配或未归类的开发任务")
    lines.append("")
    lines.append(
        f"> 与任一「需求内容」相似度均 **低于 {PMO_MATCH_MIN_SCORE:.2f}** 的开发任务（前 **{min(120, len(dev_unmatched))}** 条）。"
    )
    lines.append("")
    if dev_unmatched:
        lines.append("| " + " | ".join(tbl_headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(tbl_headers)) + " |")
        for row in dev_unmatched[:120]:
            lines.append("| " + " | ".join(_md_cell(row.get(h, "")) for h in tbl_headers) + " |")
        lines.append("")
    else:
        lines.append("（无）")
        lines.append("")

    lines.append("## 附录 B · 弱匹配或未归类的美术任务")
    lines.append("")
    lines.append(
        f"> 与任一「需求内容」相似度均 **低于 {PMO_MATCH_MIN_SCORE:.2f}** 的美术任务（前 **{min(120, len(art_unmatched))}** 条）。"
    )
    lines.append("")
    if art_unmatched:
        lines.append("| " + " | ".join(tbl_headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(tbl_headers)) + " |")
        for row in art_unmatched[:120]:
            lines.append("| " + " | ".join(_md_cell(row.get(h, "")) for h in tbl_headers) + " |")
        lines.append("")
    else:
        lines.append("（无）")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


PMO_BIG_ALIGN_AGENT_INSTRUCTIONS = """\
你是 L3 执行代理，可复核或润色「大需求对齐」报告。**默认已由技能脚本确定性生成**，无需再手写全文。

## 自动生成（推荐）

- 运行：`python -m l3_node.primitives.skills.pmo_bmo.main_skill align`（或 `run_pmo_big_requirement_alignment_task`）。
- 脚本读取 ``~/.jachin/.../PMO/raw/{date}_req_march_coarse.json``、``..._dev_tasks_view_core.json``、``..._art_tasks_completed.json``，
  以 **req_march_coarse 每行「列2·需求内容」** 为分类锚点，将开发/美术小任务做 **文本相似度匹配**，写入
  ``docs/pmo_bmo_plugin/output/PMO_大需求对齐.md``（固定文件名，每次覆盖）。
- **不纳入** 产品细表（req_march_fine）；产品条线无数据时不参与计算。

## 若需人工补充

- 阅读 `docs/pmo_bmo_plugin/04_PROCESS_FLOW_AND_OUTPUT_SPEC.md`（§1.6）核对「推断阶段」用语。
- 可调低/调高匹配阈值：见 `main_skill.py` 中 `PMO_MATCH_MIN_SCORE`（默认 0.30）。
- 使用已有 `mcp:atom_pmo_lark_doc`（`operation=export_pmo_tables`）确保 raw 与 snapshot 一致；**不要新增 MCP**。

（任务单结束）
"""


def get_pmo_big_requirement_alignment_task_spec() -> dict[str, Any]:
    """返回大需求对齐任务元数据 + Agent 说明全文（供编排层注入对话）。"""
    return {
        "task_id": "pmo_big_requirement_alignment",
        "title": "PMO 大需求对齐（开发/美术 → 3月需求大表主线）",
        "process_flow_doc_relative": PMO_PROCESS_FLOW_DOC_REL,
        "output_dir_relative": PMO_OUTPUT_REL,
        "output_filename_pattern": PMO_BIG_ALIGN_OUTPUT_BASENAME,
        "wiki_reference_urls": dict(PMO_BIG_ALIGN_WIKI),
        "export_slugs": dict(PMO_BIG_ALIGN_SLUGS),
        "mcp_required": [
            {
                "mcp": "atom_pmo_lark_doc",
                "operation": "export_pmo_tables",
                "note": "不新增 MCP；拉取六表。大需求对齐生成器读取其中 req_march_coarse + dev_tasks_view_core + art_tasks_completed 三份 JSON。",
            }
        ],
        "agent_instructions": PMO_BIG_ALIGN_AGENT_INSTRUCTIONS.strip(),
    }


def build_pmo_big_requirement_alignment_context(
    project_root: Path | None = None,
    snapshot_date: str | None = None,
) -> dict[str, Any]:
    """
    解析 snapshot 日期的 raw / 规范 / 输出路径；**alignment_data_ready** 表示三表 JSON + 流程规范就绪，可生成大需求对齐 MD。
    """
    from l3_node.primitives.mcp.mcp_tools.pmo_bmo.paths import get_pmo_raw_dir
    from l3_node.paths import get_app_root

    root = project_root or get_app_root()
    snap = (snapshot_date or date.today().isoformat()).strip()[:10]

    raw_dir = root / PMO_RAW_REL
    json_dir = get_pmo_raw_dir()
    proc = root / PMO_PROCESS_FLOW_DOC_REL
    out_dir = root / PMO_OUTPUT_REL

    files: dict[str, Any] = {}
    missing_md: list[str] = []
    for key, slug in PMO_BIG_ALIGN_SLUGS.items():
        md_path = pmo_repo_raw_md_path(raw_dir, slug)
        js_path = json_dir / f"{snap}_{slug}.json"
        try:
            md_rel = str(md_path.relative_to(root.resolve()))
        except ValueError:
            md_rel = str(md_path)
        files[key] = {
            "slug": slug,
            "md_relative": md_rel,
            "md_exists": md_path.is_file(),
            "json_path": str(js_path),
            "json_exists": js_path.is_file(),
        }
        if not md_path.is_file():
            missing_md.append(key)

    out_md = out_dir / PMO_BIG_ALIGN_OUTPUT_BASENAME
    try:
        out_rel = str(out_md.relative_to(root.resolve()))
    except ValueError:
        out_rel = str(out_md)

    missing_align_json = [
        s for s in PMO_BIG_ALIGN_DATA_SLUGS if not (json_dir / f"{snap}_{s}.json").is_file()
    ]
    alignment_data_ready = not missing_align_json and proc.is_file()

    return {
        "snapshot_date": snap,
        "project_root": str(root.resolve()),
        "process_flow_doc": str(proc.resolve()) if proc.is_file() else str(proc),
        "process_flow_exists": proc.is_file(),
        "output_dir": str(out_dir.resolve()),
        "output_markdown": str(out_md.resolve()),
        "output_markdown_relative": out_rel,
        "raw_files": files,
        "raw_missing_keys": missing_md,
        "alignment_json_missing": missing_align_json,
        "alignment_data_ready": alignment_data_ready,
        "status": "ready" if alignment_data_ready else "incomplete",
    }


def run_pmo_big_requirement_alignment_task(
    project_root: Path | None = None,
    snapshot_date: str | None = None,
    *,
    ensure_export: bool = True,
    extra_export: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    大需求对齐：可选先导出六表，再 **确定性生成** ``PMO_大需求对齐.md``（req_march_coarse 列2 为锚点 +
    开发/美术相似度匹配）。不新增 MCP。
    """
    from l3_node.paths import get_app_root

    root = project_root or get_app_root()
    cfg = _load_skill_yaml(root)
    pipeline = cfg.get("pipeline") or {}
    snap = (snapshot_date or pipeline.get("snapshot_date") or date.today().isoformat()).strip()[:10]

    log_path = _ensure_pmo_skill_file_logging()
    slg = logging.getLogger("pmo_bmo_skill")
    _log_pmo_skill_banner(
        log_path,
        "run_pmo_big_requirement_alignment_task",
        project_root=str(root.resolve()),
        snapshot_date=snap,
        ensure_export=ensure_export,
    )
    slg.info("任务单类型=【大需求对齐】输出目标 %s（默认由脚本写入）", PMO_BIG_ALIGN_OUTPUT_BASENAME)
    slg.info("函数: get_pmo_big_requirement_alignment_task_spec + build_pmo_big_requirement_alignment_context + write_pmo_big_requirement_alignment_markdown_from_raw")

    spec = get_pmo_big_requirement_alignment_task_spec()
    export_result: dict[str, Any] | None = None
    ex = dict(extra_export or {})
    ex.setdefault("snapshot_date", snap)

    if ensure_export:
        try:
            export_result = run_pmo_export_scheduled_tables_only(
                root, extra=ex, log_export_scope_notice=False
            )
        except Exception:
            slg.error("export_pmo_tables 失败:\n%s", traceback.format_exc())
            raise

    ctx = build_pmo_big_requirement_alignment_context(root, snapshot_date=snap)
    ex_st = (export_result.get("status") or "").lower() if export_result else ""
    if export_result and ex_st == "error":
        st = "export_error"
    elif ctx.get("status") != "ready":
        st = "incomplete_raw"
    elif export_result and ex_st not in ("success", "partial", ""):
        st = "attention"
    else:
        st = "ok"

    merged = {
        "status": st,
        "snapshot_date": snap,
        "task_spec": spec,
        "alignment_context": ctx,
        "export_pmo_tables": export_result,
        "next_steps_for_agent": [
            "阅读 task_spec['agent_instructions']（可选：人工润色已生成的 MD）",
            f"流程规范: {PMO_PROCESS_FLOW_DOC_REL}",
            "若缺少三表 JSON 则调用 atom_pmo_lark_doc export_pmo_tables",
            f"已生成报告路径见 written_markdown_relative 或 {ctx.get('output_markdown_relative', PMO_OUTPUT_REL)}",
        ],
    }
    if ctx.get("alignment_data_ready") and st not in ("export_error", "incomplete_raw"):
        try:
            wp = write_pmo_big_requirement_alignment_markdown_from_raw(root, snap)
            merged["written_markdown_path"] = str(wp.resolve())
            merged["written_markdown_relative"] = str(wp.relative_to(root.resolve()))
            merged["status"] = "ok"
            slg.info("已生成大需求对齐 Markdown: %s", merged["written_markdown_path"])
        except Exception as e:
            merged["written_markdown_error"] = str(e)
            merged["status"] = "partial"
            slg.error("生成 PMO_大需求对齐 Markdown 失败: %s\n%s", e, traceback.format_exc())
    else:
        slg.info(
            "跳过写入 PMO_大需求对齐（status=%s alignment_data_ready=%s alignment_json_missing=%s）",
            st,
            ctx.get("alignment_data_ready"),
            ctx.get("alignment_json_missing"),
        )

    _log_pmo_skill_json(slg, "run_pmo_big_requirement_alignment_task 返回", {k: merged[k] for k in merged if k != "task_spec"})
    return merged


# --- 按人员统计任务（产品 / 开发 / 美术 + 干系人表）---

PMO_STAKEHOLDER_DOC_REL = "docs/bi_daily_report/bi_project/K11_需求池_干系人.md"

# PMO 仪表盘推送：提纯 CSV 文件名（与 Lark 子表一一对应；落盘 ~/.jachin/client_volumes/PMO/output/）
PMO_DASHBOARD_CSV_REQUIREMENT_STATUS = "PMO_需求完成情况.csv"
PMO_DASHBOARD_CSV_PERSON_ALLOC = "PMO_人员分配.csv"
PMO_DASHBOARD_CSV_REQ_PARTICIPATION = "PMO_需求人员参与情况.csv"
PMO_DASHBOARD_CSV_VERSION_RELEASE = "PMO_版本发布.csv"
# 当 ~/.jachin/.../PMO/output 不可写（如 CSV 被 Excel 占用）时，四表与 manifest 回退到此相对目录
PMO_DASHBOARD_CSV_FALLBACK_REL = "docs/pmo_bmo_plugin/output_dashboard"

# 同步到飞书时 CSV 列名 -> 多维表字段名（与 sync_csv_to_bitable 一致）。
# 飞书「需求完成情况」主列多为「需求内容」；提纯 CSV 已用「需求内容」与 PMO_需求人员参与情况 一致。
# 「当前完成度」飞书常见在 % 前带空格或与 CSV 半角括号不一致，故给默认映射；可用 pmo_bmo.yaml 的 field_mapping 覆盖。
# 需求战报 VChart 读多维表完成度时，须与 tool_data_visualizer._pmo_pct_field 识别的字段名一致（含「当前完成度 (%)」）。
PMO_DASHBOARD_DEFAULT_FIELD_MAPPING: dict[str, dict[str, str]] = {
    PMO_DASHBOARD_CSV_REQUIREMENT_STATUS: {
        "需求名称": "需求内容",
        "当前完成度(%)": "当前完成度 (%)",
    },
}


def _pmo_merge_dashboard_field_mapping(csv_name: str, yaml_mapping: dict[str, str] | None) -> dict[str, str]:
    """合并默认列名映射与 YAML（后者覆盖前者）。"""
    out: dict[str, str] = {}
    d = PMO_DASHBOARD_DEFAULT_FIELD_MAPPING.get(csv_name)
    if isinstance(d, dict):
        out.update(d)
    if isinstance(yaml_mapping, dict):
        out.update(yaml_mapping)
    return out


def _pmo_battle_safe_pct(val: Any) -> float:
    try:
        v = float(val)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(v):
        return 0.0
    return max(0.0, min(100.0, v))


def _pmo_battle_md_escape(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ")[:2000]


def _pmo_battle_short_label(s: str, max_len: int = 22) -> str:
    t = " ".join((s or "").strip().split())
    if len(t) <= max_len:
        return t or "（空）"
    return t[: max_len - 1] + "…"


def _pmo_battle_ascii_bar(pct: float, width: int = 10) -> str:
    p = _pmo_battle_safe_pct(pct)
    filled = int(round(width * p / 100.0))
    filled = min(width, max(0, filled))
    return "▓" * filled + "░" * (width - filled)


def pmo_parse_bitable_date_cell_to_date(raw: Any) -> date | None:
    """
    将 Lark 多维表「日期」列原始值解析为日历日期。

    API 常见形态：
    - **毫秒时间戳**（整数，约 12～13 位）：须除以 1000 再 ``fromtimestamp``（旧逻辑误用 ``>1e13`` 判断毫秒会导致全盘解析失败）。
    - **秒级时间戳**（约 10 位）。
    - **字符串**：``YYYY/MM/DD``、``YYYY-MM-DD`` 等（与界面展示一致）。
    - **dict**：部分客户端封装为 ``{\"date\": \"...\"}`` / ``{\"value\": ts}``。
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        if isinstance(raw, float) and not math.isfinite(raw):
            return None
        n = int(raw)
        if n >= 1_000_000_000_000:
            n = n // 1000
        elif n < 1_000_000_000:
            return None
        try:
            return datetime.fromtimestamp(n).date()
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(raw, str):
        s = " ".join(raw.strip().split())
        if not s or s in ("—", "-", "未完成", "空"):
            return None
        if s.isdigit():
            return pmo_parse_bitable_date_cell_to_date(int(s))
        s2 = s.replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-").strip()
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s2)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                return None
        m2 = re.match(r"^(\d{2})-(\d{2})-(\d{4})", s2)
        if m2:
            try:
                return date(int(m2.group(3)), int(m2.group(2)), int(m2.group(1)))
            except ValueError:
                return None
        return None
    if isinstance(raw, dict):
        if isinstance(raw.get("date"), str):
            return pmo_parse_bitable_date_cell_to_date(raw["date"])
        v = raw.get("value")
        if v is not None:
            return pmo_parse_bitable_date_cell_to_date(v)
        return None
    if isinstance(raw, list) and raw:
        return pmo_parse_bitable_date_cell_to_date(raw[0])
    return None


def pmo_bitable_date_cell_to_mmdd(raw: Any) -> str:
    """多维表日期列 → 战报图表用 **MM-DD**；无法解析则为「—」。"""
    d = pmo_parse_bitable_date_cell_to_date(raw)
    if d is None:
        return "—"
    return f"{d.month:02d}-{d.day:02d}"


def pmo_bitable_date_cell_to_yyyy_mm_dd(raw: Any) -> str:
    """多维表日期列 → **YYYY-MM-DD**；无法解析则空串（用于「时间跨度」文案）。"""
    d = pmo_parse_bitable_date_cell_to_date(raw)
    return d.isoformat() if d else ""


def _pmo_battle_time_placeholder(s: str) -> bool:
    t = (s or "").strip()
    return not t or t in ("—", "-")


def pmo_requirement_battle_time_span_md(start_ymd: str | None, end_ymd: str | None) -> str:
    """需求战报卡片：时间跨度行（``YYYY-MM-DD``，由 ``pmo_bitable_date_cell_to_yyyy_mm_dd`` 从「开始/结束时间」解析）。"""
    st = (start_ymd or "").strip()
    en = (end_ymd or "").strip()
    if not _pmo_battle_time_placeholder(st) and not _pmo_battle_time_placeholder(en):
        return f"📅 **时间跨度**：{st} 至 {en}"
    if not _pmo_battle_time_placeholder(st) and _pmo_battle_time_placeholder(en):
        return f"📅 **时间跨度**：{st} 至今"
    if _pmo_battle_time_placeholder(st) and not _pmo_battle_time_placeholder(en):
        return f"📅 **时间跨度**：至 {en}"
    return "📅 **时间跨度**：—"


def pmo_requirement_battle_person_details_md(req_title: str, pairs: list[tuple[str, float]]) -> str:
    """
    需求战报卡片：人员执行明细。

    不使用 ``<details>``/``<summary>``：飞书交互卡片 ``lark_md`` 对部分 HTML 支持不稳定，
    易触发接口 **Internal Error** 导致整张卡无法发送；此处用加粗小标题 + 分行列表即可。
    """
    if not pairs:
        return "*（本需求在「需求人员参与情况」中暂无人员行）*"
    hint = _pmo_battle_short_label(req_title, 28)
    lines: list[str] = [
        "**👥 人员执行明细**",
        "",
    ]
    for name, pv in pairs:
        bar = _pmo_battle_ascii_bar(pv, width=10)
        pv_s = _pmo_battle_safe_pct(pv)
        lines.append(
            f"👤 **{_pmo_battle_md_escape(name)}** · 任务: 协同「{_pmo_battle_md_escape(hint)}」 · "
            f"进度: `{bar}` **{pv_s:.0f}%**"
        )
    return "\n".join(lines)


def _parse_pmo_monthly_master_line_md(path: Path) -> list[dict[str, Any]]:
    """解析 ``PMO_月度需求总线_锚点_YYYY-MM.md`` 中的 Markdown 表（排序 | 节点日 | 汇报条目）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|") or "---" in line or "排序" in line:
            continue
        parts = [p.strip() for p in line.strip("|").split("|")]
        if len(parts) < 3:
            continue
        try:
            sort_i = int(parts[0])
        except ValueError:
            continue
        mmdd = parts[1].strip()
        title = parts[2].strip()
        if not re.match(r"^\d{2}-\d{2}$", mmdd) or not title:
            continue
        rows.append({"sort": sort_i, "mmdd": mmdd, "title": title})
    rows.sort(key=lambda x: (x["mmdd"], x["sort"]))
    return rows


def pmo_try_monthly_master_line_battle_bundle(
    project_root: Path,
    snapshot_date: str,
    cfg: dict[str, Any],
    req_recs: list[dict[str, Any]],
    part_recs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """
    若 ``pmo_monthly_master_line.enabled`` 且存在当月锚点 MD，则构造与 ``send_pmo_three_dashboard_cards`` 兼容的
    ``req_rows`` + ``part_by_key``：仅汇报锚点表中的条目，完成度优先多维表 → coarse 对齐 → dev/art 任务 NL 聚合。

    返回 ``{"req_rows": [...], "part_by_key": {...}}``；未启用或缺文件时返回 ``None``（由调用方回退为「全表」逻辑）。
    """
    mlm = (cfg.get("pmo_monthly_master_line") or {}) if isinstance(cfg, dict) else {}
    if not bool(mlm.get("enabled", False)):
        return None
    snap = snapshot_date.strip()[:10]
    ym = snap[:7]
    rel = (mlm.get("anchor_doc_rel") or "").strip() or f"docs/pmo_bmo_plugin/PMO_月度需求总线_锚点_{ym}.md"
    path = project_root / rel
    if not path.is_file():
        logger.warning("[pmo_monthly_master_line] 锚点文件不存在，跳过总线模式: %s", path)
        return None
    anchors = _parse_pmo_monthly_master_line_md(path)
    if not anchors:
        logger.warning("[pmo_monthly_master_line] 锚点表解析为空: %s", path)
        return None

    min_b = float(mlm.get("min_match_bitable", 0.28))
    min_c = float(mlm.get("min_match_coarse", 0.28))
    min_p = float(mlm.get("min_match_participation", 0.25))
    min_t = float(mlm.get("min_match_task", PMO_MATCH_MIN_SCORE))

    from l3_node.primitives.mcp.mcp_tools.pmo_bmo import tool_data_visualizer as tdv

    _pmo_req_title_cell = tdv._pmo_req_title_cell
    _pmo_pct_field = tdv._pmo_pct_field
    _pmo_field_first = tdv._pmo_field_first
    _pmo_parse_participation_pairs = tdv._pmo_parse_participation_pairs
    _pmo_norm_req_key = tdv._pmo_norm_req_key

    try:
        pipe_year = int(snap[:4])
    except ValueError:
        pipe_year = date.today().year

    align_metrics = _parse_pmo_big_requirement_alignment_metrics(project_root, snap)

    from l3_node.primitives.mcp.mcp_tools.pmo_bmo.paths import get_pmo_raw_dir

    json_dir = get_pmo_raw_dir()
    coarse_recs: list[dict[str, Any]] = []
    coarse_path = json_dir / f"{snap}_req_march_coarse.json"
    if coarse_path.is_file():
        coarse_doc = json.loads(coarse_path.read_text(encoding="utf-8"))
        coarse_recs = list(coarse_doc.get("records") or [])

    dev_recs: list[dict[str, Any]] = []
    art_recs: list[dict[str, Any]] = []
    rid_to_parent: dict[str, str | None] = {}
    rid_to_task: dict[str, str] = {}
    dp = json_dir / f"{snap}_dev_tasks_by_assignee.json"
    if dp.is_file():
        dd = json.loads(dp.read_text(encoding="utf-8"))
        dev_recs = list(dd.get("records") or [])
        from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _cell_to_text as _ct

        for r in dev_recs:
            rid = str(r.get("record_id") or r.get("id") or "")
            if not rid:
                continue
            fld0 = r.get("fields") or {}
            rid_to_task[rid] = _ct(fld0.get("任务")).strip()
            rid_to_parent[rid] = _pmo_dev_parent_record_id(fld0)
    ap = json_dir / f"{snap}_art_tasks_by_designer.json"
    if ap.is_file():
        ad = json.loads(ap.read_text(encoding="utf-8"))
        art_recs = list(ad.get("records") or [])

    part_by_key: dict[str, list[tuple[str, float]]] = {}
    req_rows: list[dict[str, Any]] = []

    for a in anchors:
        anchor_title = (a.get("title") or "").strip()
        mmdd = (a.get("mmdd") or "").strip()
        if not anchor_title or not mmdd:
            continue
        start_iso = f"{pipe_year}-{mmdd}"

        best_bf: dict[str, Any] | None = None
        best_bs = 0.0
        for rec in req_recs:
            fld = rec.get("fields") if isinstance(rec.get("fields"), dict) else {}
            bt = _pmo_req_title_cell(fld)
            if not bt:
                continue
            s = _pmo_nl_match_score(anchor_title, bt)
            if s > best_bs:
                best_bs, best_bf = s, fld

        pct_f = 0.0
        st_raw: Any = start_iso
        en_raw: Any = "未完成"

        if best_bf is not None and best_bs >= min_b:
            pct_f = float(_pmo_pct_field(best_bf))
            st_raw = _pmo_field_first(best_bf, ("开始时间", "开始日期")) or start_iso
            en_raw = _pmo_field_first(best_bf, ("结束时间", "结束日期")) or "未完成"
        else:
            best_cf: dict[str, Any] | None = None
            best_cs = 0.0
            for r in coarse_recs:
                fld = r.get("fields") if isinstance(r.get("fields"), dict) else {}
                ct = _pmo_coarse_req_title(fld)
                if not ct:
                    continue
                s = _pmo_nl_match_score(anchor_title, ct)
                if s > best_cs:
                    best_cs, best_cf = s, fld
            if best_cf is not None and best_cs >= min_c:
                am = _pmo_align_metrics_lookup(align_metrics, _pmo_coarse_req_title(best_cf))
                if am and (am.get("pct") or "").strip():
                    try:
                        pct_f = float(str(am.get("pct")).strip())
                    except ValueError:
                        pct_f = float(_pmo_pipeline_filled_pct_str(best_cf))
                else:
                    try:
                        pct_f = float(_pmo_pipeline_filled_pct_str(best_cf))
                    except ValueError:
                        pct_f = 0.0
                sd = _pmo_earliest_in_pipeline_keys(best_cf, pipe_year)
                ed = _pmo_end_from_production_release(best_cf, pipe_year)
                if sd and sd not in ("空", "未完成", "—", "-"):
                    st_raw = sd.replace("/", "-") if "/" in sd else sd
                else:
                    st_raw = start_iso
                if ed and ed not in ("未完成", "—", "-"):
                    en_raw = ed.replace("/", "-") if "/" in ed else ed
                else:
                    en_raw = "未完成"
            else:
                pvs: list[float] = []
                for r in dev_recs:
                    fld = r.get("fields") if isinstance(r.get("fields"), dict) else {}
                    rid = str(r.get("record_id") or r.get("id") or "")
                    blob = _pmo_build_dev_match_blob(fld, rid, rid_to_parent, rid_to_task)
                    if _pmo_nl_match_score(anchor_title, blob) < min_t:
                        continue
                    _, pv = _pmo_infer_task_completion_percent(fld, is_dev=True)
                    if pv is not None:
                        pvs.append(pv)
                for r in art_recs:
                    fld = r.get("fields") if isinstance(r.get("fields"), dict) else {}
                    blob = _pmo_build_art_match_blob(fld)
                    if _pmo_nl_match_score(anchor_title, blob) < min_t:
                        continue
                    _, pv = _pmo_infer_task_completion_percent(fld, is_dev=False)
                    if pv is not None:
                        pvs.append(pv)
                pct_f = sum(pvs) / len(pvs) if pvs else 0.0
                st_raw = start_iso
                en_raw = "未完成"

        best_pf: dict[str, Any] | None = None
        best_ps = 0.0
        for rec in part_recs:
            fld = rec.get("fields") if isinstance(rec.get("fields"), dict) else {}
            rk = _pmo_req_title_cell(fld)
            if not rk:
                continue
            s = _pmo_nl_match_score(anchor_title, rk)
            if s > best_ps:
                best_ps, best_pf = s, fld
        pairs: list[tuple[str, float]] = []
        if best_pf is not None and best_ps >= min_p:
            pairs = _pmo_parse_participation_pairs(best_pf)
        part_by_key[_pmo_norm_req_key(anchor_title)] = pairs

        req_rows.append(
            {
                "title": anchor_title,
                "pct": pct_f,
                "start_raw": st_raw,
                "end_raw": en_raw,
                "start_cal": pmo_bitable_date_cell_to_yyyy_mm_dd(st_raw),
                "end_cal": pmo_bitable_date_cell_to_yyyy_mm_dd(en_raw),
            }
        )

    return {"req_rows": req_rows, "part_by_key": part_by_key}


# 同步 Lark 时按 CSV 表头补建多维表缺失列（见 atom_lark_bitable_sync._ensure_bitable_columns_from_csv）。
# 人员分配「任务1…N」、需求人员参与「人员i/完成度i」列数随提纯变化；需求完成情况含映射目标列（如「当前完成度 (%)」）。
PMO_DASHBOARD_SYNC_ENSURE_COLUMNS_FROM_CSV: frozenset[str] = frozenset(
    {
        PMO_DASHBOARD_CSV_REQUIREMENT_STATUS,
        PMO_DASHBOARD_CSV_PERSON_ALLOC,
        PMO_DASHBOARD_CSV_REQ_PARTICIPATION,
        PMO_DASHBOARD_CSV_VERSION_RELEASE,
    }
)


def _pmo_dashboard_sync_ensure_columns(csv_name: str, push_cfg: dict[str, Any]) -> bool:
    """
    是否在本次同步前按 CSV 自动建列。

    - ``pmo_dashboard_push.ensure_columns: true``：四张表均建列（沿用原语义）。
    - 否则：默认对 ``PMO_DASHBOARD_SYNC_ENSURE_COLUMNS_FROM_CSV`` 内文件仍 ``True``，以 CSV 为准补 schema。
    - ``dashboard_csv_auto_columns: false``：关闭上述默认，仅当 ``ensure_columns: true`` 时才建列。
    """
    if bool(push_cfg.get("ensure_columns")):
        return True
    if push_cfg.get("dashboard_csv_auto_columns") is False:
        return False
    return str(csv_name).strip() in PMO_DASHBOARD_SYNC_ENSURE_COLUMNS_FROM_CSV


PMO_COARSE_MILESTONE_KEYS = ("立项评审", "内部评审", "需求评审", "测试验收", "发布评审", "生产发布")

# 云文档「需求表 3 月」主线列：从「分析、调研报告」至「生产发布」，用于最早开始日、完成度兜底（与表头一致）
PMO_COARSE_PIPELINE_DATE_KEYS = (
    "分析、调研报告",
    "需求文档",
    "立项评审",
    "需求评审",
    "测试验收",
    "发布评审",
    "生产发布",
)

# 云文档 docx 导出时字段名为「列1…列N」，与上列语义名的对应（与 req_march_coarse.json 表头行一致）
PMO_DOCX_COARSE_PIPELINE_KEY_TO_COL: dict[str, str] = {
    "分析、调研报告": "列5",
    "需求文档": "列6",
    "立项评审": "列7",
    "需求评审": "列8",
    "测试验收": "列9",
    "发布评审": "列10",
    "生产发布": "列11",
}

# 与用户给出的三条 Wiki 一致；导出 slug 与 PMO 六表导出一致
PMO_PERSON_STATS_WIKI: dict[str, str] = {
    "product_fine": "https://ssgkm409t6q5.sg.larksuite.com/wiki/ZItbw4omRi6Sbsksb6jlwYq8gYq?table=tblozlbpzHlL8m8m&view=vew8TxMcSh",
    "dev_core": "https://ssgkm409t6q5.sg.larksuite.com/wiki/GdQ7wTgSRiZ0olkXrNGlFcz0gad?table=tblhJN0G2EhRNwjZ&view=vewpI8lyYw",
    "art_completed": "https://ssgkm409t6q5.sg.larksuite.com/wiki/DiSnwVB1OiDvPWkk0W9lzx6AgLd?table=tblDw87UlhddFIoY&view=vew5taB9H1",
}

PMO_PERSON_STATS_SLUGS: dict[str, str] = {
    "product_fine": "req_march_fine",
    "dev_core": "dev_tasks_view_core",
    "art_completed": "art_tasks_completed",
}

PMO_PERSON_STATS_AGENT_INSTRUCTIONS = """\
你是 L3 执行代理，生成「按人员汇总的任务与进度」报告。**不要新增 MCP**；仅用 `mcp:atom_pmo_lark_doc`（`operation=export_pmo_tables`）确保三张表已导出，其余为读本地文件与写 Markdown。

## 1. 目标

- 依据 `docs/pmo_bmo_plugin/04_PROCESS_FLOW_AND_OUTPUT_SPEC.md` 理解任务表关系与阶段用语（§1、§2.3、§2.4）。
- 使用 **三张多维表导出**（产品 `req_march_fine`、开发 `dev_tasks_view_core`、美术 `art_tasks_completed`），从每行任务中识别 **负责人 / 执行人 / 设计责任人** 等字段（以各表 JSON/Markdown 中实际列名为准）。
- 使用 **人力干系人表** `docs/bi_daily_report/bi_project/K11_需求池_干系人.md`：用「名称 / 人员」列与任务里出现的人名做 **归一与匹配**（大小写、空格、中英文昵称、@ 等需容错）；未出现在干系人表中的执行人也可在报告中单独列出并标注「未在干系人表」。
- 为 **每个人**（或每个部门分组）汇总：**当前名下有哪些任务**（按来源：产品/开发/美术）、**任务摘要**、**进度/状态**（各表状态字段）、**时间相关字段**（起止、Sprint 等，以 raw 为准）。
- 输出 **一个** Markdown 到 `docs/pmo_bmo_plugin/output/`，固定文件名：`PMO_人员任务统计.md`（每次覆盖）。
- 同一次流水线还会 **自动生成** `PMO_领导视图与周负荷摘要.md`（领导四视角 + 本周周负荷 + 飞书卡片摘录块）；优先以其为卡片与多维表提纯的总索引。

## 2. 数据

- 先确保已导出：调用 MCP `export_pmo_tables`（与 `snapshot_date` 一致），则存在：
  - `docs/pmo_bmo_plugin/raw/req_march_fine.md`、`dev_tasks_view_core.md`、`art_tasks_completed.md`（固定名，每轮覆盖）；JSON：`~/.jachin/.../PMO/raw/{date}_*.json`
- 干系人表为仓库内静态 Markdown，直接读取；若缺失则在报告顶部说明。

## 3. 输出结构（建议）

- 文档标题与生成日期、数据来源说明。
- 可按 **部门**（产品/开发/设计/等）分节，再按 **人** 分子节；每人下用表格列出：**来源线**、**任务摘要**、**状态/进度**、**关键日期**、**记录 id**（便于回溯）。
- 附录：**无法匹配到具体人员的任务**（如责任人为空）。

## 4. 检查

- UTF-8、路径与仓库内 `output` 目录一致；表格可渲染。

（任务单结束）
"""


PMO_DASHBOARD_THREE_CARDS_UX_AGENT_INSTRUCTIONS = """\
你是 L3 编排/生成代理：凡涉及 **PMO 三张飞书仪表盘推送卡片**（需求战报、资源负荷、版本发布），须严格遵守下列 **UI/UX 规范**。
底层数据抓取、多维表 API 调用 **已由 Python 脚本完成**；你若补充文案或重组输出，**不得**引入未格式化的 13 位时间戳或带时分秒的杂乱日期。

## 卡片一：需求完成情况战报（VChart）

1. **日期预处理（强制）**  
   - 所有「开始时间」「结束时间」在入图或入文前必须已是 **MM-DD**（如 `03-25`）。  
   - **禁止**在卡片正文展示原始毫秒时间戳、冗长 ISO 字符串带 `T`/`Z`、或多余年份+时分秒（若脚本已清洗，你保持即可）。

2. **需求大盘图（VChart）**  
   - **禁止由大模型拼装或改写本图 data**：`display_name` / `progress` / `display_label` **仅**由仓库内 Python（`tool_data_visualizer._pmo_build_overview_bar_spec` / `_pmo_build_single_req_total_progress_spec`）生成；Agent **不得**输出替代 JSON。  
   - **数据契约（单条）**：`display_name` = `[MM-DD起] 需求内容`；`progress` = 0～100；`display_label` = `至MM-DD (xx%)`（结束日 + 完成度，**label 必须 `valueField: display_label`**，禁止默认只显示数值）。  
   - `type` 必须为 **bar**，`direction` 必须为 **horizontal**；**`yField`** 绑定 **`display_name`**；**`xField`** 绑定 **`progress`**。  
   - **X 轴（进度）**：数值域 **固定 min=0、max=100**。  
   - **配色**：主色 **飞书蓝 `#3370FF`**；完成度 **≥99.5%** 视为已满，条形主色可用 **绿色 `#24A159`**。  
   - **柱宽与高度（脚本已注入，你禁止擅自去掉）**：`chart_spec` 顶层须含 **`barMaxWidth`/`barWidth`（如 20px）**；单条主进度卡片使用 **`height: 140px`** 级固定高度。  
   - 不在此卡用环形图代替条形图。

3. **人员子进度 + 时间跨度**  
   - 飞书不支持图表点击下钻：人员明细必须用 **Markdown** 呈现。  
   - **禁止**在 ``lark_md`` 中使用 ``<details>``/``<summary>``（易触发飞书 **Internal Error**）；用 **加粗标题** ``**👥 人员执行明细**`` + 分行列表即可；**禁止**「（点击展开）」等冗余提示。  
   - 每行格式示例：👤 **姓名** · 任务: … · 进度: ▓/░ 模拟 10 格小进度条 **60%**（与数据一致）。  
   - 人员块下方**单独**一条 Markdown：`📅 **时间跨度**：{YYYY-MM-DD} 至 {YYYY-MM-DD}`；无结束或占位则用 `{开始} 至今`（日期来自多维表原始列经 `pmo_parse_bitable_date_cell_to_date`；文案见 `pmo_requirement_battle_time_span_md`）。

## 卡片二：资源任务负荷（原生表格 + 说明）

- **禁止**为此卡生成 VChart。  
- **禁止**用无序列表/纯文字堆人名任务代替表格；脚本已用飞书 **``table``** 组件渲染四列，Agent **若手工补全**须保持「表格式」可读性，勿覆盖为纯列表。  
- **推送范围（与多维表不同）**：`tool_data_visualizer._pmo_build_resource_load_interactive_card` **仅包含「任务1…」中至少有一条非空的人员**；任务列全空的人员**不出现在本卡片**（多维表「人员分配」仍为干系人全量行）。  
- **列语义**：**任务1→🔴 P0**，**任务2→🟠 P1/P2**，**任务3 及以后→🟢 其它**；空单元格为 **「—」**；多任务用 **`<br>`** 换行。  

**列结构示意（与脚本一致，实际由 table 组件绘制）：**

| 研发人员 | P0 高优 | P1/P2 | 其它/日常 |
| --- | --- | --- | --- |
| Seth | 并发状态机… | 基础接口… | — |
| Gavin | — | 动效联调… | 日常修复… |

## 卡片三：版本发布情况（纯 Markdown 富文本）

- **禁止**统计图。  
- 使用 **Emoji** 层级：📦 版本标题行、✨ 核心需求（✅ 条目）、🐛 修复与优化（🛠️ 条目，可按关键词将「修复类」从长文本中拆分）。  
- 发布时间优先来自表字段；缺省时用快照日并标明「快照参照」。

（规范结束）
"""


def get_pmo_dashboard_three_cards_task_spec() -> dict[str, Any]:
    """三张仪表盘飞书卡：供编排层注入 `agent_instructions`（与 `tool_data_visualizer.send_pmo_three_dashboard_cards` 的 Python 实现一致）。"""
    return {
        "task_id": "pmo_dashboard_three_cards_ux",
        "title": "PMO 三张仪表盘飞书卡片（需求战报 / 资源负荷 / 版本发布）UI/UX 规范",
        "implementation_note": (
            "卡片 JSON 由 `l3_node.primitives.mcp.mcp_tools.pmo_bmo.tool_data_visualizer.send_pmo_three_dashboard_cards` 构建；"
            "需求战报 VChart 的 data 与 chart_spec 字段名以代码为准：display_name、progress、display_label。"
            "本 spec 供 L3 Agent 对齐文案与任何手工补全时的视觉规范。"
        ),
        "requirement_battle_vchart_schema": {
            "data_row": {
                "display_name": "[MM-DD起] 需求内容",
                "progress": "number 0-100",
                "display_label": "至MM-DD (xx%)",
            },
            "chart_spec_bindings": {
                "yField": "display_name",
                "xField": "progress",
                "label.valueField": "display_label",
            },
        },
        "agent_instructions": PMO_DASHBOARD_THREE_CARDS_UX_AGENT_INSTRUCTIONS.strip(),
    }


def get_pmo_person_task_stats_task_spec() -> dict[str, Any]:
    """按人任务统计：任务元数据 + Agent 说明全文。"""
    return {
        "task_id": "pmo_person_task_stats",
        "title": "PMO 按人员统计任务（产品/开发/美术 + 干系人表）",
        "process_flow_doc_relative": PMO_PROCESS_FLOW_DOC_REL,
        "stakeholder_doc_relative": PMO_STAKEHOLDER_DOC_REL,
        "output_dir_relative": PMO_OUTPUT_REL,
        "output_filename_pattern": PMO_PERSON_STATS_OUTPUT_BASENAME,
        "wiki_reference_urls": dict(PMO_PERSON_STATS_WIKI),
        "export_slugs": dict(PMO_PERSON_STATS_SLUGS),
        "mcp_required": [
            {
                "mcp": "atom_pmo_lark_doc",
                "operation": "export_pmo_tables",
                "note": "拉取六表（含本任务所需三张）；不新增 MCP。",
            }
        ],
        "agent_instructions": PMO_PERSON_STATS_AGENT_INSTRUCTIONS.strip(),
    }


def build_pmo_person_task_stats_context(
    project_root: Path | None = None,
    snapshot_date: str | None = None,
) -> dict[str, Any]:
    """解析 raw、流程规范、干系人表、输出 MD 路径。"""
    from l3_node.primitives.mcp.mcp_tools.pmo_bmo.paths import get_pmo_raw_dir
    from l3_node.paths import get_app_root

    root = project_root or get_app_root()
    snap = (snapshot_date or date.today().isoformat()).strip()[:10]

    raw_dir = root / PMO_RAW_REL
    json_dir = get_pmo_raw_dir()
    proc = root / PMO_PROCESS_FLOW_DOC_REL
    stake = root / PMO_STAKEHOLDER_DOC_REL
    out_dir = root / PMO_OUTPUT_REL

    files: dict[str, Any] = {}
    missing_md: list[str] = []
    for key, slug in PMO_PERSON_STATS_SLUGS.items():
        md_path = pmo_repo_raw_md_path(raw_dir, slug)
        js_path = json_dir / f"{snap}_{slug}.json"
        try:
            md_rel = str(md_path.relative_to(root.resolve()))
        except ValueError:
            md_rel = str(md_path)
        files[key] = {
            "slug": slug,
            "md_relative": md_rel,
            "md_exists": md_path.is_file(),
            "json_path": str(js_path),
            "json_exists": js_path.is_file(),
        }
        if not md_path.is_file():
            missing_md.append(key)

    try:
        st_rel = str(stake.relative_to(root.resolve()))
    except ValueError:
        st_rel = str(stake)

    out_md = out_dir / PMO_PERSON_STATS_OUTPUT_BASENAME
    try:
        out_rel = str(out_md.relative_to(root.resolve()))
    except ValueError:
        out_rel = str(out_md)

    ready = (
        not missing_md
        and proc.is_file()
        and stake.is_file()
    )
    return {
        "snapshot_date": snap,
        "project_root": str(root.resolve()),
        "process_flow_doc": str(proc.resolve()) if proc.is_file() else str(proc),
        "process_flow_exists": proc.is_file(),
        "stakeholder_doc": str(stake.resolve()) if stake.is_file() else str(stake),
        "stakeholder_doc_relative": st_rel,
        "stakeholder_doc_exists": stake.is_file(),
        "output_dir": str(out_dir.resolve()),
        "output_markdown": str(out_md.resolve()),
        "output_markdown_relative": out_rel,
        "raw_files": files,
        "raw_missing_keys": missing_md,
        "status": "ready" if ready else "incomplete",
    }


def _parse_stakeholder_name_dept(stakeholder_path: Path) -> dict[str, str]:
    """名称(小写) -> 部门"""
    out: dict[str, str] = {}
    if not stakeholder_path.is_file():
        return out
    for line in stakeholder_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("|") or "---" in line[:10]:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 4:
            continue
        name = parts[1].lower()
        dept = parts[2] if len(parts) > 2 else ""
        if name and name != "名称":
            out[name] = dept
    return out


def _parse_stakeholder_table_rows(stakeholder_path: Path) -> list[dict[str, str]]:
    """
    解析 ``K11_需求池_干系人.md`` 主表：| 名称 | 部门 | 职能 | 人员 |
    返回每行 dict：名称（小写键）、部门、职能、人员（展示名，用于 Lark「人员」列）。
    """
    rows: list[dict[str, str]] = []
    if not stakeholder_path.is_file():
        return rows
    for line in stakeholder_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("|") or "---" in line[:12]:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 5:
            continue
        key = parts[1].lower()
        if not key or key == "名称":
            continue
        rows.append(
            {
                "名称": key,
                "部门": parts[2] if len(parts) > 2 else "",
                "职能": parts[3] if len(parts) > 3 else "",
                "人员": parts[4] if len(parts) > 4 else "",
            }
        )
    return rows


def _pmo_split_assignee_tokens(s: str) -> list[str]:
    """多维表「张三;李四」或「A; B」拆成单人 token。"""
    out: list[str] = []
    for seg in (s or "").replace("；", ";").split(";"):
        t = seg.strip()
        if t:
            out.append(t)
    return out


def _pmo_match_stakeholder_sid(token: str, stakeholders: list[dict[str, str]]) -> str | None:
    """将 JSON 里的人名/token 对齐到干系人行的「名称」小写键。"""
    t = (token or "").strip()
    if not t:
        return None
    tl = t.lower()
    for r in stakeholders:
        if r["名称"] == tl:
            return r["名称"]
    for r in stakeholders:
        per = (r.get("人员") or "").strip().lower()
        if per and per == tl:
            return r["名称"]
    for r in stakeholders:
        per = (r.get("人员") or "").strip().lower()
        if per:
            parts = per.split()
            if tl == parts[0] or (len(tl) >= 2 and tl in parts):
                return r["名称"]
            if tl in per.replace(" ", ""):
                return r["名称"]
    for r in stakeholders:
        nk = r["名称"]
        if tl in nk or nk in tl:
            return r["名称"]
    return None


def _pmo_tasks_by_stakeholder_from_dev_art(
    dev_recs: list[dict[str, Any]],
    art_recs: list[dict[str, Any]],
    stakeholders: list[dict[str, str]],
    *,
    max_tasks_per_person: int = 80,
) -> dict[str, list[str]]:
    """
    按干系人「名称」键汇总任务：开发来自 `任务执行人`+`任务`，美术来自 `设计责任人`+任务标题列。
    同一任务文本去重；开发前缀「开发:」，美术前缀「美术:」。
    """
    from collections import defaultdict

    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _cell_to_text

    acc: dict[str, list[str]] = defaultdict(list)
    seen: dict[str, set[str]] = defaultdict(set)

    def push(sid: str, prefix: str, title: str) -> None:
        title = (title or "").strip()
        if not title:
            return
        line = f"{prefix}{title}" if prefix else title
        if line in seen[sid]:
            return
        if len(acc[sid]) >= max_tasks_per_person:
            return
        seen[sid].add(line)
        acc[sid].append(line)

    for r in dev_recs:
        fld = r.get("fields") or {}
        for tok in _pmo_split_assignee_tokens(_cell_to_text(fld.get("任务执行人"))):
            sid = _pmo_match_stakeholder_sid(tok, stakeholders)
            if sid:
                push(sid, "开发: ", _cell_to_text(fld.get("任务")))

    art_title_key = "任务（交互动画注意问题请标注）"
    for r in art_recs:
        fld = r.get("fields") or {}
        title = _cell_to_text(fld.get(art_title_key) or fld.get("任务"))
        for tok in _pmo_split_assignee_tokens(_cell_to_text(fld.get("设计责任人"))):
            sid = _pmo_match_stakeholder_sid(tok, stakeholders)
            if sid:
                push(sid, "美术: ", title)

    return dict(acc)


def _pmo_report_week_bounds(reference: date) -> tuple[date, date]:
    """
    自然周：周一至周日，周界包含 reference 所在周。
    例：reference=2026-04-02（四）→ 周一 2026-03-30 ～ 周日 2026-04-05。
    """
    monday = reference - timedelta(days=reference.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def _pmo_parse_iso_date(s: str) -> date | None:
    t = (s or "").strip()[:10]
    if len(t) < 10 or t[4] != "-":
        return None
    try:
        y, m, d = int(t[:4]), int(t[5:7]), int(t[8:10])
        return date(y, m, d)
    except ValueError:
        return None


def _pmo_field_to_date(val: Any) -> date | None:
    ts = _fmt_bitable_ts(val)
    if ts:
        return _pmo_parse_iso_date(ts)
    return None


def _pmo_dev_art_interval_overlaps_week(fld: dict[str, Any], week_start: date, week_end: date) -> bool:
    """
    开发/美术任务是否与本自然周有交集：用「开始日期」「交付日期」构成的区间与 [week_start, week_end] 重叠。
    两日期皆空则 **不包含**（避免把无日期任务整表扫入本周）。
    """
    s = _pmo_field_to_date(fld.get("开始日期"))
    e = _pmo_field_to_date(fld.get("交付日期"))
    if s is None and e is None:
        return False
    if s is not None and e is None:
        return week_start <= s <= week_end
    if s is None and e is not None:
        return week_start <= e <= week_end
    assert s is not None and e is not None
    a, b = (s, e) if s <= e else (e, s)
    return a <= week_end and b >= week_start


def _pmo_product_row_in_week(fld: dict[str, Any], week_start: date, week_end: date) -> bool:
    """产品细表：「开始日期」「交付日期」（或「开始」「交付」）；皆空则不计入本周。"""
    s = _pmo_field_to_date(fld.get("开始日期")) or _pmo_field_to_date(fld.get("开始"))
    e = _pmo_field_to_date(fld.get("交付日期")) or _pmo_field_to_date(fld.get("交付"))
    if s is None and e is None:
        return False
    if s is not None and e is None:
        return week_start <= s <= week_end
    if s is None and e is not None:
        return week_start <= e <= week_end
    assert s is not None and e is not None
    a, b = (s, e) if s <= e else (e, s)
    return a <= week_end and b >= week_start


def _pmo_fine_priority_rank(prio: str) -> tuple[int, str]:
    """细需求优先级排序键：P00 < P0 < P1 …；无法识别置后。"""
    s = (prio or "").strip().upper()
    for i, lab in enumerate(("P00", "P0", "P1", "P2", "P3", "P4")):
        if lab in s or s.startswith(lab):
            return (i, s)
    return (50, s)


def _pmo_coarse_milestone_touches_week(
    c: dict[str, Any], week_start: date, week_end: date, year_hint: int
) -> bool:
    """主线表实施阶段单元格内若出现落在本周的日期（ISO 或 本年 MM.DD），则视为本周相关需求。"""
    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _cell_to_text

    for v in (c.get("milestones") or {}).values():
        t = _cell_to_text(v)
        for m in re.finditer(r"\d{4}-\d{2}-\d{2}", t):
            d = _pmo_parse_iso_date(m.group(0))
            if d and week_start <= d <= week_end:
                return True
        for m in re.finditer(r"\b(\d{1,2})\.(\d{1,2})\b", t):
            mm, dd = int(m.group(1)), int(m.group(2))
            try:
                d = date(year_hint, mm, dd)
                if week_start <= d <= week_end:
                    return True
            except ValueError:
                continue
    return False


def _fmt_bitable_ts(val: Any) -> str:
    """将多维表单元格转为 YYYY-MM-DD；无法识别则返回空，避免同步到 Lark 日期列时出现 DatetimeFieldConvFail。"""
    if val is None or val == "":
        return ""
    try:
        if isinstance(val, (int, float)):
            from datetime import datetime, timezone

            return datetime.fromtimestamp(float(val) / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        pass
    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _cell_to_text

    t = _cell_to_text(val).strip()
    if not t or t in ("-", "—"):
        return ""
    # 已是日期前缀
    import re as _re

    m = _re.match(r"^(\d{4}-\d{2}-\d{2})", t)
    if m:
        return m.group(1)
    # 纯数字毫秒时间戳字符串
    if t.replace(".", "").isdigit() and "-" not in t:
        try:
            from datetime import datetime, timezone

            raw = float(t)
            if raw > 1e12:
                return datetime.fromtimestamp(raw / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
            if raw > 1e9:
                return datetime.fromtimestamp(raw, tz=timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            return ""
    return ""


def _md_cell(s: Any) -> str:
    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _cell_to_text

    t = _cell_to_text(s) if s is not None else ""
    return t.replace("|", "\\|").replace("\n", " ")[:800]


def _norm_person_key(display: str) -> str:
    s = (display or "").strip()
    if not s:
        return "__unassigned__"
    return s.lower()


def _pmo_field_user_list(raw: Any) -> list[str]:
    """多维表「任务执行人」「设计责任人」等：用户数组或分号分隔字符串 → 展示名列表。"""
    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _cell_to_text

    out: list[str] = []
    if isinstance(raw, list):
        for it in raw:
            if isinstance(it, dict):
                d = (it.get("name") or it.get("en_name") or "").strip()
                if d:
                    out.append(d)
            else:
                t = _cell_to_text(it).strip()
                if t:
                    out.extend(_pmo_split_assignee_tokens(t))
    elif raw is not None:
        t = _cell_to_text(raw).strip()
        if t:
            out.extend(_pmo_split_assignee_tokens(t))
    return out


def _pmo_infer_task_completion_percent(fld: dict[str, Any], *, is_dev: bool) -> tuple[str, float | None]:
    """
    根据进度/状态等启发式估算 0～100；无法估算时返回 (「—」, None)。
    标签第二列用于说明依据，便于人工复核。
    """
    import re

    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _cell_to_text

    prog = _cell_to_text(fld.get("进度"))
    st = _cell_to_text(fld.get("状态"))
    blob = f"{prog} {st}"
    if "提前" in prog or "🟢" in prog:
        return ("进度", 100.0)
    if "按时" in prog or "🔵" in prog:
        return ("进度", 100.0)
    if "延期" in prog or "🔴" in prog:
        return ("进度", 65.0)
    m = re.search(r"(\d{1,3})\s*%", blob)
    if m:
        v = int(m.group(1))
        if 0 <= v <= 100:
            return ("进度中的%", float(v))
    if is_dev:
        if "自测" in st or ("完成" in st and "开发" in st):
            return ("状态", 90.0)
        if "测试" in st or "提测" in st:
            return ("状态", 85.0)
        if "开发中" in st or "进行中" in st:
            return ("状态", 55.0)
        if "确认" in st:
            return ("状态", 40.0)
        if "待" in st:
            return ("状态", 20.0)
    else:
        fin = _cell_to_text(fld.get("最终验收")).strip()
        if fin and fin not in ("-", "—", "－"):
            return ("最终验收", 100.0)
    return ("—", None)


def write_pmo_requirement_participants_markdown_from_raw(project_root: Path, snapshot_date: str) -> Path:
    """
    以 **req_march_coarse** 每行「列2·需求内容」为锚点，将 **dev_tasks_by_assignee** / **art_tasks_by_designer**
    中每条任务经 NL 匹配归入大需求；按 **任务执行人** / **设计责任人** 列出其 **归属于该需求** 的任务与完成度估算。
    输出 ``docs/pmo_bmo_plugin/output/PMO_需求人员参与明细.md``（覆盖）。
    """
    from collections import defaultdict

    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _cell_to_text
    from l3_node.primitives.mcp.mcp_tools.pmo_bmo.paths import get_pmo_raw_dir

    snap = snapshot_date.strip()[:10]
    json_dir = get_pmo_raw_dir()
    paths = {
        "coarse": json_dir / f"{snap}_req_march_coarse.json",
        "dev_assign": json_dir / f"{snap}_dev_tasks_by_assignee.json",
        "art_designer": json_dir / f"{snap}_art_tasks_by_designer.json",
    }
    for k, p in paths.items():
        if not p.is_file():
            raise FileNotFoundError(f"缺少需求人员参与 raw JSON ({k}): {p}")

    coarse_doc = json.loads(paths["coarse"].read_text(encoding="utf-8"))
    dev_doc = json.loads(paths["dev_assign"].read_text(encoding="utf-8"))
    art_doc = json.loads(paths["art_designer"].read_text(encoding="utf-8"))

    coarse_list = _pmo_parse_coarse_requirements_rows(coarse_doc.get("records") or [])
    if not coarse_list:
        raise ValueError("req_march_coarse 无有效需求行（列2 为空或仅表头）")

    ref_day = date.today()
    week_start, week_end = _pmo_report_week_bounds(ref_day)

    needles = [f"{c['category']} {c['title']}".strip() for c in coarse_list]
    n_req = len(coarse_list)

    rid_to_parent: dict[str, str | None] = {}
    rid_to_task: dict[str, str] = {}
    for r in dev_doc.get("records") or []:
        rid = str(r.get("record_id") or r.get("id") or "")
        if not rid:
            continue
        fld0 = r.get("fields") or {}
        rid_to_task[rid] = _cell_to_text(fld0.get("任务")).strip()
        rid_to_parent[rid] = _pmo_dev_parent_record_id(fld0)

    by_req_person: list[defaultdict[str, dict[str, Any]]] = [
        defaultdict(lambda: {"display": "", "dev": [], "art": []}) for _ in range(n_req)
    ]

    dev_unmatched: list[dict[str, Any]] = []
    art_title_key = "任务（交互动画注意问题请标注）"

    for r in dev_doc.get("records") or []:
        fld = r.get("fields") or {}
        rid = str(r.get("record_id") or r.get("id") or "")
        if not _pmo_dev_art_interval_overlaps_week(fld, week_start, week_end):
            continue
        blob = _pmo_build_dev_match_blob(fld, rid, rid_to_parent, rid_to_task)
        if not blob.strip():
            continue
        best_i, best_s = 0, 0.0
        for i, nd in enumerate(needles):
            s = _pmo_nl_match_score(nd, blob)
            if s > best_s:
                best_s, best_i = s, i
        pct_label, pct = _pmo_infer_task_completion_percent(fld, is_dev=True)
        row: dict[str, Any] = {
            "任务摘要": _md_cell(fld.get("任务")),
            "匹配分": f"{best_s:.2f}",
            "状态": _md_cell(fld.get("状态")),
            "进度": _md_cell(fld.get("进度")),
            "完成度_pct": pct,
            "完成度标签": pct_label,
            "Sprint": _md_cell(fld.get("Sprint")),
            "起止": f"{_fmt_bitable_ts(fld.get('开始日期'))} → {_fmt_bitable_ts(fld.get('交付日期'))}",
            "record_id": rid,
        }
        people = _pmo_field_user_list(fld.get("任务执行人"))
        if best_s < PMO_MATCH_MIN_SCORE:
            dev_unmatched.append({**row, "人员": ", ".join(people) if people else "—"})
            continue
        if not people:
            dev_unmatched.append({**row, "人员": "（未指定）"})
            continue
        for disp in people:
            pk = _norm_person_key(disp)
            bucket = by_req_person[best_i][pk]
            if not bucket["display"]:
                bucket["display"] = disp.strip() or pk
            bucket["dev"].append(row)

    art_unmatched: list[dict[str, Any]] = []
    for r in art_doc.get("records") or []:
        fld = r.get("fields") or {}
        rid = str(r.get("record_id") or r.get("id") or "")
        if not _pmo_dev_art_interval_overlaps_week(fld, week_start, week_end):
            continue
        blob = _pmo_build_art_match_blob(fld)
        title = _cell_to_text(fld.get(art_title_key) or fld.get("任务")).strip()
        if not title and not blob.strip():
            continue
        best_i, best_s = 0, 0.0
        for i, nd in enumerate(needles):
            s = _pmo_nl_match_score(nd, blob or title)
            if s > best_s:
                best_s, best_i = s, i
        pct_label, pct = _pmo_infer_task_completion_percent(fld, is_dev=False)
        row = {
            "任务摘要": _md_cell(fld.get(art_title_key) or fld.get("任务")),
            "匹配分": f"{best_s:.2f}",
            "状态": _md_cell(fld.get("状态")),
            "进度": _md_cell(fld.get("进度")),
            "完成度_pct": pct,
            "完成度标签": pct_label,
            "Sprint": _md_cell(fld.get("Sprint")),
            "起止": f"{_fmt_bitable_ts(fld.get('开始日期'))} → {_fmt_bitable_ts(fld.get('交付日期'))}",
            "record_id": rid,
        }
        people = _pmo_field_user_list(fld.get("设计责任人"))
        if best_s < PMO_MATCH_MIN_SCORE:
            art_unmatched.append({**row, "人员": ", ".join(people) if people else "—"})
            continue
        if not people:
            art_unmatched.append({**row, "人员": "（未指定）"})
            continue
        for disp in people:
            pk = _norm_person_key(disp)
            bucket = by_req_person[best_i][pk]
            if not bucket["display"]:
                bucket["display"] = disp.strip() or pk
            bucket["art"].append(row)

    dept_by_name = _parse_stakeholder_name_dept(project_root / PMO_STAKEHOLDER_DOC_REL)

    out_dir = project_root / PMO_OUTPUT_REL
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / PMO_REQ_PARTICIPANTS_OUTPUT_BASENAME

    lines: list[str] = [
        f"# PMO 大需求执行人员与任务（{snap} · 本周 {week_start.isoformat()}～{week_end.isoformat()}）",
        "",
        f"> **输出说明**：固定文件 `docs/pmo_bmo_plugin/output/{PMO_REQ_PARTICIPANTS_OUTPUT_BASENAME}`，每次运行 **覆盖**。"
        f"每人仅列出 **经 NL 匹配归入本需求** 的开发/美术任务（非此人全部任务）。",
        f"> **本周**：以 **{ref_day.isoformat()}** 所在自然周为准；仅含 **开始/交付** 与该周有交集的任务。",
        f"> **关联**：领导视图与周负荷总览见同目录 `{PMO_LEADERSHIP_BRIEF_OUTPUT_BASENAME}`（与 person-stats / full 第三步一并生成）。",
        "",
        "## 数据源与方法",
        "",
        f"- **大需求锚点**：`{snap}_req_march_coarse.json`（列2·需求内容；列1 继承），与 `PMO_大需求对齐.md` 一致。",
        f"- **开发**：`{snap}_dev_tasks_by_assignee.json`（**任务执行人** 可多选）。",
        f"- **美术**：`{snap}_art_tasks_by_designer.json`（**设计责任人** 可多选）。",
        f"- **匹配**：`需求类型 + 需求内容` vs 任务标题+父链+Sprint 等；阈值 **≥ {PMO_MATCH_MIN_SCORE:.2f}**（同 `PMO_MATCH_MIN_SCORE`）。",
        "- **完成度**：由进度/状态等 **启发式估算**，「—」表示无法从字段推断。",
        f"- **部门**：来自 `{PMO_STAKEHOLDER_DOC_REL}`（名称小写匹配）。",
        "",
    ]

    def _avg_pct(tasks: list[dict[str, Any]]) -> str:
        vals = [float(t["完成度_pct"]) for t in tasks if t.get("完成度_pct") is not None]
        if not vals:
            return "—"
        return f"{sum(vals) / len(vals):.0f}%"

    hdr_dev = [
        "任务摘要",
        "匹配分",
        "状态",
        "进度",
        "完成度(估算)",
        "依据",
        "Sprint",
        "起止",
        "record_id",
    ]

    for ci, c in enumerate(coarse_list):
        bucket_map = by_req_person[ci]
        ordered_keys = sorted(bucket_map.keys(), key=lambda k: bucket_map[k]["display"].lower())
        any_out = False
        for pk in ordered_keys:
            info = bucket_map[pk]
            dev_tasks: list[dict[str, Any]] = info["dev"]
            art_tasks: list[dict[str, Any]] = info["art"]
            if dev_tasks or art_tasks:
                any_out = True
                break
        if not any_out and not _pmo_coarse_milestone_touches_week(c, week_start, week_end, ref_day.year):
            continue

        lines.append(f"## {c['title']}")
        lines.append("")
        cat_disp = c["category"] or "—"
        lines.append(
            f"- **需求类型**：{cat_disp}　**负责人**：{_md_cell(c['owner'])}　**优先级**：{_md_cell(c['priority'])}"
        )
        lines.append("")

        if not bucket_map:
            lines.append("### 参与人员与任务")
            lines.append("")
            lines.append("（无）")
            lines.append("")
            continue

        lines.append("### 参与人员与任务")
        lines.append("")
        any_out = False
        for pk in ordered_keys:
            info = bucket_map[pk]
            disp = info["display"] or pk
            lk = disp.lower()
            dept = dept_by_name.get(lk, dept_by_name.get(lk.split()[0], "") if lk else "")
            dev_tasks = info["dev"]
            art_tasks = info["art"]
            if not dev_tasks and not art_tasks:
                continue
            any_out = True
            if dev_tasks:
                lines.append(f"#### 开发 · {_md_cell(disp)}（部门：{dept or '—'}）")
                lines.append("")
                lines.append(
                    f"- **本需求下开发任务数**：{len(dev_tasks)}　**平均完成度（估算）**：{_avg_pct(dev_tasks)}"
                )
                lines.append("")
                lines.append("| " + " | ".join(hdr_dev) + " |")
                lines.append("| " + " | ".join(["---"] * len(hdr_dev)) + " |")
                for t in sorted(dev_tasks, key=lambda x: float(x.get("匹配分", "0") or 0), reverse=True):
                    pct_s = "—" if t.get("完成度_pct") is None else f"{float(t['完成度_pct']):.0f}%"
                    lines.append(
                        "| "
                        + " | ".join(
                            [
                                t["任务摘要"],
                                t["匹配分"],
                                t["状态"],
                                t["进度"],
                                pct_s,
                                _md_cell(t.get("完成度标签")),
                                t["Sprint"],
                                t["起止"],
                                t["record_id"],
                            ]
                        )
                        + " |"
                    )
                lines.append("")
            if art_tasks:
                lines.append(f"#### 美术 · {_md_cell(disp)}（部门：{dept or '—'}）")
                lines.append("")
                lines.append(
                    f"- **本需求下美术任务数**：{len(art_tasks)}　**平均完成度（估算）**：{_avg_pct(art_tasks)}"
                )
                lines.append("")
                lines.append("| " + " | ".join(hdr_dev) + " |")
                lines.append("| " + " | ".join(["---"] * len(hdr_dev)) + " |")
                for t in sorted(art_tasks, key=lambda x: float(x.get("匹配分", "0") or 0), reverse=True):
                    pct_s = "—" if t.get("完成度_pct") is None else f"{float(t['完成度_pct']):.0f}%"
                    lines.append(
                        "| "
                        + " | ".join(
                            [
                                t["任务摘要"],
                                t["匹配分"],
                                t["状态"],
                                t["进度"],
                                pct_s,
                                _md_cell(t.get("完成度标签")),
                                t["Sprint"],
                                t["起止"],
                                t["record_id"],
                            ]
                        )
                        + " |"
                    )
                lines.append("")
        if not any_out:
            lines.append("（本需求下无已匹配且指定执行人/设计人的子任务。）")
            lines.append("")

    lines.append("## 附录 A · 未达匹配阈值或未指定执行人的开发任务")
    lines.append("")
    lines.append(
        f"> 相似度 **低于 {PMO_MATCH_MIN_SCORE:.2f}** 或无 **任务执行人**（前 **{min(80, len(dev_unmatched))}** 条）。"
    )
    lines.append("")
    uh = ["任务摘要", "匹配分", "状态", "进度", "人员", "record_id"]
    if dev_unmatched:
        lines.append("| " + " | ".join(uh) + " |")
        lines.append("| " + " | ".join(["---"] * len(uh)) + " |")
        for row in dev_unmatched[:80]:
            lines.append(
                "| "
                + " | ".join(
                    [
                        row["任务摘要"],
                        row["匹配分"],
                        row["状态"],
                        row["进度"],
                        _md_cell(row.get("人员")),
                        row["record_id"],
                    ]
                )
                + " |"
            )
        lines.append("")
    else:
        lines.append("（无）")
        lines.append("")

    lines.append("## 附录 B · 未达匹配阈值或未指定设计人的美术任务")
    lines.append("")
    lines.append(
        f"> 相似度 **低于 {PMO_MATCH_MIN_SCORE:.2f}** 或无 **设计责任人**（前 **{min(80, len(art_unmatched))}** 条）。"
    )
    lines.append("")
    if art_unmatched:
        lines.append("| " + " | ".join(uh) + " |")
        lines.append("| " + " | ".join(["---"] * len(uh)) + " |")
        for row in art_unmatched[:80]:
            lines.append(
                "| "
                + " | ".join(
                    [
                        row["任务摘要"],
                        row["匹配分"],
                        row["状态"],
                        row["进度"],
                        _md_cell(row.get("人员")),
                        row["record_id"],
                    ]
                )
                + " |"
            )
        lines.append("")
    else:
        lines.append("（无）")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def write_pmo_person_task_stats_markdown_from_raw(project_root: Path, snapshot_date: str) -> Path:
    """
    从当日 PMO raw JSON（产品/开发/美术）确定性生成 ``docs/pmo_bmo_plugin/output/PMO_人员任务统计.md``（覆盖）。
    不调用 LLM；按责任人 / 任务执行人 / 设计责任人归并。
    """
    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _cell_to_text
    from l3_node.primitives.mcp.mcp_tools.pmo_bmo.paths import get_pmo_raw_dir

    snap = snapshot_date.strip()[:10]
    json_dir = get_pmo_raw_dir()
    out_dir = project_root / PMO_OUTPUT_REL
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / PMO_PERSON_STATS_OUTPUT_BASENAME

    paths = {
        "product": json_dir / f"{snap}_req_march_fine.json",
        "dev": json_dir / f"{snap}_dev_tasks_view_core.json",
        "art": json_dir / f"{snap}_art_tasks_completed.json",
    }
    for k, p in paths.items():
        if not p.is_file():
            raise FileNotFoundError(f"缺少 raw JSON: {p}")

    dept_by_name = _parse_stakeholder_name_dept(project_root / PMO_STAKEHOLDER_DOC_REL)

    ref_day = date.today()
    week_start, week_end = _pmo_report_week_bounds(ref_day)

    # person_key -> { display, dept, rows: { product|dev|art: [dict] } }
    buckets: dict[str, dict[str, Any]] = {}
    max_per = 150  # 每人每来源最多行数，避免单文件过大

    def _ensure_bucket(person_display: str) -> dict[str, Any]:
        pk = _norm_person_key(person_display)
        disp = person_display.strip() or "（未指定责任人）"
        if pk not in buckets:
            lk = disp.lower()
            dept = dept_by_name.get(lk, dept_by_name.get(lk.split()[0], "") if lk else "")
            buckets[pk] = {"display": disp, "dept": dept or "—", "product": [], "dev": [], "art": []}
        return buckets[pk]

    def _add_line(bucket: dict[str, Any], src: str, row: dict[str, Any]) -> None:
        lst = bucket[src]
        if len(lst) >= max_per:
            return
        lst.append(row)

    # --- product ---
    prod = json.loads(paths["product"].read_text(encoding="utf-8"))
    for rec in prod.get("records") or []:
        fld = rec.get("fields") or {}
        if not _pmo_product_row_in_week(fld, week_start, week_end):
            continue
        rid = rec.get("record_id") or rec.get("id") or ""
        person = ""
        for k in ("责任人", "开发执行人", "美术执行人"):
            t = _cell_to_text(fld.get(k)).strip()
            if t:
                person = t.split(";")[0].strip()
                break
        b = _ensure_bucket(person)
        _add_line(
            b,
            "product",
            {
                "需求简述": _md_cell(fld.get("需求简述")),
                "Sprint": _md_cell(fld.get("Sprint")),
                "优先级": _md_cell(fld.get("优先级")),
                "需求状态": _md_cell(fld.get("需求状态")),
                "开发状态": _md_cell(fld.get("开发状态")),
                "进度": _md_cell(fld.get("进度")),
                "record_id": str(rid),
            },
        )

    # --- dev ---
    dev = json.loads(paths["dev"].read_text(encoding="utf-8"))
    for rec in dev.get("records") or []:
        fld = rec.get("fields") or {}
        if not _pmo_dev_art_interval_overlaps_week(fld, week_start, week_end):
            continue
        rid = rec.get("record_id") or rec.get("id") or ""
        person = _cell_to_text(fld.get("任务执行人")).strip()
        b = _ensure_bucket(person)
        _add_line(
            b,
            "dev",
            {
                "任务": _md_cell(fld.get("任务")),
                "优先级": _md_cell(fld.get("优先级")),
                "Sprint": _md_cell(fld.get("Sprint")),
                "状态": _md_cell(fld.get("状态")),
                "进度": _md_cell(fld.get("进度")),
                "开始": _fmt_bitable_ts(fld.get("开始日期")),
                "交付": _fmt_bitable_ts(fld.get("交付日期")),
                "record_id": str(rid),
            },
        )

    # --- art ---
    art = json.loads(paths["art"].read_text(encoding="utf-8"))
    for rec in art.get("records") or []:
        fld = rec.get("fields") or {}
        if not _pmo_dev_art_interval_overlaps_week(fld, week_start, week_end):
            continue
        rid = rec.get("record_id") or rec.get("id") or ""
        person = _cell_to_text(fld.get("设计责任人")).strip()
        b = _ensure_bucket(person)
        title_key = "任务（交互动画注意问题请标注）"
        _add_line(
            b,
            "art",
            {
                "任务": _md_cell(fld.get(title_key) or fld.get("任务")),
                "需求人": _md_cell(fld.get("需求人")),
                "优先级": _md_cell(fld.get("优先级")),
                "Sprint": _md_cell(fld.get("Sprint")),
                "进度": _md_cell(fld.get("进度")),
                "开始": _fmt_bitable_ts(fld.get("开始日期")),
                "交付": _fmt_bitable_ts(fld.get("交付日期")),
                "record_id": str(rid),
            },
        )

    # --- render ---
    lines: list[str] = [
        f"# PMO 人员任务统计（{snap} · 本周 {week_start.isoformat()}～{week_end.isoformat()}）",
        "",
        f"> **输出说明**：固定文件 `docs/pmo_bmo_plugin/output/{PMO_PERSON_STATS_OUTPUT_BASENAME}`，每次运行 **覆盖**；raw 快照 **snapshot_date={snap}**；**本周** 以生成日 **{ref_day.isoformat()}** 所在自然周为准。",
        "> 由 `l3_node.primitives.skills.pmo_bmo.main_skill` 根据 PMO 导出 JSON **自动生成**（规则归并，非 NL 推理）。",
        f"> **筛选**：仅列出「开始日期/交付日期」与本周有交集的产品/开发/美术任务；无日期行不纳入。",
        f"> 干系人部门来自 `{PMO_STAKEHOLDER_DOC_REL}`（按名称小写匹配）。",
        f"> 每人每来源最多展示 **{max_per}** 条，超出部分请直接查 raw JSON。",
        f"> **关联**：同目录 `{PMO_LEADERSHIP_BRIEF_OUTPUT_BASENAME}` 含本周负荷汇总、全量细需求表与卡片摘录（本任务一并生成）。",
        "",
        "## 数据源",
        "",
        f"- 产品：`{snap}_req_march_fine.json`（责任人 / 开发执行人 / 美术执行人）",
        f"- 开发：`{snap}_dev_tasks_view_core.json`（任务执行人）",
        f"- 美术：`{snap}_art_tasks_completed.json`（设计责任人）",
        "",
    ]

    def _emit_table(title: str, headers: list[str], rows: list[dict[str, Any]]) -> None:
        if not rows:
            lines.append(f"### {title}")
            lines.append("（无）")
            lines.append("")
            return
        lines.append(f"### {title}")
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
        for r in rows:
            lines.append("| " + " | ".join(_md_cell(r.get(h, "")) for h in headers) + " |")
        lines.append("")

    # stable sort by display name；跳过三周均无行的空桶
    ordered = sorted(buckets.values(), key=lambda x: x["display"].lower())
    for b in ordered:
        if not b["product"] and not b["dev"] and not b["art"]:
            continue
        lines.append(f"## {b['display']}（部门：{b['dept']}）")
        lines.append("")
        _emit_table(
            "产品（req_march_fine）",
            ["需求简述", "Sprint", "优先级", "需求状态", "开发状态", "进度", "record_id"],
            b["product"],
        )
        _emit_table(
            "开发（dev_tasks_view_core）",
            ["任务", "优先级", "Sprint", "状态", "进度", "开始", "交付", "record_id"],
            b["dev"],
        )
        _emit_table(
            "美术（art_tasks_completed）",
            ["任务", "需求人", "优先级", "Sprint", "进度", "开始", "交付", "record_id"],
            b["art"],
        )

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def write_pmo_leadership_weekly_brief_markdown_from_raw(project_root: Path, snapshot_date: str) -> Path:
    """
    汇总领导视角（时间跨度/完成度、资源↔需求、参与与版本维度）与 **本周周负荷**，输出
    ``docs/pmo_bmo_plugin/output/PMO_领导视图与周负荷摘要.md``（覆盖）。

    依赖：``req_march_fine`` + ``dev_tasks_view_core`` + ``art_tasks_completed``（与按人统计一致）。
    ``req_march_coarse`` 可选；缺失时跳过「大需求主线」小节并在文首说明。
    """
    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _cell_to_text
    from l3_node.primitives.mcp.mcp_tools.pmo_bmo.paths import get_pmo_raw_dir

    snap = snapshot_date.strip()[:10]
    json_dir = get_pmo_raw_dir()
    p_fine = json_dir / f"{snap}_req_march_fine.json"
    p_dev = json_dir / f"{snap}_dev_tasks_view_core.json"
    p_art = json_dir / f"{snap}_art_tasks_completed.json"
    p_coarse = json_dir / f"{snap}_req_march_coarse.json"
    for p in (p_fine, p_dev, p_art):
        if not p.is_file():
            raise FileNotFoundError(f"缺少领导摘要 raw JSON: {p}")

    prod_doc = json.loads(p_fine.read_text(encoding="utf-8"))
    dev_doc = json.loads(p_dev.read_text(encoding="utf-8"))
    art_doc = json.loads(p_art.read_text(encoding="utf-8"))
    coarse_missing = not p_coarse.is_file()
    coarse_doc: dict[str, Any] = (
        {"records": []} if coarse_missing else json.loads(p_coarse.read_text(encoding="utf-8"))
    )

    ref_day = date.today()
    week_start, week_end = _pmo_report_week_bounds(ref_day)
    try:
        pipe_year = int(snap[:4])
    except ValueError:
        pipe_year = ref_day.year

    align_metrics = _parse_pmo_big_requirement_alignment_metrics(project_root, snap)
    prod_recs = prod_doc.get("records") or []
    dev_recs = dev_doc.get("records") or []
    art_recs = art_doc.get("records") or []
    coarse_recs = coarse_doc.get("records") or []

    rid_to_parent: dict[str, str | None] = {}
    rid_to_task: dict[str, str] = {}
    for r in dev_recs:
        rid = str(r.get("record_id") or r.get("id") or "")
        if not rid:
            continue
        fld = r.get("fields") or {}
        rid_to_task[rid] = _cell_to_text(fld.get("任务")).strip()
        rid_to_parent[rid] = _pmo_dev_parent_record_id(fld)

    coarse_names: list[str] = []
    for r in coarse_recs:
        rid = str(r.get("record_id") or "")
        if _pmo_coarse_skip_docx_header_record(rid):
            continue
        fld = r.get("fields") or {}
        name = _pmo_coarse_req_title(fld)
        if name:
            coarse_names.append(name)

    loads: dict[str, dict[str, Any]] = {}

    def _bump(pk: str, display: str, key: str) -> None:
        if pk not in loads:
            loads[pk] = {"display": display, "prod": 0, "dev": 0, "art": 0}
        loads[pk][key] += 1

    def _fine_owner(fld: dict[str, Any]) -> tuple[str, str]:
        for k in ("责任人", "开发执行人", "美术执行人"):
            t = _cell_to_text(fld.get(k)).strip()
            if t:
                first = t.split(";")[0].strip()
                return _norm_person_key(first), first
        return "__none__", "（未指定责任人）"

    for r in prod_recs:
        fld = r.get("fields") or {}
        if not _pmo_product_row_in_week(fld, week_start, week_end):
            continue
        pk, disp = _fine_owner(fld)
        _bump(pk, disp, "prod")

    for r in dev_recs:
        fld = r.get("fields") or {}
        if not _pmo_dev_art_interval_overlaps_week(fld, week_start, week_end):
            continue
        for tok in _pmo_split_assignee_tokens(_cell_to_text(fld.get("任务执行人"))):
            t = tok.strip()
            if not t:
                continue
            _bump(_norm_person_key(t), t, "dev")

    art_title_key = "任务（交互动画注意问题请标注）"
    for r in art_recs:
        fld = r.get("fields") or {}
        if not _pmo_dev_art_interval_overlaps_week(fld, week_start, week_end):
            continue
        for tok in _pmo_split_assignee_tokens(_cell_to_text(fld.get("设计责任人"))):
            t = tok.strip()
            if not t:
                continue
            _bump(_norm_person_key(t), t, "art")

    for v in loads.values():
        v["total"] = int(v["prod"]) + int(v["dev"]) + int(v["art"])

    load_rows = sorted(loads.values(), key=lambda x: (-int(x["total"]), str(x["display"]).lower()))
    n_dev_week = sum(1 for r in dev_recs if _pmo_dev_art_interval_overlaps_week(r.get("fields") or {}, week_start, week_end))
    n_art_week = sum(1 for r in art_recs if _pmo_dev_art_interval_overlaps_week(r.get("fields") or {}, week_start, week_end))

    fine_rows: list[dict[str, Any]] = []
    n_fine_week = 0
    owner_agg: dict[str, dict[str, Any]] = {}
    for r in prod_recs:
        fld = r.get("fields") or {}
        rid = str(r.get("record_id") or r.get("id") or "")
        prio_raw = _cell_to_text(fld.get("优先级"))
        rk = _pmo_fine_priority_rank(prio_raw)
        title = _md_cell(fld.get("需求简述"))
        st_d = _fmt_bitable_ts(fld.get("开始日期")) or _fmt_bitable_ts(fld.get("开始")) or ""
        en_d = _fmt_bitable_ts(fld.get("交付日期")) or _fmt_bitable_ts(fld.get("交付")) or ""
        in_week = "是" if _pmo_product_row_in_week(fld, week_start, week_end) else "否"
        if in_week == "是":
            n_fine_week += 1
        fine_rows.append(
            {
                "_rk": rk,
                "需求简述": title,
                "优先级": _md_cell(fld.get("优先级")),
                "Sprint": _md_cell(fld.get("Sprint")),
                "开始": st_d,
                "交付": en_d,
                "本周相关": in_week,
                "进度": _md_cell(fld.get("进度")),
                "需求状态": _md_cell(fld.get("需求状态")),
                "开发状态": _md_cell(fld.get("开发状态")),
                "生产发布": _md_cell(fld.get("生产发布")),
                "record_id": rid,
            }
        )
        pk_o, disp_o = _fine_owner(fld)
        tshort = _cell_to_text(fld.get("需求简述")).strip() or "(无简述)"
        pr = _cell_to_text(fld.get("优先级")).strip()
        line = f"{tshort}（{pr}）" if pr else tshort
        if pk_o not in owner_agg:
            owner_agg[pk_o] = {"display": disp_o, "items": []}
        if len(owner_agg[pk_o]["items"]) < 120:
            owner_agg[pk_o]["items"].append(line)

    fine_rows.sort(key=lambda x: (x["_rk"][0], x["_rk"][1], x["需求简述"]))
    n_fine_total = len(fine_rows)
    cap = PMO_LEADERSHIP_BRIEF_FINE_CAP
    fine_shown = fine_rows[:cap]

    sprint_to_roots: dict[str, set[str]] = {}
    for r in dev_recs:
        fld = r.get("fields") or {}
        sp = _cell_to_text(fld.get("Sprint")).strip()
        if not sp:
            continue
        rid = str(r.get("record_id") or r.get("id") or "")
        if not rid:
            continue
        root = _pmo_dev_root_id(rid, rid_to_parent)
        root_title = rid_to_task.get(root, "").strip()
        cn = _pmo_pick_coarse_name(root_title, coarse_names)
        label = cn or root_title
        if label:
            sprint_to_roots.setdefault(sp, set()).add(label)

    out_dir = project_root / PMO_OUTPUT_REL
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / PMO_LEADERSHIP_BRIEF_OUTPUT_BASENAME

    lines: list[str] = [
        f"# PMO 领导视图与周负荷摘要（{snap}）",
        "",
        f"> **输出说明**：固定文件 `docs/pmo_bmo_plugin/output/{PMO_LEADERSHIP_BRIEF_OUTPUT_BASENAME}`，每次运行 **覆盖**。",
        f"> **快照**：raw **snapshot_date={snap}**；**本周** 以生成日 **{ref_day.isoformat()}** 所在自然周 **{week_start.isoformat()}～{week_end.isoformat()}**（周一至周日）。",
        f"> **用途**：飞书消息卡片文案、与 `{PMO_DASHBOARD_CSV_REQUIREMENT_STATUS}` 等提纯 CSV 字段对齐；细表见同目录 `{PMO_BIG_ALIGN_OUTPUT_BASENAME}`、`{PMO_PERSON_STATS_OUTPUT_BASENAME}`、`{PMO_REQ_PARTICIPANTS_OUTPUT_BASENAME}`。",
        f"> **周负荷口径**：与 `{PMO_PERSON_STATS_OUTPUT_BASENAME}` 一致——产品行用「开始/交付」与本周交集；开发/美术用「开始日期/交付日期」与本周交集；**无日期行不计入本周**。" + (
            "" if not coarse_missing else f" **注意**：未找到 `{snap}_req_march_coarse.json`，已跳过「大需求主线」小节。"
        ),
        "",
        "## 1. 本周周负荷概览",
        "",
        f"- **本周产品细需求（有日期且与本周相交）**：{n_fine_week} 条（全表共 {n_fine_total} 条）。",
        f"- **本周开发子任务**：{n_dev_week} 条；**本周美术子任务**：{n_art_week} 条。",
        "- **按人汇总（产品 / 开发 / 美术 条数）**：同一人可同时在三线出现；**合计**为三线之和。",
        "",
        "| 人员（展示名） | 产品(本周) | 开发(本周) | 美术(本周) | 合计 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for row in load_rows[:200]:
        lines.append(
            f"| {_md_cell(row['display'])} | {row['prod']} | {row['dev']} | {row['art']} | {row['total']} |"
        )
    if len(load_rows) > 200:
        lines.append(f"| … | … | … | … | *共 {len(load_rows)} 人，仅列前 200* |")
    lines.append("")

    lines.extend(
        [
            "## 2. 细需求：时间跨度与完成度（全表快照，按优先级）",
            "",
            f"共 **{n_fine_total}** 条；下表最多 **{cap}** 条（按优先级 P00→P4 再按简述排序）。列 **本周相关**=「是」表示该条产品行的日期区间与本周相交。",
            "",
            "| 需求简述 | 优先级 | Sprint | 开始 | 交付 | 本周相关 | 进度 | 需求状态 | 开发状态 | 生产发布 | record_id |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for fr in fine_shown:
        lines.append(
            "| "
            + " | ".join(
                [
                    fr["需求简述"],
                    fr["优先级"],
                    fr["Sprint"],
                    fr["开始"],
                    fr["交付"],
                    fr["本周相关"],
                    fr["进度"],
                    fr["需求状态"],
                    fr["开发状态"],
                    fr["生产发布"],
                    fr["record_id"],
                ]
            )
            + " |"
        )
    if n_fine_total > cap:
        lines.append("")
        lines.append(f"（余下 **{n_fine_total - cap}** 条请查 `{snap}_req_march_fine.json` 或缩小筛选。）")
    lines.append("")

    if not coarse_missing:
        lines.extend(["## 3. 大需求主线（req_march_coarse）", ""])
        lines.append("| 需求内容 | 需求类型 | 开始时间 | 结束时间 | 当前完成度(%) | 完成到哪一步 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        last_req_type = ""
        for r in coarse_recs:
            rid = str(r.get("record_id") or "")
            if _pmo_coarse_skip_docx_header_record(rid):
                continue
            fld = r.get("fields") or {}
            title = _pmo_coarse_req_title(fld)
            if not title:
                continue
            rt_cell = _cell_to_text(fld.get("需求类型")).strip() or _cell_to_text(fld.get("列1")).strip()
            if rt_cell:
                last_req_type = rt_cell
            req_type_disp = rt_cell or last_req_type
            start_disp = _pmo_earliest_in_pipeline_keys(fld, pipe_year)
            end_disp = _pmo_end_from_production_release(fld, pipe_year)
            am = _pmo_align_metrics_lookup(align_metrics, title)
            if am and (am.get("pct") or "").strip():
                pct_disp = (am.get("pct") or "").strip()
            else:
                pct_disp = _pmo_pipeline_filled_pct_str(fld)
            if am and (am.get("step") or "").strip():
                step_disp = (am.get("step") or "").strip()
            else:
                step_disp = _pmo_pipeline_last_stage_label(fld)
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md_cell(title),
                        _md_cell(req_type_disp),
                        _md_cell(start_disp),
                        _md_cell(end_disp),
                        _md_cell(pct_disp),
                        _md_cell(step_disp),
                    ]
                )
                + " |"
            )
        lines.append("")
    else:
        lines.extend(
            [
                "## 3. 大需求主线（req_march_coarse）",
                "",
                "（跳过：当前快照缺少 coarse JSON。）",
                "",
            ]
        )

    lines.extend(
        [
            "## 4. 产品责任人 → 细需求（全表按人聚合）",
            "",
            "每条为「责任人 / 开发执行人 / 美术执行人」优先取 **责任人** 的首个姓名；每人最多列 **120** 条简述。",
            "",
        ]
    )
    owner_ordered = sorted(owner_agg.values(), key=lambda x: (-len(x["items"]), str(x["display"]).lower()))
    for ob in owner_ordered:
        lines.append(f"### {ob['display']}")
        lines.append("")
        for it in ob["items"]:
            lines.append(f"- {it}")
        lines.append("")

    lines.extend(
        [
            "## 5. Sprint（开发树）→ 粗需求 / 根标题",
            "",
            "与 `write_pmo_dashboard_csvs` 中 **版本发布** 表口径一致：**Sprint** 字段聚合开发任务树根标题，并优先对齐 coarse **需求内容** 名称。",
            "",
        ]
    )
    for sp in sorted(sprint_to_roots.keys(), key=lambda x: x):
        names = sorted(sprint_to_roots[sp])
        joined = "；".join(names[:100])
        extra = f" …（共 {len(names)} 项）" if len(names) > 100 else ""
        lines.append(f"- **{sp}**：{joined}{extra}")
    if not sprint_to_roots:
        lines.append("（无 Sprint 或无法解析开发树。）")
    lines.append("")

    # --- 飞书卡片可粘贴块（lark_md）---
    top_busy = load_rows[:8]
    card_lines = [
        "## 6. 消息卡片用摘录（可直接复制到飞书富文本）",
        "",
        "### 块 A：本周范围 + 负荷一句话",
        "",
        "```",
        f"【PMO 周报 {snap}】本周 {week_start.strftime('%m-%d')}～{week_end.strftime('%m-%d')} · ",
        f"产品本周 {n_fine_week} 条 / 开发子任务 {n_dev_week} / 美术子任务 {n_art_week}。",
        "```",
        "",
        "### 块 B：周负荷较高人员（按合计条数 Top）",
        "",
        "```",
    ]
    for row in top_busy:
        card_lines.append(
            f"- {row['display']}: 产品{row['prod']} + 开发{row['dev']} + 美术{row['art']} = {row['total']}"
        )
    card_lines.extend(["```", "", "### 块 C：Sprint 与需求（简版，前 12 个 Sprint）", "", "```"])
    for sp in sorted(sprint_to_roots.keys(), key=lambda x: x)[:12]:
        names = sprint_to_roots[sp]
        card_lines.append(f"- {sp}: {'、'.join(sorted(names)[:6])}{'…' if len(names) > 6 else ''}")
    card_lines.extend(["```", ""])

    lines.extend(card_lines)
    coarse_data_line = (
        f"- `{snap}_req_march_coarse.json`" if not coarse_missing else "- （可选）`req_march_coarse`"
    )
    lines.extend(
        [
            "## 7. 数据源与脚本",
            "",
            "- `write_pmo_leadership_weekly_brief_markdown_from_raw`（本文件）",
            f"- `{snap}_req_march_fine.json` / `{snap}_dev_tasks_view_core.json` / `{snap}_art_tasks_completed.json`",
            coarse_data_line,
            f"- 流程说明：`{PMO_PROCESS_FLOW_DOC_REL}`",
            "",
        ]
    )

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def _pmo_import_sync_csv_to_bitable() -> Any:
    """懒加载 BI 同款 Lark 同步（skills_repo plugin）。"""
    import sys

    from l3_node.paths import get_app_root

    plugin_root = get_app_root() / "skills_repo" / "plugin" / "com.jachin.hr.recruitment"
    if plugin_root.exists() and str(plugin_root) not in sys.path:
        sys.path.insert(0, str(plugin_root))
    from tools.atom_lark_bitable_sync import sync_csv_to_bitable  # type: ignore[import-untyped]

    return sync_csv_to_bitable


def _pmo_dev_parent_record_id(fields: dict[str, Any]) -> str | None:
    pr = fields.get("父记录")
    if isinstance(pr, list):
        for it in pr:
            if isinstance(it, dict) and it.get("record_ids"):
                ids = it.get("record_ids") or []
                if ids:
                    return str(ids[0])
    return None


def _pmo_dev_root_id(rid: str, rid_to_parent: dict[str, str | None]) -> str:
    seen: set[str] = set()
    cur = rid
    while cur and cur not in seen:
        seen.add(cur)
        p = rid_to_parent.get(cur)
        if not p:
            return cur
        cur = p
    return cur


def _pmo_milestone_filled(val: Any) -> bool:
    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _cell_to_text

    t = _cell_to_text(val).strip()
    if not t or t in ("-", "—", "－"):
        return False
    try:
        v = float(str(t).replace(",", ""))
        if v > 1e11:
            return True
        if abs(v) < 1e-9:
            return False
    except (ValueError, TypeError):
        pass
    return bool(t)


def _pmo_milestone_pct_str(fields: dict[str, Any]) -> str:
    n = sum(1 for k in PMO_COARSE_MILESTONE_KEYS if _pmo_milestone_filled(fields.get(k)))
    if not PMO_COARSE_MILESTONE_KEYS:
        return ""
    pct = 100.0 * n / len(PMO_COARSE_MILESTONE_KEYS)
    return f"{pct:.1f}"


def _pmo_coarse_step_text(fields: dict[str, Any]) -> str:
    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _cell_to_text

    parts: list[str] = []
    rs = _cell_to_text(fields.get("需求状态")).strip()
    ds = _cell_to_text(fields.get("开发状态")).strip()
    if rs:
        parts.append(f"需求:{rs}")
    if ds:
        parts.append(f"开发:{ds}")
    last_m = None
    for k in PMO_COARSE_MILESTONE_KEYS:
        if _pmo_milestone_filled(fields.get(k)):
            last_m = k
    if last_m:
        parts.append(f"里程碑:{last_m}")
    return " | ".join(parts) if parts else "—"


def _pmo_pick_coarse_name(root_title: str, coarse_names: list[str]) -> str | None:
    rt = (root_title or "").strip()
    if not rt:
        return None
    for cn in coarse_names:
        if cn == rt:
            return cn
    for cn in coarse_names:
        if rt in cn or cn in rt:
            return cn
    return None


def _pmo_coarse_is_docx_flat_fields(fld: dict[str, Any]) -> bool:
    """req_march_coarse 为云文档表格导出时，records.fields 为列1/列2…而非「需求内容」。"""
    return "列1" in fld and "列2" in fld


def _pmo_coarse_fld_pipeline_val(fld: dict[str, Any], semantic_key: str) -> Any:
    """读管线日期单元格：Bitable 用语义字段名；docx 用列号。"""
    v = fld.get(semantic_key)
    if v is not None and str(v).strip() != "":
        return v
    if _pmo_coarse_is_docx_flat_fields(fld):
        col = PMO_DOCX_COARSE_PIPELINE_KEY_TO_COL.get(semantic_key)
        if col:
            return fld.get(col)
    return fld.get(semantic_key)


def _pmo_coarse_req_title(fld: dict[str, Any]) -> str:
    """主线表主键：Bitable「需求内容」/「需求简述」；docx 表为「列2」（与 _pmo_parse_coarse_requirements_rows 一致）。"""
    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _cell_to_text

    t = _cell_to_text(fld.get("需求内容")).strip()
    if t:
        return t
    t = _cell_to_text(fld.get("需求简述")).strip()
    if t:
        return t
    t = _cell_to_text(fld.get("列2")).strip()
    if t and t != "需求内容":
        return t
    return ""


def _pmo_coarse_skip_docx_header_record(record_id: str) -> bool:
    """云文档表前两行常为表头/阶段表头，与 _pmo_parse_coarse_requirements_rows 一致。"""
    return record_id in ("docx_r0", "docx_r1")


def _pmo_parse_dates_mmdd_and_iso(val: Any, year: int) -> list:
    """从单元格提取 MM.DD 及可解析为日期的值，用于比较早晚。"""
    from datetime import date
    import re

    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _cell_to_text

    out: list[date] = []
    iso = _fmt_bitable_ts(val)
    if iso and len(iso) >= 10:
        try:
            y, m, d = int(iso[:4]), int(iso[5:7]), int(iso[8:10])
            out.append(date(y, m, d))
        except ValueError:
            pass
    t = _cell_to_text(val)
    for m in re.finditer(r"\b(\d{1,2})\.(\d{1,2})\b", t):
        mm, dd = int(m.group(1)), int(m.group(2))
        try:
            out.append(date(year, mm, dd))
        except ValueError:
            continue
    return out


def _pmo_earliest_in_pipeline_keys(fld: dict[str, Any], year: int) -> str:
    """「开始时间」：管线各列中最早日期 → YYYY-MM-DD；全无则「空」。"""
    from datetime import date

    all_d: list[date] = []
    for k in PMO_COARSE_PIPELINE_DATE_KEYS:
        all_d.extend(_pmo_parse_dates_mmdd_and_iso(_pmo_coarse_fld_pipeline_val(fld, k), year))
    if not all_d:
        return "空"
    mn = min(all_d)
    return mn.isoformat()


def _pmo_end_from_production_release(fld: dict[str, Any], year: int) -> str:
    """「结束时间」：生产发布列；无有效日期则「未完成」。"""
    dates = _pmo_parse_dates_mmdd_and_iso(_pmo_coarse_fld_pipeline_val(fld, "生产发布"), year)
    if not dates:
        return "未完成"
    return max(dates).isoformat()


def _pmo_pipeline_filled_pct_str(fld: dict[str, Any]) -> str:
    """按管线列已填节点比例兜底完成度（0～100，一位小数）。"""
    n = sum(
        1
        for k in PMO_COARSE_PIPELINE_DATE_KEYS
        if _pmo_milestone_filled(_pmo_coarse_fld_pipeline_val(fld, k))
    )
    tot = len(PMO_COARSE_PIPELINE_DATE_KEYS)
    if tot <= 0:
        return "0.0"
    return f"{100.0 * n / tot:.1f}"


def _pmo_pipeline_last_stage_label(fld: dict[str, Any]) -> str:
    """兜底「完成到哪一步」：已填节点中沿管线最后一个阶段名。"""
    last: str | None = None
    for k in PMO_COARSE_PIPELINE_DATE_KEYS:
        if _pmo_milestone_filled(_pmo_coarse_fld_pipeline_val(fld, k)):
            last = k
    return last or "未进入主线"


def _parse_pmo_big_requirement_alignment_metrics(project_root: Path, snap: str) -> dict[str, dict[str, str]]:
    """
    解析 ``PMO_大需求对齐_{snap}.md`` 或 ``PMO_大需求对齐.md`` 中每节的
    「主线里程碑完成度」百分比与「推断阶段」，键为二级标题（需求名）。
    """
    import re

    out: dict[str, dict[str, str]] = {}
    base = project_root / PMO_OUTPUT_REL
    candidates = [base / f"PMO_大需求对齐_{snap}.md", base / "PMO_大需求对齐.md"]
    path = next((p for p in candidates if p.is_file()), None)
    if not path:
        return out
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    parts = re.split(r"^## (.+)$", text, flags=re.MULTILINE)
    for i in range(1, len(parts), 2):
        if i + 1 >= len(parts):
            break
        title = parts[i].strip()
        body = parts[i + 1]
        if "主线里程碑" not in body and "推断阶段" not in body:
            continue
        pct_s, step_s = "", ""
        pm = re.search(r"约\s*\*\*([\d.]+)%\*\*", body)
        if pm:
            pct_s = pm.group(1).strip()
        sm = re.search(r"推断阶段[：:]\s*\*\*([^*]+)\*\*", body)
        if sm:
            step_s = sm.group(1).strip()
        out[title] = {"pct": pct_s, "step": step_s}
    return out


def _pmo_align_metrics_lookup(amap: dict[str, dict[str, str]], title: str) -> dict[str, str] | None:
    if not title or not amap:
        return None
    t = title.strip()
    if t in amap:
        return amap[t]
    tn = "".join(t.split())
    for k, v in amap.items():
        if t == k or t in k or k in t:
            return v
        kn = "".join(k.split())
        if tn == kn or tn in kn or kn in tn:
            return v
    return None


# 「需求人员参与情况」CSV 中人员列对数上限（与 Lark 可扩展列一致）
PMO_REQ_PARTICIPATION_MAX_PAIR_COLS = 40


def _pmo_normalize_participant_pct_cell(raw: str) -> str:
    """明细中的「100%」「82.5%」「—」→ CSV 数字字符串或空（便于多维表数字列）。"""
    s = (raw or "").strip()
    if not s or s in ("—", "-", "－"):
        return ""
    if s.endswith("%"):
        return s[:-1].strip()
    return s


def _parse_pmo_req_participants_detail_md(project_root: Path, snap: str) -> dict[str, list[tuple[str, str]]]:
    """
    解析 ``output/PMO_需求人员参与明细.md``（或带日期的同名变体）。
    每个二级标题（大需求名）下，按 ``#### 开发/美术 · 姓名`` 小节提取
    （人员展示名, 平均完成度原文）。
    """
    base = project_root / PMO_OUTPUT_REL
    candidates = [
        base / PMO_REQ_PARTICIPANTS_OUTPUT_BASENAME,
        base / f"PMO_需求人员参与明细_{snap}.md",
    ]
    path = next((p for p in candidates if p.is_file()), None)
    if not path:
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    out: dict[str, list[tuple[str, str]]] = {}
    parts = re.split(r"^## (.+)$", text, flags=re.MULTILINE)
    for i in range(1, len(parts), 2):
        if i + 1 >= len(parts):
            break
        title = parts[i].strip()
        body = parts[i + 1]
        if not title or title.startswith("数据源") or "数据源与方法" in title:
            continue
        pairs: list[tuple[str, str]] = []
        for m in re.finditer(
            r"^#### (开发|美术) · (.+?)（部门：[^）]*）",
            body,
            flags=re.MULTILINE,
        ):
            role = m.group(1)
            name = (m.group(2) or "").strip()
            start = m.end()
            rest = body[start:]
            nxt = re.search(r"^#### |^## ", rest, flags=re.MULTILINE)
            chunk = rest[: nxt.start()] if nxt else rest
            pm = re.search(r"\*\*平均完成度（估算）\*\*[：:]\s*(\d+%|—)", chunk)
            pct_raw = pm.group(1).strip() if pm else "—"
            person_disp = f"{role}·{name}" if name else role
            pairs.append((person_disp, pct_raw))
        if pairs:
            out[title] = pairs
    return out


def _pmo_participants_detail_lookup(
    pmap: dict[str, list[tuple[str, str]]], title: str
) -> list[tuple[str, str]]:
    if not title or not pmap:
        return []
    t = title.strip()
    if t in pmap:
        return list(pmap[t])
    tn = "".join(t.split())
    for k, v in pmap.items():
        if t == k or t in k or k in t:
            return list(v)
        kn = "".join(k.split())
        if tn == kn or tn in kn or kn in tn:
            return list(v)
    return []


def write_pmo_dashboard_csvs(
    project_root: Path,
    snapshot_date: str,
) -> dict[str, Any]:
    """
    从 PMO raw JSON 生成四张提纯 CSV，默认落盘到 `~/.jachin/client_volumes/PMO/output/`；
    若该目录不可写（如 CSV 被 Excel 占用），整批改写到仓库内 ``docs/pmo_bmo_plugin/output_dashboard/``。

    「人员分配」以仓库 ``K11_需求池_干系人.md`` 为行基准（人员/部门/职能），任务来自
    ``{snap}_dev_tasks_by_assignee.json`` 与 ``{snap}_art_tasks_by_designer.json``（开发/美术每人视图），
    动态列 ``任务1``…``任务N``（与 Lark「人员分配」子表一致，同步时建议 ``ensure_columns: true``）。
    """
    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _cell_to_text
    from l3_node.primitives.mcp.mcp_tools.pmo_bmo.paths import ensure_pmo_dirs, get_pmo_output_client_dir, get_pmo_raw_dir

    ensure_pmo_dirs()
    snap = snapshot_date.strip()[:10]
    raw_dir = get_pmo_raw_dir()
    primary_out = get_pmo_output_client_dir()
    fallback_out = (project_root / PMO_DASHBOARD_CSV_FALLBACK_REL).resolve()
    primary_out.mkdir(parents=True, exist_ok=True)

    coarse_path = raw_dir / f"{snap}_req_march_coarse.json"
    dev_path = raw_dir / f"{snap}_dev_tasks_view_core.json"
    art_path = raw_dir / f"{snap}_art_tasks_completed.json"
    dev_assign_path = raw_dir / f"{snap}_dev_tasks_by_assignee.json"
    art_designer_path = raw_dir / f"{snap}_art_tasks_by_designer.json"
    required_paths = (dev_path, art_path, dev_assign_path, art_designer_path)
    missing = [str(p) for p in required_paths if not p.is_file()]
    if missing:
        raise FileNotFoundError("缺少 raw JSON: " + "; ".join(missing))

    coarse_missing = not coarse_path.is_file()
    if coarse_missing:
        logging.getLogger("pmo_bmo_skill").warning(
            "未找到 %s；需求完成情况/需求人员参与情况将为空，"
            "版本发布仅按开发任务根标题聚合。请修复 Docx 权限后重新导出六表。",
            coarse_path,
        )
        coarse_doc: dict[str, Any] = {"records": []}
    else:
        coarse_doc = json.loads(coarse_path.read_text(encoding="utf-8"))
    dev_doc = json.loads(dev_path.read_text(encoding="utf-8"))
    art_doc = json.loads(art_path.read_text(encoding="utf-8"))
    dev_assign_doc = json.loads(dev_assign_path.read_text(encoding="utf-8"))
    art_designer_doc = json.loads(art_designer_path.read_text(encoding="utf-8"))
    coarse_recs = coarse_doc.get("records") or []
    dev_recs = dev_doc.get("records") or []
    art_recs = art_doc.get("records") or []
    dev_assign_recs = dev_assign_doc.get("records") or []
    art_designer_recs = art_designer_doc.get("records") or []

    rid_to_parent: dict[str, str | None] = {}
    rid_to_task: dict[str, str] = {}
    for r in dev_recs:
        rid = str(r.get("record_id") or r.get("id") or "")
        if not rid:
            continue
        fld = r.get("fields") or {}
        rid_to_task[rid] = _cell_to_text(fld.get("任务")).strip()
        rid_to_parent[rid] = _pmo_dev_parent_record_id(fld)

    coarse_names: list[str] = []
    for r in coarse_recs:
        rid = str(r.get("record_id") or "")
        if _pmo_coarse_skip_docx_header_record(rid):
            continue
        fld = r.get("fields") or {}
        name = _pmo_coarse_req_title(fld)
        if not name:
            continue
        coarse_names.append(name)

    # --- 1) 需求完成情况（主列名「需求内容」与飞书多维表主字段一致，取值来自 coarse「需求内容」/「需求简述」）
    try:
        pipe_year = int(snap[:4])
    except ValueError:
        pipe_year = date.today().year
    align_metrics = _parse_pmo_big_requirement_alignment_metrics(project_root, snap)
    last_req_type_row = ""
    rows_req: list[dict[str, str]] = []
    for r in coarse_recs:
        rid = str(r.get("record_id") or "")
        if _pmo_coarse_skip_docx_header_record(rid):
            continue
        fld = r.get("fields") or {}
        title = _pmo_coarse_req_title(fld)
        if not title:
            continue
        rt_cell = _cell_to_text(fld.get("需求类型")).strip()
        if not rt_cell:
            rt_cell = _cell_to_text(fld.get("列1")).strip()
        if rt_cell:
            last_req_type_row = rt_cell
        req_type_disp = rt_cell or last_req_type_row
        start_disp = _pmo_earliest_in_pipeline_keys(fld, pipe_year)
        end_disp = _pmo_end_from_production_release(fld, pipe_year)
        am = _pmo_align_metrics_lookup(align_metrics, title)
        if am and (am.get("pct") or "").strip():
            pct_disp = (am.get("pct") or "").strip()
        else:
            pct_disp = _pmo_pipeline_filled_pct_str(fld)
        if am and (am.get("step") or "").strip():
            step_disp = (am.get("step") or "").strip()
        else:
            step_disp = _pmo_pipeline_last_stage_label(fld)
        rows_req.append(
            {
                "需求内容": title,
                "需求类型": req_type_disp,
                "开始时间": start_disp,
                "结束时间": end_disp,
                "当前完成度(%)": pct_disp,
                "完成到哪一步了": step_disp,
            }
        )

    # --- 2) 人员分配：干系人表一行一人；任务来自开发/美术「每人」视图 JSON ---
    stakeholders = _parse_stakeholder_table_rows(project_root / PMO_STAKEHOLDER_DOC_REL)
    if not stakeholders:
        raise FileNotFoundError(f"干系人表为空或不存在: {PMO_STAKEHOLDER_DOC_REL}")
    tasks_by_sid = _pmo_tasks_by_stakeholder_from_dev_art(
        dev_assign_recs, art_designer_recs, stakeholders
    )
    _max_task_cols = 50
    max_n = 2
    for s in stakeholders:
        n = len(tasks_by_sid.get(s["名称"], []))
        if n > max_n:
            max_n = n
    max_n = min(max(max_n, 2), _max_task_cols)
    task_cols = [f"任务{i}" for i in range(1, max_n + 1)]
    person_fieldnames = ["人员", "部门", "职能", *task_cols]
    rows_person: list[dict[str, str]] = []
    for s in stakeholders:
        disp = (s.get("人员") or "").strip() or s["名称"]
        row: dict[str, str] = {
            "人员": disp,
            "部门": s.get("部门", ""),
            "职能": s.get("职能", ""),
        }
        ts = tasks_by_sid.get(s["名称"], [])
        for i in range(1, max_n + 1):
            row[f"任务{i}"] = ts[i - 1] if i - 1 < len(ts) else ""
        rows_person.append(row)

    # --- 3) 需求人员参与情况（列与 Lark 一致：需求内容、需求类型、人员1/完成度1…动态扩展）
    participants_detail = _parse_pmo_req_participants_detail_md(project_root, snap)
    last_req_type_part = ""
    part_rows_data: list[tuple[str, str, list[tuple[str, str]]]] = []
    for r in coarse_recs:
        rid = str(r.get("record_id") or "")
        if _pmo_coarse_skip_docx_header_record(rid):
            continue
        fld = r.get("fields") or {}
        req_title = _pmo_coarse_req_title(fld)
        if not req_title:
            continue
        rt_cell = _cell_to_text(fld.get("需求类型")).strip()
        if not rt_cell:
            rt_cell = _cell_to_text(fld.get("列1")).strip()
        if rt_cell:
            last_req_type_part = rt_cell
        req_type_disp = rt_cell or last_req_type_part
        pairs = _pmo_participants_detail_lookup(participants_detail, req_title)
        pairs = pairs[:PMO_REQ_PARTICIPATION_MAX_PAIR_COLS]
        part_rows_data.append((req_title, req_type_disp, pairs))

    max_pn = min(
        max((len(t[2]) for t in part_rows_data), default=0),
        PMO_REQ_PARTICIPATION_MAX_PAIR_COLS,
    )
    part_fieldnames: list[str] = ["需求内容", "需求类型"]
    for i in range(1, max_pn + 1):
        part_fieldnames.extend([f"人员{i}", f"完成度{i}"])

    rows_part: list[dict[str, str]] = []
    for req_title, req_type_disp, pairs in part_rows_data:
        row: dict[str, str] = {"需求内容": req_title, "需求类型": req_type_disp}
        for i in range(1, max_pn + 1):
            if i <= len(pairs):
                person, pct_raw = pairs[i - 1]
                row[f"人员{i}"] = person
                row[f"完成度{i}"] = _pmo_normalize_participant_pct_cell(pct_raw)
            else:
                row[f"人员{i}"] = ""
                row[f"完成度{i}"] = ""
        rows_part.append(row)

    # --- 4) 版本发布：按 Sprint 聚合根需求名称 ---
    sprint_to_roots: dict[str, set[str]] = {}
    for r in dev_recs:
        fld = r.get("fields") or {}
        sp = _cell_to_text(fld.get("Sprint")).strip()
        if not sp:
            continue
        rid = str(r.get("record_id") or r.get("id") or "")
        if not rid:
            continue
        root = _pmo_dev_root_id(rid, rid_to_parent)
        root_title = rid_to_task.get(root, "").strip()
        cn = _pmo_pick_coarse_name(root_title, coarse_names)
        if cn:
            sprint_to_roots.setdefault(sp, set()).add(cn)
        elif root_title:
            sprint_to_roots.setdefault(sp, set()).add(root_title)

    rows_ver: list[dict[str, str]] = []
    for sp in sorted(sprint_to_roots.keys(), key=lambda x: x):
        names = sorted(sprint_to_roots[sp])
        rows_ver.append({"版本号": sp, "完成的需求": "；".join(names[:80])})

    def _write_csv_to(target: Path, name: str, fieldnames: list[str], rows: list[dict[str, str]]) -> Path:
        p = target / name
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            for row in rows:
                w.writerow({k: row.get(k, "") for k in fieldnames})
        return p

    def _write_all_csvs(target: Path) -> tuple[Path, Path, Path, Path, Path]:
        target.mkdir(parents=True, exist_ok=True)
        a = _write_csv_to(
            target,
            PMO_DASHBOARD_CSV_REQUIREMENT_STATUS,
            [
                "需求内容",
                "需求类型",
                "开始时间",
                "结束时间",
                "当前完成度(%)",
                "完成到哪一步了",
            ],
            rows_req,
        )
        b = _write_csv_to(target, PMO_DASHBOARD_CSV_PERSON_ALLOC, person_fieldnames, rows_person)
        c = _write_csv_to(target, PMO_DASHBOARD_CSV_REQ_PARTICIPATION, part_fieldnames, rows_part)
        d = _write_csv_to(target, PMO_DASHBOARD_CSV_VERSION_RELEASE, ["版本号", "完成的需求"], rows_ver)
        mf = {
            "snapshot_date": snap,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "req_march_coarse_json_missing": coarse_missing,
            "row_counts": {
                "requirement_status": len(rows_req),
                "person_allocation": len(rows_person),
                "requirement_participation": len(rows_part),
                "version_release": len(rows_ver),
            },
            "files": {
                "requirement_status": str(a),
                "person_allocation": str(b),
                "requirement_participation": str(c),
                "version_release": str(d),
            },
        }
        mp = target / f"pmo_dashboard_manifest_{snap}.json"
        mp.write_text(json.dumps(mf, ensure_ascii=False, indent=2), encoding="utf-8")
        return a, b, c, d, mp

    slg = logging.getLogger("pmo_bmo_skill")
    out_dir = primary_out
    try:
        p1, p2, p3, p4, man_path = _write_all_csvs(primary_out)
    except PermissionError as e:
        for name in (
            PMO_DASHBOARD_CSV_REQUIREMENT_STATUS,
            PMO_DASHBOARD_CSV_PERSON_ALLOC,
            PMO_DASHBOARD_CSV_REQ_PARTICIPATION,
            PMO_DASHBOARD_CSV_VERSION_RELEASE,
            f"pmo_dashboard_manifest_{snap}.json",
        ):
            try:
                (primary_out / name).unlink(missing_ok=True)
            except OSError:
                pass
        slg.warning(
            "无法写入 PMO 仪表盘目录 %s（%s）。已改写到仓库内 %s；请关闭占用 CSV 的程序后可将文件拷回主目录。",
            primary_out,
            e,
            fallback_out,
        )
        out_dir = fallback_out
        p1, p2, p3, p4, man_path = _write_all_csvs(fallback_out)

    row_counts = {
        "requirement_status": len(rows_req),
        "person_allocation": len(rows_person),
        "requirement_participation": len(rows_part),
        "version_release": len(rows_ver),
    }

    st = "partial" if coarse_missing else "ok"
    out: dict[str, Any] = {
        "status": st,
        "snapshot_date": snap,
        "output_dir": str(out_dir.resolve()),
        "manifest": str(man_path.resolve()),
        "csv_paths": {
            "requirement_status": str(p1.resolve()),
            "person_allocation": str(p2.resolve()),
            "requirement_participation": str(p3.resolve()),
            "version_release": str(p4.resolve()),
        },
        "row_counts": row_counts,
    }
    if coarse_missing:
        out["warning"] = (
            "缺少主线表 raw（req_march_coarse）；已生成其余 CSV，需求完成情况与需求人员参与情况为空或降级。"
        )
    return out


def sync_pmo_dashboard_to_lark(
    project_root: Path,
    snapshot_date: str,
    *,
    cfg: dict[str, Any] | None = None,
    csv_dir: Path | str | None = None,
) -> dict[str, Any]:
    """
    将 `write_pmo_dashboard_csvs` 生成的 CSV 同步到 Lark「PMO测试」多维表（需配置 app_token 与各 table_id）。
    凭证与 `pmo_bmo.yaml` 的 lark 段一致。

    csv_dir：CSV 实际目录（与 write_pmo_dashboard_csvs 返回的 output_dir 一致；默认同 ~/.jachin/.../PMO/output）。

    对 ``PMO_人员分配.csv`` 等提纯结果，列数会随任务数变化；即使未设 ``ensure_columns: true``，也会按 CSV 在多维表补建缺失列（可用 ``dashboard_csv_auto_columns: false`` 关闭）。
    """
    from l3_node.primitives.mcp.mcp_tools.pmo_bmo.paths import get_pmo_output_client_dir

    root = project_root
    merged_cfg = dict(cfg or {})
    if not merged_cfg:
        merged_cfg = _load_skill_yaml(root)
    push = (merged_cfg.get("pmo_dashboard_push") or {}) if isinstance(merged_cfg, dict) else {}
    if not push.get("enabled"):
        return {"status": "skipped", "reason": "pmo_dashboard_push.enabled 未为 true"}

    app_token = (push.get("app_token") or "").strip()
    tables_map = push.get("tables") or {}
    if not app_token or not isinstance(tables_map, dict) or len(tables_map) == 0:
        return {"status": "error", "error": "pmo_dashboard_push 需配置 app_token 与 tables（CSV 文件名 -> table_id）"}

    lk = merged_cfg.get("lark") or {}
    aid = (push.get("app_id") or lk.get("app_id") or os.environ.get("LARK_APP_ID") or "").strip()
    sec = (push.get("app_secret") or lk.get("app_secret") or os.environ.get("LARK_APP_SECRET") or "").strip()
    if aid and sec:
        os.environ.setdefault("LARK_APP_ID", aid)
        os.environ.setdefault("LARK_APP_SECRET", sec)
    if push.get("lark_use_feishu") or lk.get("lark_use_feishu"):
        os.environ["LARK_USE_FEISHU"] = "1"

    sync_csv_to_bitable = _pmo_import_sync_csv_to_bitable()
    out_dir = Path(csv_dir).expanduser().resolve() if csv_dir else get_pmo_output_client_dir()
    replace_default = bool(push.get("replace_tables", True))
    text_by_file = push.get("text_columns") or {}
    fm_by_file = push.get("field_mapping") or {}

    results: list[dict[str, Any]] = []
    errors: list[str] = []
    ok = 0
    for csv_name, table_id in tables_map.items():
        tid = (table_id or "").strip()
        if not tid:
            continue
        path = out_dir / str(csv_name).strip()
        if not path.is_file():
            errors.append(f"{csv_name}: 文件不存在（请先运行 write_pmo_dashboard_csvs）")
            continue
        tc = text_by_file.get(csv_name) if isinstance(text_by_file, dict) else None
        fm_yaml = fm_by_file.get(csv_name) if isinstance(fm_by_file, dict) else None
        fm = _pmo_merge_dashboard_field_mapping(str(csv_name), fm_yaml if isinstance(fm_yaml, dict) else None)
        ensure_this = _pmo_dashboard_sync_ensure_columns(str(csv_name), push)
        r = sync_csv_to_bitable(
            csv_path=str(path),
            app_token=app_token,
            table_id=tid,
            replace_table=replace_default,
            ensure_columns=ensure_this,
            text_columns=tc,
            field_mapping=fm,
        )
        results.append({"file": csv_name, "table_id": tid, **r})
        if r.get("success"):
            ok += 1
        else:
            errors.append(f"{csv_name}: {r.get('error', 'unknown')}")

    st = "ok" if ok == len(results) and not errors else ("partial" if ok else "error")
    return {
        "status": st,
        "synced_ok": ok,
        "results": results,
        "errors": errors,
    }


def _pmo_send_battle_report_style_cards(
    project_root: Path,
    snapshot_date: str,
    cfg: dict[str, Any],
    *,
    csv_output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """
    与 ``battle-report`` / ``run_pmo_battle_report_card_only`` 一致：按 ``pmo_dashboard_three_cards.enabled``
    发送三张仪表盘卡或 K11 单卡。

    **不依赖**本轮 ``sync_pmo_dashboard_to_lark`` 是否成功；读多维表或本地 CSV 由 ``tool_data_visualizer`` 内逻辑决定
    （与单独执行 ``python -m ... battle-report`` 相同）。
    """
    from l3_node.primitives.mcp.mcp_tools.pmo_bmo.tool_data_visualizer import (
        send_pmo_k11_battle_report_card,
        send_pmo_three_dashboard_cards,
    )

    tdc = cfg.get("pmo_dashboard_three_cards") or {}
    if bool(tdc.get("enabled", False)):
        return send_pmo_three_dashboard_cards(
            project_root=project_root, snapshot_date=snapshot_date, cfg=cfg
        )
    return send_pmo_k11_battle_report_card(
        project_root=project_root,
        snapshot_date=snapshot_date,
        cfg=cfg,
        csv_output_dir=csv_output_dir,
    )


def run_pmo_dashboard_push(
    project_root: Path | None = None,
    snapshot_date: str | None = None,
    *,
    sync_lark: bool = True,
    log_banner: bool = True,
    battle_report: bool | None = None,
    skip_write_csv: bool = False,
) -> dict[str, Any]:
    """
    默认：生成四张提纯 CSV（PMO/output），按 pmo_bmo.yaml 的 pmo_dashboard_push 同步 Lark，再可选发 VChart 交互卡片。
    ``skip_write_csv=True``：假定 ``~/.jachin/client_volumes/PMO/output/`` 下已有四张 ``PMO_*.csv``，跳过提纯，仅同步与发卡片（供单独测试）。

    若 ``pmo_dashboard_three_cards.enabled=true``，**不再**发送旧版 ``pmo_battle_report_card``（K11 环形图+TOP10 单卡），
    仅发送三张新仪表盘卡片，避免与 ``full`` 流水线重复推送。

    消息卡片发送与 ``battle-report`` 子命令 **同一路径**（``_pmo_send_battle_report_style_cards``）：**不因** Lark 表同步失败或 ``--no-sync`` 而跳过发卡。
    """
    from l3_node.paths import get_app_root
    from l3_node.primitives.mcp.mcp_tools.pmo_bmo.paths import get_pmo_output_client_dir

    root = project_root or get_app_root()
    cfg = _load_skill_yaml(root)
    pipeline = cfg.get("pipeline") or {}
    snap = (snapshot_date or pipeline.get("snapshot_date") or date.today().isoformat()).strip()[:10]

    log_path = _ensure_pmo_skill_file_logging()
    slg = logging.getLogger("pmo_bmo_skill")
    if log_banner:
        _log_pmo_skill_banner(log_path, "run_pmo_dashboard_push", project_root=str(root.resolve()), snapshot_date=snap)
        if skip_write_csv:
            slg.info(
                "skip_write_csv=True：跳过提纯，直接读已有 CSV（%s）",
                get_pmo_output_client_dir(),
            )
        else:
            slg.info("将写入 CSV 目录: ~/.jachin/client_volumes/PMO/output/")

    out: dict[str, Any] = {
        "snapshot_date": snap,
        "write_csv": None,
        "lark_sync": None,
        "battle_report_card": None,
        "three_dashboard_cards": None,
        "skip_write_csv": skip_write_csv,
    }
    if skip_write_csv:
        out_dir = get_pmo_output_client_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        out["write_csv"] = {
            "status": "skipped",
            "reason": "skip_write_csv：使用目录内已有 PMO_*.csv",
            "output_dir": str(out_dir.resolve()),
        }
    else:
        try:
            out["write_csv"] = write_pmo_dashboard_csvs(root, snap)
        except Exception as e:
            out["status"] = "error"
            out["error"] = str(e)
            slg.error("write_pmo_dashboard_csvs 失败: %s\n%s", e, traceback.format_exc())
            return out

    wc_dir = (out.get("write_csv") or {}).get("output_dir")

    if sync_lark:
        try:
            out["lark_sync"] = sync_pmo_dashboard_to_lark(
                root, snap, cfg=cfg, csv_dir=wc_dir
            )
        except Exception as e:
            out["lark_sync"] = {"status": "error", "error": str(e)}
            slg.error("sync_pmo_dashboard_to_lark 失败: %s\n%s", e, traceback.format_exc())
    else:
        out["lark_sync"] = {"status": "skipped", "reason": "sync_lark=False"}

    br_yaml = cfg.get("pmo_battle_report_card") or {}
    tdc_yaml = cfg.get("pmo_dashboard_three_cards") or {}
    do_three_cfg = bool(tdc_yaml.get("enabled", False))
    do_br = battle_report if battle_report is not None else bool(br_yaml.get("enabled", False))
    # 三张新仪表盘卡片与旧版 K11 单卡（环形图+人员 TOP）二选一：启用前者时不再发后者，避免 full 与 battle-report 重复风格
    k11_suppressed_by_three = False
    if do_three_cfg and battle_report is None and do_br:
        do_br = False
        k11_suppressed_by_three = True

    # 发卡片：与 battle-report 相同（不依赖本轮 lark_sync；K11 与三张卡分支互斥，见 do_br / do_three_cfg）
    if do_br:
        try:
            from l3_node.primitives.mcp.mcp_tools.pmo_bmo.tool_data_visualizer import send_pmo_k11_battle_report_card

            out["battle_report_card"] = send_pmo_k11_battle_report_card(
                root, snapshot_date=snap, cfg=cfg, csv_output_dir=wc_dir
            )
            if (out["battle_report_card"] or {}).get("status") != "success":
                slg.warning(
                    "K11 战报卡片发送未成功: %s",
                    (out["battle_report_card"] or {}).get("error", out["battle_report_card"]),
                )
        except Exception as e:
            out["battle_report_card"] = {"status": "error", "error": str(e)}
            slg.error("send_pmo_k11_battle_report_card 失败: %s\n%s", e, traceback.format_exc())
    elif k11_suppressed_by_three:
        out["battle_report_card"] = {
            "status": "skipped",
            "reason": "pmo_dashboard_three_cards.enabled=true：不发送旧版 K11「研发效能大盘日线图」单卡，仅发送三张仪表盘卡片",
        }

    if do_three_cfg:
        try:
            from l3_node.primitives.mcp.mcp_tools.pmo_bmo.tool_data_visualizer import send_pmo_three_dashboard_cards

            out["three_dashboard_cards"] = send_pmo_three_dashboard_cards(
                root, snapshot_date=snap, cfg=cfg
            )
            if (out["three_dashboard_cards"] or {}).get("status") not in ("success", "partial"):
                slg.warning(
                    "三张仪表盘卡片未全部成功: %s",
                    (out["three_dashboard_cards"] or {}).get("error", out["three_dashboard_cards"]),
                )
        except Exception as e:
            out["three_dashboard_cards"] = {"status": "error", "error": str(e)}
            slg.error("send_pmo_three_dashboard_cards 失败: %s\n%s", e, traceback.format_exc())

    ls = out.get("lark_sync") or {}
    wc = out.get("write_csv") or {}
    wc_st = wc.get("status")
    if not skip_write_csv and wc_st not in ("ok", "partial"):
        out["status"] = "error"
    elif not skip_write_csv and wc_st == "partial":
        out["status"] = "partial"
    elif skip_write_csv or wc_st in ("ok", "partial"):
        if not sync_lark or ls.get("status") in ("ok", "skipped"):
            out["status"] = "ok"
        elif ls.get("status") == "partial":
            out["status"] = "partial"
        else:
            out["status"] = "partial" if ls.get("status") == "error" and wc_st == "ok" else "error"
    else:
        out["status"] = "partial" if ls.get("status") == "error" and wc_st == "ok" else "error"
    _log_pmo_skill_json(slg, "run_pmo_dashboard_push 返回(摘要)", _redact_for_log(out))
    return out


def _pmo_cli_snapshot_from_argv(argv: list[str]) -> str | None:
    """解析 ``--snapshot=YYYY-MM-DD`` 或 ``--snapshot YYYY-MM-DD``。"""
    for i, a in enumerate(argv):
        if a.startswith("--snapshot="):
            return a.split("=", 1)[1].strip()[:10]
        if (a.strip().lower() == "--snapshot" or a.strip().lower() == "-s") and i + 1 < len(argv):
            return argv[i + 1].strip()[:10]
    return None


def run_pmo_battle_report_card_only(
    project_root: Path | None = None,
    snapshot_date: str | None = None,
    *,
    log_banner: bool = True,
) -> dict[str, Any]:
    """
    仅发战报类消息卡片（**不写 CSV、不同步多维表**）。

    - 若 ``pmo_dashboard_three_cards.enabled: true``：**连发三张**仪表盘卡片（需求战报 VChart + 资源负荷表 + 版本发布），
      与 ``three-dashboard-cards`` 子命令相同，数据源为 Lark 多维表四张子表。
    - 否则：沿用旧版 **K11** 单卡（环形图 + 人员负荷 TOP），即 ``send_pmo_k11_battle_report_card``；
      读数默认亦为多维表（``PMO_BATTLE_REPORT_DATA_SOURCE=csv`` 或 ``pmo_battle_report_card.data_source: csv`` 时读本地 CSV）。
    """
    from l3_node.paths import get_app_root

    root = project_root or get_app_root()
    cfg = _load_skill_yaml(root)
    pipeline = cfg.get("pipeline") or {}
    snap = (snapshot_date or pipeline.get("snapshot_date") or date.today().isoformat()).strip()[:10]

    tdc = cfg.get("pmo_dashboard_three_cards") or {}
    use_three = bool(tdc.get("enabled", False))

    if log_banner:
        log_path = _ensure_pmo_skill_file_logging()
        slg = logging.getLogger("pmo_bmo_skill")
        _log_pmo_skill_banner(
            log_path,
            "run_pmo_battle_report_card_only",
            project_root=str(root.resolve()),
            snapshot_date=snap,
        )
        if use_three:
            slg.info(
                "跳过：提纯 CSV、同步多维表；执行：send_pmo_three_dashboard_cards（三张卡片，数据源=Lark 多维表）"
            )
        else:
            slg.info(
                "跳过：提纯 CSV、同步多维表；执行：send_pmo_k11_battle_report_card（chart_data_source 见返回字段）"
            )

    return _pmo_send_battle_report_style_cards(root, snap, cfg, csv_output_dir=None)


def run_pmo_three_dashboard_cards_only(
    project_root: Path | None = None,
    snapshot_date: str | None = None,
    *,
    log_banner: bool = True,
) -> dict[str, Any]:
    """
    仅连发三张仪表盘消息卡片（需求完成情况战报 + 资源任务负荷 + 版本发布），**全部从 Lark 多维表读数**。

    需在 ``pmo_bmo.yaml`` 中 ``pmo_dashboard_three_cards.enabled: true``，并配置 ``pmo_dashboard_push`` 四张子表与 ``lark`` 凭证。
    不调用 ``write_pmo_dashboard_csvs``、``sync_pmo_dashboard_to_lark``。
    """
    from l3_node.paths import get_app_root
    from l3_node.primitives.mcp.mcp_tools.pmo_bmo.tool_data_visualizer import send_pmo_three_dashboard_cards

    root = project_root or get_app_root()
    cfg = _load_skill_yaml(root)
    pipeline = cfg.get("pipeline") or {}
    snap = (snapshot_date or pipeline.get("snapshot_date") or date.today().isoformat()).strip()[:10]

    if log_banner:
        log_path = _ensure_pmo_skill_file_logging()
        slg = logging.getLogger("pmo_bmo_skill")
        _log_pmo_skill_banner(
            log_path,
            "run_pmo_three_dashboard_cards_only",
            project_root=str(root.resolve()),
            snapshot_date=snap,
        )
        slg.info("执行：send_pmo_three_dashboard_cards（三张卡片，数据源=Lark 多维表）")

    return send_pmo_three_dashboard_cards(project_root=root, snapshot_date=snap, cfg=cfg)


def run_pmo_person_task_stats_task(
    project_root: Path | None = None,
    snapshot_date: str | None = None,
    *,
    ensure_export: bool = True,
    extra_export: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    按人任务统计任务单入口：可选先导出六表，再返回 spec + context，并由脚本写入 `PMO_人员任务统计.md`。
    """
    from l3_node.paths import get_app_root

    root = project_root or get_app_root()
    cfg = _load_skill_yaml(root)
    pipeline = cfg.get("pipeline") or {}
    snap = (snapshot_date or pipeline.get("snapshot_date") or date.today().isoformat()).strip()[:10]

    log_path = _ensure_pmo_skill_file_logging()
    slg = logging.getLogger("pmo_bmo_skill")
    _log_pmo_skill_banner(
        log_path,
        "run_pmo_person_task_stats_task",
        project_root=str(root.resolve()),
        snapshot_date=snap,
        ensure_export=ensure_export,
    )
    slg.info("任务单类型=【按人任务统计】；将尝试从 raw JSON 自动生成 output/%s", PMO_PERSON_STATS_OUTPUT_BASENAME)
    slg.info("函数: get_pmo_person_task_stats_task_spec + build_pmo_person_task_stats_context + write_pmo_person_task_stats_markdown_from_raw")
    slg.info("干系人表相对路径=%s", PMO_STAKEHOLDER_DOC_REL)

    spec = get_pmo_person_task_stats_task_spec()
    export_result: dict[str, Any] | None = None
    ex = dict(extra_export or {})
    ex.setdefault("snapshot_date", snap)

    if ensure_export:
        try:
            export_result = run_pmo_export_scheduled_tables_only(
                root, extra=ex, log_export_scope_notice=False
            )
        except Exception:
            slg.error("export_pmo_tables 失败:\n%s", traceback.format_exc())
            raise

    ctx = build_pmo_person_task_stats_context(root, snapshot_date=snap)
    ex_st = (export_result.get("status") or "").lower() if export_result else ""
    if export_result and ex_st == "error":
        st = "export_error"
    elif ctx.get("status") != "ready":
        st = "incomplete_raw"
    elif export_result and ex_st not in ("success", "partial", ""):
        st = "attention"
    else:
        st = "ok"

    merged: dict[str, Any] = {
        "status": st,
        "snapshot_date": snap,
        "task_spec": spec,
        "person_stats_context": ctx,
        "export_pmo_tables": export_result,
        "next_steps_for_agent": [
            "阅读 task_spec['agent_instructions']（可选：已由脚本生成基准 MD，可再人工润色）",
            f"读取流程规范: {PMO_PROCESS_FLOW_DOC_REL}",
            f"读取干系人表: {PMO_STAKEHOLDER_DOC_REL}",
            "若 raw 缺失则调用 atom_pmo_lark_doc export_pmo_tables",
            f"按人汇总后写入: {ctx.get('output_markdown_relative', PMO_OUTPUT_REL)}",
        ],
    }
    if st == "ok" and ctx.get("status") == "ready":
        try:
            wp = write_pmo_person_task_stats_markdown_from_raw(root, snap)
            merged["written_markdown_path"] = str(wp.resolve())
            merged["written_markdown_relative"] = str(wp.relative_to(root.resolve()))
            slg.info("已生成按人任务统计 Markdown: %s", merged["written_markdown_path"])
        except Exception as e:
            merged["written_markdown_error"] = str(e)
            slg.error("生成 PMO_人员任务统计 Markdown 失败: %s\n%s", e, traceback.format_exc())
        try:
            wp2 = write_pmo_requirement_participants_markdown_from_raw(root, snap)
            merged["written_participants_markdown_path"] = str(wp2.resolve())
            merged["written_participants_markdown_relative"] = str(wp2.relative_to(root.resolve()))
            slg.info("已生成需求人员参与明细 Markdown: %s", merged["written_participants_markdown_path"])
        except FileNotFoundError as e:
            slg.warning("未生成 %s（缺少 coarse/按执行人/按设计人 raw）: %s", PMO_REQ_PARTICIPANTS_OUTPUT_BASENAME, e)
        except Exception as e:
            merged["written_participants_markdown_error"] = str(e)
            slg.error("生成 %s 失败: %s\n%s", PMO_REQ_PARTICIPANTS_OUTPUT_BASENAME, e, traceback.format_exc())
        try:
            wp3 = write_pmo_leadership_weekly_brief_markdown_from_raw(root, snap)
            merged["written_leadership_brief_path"] = str(wp3.resolve())
            merged["written_leadership_brief_relative"] = str(wp3.relative_to(root.resolve()))
            slg.info("已生成领导视图与周负荷摘要: %s", merged["written_leadership_brief_path"])
        except Exception as e:
            merged["written_leadership_brief_error"] = str(e)
            slg.warning("未生成 %s: %s", PMO_LEADERSHIP_BRIEF_OUTPUT_BASENAME, e)
    else:
        slg.info("跳过写入 PMO_人员任务统计（status=%s context.status=%s）", st, ctx.get("status"))

    _log_pmo_skill_json(slg, "run_pmo_person_task_stats_task 返回(摘要)", {k: merged[k] for k in merged if k != "task_spec"})
    return merged


def run_pmo_requirement_participants_report_task(
    project_root: Path | None = None,
    snapshot_date: str | None = None,
    *,
    ensure_export: bool = True,
    extra_export: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    仅生成 ``PMO_需求人员参与明细.md``：可选先六表导出，再读
    ``req_march_coarse`` + ``dev_tasks_by_assignee`` + ``art_tasks_by_designer``。
    """
    from l3_node.paths import get_app_root

    root = project_root or get_app_root()
    cfg = _load_skill_yaml(root)
    pipeline = cfg.get("pipeline") or {}
    snap = (snapshot_date or pipeline.get("snapshot_date") or date.today().isoformat()).strip()[:10]

    log_path = _ensure_pmo_skill_file_logging()
    slg = logging.getLogger("pmo_bmo_skill")
    _log_pmo_skill_banner(
        log_path,
        "run_pmo_requirement_participants_report_task",
        project_root=str(root.resolve()),
        snapshot_date=snap,
        ensure_export=ensure_export,
    )
    slg.info("输出目标: %s（coarse + dev_tasks_by_assignee + art_tasks_by_designer）", PMO_REQ_PARTICIPANTS_OUTPUT_BASENAME)

    export_result: dict[str, Any] | None = None
    ex = dict(extra_export or {})
    ex.setdefault("snapshot_date", snap)

    if ensure_export:
        try:
            export_result = run_pmo_export_scheduled_tables_only(
                root, extra=ex, log_export_scope_notice=False
            )
        except Exception:
            slg.error("export_pmo_tables 失败:\n%s", traceback.format_exc())
            raise

    merged: dict[str, Any] = {
        "status": "ok",
        "snapshot_date": snap,
        "export_pmo_tables": export_result,
    }
    try:
        wp = write_pmo_requirement_participants_markdown_from_raw(root, snap)
        merged["written_markdown_path"] = str(wp.resolve())
        merged["written_markdown_relative"] = str(wp.relative_to(root.resolve()))
        slg.info("已写入 %s", merged["written_markdown_path"])
    except Exception as e:
        merged["status"] = "error"
        merged["error"] = str(e)
        slg.error("写入失败: %s\n%s", e, traceback.format_exc())

    _log_pmo_skill_json(slg, "run_pmo_requirement_participants_report_task 返回(摘要)", merged)
    return merged


def run_pmo_output_docs_from_raw(
    project_root: Path | None = None,
    snapshot_date: str | None = None,
    *,
    extra_export: dict[str, Any] | None = None,
    log_banner: bool = True,
) -> dict[str, Any]:
    """
    **独立环节**：假定 ``~/.jachin/.../client_volumes/PMO/raw`` 已有当日快照 JSON，
    仅生成 ``docs/pmo_bmo_plugin/output`` 下任务单 Markdown（大需求对齐、按人任务统计、参与明细、领导视图等）。

    **不**调用 ``export_pmo_tables``，**不**写仪表盘 CSV，**不**发 Lark。可与「仅六表导出」分先后由人工或 agent 编排。
    """
    from l3_node.paths import get_app_root

    root = project_root or get_app_root()
    cfg = _load_skill_yaml(root)
    pipeline = cfg.get("pipeline") or {}
    snap = (snapshot_date or pipeline.get("snapshot_date") or date.today().isoformat()).strip()[:10]

    log_path = _ensure_pmo_skill_file_logging()
    slg = logging.getLogger("pmo_bmo_skill")
    if log_banner:
        _log_pmo_skill_banner(
            log_path,
            "run_pmo_output_docs_from_raw",
            project_root=str(root.resolve()),
            snapshot_date=snap,
        )
        slg.info(
            "仅根据已有 PMO/raw 生成 docs/pmo_bmo_plugin/output（不拉表、不写 CSV、不发 Lark）"
        )

    ex = dict(extra_export or {})
    ex.setdefault("snapshot_date", snap)

    out: dict[str, Any] = {
        "pipeline_id": "pmo_output_docs_from_raw",
        "snapshot_date": snap,
        "steps": {},
    }

    slg.info("---------- [1/2] 各需求进度分析（大需求对齐任务单）----------")
    try:
        r2 = run_pmo_big_requirement_alignment_task(
            root, snapshot_date=snap, ensure_export=False, extra_export=ex
        )
    except Exception:
        slg.error("[1/2] 异常:\n%s", traceback.format_exc())
        out["status"] = "error"
        out["failed_step"] = 1
        out["steps"]["big_requirement_alignment"] = {"error": traceback.format_exc()}
        return out
    out["steps"]["big_requirement_alignment"] = r2

    slg.info("---------- [2/2] 各任务分配分析（按人任务单等）----------")
    try:
        r3 = run_pmo_person_task_stats_task(
            root, snapshot_date=snap, ensure_export=False, extra_export=ex
        )
    except Exception:
        slg.error("[2/2] 异常:\n%s", traceback.format_exc())
        out["status"] = "error"
        out["failed_step"] = 2
        out["steps"]["person_task_stats"] = {"error": traceback.format_exc()}
        return out
    out["steps"]["person_task_stats"] = r3

    s2 = (r2.get("status") or "").lower()
    s3 = (r3.get("status") or "").lower()
    if s2 == "ok" and s3 == "ok":
        out["status"] = "ok"
    elif s2 in ("ok", "incomplete_raw", "attention") and s3 in ("ok", "incomplete_raw", "attention"):
        out["status"] = "partial"
    else:
        out["status"] = "attention"

    slg.info("---------- run_pmo_output_docs_from_raw 结束 overall=%s ----------", out["status"])
    _log_pmo_skill_json(
        slg,
        "run_pmo_output_docs_from_raw 摘要",
        {"status": out["status"], "snapshot_date": snap, "step_align": s2, "step_person": s3},
    )
    return out


def run_pmo_full_business_pipeline(
    project_root: Path | None = None,
    snapshot_date: str | None = None,
    *,
    skip_export: bool = False,
    skip_output_docs: bool = False,
    extra_export: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    业务顺序（一次跑完）：

    1. **六表拉取** — `run_pmo_export_scheduled_tables_only`（`export_pmo_tables`）
    2+3. **output 文档** — `run_pmo_output_docs_from_raw`（与拉表解耦的独立函数；内部为对齐 + 按人统计等）→ ``docs/pmo_bmo_plugin/output``
    4. **仪表盘提纯 + 可选 Lark** — `run_pmo_dashboard_push(sync_lark=True, log_banner=False)`（读 raw JSON → `~/.jachin/.../PMO/output/*.csv`；同步受 `pmo_dashboard_push.enabled` 控制）

    第 2+3 步会生成 `PMO_大需求对齐.md`、`PMO_人员任务统计.md`，并在 raw 齐全时 **额外** 生成 `PMO_需求人员参与明细.md` 等（与单独调用 `run_pmo_output_docs_from_raw` 一致）。

    - skip_export: 为 True 时跳过第 1 步（假定 raw 已存在）。
    - skip_output_docs: 为 True 时跳过第 2+3 步（仅 ① 或仅 ①+④，视 skip_export 而定）。
    """
    from l3_node.paths import get_app_root

    root = project_root or get_app_root()
    cfg = _load_skill_yaml(root)
    pipeline = cfg.get("pipeline") or {}
    snap = (snapshot_date or pipeline.get("snapshot_date") or date.today().isoformat()).strip()[:10]

    log_path = _ensure_pmo_skill_file_logging()
    slg = logging.getLogger("pmo_bmo_skill")
    _log_pmo_skill_banner(
        log_path,
        "run_pmo_full_business_pipeline",
        project_root=str(root.resolve()),
        snapshot_date=snap,
        skip_export=skip_export,
        skip_output_docs=skip_output_docs,
    )
    slg.info(
        "业务流水线顺序: [1/4] 六表拉取 → [2+3/4] output 文档(run_pmo_output_docs_from_raw，可跳过) → "
        "[4/4] 仪表盘提纯 CSV + 可选 Lark 同步"
    )
    slg.info(
        "说明: [2+3] 写入 docs/pmo_bmo_plugin/output；[4] 写 `PMO_*.csv` 至 ~/.jachin/.../PMO/output/，"
        "Lark 同步需 pmo_dashboard_push.enabled=true。"
    )

    ex = dict(extra_export or {})
    ex.setdefault("snapshot_date", snap)

    out: dict[str, Any] = {
        "pipeline_id": "pmo_full_business",
        "snapshot_date": snap,
        "steps": {},
    }

    if not skip_export:
        slg.info("---------- [1/4] 六表拉取 ----------")
        slg.info("全流水线内第 1 步：后续同进程将执行 [2+3][4]，故不打印「仅导出」独占说明。")
        try:
            r1 = run_pmo_export_scheduled_tables_only(
                root, extra=ex, log_export_scope_notice=False
            )
        except Exception:
            slg.error("[1/4] 异常:\n%s", traceback.format_exc())
            out["status"] = "error"
            out["failed_step"] = 1
            out["steps"]["export_six_tables"] = {"error": traceback.format_exc()}
            return out
        out["steps"]["export_six_tables"] = r1
        if (r1.get("status") or "").lower() == "error":
            out["status"] = "error"
            out["failed_step"] = 1
            out["error"] = r1.get("error")
            return out
    else:
        slg.info("---------- [1/4] 跳过（skip_export=True），假定 raw 已就绪 ----------")
        out["steps"]["export_six_tables"] = {"skipped": True}

    if not skip_output_docs:
        slg.info("---------- [2+3/4] 生成 docs/pmo_bmo_plugin/output（run_pmo_output_docs_from_raw）----------")
        try:
            r_docs = run_pmo_output_docs_from_raw(
                root, snapshot_date=snap, extra_export=ex, log_banner=False
            )
        except Exception:
            slg.error("[2+3/4] 异常:\n%s", traceback.format_exc())
            out["status"] = "error"
            out["failed_step"] = "output_docs"
            out["steps"]["output_docs_from_raw"] = {"error": traceback.format_exc()}
            return out
        out["steps"]["output_docs_from_raw"] = {
            "status": r_docs.get("status"),
            "pipeline_id": r_docs.get("pipeline_id"),
        }
        r2 = r_docs["steps"]["big_requirement_alignment"]
        r3 = r_docs["steps"]["person_task_stats"]
        out["steps"]["big_requirement_alignment"] = r2
        out["steps"]["person_task_stats"] = r3
        if (r_docs.get("status") or "").lower() == "error":
            out["status"] = "error"
            out["failed_step"] = r_docs.get("failed_step")
            return out
        s2 = (r2.get("status") or "").lower()
        s3 = (r3.get("status") or "").lower()
        if s2 == "ok" and s3 == "ok":
            out["status"] = "ok"
        elif s2 in ("ok", "incomplete_raw", "attention") and s3 in ("ok", "incomplete_raw", "attention"):
            out["status"] = "partial"
        else:
            out["status"] = "attention"
    else:
        slg.info("---------- [2+3/4] 跳过（skip_output_docs=True），不生成 output 文档 ----------")
        sk = {"status": "skipped", "reason": "skip_output_docs"}
        out["steps"]["output_docs_from_raw"] = sk
        out["steps"]["big_requirement_alignment"] = sk
        out["steps"]["person_task_stats"] = sk
        s2 = s3 = "skipped"
        out["status"] = "ok"

    slg.info("---------- [4/4] 仪表盘提纯 CSV + 可选同步 Lark ----------")
    try:
        r4 = run_pmo_dashboard_push(root, snapshot_date=snap, sync_lark=True, log_banner=False)
    except Exception:
        slg.error("[4/4] 异常:\n%s", traceback.format_exc())
        out["steps"]["pmo_dashboard_push"] = {"error": traceback.format_exc(), "status": "error"}
        if out["status"] == "ok":
            out["status"] = "partial"
        return out
    out["steps"]["pmo_dashboard_push"] = r4
    s4 = (r4.get("status") or "").lower()
    if s4 in ("error", "partial") and out["status"] == "ok":
        out["status"] = "partial"

    slg.info("---------- run_pmo_full_business_pipeline 结束 overall=%s ----------", out["status"])
    slg.info(
        "各步 status: export=%s align=%s person=%s dashboard=%s",
        (out["steps"].get("export_six_tables") or {}).get("status")
        if isinstance(out["steps"].get("export_six_tables"), dict)
        else "skipped",
        s2,
        s3,
        s4,
    )
    _log_pmo_skill_json(
        slg,
        "run_pmo_full_business_pipeline 摘要",
        {
            "status": out["status"],
            "snapshot_date": snap,
            "step_export": (out["steps"].get("export_six_tables") or {}).get("status")
            if isinstance(out["steps"].get("export_six_tables"), dict)
            else None,
            "step_align": s2,
            "step_person": s3,
            "step_dashboard": s4,
        },
    )
    return out


def run_pmo_knowledge_sync(project_root: Path | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    执行：Lark 同步 → 知识库分块 ingest。

    extra 可覆盖 MCP 参数（如 operation、wiki_urls）。
    """
    from l3_node.paths import get_app_root
    from l3_node.primitives.mcp.mcp_tools.pmo_bmo.tool_lark_doc import run_pmo_lark_doc
    from l3_node.primitives.mcp.mcp_tools.pmo_bmo.tool_knowledge_base import run_pmo_knowledge_base

    root = project_root or get_app_root()
    log_path = _ensure_pmo_skill_file_logging()
    slg = logging.getLogger("pmo_bmo_skill")
    _log_pmo_skill_banner(
        log_path,
        "run_pmo_knowledge_sync",
        project_root=str(root.resolve()),
        skill_yaml_path=_pmo_skill_yaml_resolved_path(root) or "(未找到 pmo_bmo.yaml)",
        extra_keys=list(extra.keys()) if isinstance(extra, dict) else extra,
    )

    cfg = _load_skill_yaml(root)
    pipeline = cfg.get("pipeline") or {}
    _log_pmo_skill_json(slg, "run_pmo_knowledge_sync 使用的 skill 配置(脱敏)", _redact_for_log(cfg))
    if not pipeline.get("knowledge_sync_enabled", True):
        slg.info("跳过: pipeline.knowledge_sync_enabled 为 false")
        return {"status": "skipped", "msg": "pipeline.knowledge_sync_enabled 为 false"}

    doc_args: dict[str, Any] = {"operation": "sync"}
    kb_args: dict[str, Any] = {"operation": "ingest"}
    if isinstance(pipeline.get("lark_doc"), dict):
        doc_args.update(pipeline["lark_doc"])
    lk = cfg.get("lark") or {}
    if isinstance(lk, dict):
        for k in ("app_id", "app_secret", "lark_use_feishu"):
            if k in lk and lk[k] is not None and str(lk[k]).strip() != "":
                doc_args[k] = lk[k]
    if isinstance(pipeline.get("knowledge_base"), dict):
        kb_args.update(pipeline["knowledge_base"])
    if extra:
        for k in (
            "operation",
            "wiki_urls",
            "output_dir_relative",
            "use_k11_default_tables",
            "daily_snapshot",
            "snapshot_date",
            "space_id",
            "parent_node_token",
            "node_token",
            "max_records_per_table",
            "max_discovered_links",
            "recurse_children_depth",
            "max_export_records",
            "json_raw_dir",
            "md_raw_rel",
            "duckdb_path",
        ):
            if k in extra and extra[k] is not None:
                doc_args[k] = extra[k]
        for k in ("operation", "source_dir_relative", "corpus_dir_relative", "embed", "chunk_max_chars", "chunk_overlap"):
            if k in extra and extra[k] is not None:
                kb_args[k] = extra[k]

    r_export: dict[str, Any] | None = None
    if pipeline.get("export_scheduled_tables", True):
        export_args: dict[str, Any] = {"operation": "export_pmo_tables"}
        if isinstance(pipeline.get("pmo_export"), dict):
            export_args.update(pipeline["pmo_export"])
        psd = pipeline.get("snapshot_date")
        if psd:
            export_args["snapshot_date"] = str(psd).strip()[:10]
        if isinstance(lk, dict):
            for k in ("app_id", "app_secret", "lark_use_feishu"):
                if k in lk and lk[k] is not None and str(lk[k]).strip() != "":
                    export_args[k] = lk[k]
        if isinstance(pipeline.get("lark_doc"), dict):
            for k in (
                "app_id",
                "app_secret",
                "lark_use_feishu",
                "max_export_records",
                "json_raw_dir",
                "md_raw_rel",
                "duckdb_path",
                "snapshot_date",
            ):
                if k in pipeline["lark_doc"] and pipeline["lark_doc"][k] is not None:
                    export_args[k] = pipeline["lark_doc"][k]
        if extra:
            for k in ("snapshot_date", "max_export_records", "json_raw_dir", "md_raw_rel", "duckdb_path", "docx_document_ids"):
                if extra.get(k) is not None:
                    export_args[k] = extra[k]
        _apply_pmo_req_march_coarse_docx_env(export_args)
        _log_pmo_skill_json(slg, "六表导出 export_args", export_args)
        try:
            with _pmo_heartbeat_while("export_pmo_tables（知识库流水线前置六表）"):
                r_export = run_pmo_lark_doc(export_args)
        except Exception:
            slg.error("六表导出 run_pmo_lark_doc 异常:\n%s", traceback.format_exc())
            raise
        _log_pmo_skill_json(slg, "六表导出返回 pmo_bitable_export", r_export)

    _log_pmo_skill_json(slg, "Wiki sync doc_args", doc_args)
    try:
        with _pmo_heartbeat_while("atom_pmo_lark_doc sync（Wiki 全文同步）"):
            r1 = run_pmo_lark_doc(doc_args)
    except Exception:
        slg.error("atom_pmo_lark_doc sync 异常:\n%s", traceback.format_exc())
        raise
    if (r1.get("status") or "").lower() == "error":
        slg.error("Wiki sync 失败，不再执行 knowledge_base。响应: %s", r1)
        return {"status": "error", "step": "atom_pmo_lark_doc", "lark_doc": r1}

    _log_pmo_skill_json(slg, "knowledge_base kb_args", kb_args)
    try:
        r2 = run_pmo_knowledge_base(kb_args)
    except Exception:
        slg.error("run_pmo_knowledge_base 异常:\n%s", traceback.format_exc())
        raise
    out: dict[str, Any] = {
        "status": r2.get("status", "success"),
        "pmo_bitable_export": r_export,
        "lark_doc": r1,
        "knowledge_base": r2,
    }

    notify = pipeline.get("notify") or {}
    if notify.get("after_sync") and notify.get("markdown"):
        try:
            from l3_node.primitives.mcp.mcp_tools.bi.tool_lark_notifier import send_lark_markdown

            send_lark_markdown(
                webhook_url=str(notify.get("webhook_url") or ""),
                markdown_content=str(notify["markdown"]),
                title=str(notify.get("title") or "PMO 知识库同步"),
                chat_id=str(notify.get("chat_id") or "") or None,
            )
            out["notify_sent"] = True
        except Exception as e:
            logger.warning("[pmo_bmo] 同步后 Lark 通知失败: %s", e)
            out["notify_error"] = str(e)

    _log_pmo_skill_json(slg, "run_pmo_knowledge_sync 最终输出(完整)", out)
    return out


def pmo_bmo_skill_cli_main() -> None:
    """供 ``python -m l3_node.primitives.skills.pmo_bmo.main_skill`` 与兼容入口 ``l3_node.skills.pmo_bmo.main_skill`` 调用。"""
    _configure_pmo_cli_stdio_line_buffering()
    _ensure_pmo_cli_stderr_streaming()
    _lp = _ensure_pmo_skill_file_logging()
    _cli_mode = _log_pmo_skill_cli_entry(sys.argv, _lp)
    logging.getLogger("pmo_bmo_skill").info("CLI 路由结果 cli_mode=%s", _cli_mode)
    _argv1 = sys.argv[1].strip().lower() if len(sys.argv) > 1 else ""
    if _argv1 in ("align", "big-align", "pmo-align"):
        _r = run_pmo_big_requirement_alignment_task()
    elif _argv1 in ("person-stats", "by-person", "pmo-person"):
        _r = run_pmo_person_task_stats_task()
    elif _argv1 in ("req-participants", "req-people", "pmo-req-participants"):
        _r = run_pmo_requirement_participants_report_task()
    elif _argv1 in ("push-dashboard", "dashboard-push", "pmo-push"):
        _argv_rest = [x.strip().lower() for x in sys.argv[2:]]
        _no_sync = "--no-sync" in _argv_rest
        _no_battle = "--no-battle-report" in _argv_rest
        _sync_only = "--sync-only" in _argv_rest or "--skip-write-csv" in _argv_rest
        _r = run_pmo_dashboard_push(
            sync_lark=not _no_sync,
            battle_report=False if _no_battle else None,
            skip_write_csv=_sync_only,
        )
    elif _argv1 in ("battle-report", "k11-card", "pmo-battle-report", "send-battle-report"):
        _r = run_pmo_battle_report_card_only(snapshot_date=_pmo_cli_snapshot_from_argv(sys.argv))
    elif _argv1 in ("three-dashboard-cards", "pmo-three-cards", "dashboard-three-cards"):
        _r = run_pmo_three_dashboard_cards_only(snapshot_date=_pmo_cli_snapshot_from_argv(sys.argv))
    elif _argv1 in ("full", "all", "pipeline", "pmo-full"):
        _argv_rest = [x.strip().lower() for x in sys.argv[2:]]
        _skip_od = "--skip-output-docs" in _argv_rest
        _r = run_pmo_full_business_pipeline(
            snapshot_date=_pmo_cli_snapshot_from_argv(sys.argv),
            skip_output_docs=_skip_od,
        )
    elif _argv1 in ("output-docs", "pmo-output-docs", "gen-output-md", "output-md"):
        _r = run_pmo_output_docs_from_raw(snapshot_date=_pmo_cli_snapshot_from_argv(sys.argv))
    else:
        _r = run_pmo_export_scheduled_tables_only()
    _out = json.dumps(_r, ensure_ascii=False, indent=2) + "\n"
    try:
        sys.stdout.write(_out)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(_out.encode("utf-8", errors="replace"))
    try:
        sys.stdout.flush()
    except Exception:
        pass
    _st = (_r.get("status") or "").lower()
    if _argv1 in ("full", "all", "pipeline", "pmo-full"):
        sys.exit(0 if _st in ("ok", "partial") else 1)
    if _argv1 in ("output-docs", "pmo-output-docs", "gen-output-md", "output-md"):
        sys.exit(0 if _st in ("ok", "partial") else 1)
    if _argv1 in (
        "align",
        "big-align",
        "pmo-align",
        "person-stats",
        "by-person",
        "pmo-person",
        "req-participants",
        "req-people",
        "pmo-req-participants",
    ):
        sys.exit(0 if _st in ("ok",) else 1)
    if _argv1 in ("push-dashboard", "dashboard-push", "pmo-push"):
        sys.exit(0 if _st in ("ok", "partial") else 1)
    if _argv1 in ("battle-report", "k11-card", "pmo-battle-report", "send-battle-report"):
        sys.exit(0 if _st in ("success", "partial") else 1)
    if _argv1 in ("three-dashboard-cards", "pmo-three-cards", "dashboard-three-cards"):
        sys.exit(0 if _st in ("success", "partial") else 1)
    sys.exit(0 if _st == "success" else (2 if _st == "partial" else 1))


if __name__ == "__main__":
    pmo_bmo_skill_cli_main()
