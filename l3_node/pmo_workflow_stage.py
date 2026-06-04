"""
PMO 大需求「状态」列：按《项目开发全流程说明》泳道三阶段 × 四职能推断当前步骤。

SSOT 文档：docs/pmo_bmo_plugin/项目开发全流程说明.md §1
禁止战报 📊 仅写「待开始 / 进行中 / 已完成」等粗粒度词。
"""
from __future__ import annotations

import re
from typing import Any

from l3_node.pmo_report_format import is_terminal_personnel_task

# (rank, 阶段, 步骤) — rank 越大越靠后；与全流程图末端「总结复盘」对齐
_WORKFLOW_RANK_MAX = 98

_SIMPLE_STATUS_FORBIDDEN = frozenset(
    {"待开始", "进行中", "已完成", "待办", "未开始", "完成"}
)

_LANE_LABELS: dict[str, str] = {
    "product": "产品",
    "art": "美术",
    "tech": "技术",
    "ops": "市场运营",
}


def _blob(task: dict[str, Any]) -> str:
    parts = [
        str(task.get("progress") or ""),
        str(task.get("status") or ""),
        str(task.get("status_text") or ""),
        str(task.get("task") or ""),
        str(task.get("expectation_purpose") or ""),
    ]
    return " ".join(parts).strip()


def is_workflow_placeholder_child(task: dict[str, Any]) -> bool:
    """
    飞书表「部门/泳道」占位行：Requirement 为 前端开发/开发 等且无有效 Progress。
    不得单独拉低大需求 📊 状态（案例：Laro GO 下空「前端开发」→ 误显示需求评审）。
    """
    from l3_node.tools.pmo_db_tools import _DEPT_PLACEHOLDER_ROW_NAMES

    name = str(task.get("task") or "").strip()
    if not name or name in _DEPT_PLACEHOLDER_ROW_NAMES:
        return True
    prog = str(task.get("progress") or "").strip()
    st = str(task.get("status") or task.get("status_text") or "").strip()
    if name in ("前端开发", "后端开发", "程序开发") and not prog and not st:
        return True
    return False


def _has_delivery_mark(task: dict[str, Any]) -> bool:
    return bool(
        task.get("actual_delivery_date")
        or task.get("actual_delivery_date_iso")
        or task.get("acceptance_date")
        or task.get("acceptance_date_iso")
    )


def task_lane(department: str | None) -> str:
    d = str(department or "").strip()
    if not d or d in ("—", "-"):
        return "tech"
    if "产品" in d or d == "产品":
        return "product"
    if "美术" in d or d == "设计":
        return "art"
    if "运营" in d or "市场" in d:
        return "ops"
    return "tech"


def _match_any(blob: str, keywords: tuple[str, ...]) -> bool:
    b = blob.lower()
    return any(k.lower() in b for k in keywords)


def _step(rank: int, phase: str, step: str) -> tuple[int, str, str]:
    return (rank, phase, step)


def rank_to_workflow_completion_pct(rank: int) -> int:
    """泳道步骤 rank → 📊 完成度百分比（禁止用子任务条数占比冒充）。"""
    r = max(0, min(_WORKFLOW_RANK_MAX, int(rank)))
    return max(0, min(100, round(100 * r / _WORKFLOW_RANK_MAX)))


def task_workflow_rank(task: dict[str, Any]) -> int:
    return infer_task_workflow_step(task)[0]


def infer_epic_workflow_completion_pct(
    epic: dict[str, Any],
    children: list[dict[str, Any]],
) -> int:
    """
    大需求完成度：各职能线（产品/美术/技术/运营）按子任务步骤 rank 取均值，再跨线平均。

    与 infer_epic_workflow_status 同源推断，避免「3 条里完成 1 条 = 33%」与泳道阶段脱节。
    """
    children = _filter_children_for_workflow_infer(children)
    if not children:
        if is_terminal_personnel_task(epic):
            return 100
        return rank_to_workflow_completion_pct(task_workflow_rank(epic))

    if all(is_terminal_personnel_task(c) for c in children):
        return 100

    lane_avg_ranks: list[float] = []
    for lane in ("product", "art", "tech", "ops"):
        lane_tasks = [c for c in children if task_lane(c.get("department")) == lane]
        if not lane_tasks:
            continue
        ranks = [task_workflow_rank(t) for t in lane_tasks]
        lane_avg_ranks.append(sum(ranks) / len(ranks))

    if lane_avg_ranks:
        mean_rank = sum(lane_avg_ranks) / len(lane_avg_ranks)
    else:
        ranks = [task_workflow_rank(t) for t in children]
        mean_rank = sum(ranks) / len(ranks)

    return rank_to_workflow_completion_pct(round(mean_rank))


def format_workflow_progress_bar(pct: int) -> str:
    """10 格进度条 + 百分比（📊 完成度列 SSOT）。"""
    pct = max(0, min(100, int(pct)))
    filled = round(pct / 10)
    return f"[{'▓' * filled}{'░' * (10 - filled)}] {pct}%"


def infer_task_workflow_step(task: dict[str, Any]) -> tuple[int, str, str]:
    """单条子任务 → (rank, 阶段, 步骤)。"""
    if is_terminal_personnel_task(task):
        blob = _blob(task)
        lane = task_lane(task.get("department"))
        if _match_any(blob, ("复盘", "总结", "归因")):
            return _step(98, "上线发布", "总结复盘")
        if _match_any(blob, ("上线", "发布", "班车", "已发")):
            return _step(92, "上线发布", "班车发布")
        if _match_any(blob, ("冒烟",)):
            return _step(88, "上线发布", "冒烟测试")
        if _match_any(blob, ("发布评审",)):
            return _step(86, "上线发布", "发布评审")
        if lane == "product" and _match_any(blob, ("验收",)):
            return _step(82, "开发/验收", "产品验收")
        if lane == "art" and _match_any(blob, ("验收", "交付", "物料")):
            return _step(78, "开发/验收", "美术验收")
        if _match_any(blob, ("自测", "联调", "测试通过")):
            return _step(74, "开发/验收", "技术自测验收")
        if _match_any(blob, ("测试环境", "提交测试", "部署", "预发", "测试服")):
            return _step(68, "开发/验收", "环境部署")
        # 镜像常见：Progress 仍写「开发中」但已交付/按时完成 → 按测试环境阶段计（非「需求评审」）
        if _match_any(blob, ("开发中", "实现中", "按时完成", "已完成")) and _has_delivery_mark(
            task
        ):
            return _step(68, "开发/验收", "环境部署")
        return _step(90, "上线发布", "子项已闭环")

    blob = _blob(task)
    lane = task_lane(task.get("department"))

    if _match_any(blob, ("复盘", "总结", "归因", "数据跟踪")):
        return _step(95, "上线发布", "总结复盘")
    if _match_any(blob, ("班车", "发布上线", "已上线", "提审发布", "发布评审")):
        if "评审" in blob:
            return _step(86, "上线发布", "发布评审")
        return _step(90, "上线发布", "班车发布")
    if _match_any(blob, ("冒烟",)):
        return _step(88, "上线发布", "冒烟测试")
    if _match_any(blob, ("产品验收", "整体验收", "PM验收")) or (
        lane == "product" and _match_any(blob, ("验收", "Acceptance"))
    ):
        return _step(80, "开发/验收", "产品验收")
    if _match_any(blob, ("联调", "集成调试")):
        return _step(72, "开发/验收", "联调")
    if _match_any(blob, ("自测", "技术自测", "研发自测", "自测验收")):
        return _step(70, "开发/验收", "技术自测验收")
    if _match_any(
        blob,
        ("提交测试", "测试环境", "部署测试", "部署生产", "环境部署", "预发", "测试服"),
    ):
        return _step(65, "开发/验收", "环境部署")
    if _match_any(blob, ("物料整理", "物料交付", "素材整理")):
        return _step(62, "开发/验收", "物料整理")
    if lane == "art" and _match_any(blob, ("美术验收", "交付前端", "资产验收")):
        return _step(58, "开发/验收", "美术验收")

    if lane == "product":
        if _match_any(blob, ("需求跟进", "跟进", "开发期")):
            return _step(35, "开发/验收", "需求跟进")
        if _match_any(blob, ("需求评审", "PRD宣讲", "宣讲", "评审通过", "Review")):
            return _step(22, "立项/评审", "需求评审")
        if _match_any(blob, ("需求文档", "PRD", "立项", "提案")):
            return _step(12, "立项/评审", "需求文档")
        if _match_any(blob, ("开发中", "实现")):
            return _step(35, "开发/验收", "需求跟进")
        return _step(18, "立项/评审", "需求评审")

    if lane == "art":
        if _match_any(blob, ("美术开发", "制作", "出图", "开发中")):
            return _step(48, "开发/验收", "美术开发")
        if _match_any(blob, ("美术评审", "方案评审", "风格评审")):
            return _step(28, "开发/验收", "美术评审")
        if _match_any(blob, ("确认需求", "评估", "对齐")):
            return _step(16, "立项/评审", "确认需求")
        return _step(40, "开发/验收", "美术开发")

    if lane == "ops":
        if _match_any(blob, ("投放", "归因", "数据同步")):
            return _step(88, "上线发布", "数据同步·投放优化")
        if _match_any(blob, ("运营计划", "活动计划", "物料节奏")):
            return _step(32, "立项/评审", "制定运营计划")
        if _match_any(blob, ("确认需求",)):
            return _step(14, "立项/评审", "确认需求")
        return _step(25, "立项/评审", "制定运营计划")

    # tech (开发 / 中台 / 客户端 / 后端 …)
    if _match_any(
        blob,
        (
            "开发中",
            "实现中",
            "编码",
            "接口开发",
            "程序开发",
            "客户端",
            "后端开发",
            "技术改造",
        ),
    ):
        return _step(50, "开发/验收", "技术开发")
    if _match_any(blob, ("确认需求", "技术评估", "方案评估", "拆分")):
        return _step(18, "立项/评审", "确认需求")
    if _match_any(blob, ("待开始", "未开始", "待办", "排期")):
        return _step(10, "立项/评审", "确认需求")
    if not blob or blob in ("—", "-", "null"):
        return _step(8, "立项/评审", "需求文档")
    return _step(45, "开发/验收", "技术开发")


def _lane_progress_hint(
    children: list[dict[str, Any]],
    *,
    lane: str,
) -> str:
    lane_tasks = [c for c in children if task_lane(c.get("department")) == lane]
    if not lane_tasks:
        return ""
    done = sum(1 for t in lane_tasks if is_terminal_personnel_task(t))
    total = len(lane_tasks)
    label = _LANE_LABELS.get(lane, lane)
    return f"{label} {done}/{total}"


def _health_emoji(
    *,
    children: list[dict[str, Any]],
    completion_pct: int,
    bottleneck_rank: int,
) -> str:
    blob = " ".join(_blob(c) for c in children)
    if any(x in blob for x in ("🔴", "延期", "滞后", "严重落后")):
        return "🔴"
    if completion_pct >= 100 or (
        children and all(is_terminal_personnel_task(c) for c in children)
    ):
        return "🟢"
    if bottleneck_rank >= 85:
        return "🟢"
    if bottleneck_rank >= 25 or completion_pct > 0:
        return "🔵"
    return "🟡"


def _filter_children_for_workflow_infer(
    children: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [c for c in children if not is_workflow_placeholder_child(c)]


def infer_epic_workflow_status(
    epic: dict[str, Any],
    children: list[dict[str, Any]],
    *,
    completion_pct: int | None = None,
) -> str:
    """
    综合 Epic + 子任务推断 📊「状态」列文案：{emoji} {阶段} · {步骤}（{hint}）

    瓶颈 = 各职能线 **最靠后** 步骤中的 **最慢线**（min of per-lane max rank）。
    **含已闭环子任务**（此前跳过终态会导致只剩空占位行 → 误显示「需求评审」）。
    """
    if completion_pct is None:
        preset = epic.get("workflow_completion_pct")
        if preset is not None and str(preset).strip() != "":
            try:
                completion_pct = int(preset)
            except (TypeError, ValueError):
                completion_pct = infer_epic_workflow_completion_pct(epic, children)
        else:
            completion_pct = infer_epic_workflow_completion_pct(epic, children)

    kids = _filter_children_for_workflow_infer(list(children))
    tasks_for_infer = kids if kids else (list(children) if children else [epic])

    lane_max: dict[str, int] = {}
    lane_step: dict[str, tuple[int, str, str]] = {}
    for t in tasks_for_infer:
        lane = task_lane(t.get("department"))
        rank, phase, step = infer_task_workflow_step(t)
        if lane not in lane_max or rank > lane_max[lane]:
            lane_max[lane] = rank
            lane_step[lane] = (rank, phase, step)

    if not lane_max:
        if completion_pct >= 100:
            rank, phase, step = _step(95, "上线发布", "总结复盘")
        else:
            rank, phase, step = infer_task_workflow_step(epic)
        emoji = _health_emoji(
            children=children,
            completion_pct=completion_pct,
            bottleneck_rank=rank,
        )
        return f"{emoji} {phase} · {step}"

    bottleneck_lane = min(lane_max, key=lambda ln: lane_max[ln])
    rank, phase, step = lane_step[bottleneck_lane]
    # 完成度条已较高而瓶颈仍落在立项：与 completion_pct 对齐（避免 83% + 需求评审）
    if completion_pct is not None and completion_pct >= 55 and rank < 45:
        adj = max(rank, round(completion_pct * _WORKFLOW_RANK_MAX / 100))
        if adj >= 65:
            rank, phase, step = _step(68, "开发/验收", "环境部署")
        elif adj >= 45:
            rank, phase, step = _step(50, "开发/验收", "技术开发")
    hints: list[str] = []
    hint_children = kids if kids else children
    for ln in ("tech", "art", "product", "ops"):
        h = _lane_progress_hint(hint_children, lane=ln)
        if h:
            hints.append(h)
    hint = " · ".join(hints[:3]) if hints else ""
    emoji = _health_emoji(
        children=children,
        completion_pct=completion_pct,
        bottleneck_rank=rank,
    )
    if hint:
        return f"{emoji} {phase} · {step}（{hint}）"
    return f"{emoji} {phase} · {step}"


def is_forbidden_simple_demand_status(label: str) -> bool:
    """战报 📊 状态列是否为禁止的粗粒度词。"""
    s = re.sub(r"\*+", "", str(label or "")).strip()
    s = re.sub(r"^[🟢🔵🟡🔴⚠️✅❌]\s*", "", s).strip()
    return s in _SIMPLE_STATUS_FORBIDDEN or s.endswith("待开始") and "·" not in s
