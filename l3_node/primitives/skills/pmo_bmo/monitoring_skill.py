"""
PMO Monitoring_skill — 资源预警（按**自然周排期任务**的进度节奏：过载 / 过早清空）

- **拉表策略**：运行时会先检查 ``~/.jachin/client_volumes/PMO/raw`` 是否存在且已有**本次快照日**的完整四表 JSON（含 ``req_march_coarse``）；若无则调用 ``main_skill.ensure_pmo_raw_for_monitoring`` → ``export_pmo_tables``。若已是最新完整快照则跳过拉取。
- **读数**：诊断读仓库 ``docs/pmo_bmo_plugin/raw`` 或回退 ``~/.jachin/.../PMO/raw``（与 main_skill 同源）。
- **饱和度口径**：只统计「**开始/交付日期** 与 **快照所在自然周**（周一至周日）有交集」的产品/开发/美术任务（与 ``main_skill`` 周筛选一致），**不把历史未完成任务整表算入**。
- **过载**：截至快照日，**已完成数** 明显低于按周历进度应有的期望（例：周四应对齐约 4/7 工作量，却只完成少量）。
- **过早清空 / 饥饿型**：本周排期不少于 2 条，但在周初即**已全部完成**，视为负荷分配可能过轻；另保留「任务均为等待/阻塞类」的提示。
- 生成 **高危资源预警** Markdown 卡片。
- 通过 Lark IM 推送：**收件人与** ``pmo_battle_report_card`` / ``pmo_dashboard_three_cards`` **同一套回退**（见 ``_resolve_monitoring_receive``）；返回 JSON 含 ``lark_recipient.source`` 标明实际使用的配置键。

**一体化测试（诊断 + 发飞书群，单条命令）**

1. 准备数据：同一 ``YYYY-MM-DD`` 下存在四份 raw JSON（可先跑 ``python -m l3_node.primitives.skills.pmo_bmo.main_skill`` 导出计划表，数据在 ``~/.jachin/.../PMO/raw``）。
2. 编辑 ``config/skills/com.jachin.pmo.bmo/pmo_bmo.yaml`` → ``pmo_resource_monitoring``：
   ``enabled: true``，``chat_id`` 为目标群，``receive_id_type: chat_id``；``lark`` 段 ``app_id``/``app_secret`` 有效。
3. 确认应用机器人已加入该群且有发消息权限。
4. 在项目根执行（**不要**加 ``--dry-run``）::

    python -m l3_node.primitives.skills.pmo_bmo.monitoring_skill --date YYYY-MM-DD

5. 成功时：终端 JSON 中 ``status`` 为 ``ok``，``lark.status`` 为 ``success``，群内出现卡片；仅诊断不发群时用同命令加 ``--dry-run`` 或将 ``enabled`` 设为 ``false``。

CLI 速查::

    python -m l3_node.primitives.skills.pmo_bmo.monitoring_skill --help
    python -m l3_node.primitives.skills.pmo_bmo.monitoring_skill
    python -m l3_node.primitives.skills.pmo_bmo.monitoring_skill --date 2026-03-31 --dry-run

**单独测监控（不发飞书，推荐先做）**

1. 任选快照日 ``YYYY-MM-DD``；若 ``~/.jachin/.../PMO/raw`` 下尚无当日 **完整四表** JSON，本模块会先调 ``ensure_pmo_raw_for_monitoring`` 自动导出（需 Lark 凭证）。
2. 诊断读数优先 ``docs/pmo_bmo_plugin/raw/{日期}_*.json``，否则用上述 client raw；计算负荷至少需要三份：
   ``req_march_coarse``、``req_march_fine``、``dev_tasks_view_core``、``art_tasks_completed``（与 ``export_pmo_tables`` 计划一致）。
3. 执行 ``--dry-run``：**仍会** 拉表检查、聚合饱和度、生成 ``alerts`` / ``markdown``，**仅跳过** ``send_markdown_card``；JSON 里已有 ``lark_recipient``（可核对将发到哪）。
4. 程序化调用：``run_pmo_resource_monitoring(..., dry_run=True)``（可传 ``snapshot_date='YYYY-MM-DD'``），返回值含 ``alerts``、``markdown``、``meta``、``pmo_raw_ensure``。

**单独测监控 + 发 Lark 群聊（真发消息）**

1. **凭证与群**：``pmo_bmo.yaml`` 的 ``lark.app_id`` / ``app_secret`` 有效；应用机器人已加入目标群且能发消息。
2. **收件人**：``pmo_resource_monitoring.chat_id`` 填群 ``oc_…``，或**留空**以与 ``pmo_battle_report_card.chat_id`` / ``pmo_dashboard_three_cards.chat_id`` / notifier ``default_chat_id`` 同源回退（见 ``_resolve_monitoring_receive``）。
3. **开启发送**：合并后 YAML 中 ``pmo_resource_monitoring.enabled: true``，或临时 ``set PMO_RESOURCE_MONITORING_ENABLED=1``（PowerShell：``$env:PMO_RESOURCE_MONITORING_ENABLED='1'``）。
4. **执行（不要带 --dry-run）**，项目根目录::

    python -m l3_node.primitives.skills.pmo_bmo.monitoring_skill --date YYYY-MM-DD

5. **成功判据**：终端 JSON ``status`` 为 ``ok``，``lark.status`` 为 ``success``（或等价成功字段），群内出现「高危资源预警」Markdown 卡片；若失败看 ``lark`` / ``error`` 与 ``lark_recipient``。

配置：``config/skills/com.jachin.pmo.bmo/pmo_bmo.yaml`` → ``pmo_resource_monitoring``。

``enabled: true`` 时才向飞书发消息；为 ``false`` 或未配置时仍会**完整跑诊断**并打印 ``alerts`` / ``markdown``，仅跳过发送。

配置合并：仓库内 ``config/skills/.../pmo_bmo.yaml`` 与 ``~/.jachin/config/.../pmo_bmo.yaml`` **都会读取并深度合并**，**用户目录覆盖仓库**。仅改 ~/.jachin 里 ``pmo_resource_monitoring.enabled`` 即可发群，无需改仓库副本。

可选环境变量 ``PMO_RESOURCE_MONITORING_ENABLED=1`` / ``0`` 覆盖 YAML 中的 ``enabled``（便于 Agent/流水线注入）。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _person_key(display: str) -> str:
    s = (display or "").strip()
    if not s:
        return "__unassigned__"
    return s.lower()


def _deep_merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """浅层递归合并；override 覆盖 base 同名键，嵌套 dict 再合并。"""
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge_dict(out[k], v)
        else:
            out[k] = v
    return out


def _load_skill_yaml(project_root: Path) -> dict[str, Any]:
    """
    合并两处配置（**后者覆盖前者**）：

    1. ``<project>/config/skills/.../pmo_bmo.yaml``
    2. ``~/.jachin/config/skills/.../pmo_bmo.yaml``

    旧逻辑「只读第一个存在的文件」会导致：仓库里 ``enabled: false`` 时，即使用户在 ~/.jachin 里写了 ``true`` 也永远不生效。
    """
    import yaml

    candidates = [
        project_root / "config" / "skills" / "com.jachin.pmo.bmo" / "pmo_bmo.yaml",
        Path.home() / ".jachin" / "config" / "skills" / "com.jachin.pmo.bmo" / "pmo_bmo.yaml",
    ]
    merged: dict[str, Any] = {}
    loaded: list[str] = []
    for p in candidates:
        if not p.is_file():
            continue
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                continue
            merged = _deep_merge_dict(merged, data)
            loaded.append(str(p.resolve()))
        except Exception as e:
            logger.warning("[monitoring_skill] 读取 YAML 失败 %s: %s", p, e)
    if loaded:
        logger.debug("[monitoring_skill] pmo_bmo.yaml 已合并 %s 个文件: %s", len(loaded), loaded)
    return merged


def _coerce_yaml_bool(val: Any, *, default: bool = False) -> bool:
    """兼容 YAML 布尔、数字、字符串；避免 ``bool(\"false\") == True`` 的坑。"""
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val != 0
    s = str(val).strip().lower()
    if s in ("true", "1", "yes", "on", "y"):
        return True
    if s in ("false", "0", "no", "off", "n", ""):
        return False
    return default


def _monitoring_send_enabled(mon: dict[str, Any]) -> tuple[bool, str]:
    """
    是否发飞书：环境变量优先，其次 ``pmo_resource_monitoring.enabled``。

    环境变量 ``PMO_RESOURCE_MONITORING_ENABLED``：1/true/yes → 开；0/false/no → 关；未设置 → 读配置。
    """
    raw_env = (os.environ.get("PMO_RESOURCE_MONITORING_ENABLED") or "").strip()
    if raw_env:
        e = raw_env.lower()
        if e in ("1", "true", "yes", "on", "y"):
            return True, "env:PMO_RESOURCE_MONITORING_ENABLED=1"
        if e in ("0", "false", "no", "off", "n"):
            return False, "env:PMO_RESOURCE_MONITORING_ENABLED=0"
    v = mon.get("enabled")
    return _coerce_yaml_bool(v, default=False), "yaml:pmo_resource_monitoring.enabled"


def _resolve_raw_dir(project_root: Path, snap: str) -> tuple[Path, str]:
    """优先使用仓库 ``docs/pmo_bmo_plugin/raw``，否则回退 ``~/.jachin/.../PMO/raw``。"""
    docs_raw = project_root / "docs" / "pmo_bmo_plugin" / "raw"
    dev_name = f"{snap}_dev_tasks_view_core.json"
    if (docs_raw / dev_name).is_file():
        return docs_raw, "docs/pmo_bmo_plugin/raw"
    from l3_node.primitives.mcp.mcp_tools.pmo_bmo.paths import get_pmo_raw_dir

    fallback = get_pmo_raw_dir()
    if (fallback / dev_name).is_file():
        return fallback, str(fallback)
    raise FileNotFoundError(
        f"未找到 {dev_name}，请先导出计划表或复制 raw 到 docs/pmo_bmo_plugin/raw"
    )


def _done_status(s: str) -> bool:
    t = (s or "").strip().lower()
    if not t:
        return False
    done_kw = ("完成", "已上线", "关闭", "done", "已验收", "已发布", "取消", "作废", "resolved")
    return any(k in t for k in done_kw)


def _waiting_only_status(s: str) -> bool:
    t = (s or "").strip().lower()
    if not t:
        return False
    wait_kw = ("等待", "待图", "ui", "出图", "阻塞", "pending", "hold", "暂停")
    return any(k in t for k in wait_kw)


@dataclass
class PersonLoad:
    """单人「本周排期任务」统计（仅自然周内有日期交集的行）。"""

    display: str
    week_total: int = 0
    week_done: int = 0
    # 未完成项，供过载详情展示
    task_titles: list[str] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # 已完成项摘要（过早清空告警用）
    done_titles: list[str] = field(default_factory=list)

    def active_rows(self) -> int:
        return max(0, self.week_total - self.week_done)


def _progress_looks_done(val: Any) -> bool:
    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _cell_to_text

    s = _cell_to_text(val).strip()
    if not s:
        return False
    s = s.replace("%", "").strip()
    try:
        x = float(s.replace(",", ""))
        if x >= 99.0:
            return True
        if abs(x - 1.0) < 0.02:
            return True
    except (ValueError, TypeError):
        pass
    return s in ("100", "1", "1.0", "完成", "已完成")


def _product_row_done(fld: dict[str, Any]) -> bool:
    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _cell_to_text

    for k in ("开发状态", "需求状态"):
        if _done_status(_cell_to_text(fld.get(k))):
            return True
    if _progress_looks_done(fld.get("进度")):
        return True
    blob = " ".join(_cell_to_text(fld.get(k)) for k in ("开发状态", "需求状态", "进度"))
    return _done_status(blob)


def _dev_art_row_done(fld: dict[str, Any]) -> bool:
    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _cell_to_text

    st = _cell_to_text(fld.get("状态"))
    if _done_status(st):
        return True
    if _progress_looks_done(fld.get("进度")):
        return True
    return False


def _ingest_weekly_json_records(
    path: Path,
    *,
    kind: str,
    week_start: date,
    week_end: date,
    dept: str,
) -> dict[str, PersonLoad]:
    """
    仅纳入「与本周有日期交集」的记录；与 main_skill 中
    ``_pmo_product_row_in_week`` / ``_pmo_dev_art_interval_overlaps_week`` 一致。
    kind: product | dev | art
    """
    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _cell_to_text

    from l3_node.primitives.skills.pmo_bmo.main_skill import (
        _pmo_dev_art_interval_overlaps_week,
        _pmo_product_row_in_week,
        _pmo_split_assignee_tokens,
    )

    doc = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, PersonLoad] = {}

    def _ensure(pk: str, disp: str) -> PersonLoad:
        if pk not in out:
            out[pk] = PersonLoad(display=disp.strip() or "（未指定）")
        return out[pk]

    art_title_key = "任务（交互动画注意问题请标注）"

    for rec in doc.get("records") or []:
        fld = rec.get("fields") or {}

        if kind == "product":
            if not _pmo_product_row_in_week(fld, week_start, week_end):
                continue
            person = ""
            for k in ("责任人", "开发执行人", "美术执行人"):
                t = _cell_to_text(fld.get(k)).strip()
                if t:
                    person = t.split(";")[0].strip()
                    break
            pk = _person_key(person)
            disp = person.strip() or "（未指定）"
            title = _cell_to_text(fld.get("需求简述")).strip() or "(无标题)"
            done = _product_row_done(fld)
            st_show = _cell_to_text(fld.get("开发状态")) or _cell_to_text(fld.get("需求状态")) or "—"
            pl = _ensure(pk, disp)
            pl.week_total += 1
            if done:
                pl.week_done += 1
                if len(pl.done_titles) < 12:
                    pl.done_titles.append(f"[{dept}] {title[:100]}")
            else:
                pl.task_titles.append(f"[{dept}] {title[:120]}")
                pl.statuses.append(st_show)
        elif kind == "dev":
            if not _pmo_dev_art_interval_overlaps_week(fld, week_start, week_end):
                continue
            title = _cell_to_text(fld.get("任务")).strip() or "(无标题)"
            done = _dev_art_row_done(fld)
            st_show = _cell_to_text(fld.get("状态")) or "—"
            people = _pmo_split_assignee_tokens(_cell_to_text(fld.get("任务执行人")))
            if not people:
                pk = "__unassigned__"
                disp = "（未指定）"
                pl = _ensure(pk, disp)
                pl.week_total += 1
                if done:
                    pl.week_done += 1
                    if len(pl.done_titles) < 12:
                        pl.done_titles.append(f"[{dept}] {title[:100]}")
                else:
                    pl.task_titles.append(f"[{dept}] {title[:120]}")
                    pl.statuses.append(st_show)
                continue
            for tok in people:
                t = tok.strip()
                if not t:
                    continue
                pk = _person_key(t)
                pl = _ensure(pk, t)
                pl.week_total += 1
                if done:
                    pl.week_done += 1
                    if len(pl.done_titles) < 12:
                        pl.done_titles.append(f"[{dept}] {title[:100]}")
                else:
                    pl.task_titles.append(f"[{dept}] {title[:120]}")
                    pl.statuses.append(st_show)
        elif kind == "art":
            if not _pmo_dev_art_interval_overlaps_week(fld, week_start, week_end):
                continue
            title = _cell_to_text(fld.get(art_title_key) or fld.get("任务")).strip() or "(无标题)"
            done = _dev_art_row_done(fld)
            st_show = _cell_to_text(fld.get("状态")) or "—"
            people = _pmo_split_assignee_tokens(_cell_to_text(fld.get("设计责任人")))
            if not people:
                pk = "__unassigned__"
                disp = "（未指定）"
                pl = _ensure(pk, disp)
                pl.week_total += 1
                if done:
                    pl.week_done += 1
                    if len(pl.done_titles) < 12:
                        pl.done_titles.append(f"[{dept}] {title[:100]}")
                else:
                    pl.task_titles.append(f"[{dept}] {title[:120]}")
                    pl.statuses.append(st_show)
                continue
            for tok in people:
                t = tok.strip()
                if not t:
                    continue
                pk = _person_key(t)
                pl = _ensure(pk, t)
                pl.week_total += 1
                if done:
                    pl.week_done += 1
                    if len(pl.done_titles) < 12:
                        pl.done_titles.append(f"[{dept}] {title[:100]}")
                else:
                    pl.task_titles.append(f"[{dept}] {title[:120]}")
                    pl.statuses.append(st_show)
        else:
            raise ValueError(f"unknown kind={kind!r}")

    return out


def _merge_weekly_loads(*maps: dict[str, PersonLoad]) -> dict[str, PersonLoad]:
    merged: dict[str, PersonLoad] = {}
    for m in maps:
        for pk, pl in m.items():
            if pk not in merged:
                merged[pk] = PersonLoad(display=pl.display)
            tgt = merged[pk]
            tgt.week_total += pl.week_total
            tgt.week_done += pl.week_done
            tgt.task_titles.extend(pl.task_titles)
            tgt.statuses.extend(pl.statuses)
            tgt.notes.extend(pl.notes)
            tgt.done_titles.extend(pl.done_titles)
    return merged


def compute_weekly_saturation(
    project_root: Path,
    snapshot_date: str,
    *,
    weekly_capacity_units: float = 4.0,
) -> tuple[dict[str, PersonLoad], dict[str, Any]]:
    """
    从 raw JSON 聚合每人 **本周排期任务**（与周有日期交集）的件数及完成数；
    饱和度判定改在 ``diagnose_alerts`` 中按「周历进度 vs 实际完成」计算。
    ``weekly_capacity_units`` 仍写入 meta，供 YAML 兼容与文档说明。
    """
    from l3_node.primitives.skills.pmo_bmo.main_skill import _pmo_report_week_bounds

    snap = snapshot_date.strip()[:10]
    raw_dir, raw_src = _resolve_raw_dir(project_root, snap)
    ref_date = datetime.strptime(snap, "%Y-%m-%d").date()
    week_start, week_end = _pmo_report_week_bounds(ref_date)

    meta: dict[str, Any] = {
        "raw_dir": str(raw_dir),
        "raw_source": raw_src,
        "snapshot_date": snap,
        "ref_date": ref_date.isoformat(),
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "pace_logic": "weekly_tasks_date_overlap_vs_elapsed_week_fraction",
        "weekly_capacity_units": weekly_capacity_units,
    }

    paths = {
        "product": raw_dir / f"{snap}_req_march_fine.json",
        "dev": raw_dir / f"{snap}_dev_tasks_view_core.json",
        "art": raw_dir / f"{snap}_art_tasks_completed.json",
    }
    missing = [k for k, p in paths.items() if not p.is_file()]
    if missing:
        raise FileNotFoundError(f"缺少 raw JSON: {missing} under {raw_dir}")

    prod = _ingest_weekly_json_records(
        paths["product"], kind="product", week_start=week_start, week_end=week_end, dept="产品"
    )
    dev = _ingest_weekly_json_records(
        paths["dev"], kind="dev", week_start=week_start, week_end=week_end, dept="开发"
    )
    art = _ingest_weekly_json_records(
        paths["art"], kind="art", week_start=week_start, week_end=week_end, dept="美术"
    )

    merged = _merge_weekly_loads(prod, dev, art)
    meta["person_count"] = len(merged)
    meta["week_task_totals"] = {pl.display: pl.week_total for pl in merged.values() if pl.week_total > 0}
    return merged, meta


def diagnose_alerts(
    loads: dict[str, PersonLoad],
    *,
    ref_date: date,
    week_start: date,
    overload_pct: float = 120.0,
    hunger_pct: float = 50.0,
    weekly_capacity_units: float = 4.0,
    pace_slack: float = 0.5,
    min_elapsed_days_for_overload: int = 3,
    early_finish_max_frac: float = 0.36,
) -> list[dict[str, Any]]:
    """
    基于「本周排期任务」的周历进度告警（非 LLM）。

    - **过载**：已过周中后，完成数仍明显低于 ``本周任务数 × (已过天数/7)``。
    - **过早清空**：本周排期 ≥2，但在周初即已全部完成（可能负荷过轻）。
    - **饥饿（等待型）**：未完成项全部为等待/阻塞类状态（旧规则保留）。
    ``overload_pct`` / ``hunger_pct`` / ``weekly_capacity_units`` 保留参数兼容 YAML，新逻辑主要用 pace_* 与周任务计数。
    """
    _ = (overload_pct, hunger_pct, weekly_capacity_units)  # 旧配置键保留，避免 YAML 报错

    elapsed_days = (ref_date - week_start).days + 1
    elapsed_days = max(1, min(7, elapsed_days))
    frac = elapsed_days / 7.0

    alerts: list[dict[str, Any]] = []
    for pk, pl in loads.items():
        if pk == "__unassigned__":
            continue
        n = pl.week_total
        c = pl.week_done
        if n <= 0:
            continue

        expected = n * frac
        detail_lines: list[str] = []
        for i, tit in enumerate(pl.task_titles[:8]):
            st = pl.statuses[i] if i < len(pl.statuses) else ""
            detail_lines.append(f"- {tit} ・ `{st or '—'}`")

        all_incomplete_wait = False
        if pl.task_titles and pl.statuses and len(pl.statuses) == len(pl.task_titles):
            all_incomplete_wait = all(_waiting_only_status(pl.statuses[i]) for i in range(len(pl.statuses)))

        # 1) 进度落后（过载）
        behind = (
            elapsed_days >= min_elapsed_days_for_overload
            and expected >= 1.0
            and c + pace_slack < expected
        )
        if behind:
            shortfall = max(0.0, expected - c)
            pace_pct = round(100.0 * c / n / max(frac, 0.01), 1)
            wait_hint = ""
            if all_incomplete_wait:
                wait_hint = "（未完成项均为等待/依赖类状态，请区分产能与外部依赖）"
            alerts.append(
                {
                    "level": "overload",
                    "emoji": "🔴",
                    "person": pl.display,
                    "severity": shortfall,
                    "saturation": pace_pct,
                    "load_units": round(shortfall, 2),
                    "week_total": n,
                    "week_done": c,
                    "expected_approx": round(expected, 2),
                    "summary": (
                        f"进度落后{wait_hint}：截至 **{ref_date.isoformat()}**（本周第 **{elapsed_days}/7** 天），"
                        f"本周排期 **{n}** 项中完成 **{c}** 项；按周历进度期望约 **{expected:.1f}** 项。"
                        f" 完成度相对周进度约 **{pace_pct:.0f}%**（100% 为刚好跟上）。"
                    ),
                    "details": "\n".join(detail_lines) if detail_lines else "（无未完成明细，或已完成）",
                }
            )
            continue

        # 2) 过早完成（周初已清空本周排期）
        if n >= 2 and c == n and frac <= early_finish_max_frac:
            alerts.append(
                {
                    "level": "early_finish",
                    "emoji": "🟡",
                    "person": pl.display,
                    "severity": float(n),
                    "saturation": 100.0,
                    "load_units": 0.0,
                    "week_total": n,
                    "week_done": c,
                    "expected_approx": round(expected, 2),
                    "summary": (
                        f"过早清空：本周排期 **{n}** 项已在周初（周进度约 **{frac:.0%}**）全部完成，"
                        f"相对当周时间线偏轻松，请关注是否分配偏少或需补新排期。"
                    ),
                    "details": "\n".join(f"- {t}" for t in pl.done_titles[:8]) or "—",
                }
            )
            continue

        # 3) 等待型饥饿（未触发过载时）
        if c < n and all_incomplete_wait:
            alerts.append(
                {
                    "level": "hunger",
                    "emoji": "🟢",
                    "person": pl.display,
                    "severity": 0.5,
                    "saturation": round(100.0 * c / n, 1) if n else 0.0,
                    "load_units": float(n - c),
                    "week_total": n,
                    "week_done": c,
                    "expected_approx": round(expected, 2),
                    "summary": (
                        f"等待型：本周排期 **{n}** 项中未完成 **{n - c}** 项，"
                        f"且均为等待/阻塞/UI 依赖类状态。"
                    ),
                    "details": "\n".join(detail_lines) or "—",
                }
            )

    alerts.sort(key=lambda x: (-float(x.get("severity", 0)), x["person"]))
    return alerts


def build_high_risk_card_markdown(
    alerts: list[dict[str, Any]],
    snapshot_date: str,
    *,
    extra_footer: str = "",
) -> str:
    """组装「高危资源预警」卡片正文（lark_md）。"""
    lines: list[str] = [
        f"**快照日期**：{snapshot_date}",
        "",
        "### 高危资源预警（规则诊断）",
        "",
        "判定：**🔴 进度落后**（本周排期相对周历时间明显做不完）｜**🟡 过早清空**（周初已做完本周排期）｜"
        "**🟢 等待型**（未完成项均卡在等待/依赖）。仅统计「日期与本周有交集」的排期任务。",
        "",
    ]
    if not alerts:
        lines.append("*本周未触发过载/饥饿规则（或无可归因人员）。*")
    else:
        for a in alerts:
            lines.append(f"{a['emoji']} **{a['person']}** — {a['summary']}")
            lines.append("")
            lines.append(a["details"])
            lines.append("")
    if extra_footer.strip():
        lines.append("---")
        lines.append(extra_footer.strip())
    return "\n".join(lines).strip()


def _apply_lark_env_from_yaml(cfg: dict[str, Any]) -> None:
    lk = cfg.get("lark") or {}
    if isinstance(lk, dict):
        aid = (lk.get("app_id") or "").strip()
        sec = (lk.get("app_secret") or "").strip()
        if aid and sec:
            os.environ.setdefault("LARK_APP_ID", aid)
            os.environ.setdefault("LARK_APP_SECRET", sec)
        if lk.get("lark_use_feishu"):
            os.environ["LARK_USE_FEISHU"] = "1"


def _resolve_monitoring_receive(
    merged: dict[str, Any],
) -> tuple[str, str, str]:
    """
    与 ``pmo_bmo.yaml`` 内 PMO 推送保持一致收件：**群 chat_id** 与战报/三张卡同源回退。

    优先级：

    1. ``pmo_resource_monitoring.chat_id``（本模块显式覆盖）
    2. ``pmo_resource_monitoring.manager_receive_id`` / ``manager_open_id``（单聊；配合 ``receive_id_type``）
    3. ``pmo_battle_report_card.chat_id``（K11 战报 VChart 群）
    4. ``pmo_dashboard_three_cards.chat_id``（三张仪表盘卡）
    5. ``atom_lark_notifier`` 的 ``default_chat_id`` / 环境变量 ``BI_LARK_CHAT_ID``（与 ``tool_data_visualizer._resolve_chat_id`` 一致）

    返回 ``(receive_id, receive_id_type, source_label)``；无可用 id 时 ``("", "chat_id", "none")``。
    """
    mon = (merged.get("pmo_resource_monitoring") or {}) if isinstance(merged, dict) else {}
    br = (merged.get("pmo_battle_report_card") or {}) if isinstance(merged, dict) else {}
    tdc = (merged.get("pmo_dashboard_three_cards") or {}) if isinstance(merged, dict) else {}

    cid = str(mon.get("chat_id") or "").strip()
    if cid and not str(cid).startswith("${"):
        return cid, "chat_id", "pmo_resource_monitoring.chat_id"

    mgr = str(mon.get("manager_receive_id") or mon.get("manager_open_id") or "").strip()
    if mgr and not str(mgr).startswith("${"):
        rtype = (mon.get("receive_id_type") or "open_id").strip().lower()
        if rtype not in ("open_id", "user_id", "union_id", "chat_id"):
            rtype = "open_id"
        return mgr, rtype, "pmo_resource_monitoring.manager_receive_id"

    for label, block in (
        ("pmo_battle_report_card.chat_id", br),
        ("pmo_dashboard_three_cards.chat_id", tdc),
    ):
        c = str(block.get("chat_id") or "").strip()
        if c and not str(c).startswith("${"):
            return c, "chat_id", label

    from l3_node.primitives.mcp.mcp_tools.pmo_bmo.tool_data_visualizer import _resolve_chat_id

    fb = _resolve_chat_id("")
    if fb:
        return fb, "chat_id", "atom_lark_notifier.default_chat_id|BI_LARK_CHAT_ID"

    return "", "chat_id", "none"


def run_pmo_resource_monitoring(
    project_root: Path | None = None,
    snapshot_date: str | None = None,
    *,
    dry_run: bool = False,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    执行资源监控：计算饱和度 → 生成告警文案 → 可选 Lark 推送。

    收件人：见 ``_resolve_monitoring_receive``（与 ``pmo_battle_report_card`` / ``pmo_dashboard_three_cards`` / notifier 默认群 **同一套回退**）。
    """
    from datetime import date as date_cls

    from l3_node.paths import get_app_root
    from l3_node.primitives.skills.pmo_bmo.main_skill import ensure_pmo_raw_for_monitoring

    root = project_root or get_app_root()
    merged = dict(cfg or _load_skill_yaml(root))
    mon = (merged.get("pmo_resource_monitoring") or {}) if isinstance(merged, dict) else {}
    # enabled 只控制「是否发飞书」；不设为 true 时仍执行完整诊断（便于直接跑 python -m ... 看结果）

    snap = (snapshot_date or mon.get("snapshot_date") or date_cls.today().isoformat()).strip()[:10]

    # 先检查本机 ~/.jachin/.../PMO/raw 是否有当日完整四表；缺则自动 export_pmo_tables（逻辑在 main_skill.ensure_pmo_raw_for_monitoring）
    pmo_raw_ensure = ensure_pmo_raw_for_monitoring(root, snap)
    cap = float(mon.get("weekly_capacity_units", 4.0))
    overload_pct = float(mon.get("overload_saturation_pct", 120.0))
    hunger_pct = float(mon.get("hunger_saturation_pct", 50.0))
    pace_slack = float(mon.get("pace_slack", 0.5))
    min_elapsed = int(mon.get("min_elapsed_days_for_overload", 3))
    early_frac = float(mon.get("early_finish_max_frac", 0.36))

    loads, meta = compute_weekly_saturation(root, snap, weekly_capacity_units=cap)
    ref_d = datetime.strptime(snap, "%Y-%m-%d").date()
    week_start_d = datetime.strptime(str(meta["week_start"])[:10], "%Y-%m-%d").date()
    alerts = diagnose_alerts(
        loads,
        ref_date=ref_d,
        week_start=week_start_d,
        overload_pct=overload_pct,
        hunger_pct=hunger_pct,
        weekly_capacity_units=cap,
        pace_slack=pace_slack,
        min_elapsed_days_for_overload=min_elapsed,
        early_finish_max_frac=early_frac,
    )

    extra = ""
    out_md = root / "docs" / "pmo_bmo_plugin" / "output" / f"PMO_人员任务统计_{snap}.md"
    if out_md.is_file():
        extra = f"（参考）已生成统计：`{out_md.as_posix()}`"

    md = build_high_risk_card_markdown(alerts, snap, extra_footer=extra)
    title = str(mon.get("card_title") or "🚨 高危资源预警")

    enabled_flag, enabled_source = _monitoring_send_enabled(mon if isinstance(mon, dict) else {})
    send_lark = enabled_flag and not dry_run

    out: dict[str, Any] = {
        "status": "ok",
        "snapshot_date": snap,
        "pmo_raw_ensure": pmo_raw_ensure,
        "meta": meta,
        "alerts": alerts,
        "markdown": md,
        "dry_run": dry_run,
        "send_lark": send_lark,
        "pmo_resource_monitoring_resolved": {
            "enabled": enabled_flag,
            "enabled_raw": mon.get("enabled") if isinstance(mon, dict) else None,
            "enabled_source": enabled_source,
        },
    }

    rid, rtype, recv_src = _resolve_monitoring_receive(merged)
    out["lark_recipient"] = {
        "source": recv_src,
        "receive_id_type": rtype,
        "receive_id": rid,
    }

    if dry_run:
        out["lark"] = {
            "status": "skipped",
            "reason": "已指定 --dry-run，不发飞书",
        }
        return out

    if not send_lark:
        out["lark"] = {
            "status": "skipped",
            "reason": (
                "未开启发飞书：请在合并后的 pmo_bmo.yaml 中设 pmo_resource_monitoring.enabled: true "
                "（仓库与 ~/.jachin 配置会合并，后者覆盖前者），或设置环境变量 PMO_RESOURCE_MONITORING_ENABLED=1"
            ),
        }
        return out

    if not rid:
        out["status"] = "partial"
        out["lark"] = {
            "status": "skipped",
            "reason": (
                "未解析到收件人：请配置 pmo_resource_monitoring.chat_id，或与战报一致 "
                "pmo_battle_report_card.chat_id / pmo_dashboard_three_cards.chat_id，"
                "或 config/mcps/atom_lark_notifier 的 default_chat_id（或 BI_LARK_CHAT_ID）"
            ),
        }
        return out

    _apply_lark_env_from_yaml(merged)
    try:
        from l3_node.primitives.mcp.mcp_tools.pmo_bmo.tool_data_visualizer import (
            _ensure_lark_credentials_from_notifier_config,
        )

        _ensure_lark_credentials_from_notifier_config()
    except Exception:
        pass

    from l3_node.channels.lark.im import send_markdown_card

    lr = send_markdown_card(
        receive_id=rid,
        markdown_content=md,
        title=title,
        receive_id_type=rtype,
    )
    out["lark"] = lr
    if lr.get("status") != "success":
        out["status"] = "error"
        out["error"] = lr.get("error", "lark send failed")
    return out


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="PMO 资源监控（Monitoring_skill）")
    p.add_argument("--date", dest="snapshot_date", default="", help="YYYY-MM-DD，默认今天")
    p.add_argument("--dry-run", action="store_true", help="只打印 Markdown，不发 Lark")
    args = p.parse_args(argv)

    from l3_node.paths import get_app_root

    root = get_app_root()
    sd = args.snapshot_date.strip() or None
    r = run_pmo_resource_monitoring(root, snapshot_date=sd, dry_run=args.dry_run)
    print(json.dumps(r, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
