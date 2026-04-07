"""
§3 / §5.3：将网关结构化结果注入 ReAct system（软约束，非替代 DAG 执行器）。
"""
from __future__ import annotations

import json
from typing import Any


def build_gateway_system_inject(bundle: Any) -> str:
    if bundle is None:
        return ""
    parts: list[str] = []

    nodes = bundle.extra.get("validated_subintents")
    if isinstance(nodes, list) and nodes:
        parts.append("【网关·子意图 DAG】请按依赖顺序处理各子句；未完成前一项前勿跳过。")
        for i, n in enumerate(nodes):
            if not isinstance(n, dict):
                continue
            tid = str(n.get("id") or "")
            tx = str(n.get("rewritten_text") or n.get("text_span") or "").strip()
            loc = str(n.get("locality") or "")
            dep = n.get("depends_on") or []
            prec = n.get("preconditions") or []
            line = f"  {i + 1}. [{tid}] {tx[:400]} | locality={loc} | depends_on={dep}"
            if isinstance(prec, list) and prec:
                try:
                    ps = json.dumps(prec, ensure_ascii=False)
                except (TypeError, ValueError):
                    ps = str(prec)
                if len(ps) > 420:
                    ps = ps[:420] + "…"
                line += f" | preconditions={ps}"
            parts.append(line)

    if bundle.extra.get("gateway_dag_cycle_detected"):
        parts.append(
            "【网关·拓扑告警】子意图依赖图存在环或非法依赖，已禁止按 DAG 自动串行；"
            "须向用户澄清先后条件或请用户拆分指令，勿假装依赖已满足。"
        )

    da = bundle.extra.get("dag_dependency_analysis")
    if isinstance(da, list) and da:
        try:
            das = json.dumps(da, ensure_ascii=False)
        except (TypeError, ValueError):
            das = str(da)
        if len(das) > 800:
            das = das[:800] + "…"
        parts.append("【网关·依赖分析（模型自述，供核对；不得擅自删用户约束）】\n" + das)

    if bundle.extra.get("gateway_planning_mandatory"):
        parts.append(
            "【规划门禁】本轮为复合/多子意图：须遵守 intelligence_b 计划卡/brainstorm 规则；"
            "若启用 force_task_plan_file，在 delegate/coordinate/core:fs_write（非 task_plan 自身）/core:shell_exec/core:apply_patch 前须已写好 workspace task_plan.md。"
        )

    et = str(bundle.extra.get("execution_tier") or "").strip()
    if et == "composite":
        parts.append(
            "【执行分层·composite】若节点已开启 planning_composite_gate：请先仅使用只读/记忆检索与 task_plan.md 的 fs_write 完成计划；"
            "计划中工具 id 须在白名单内；缺信息可输出 [Needs_Info: …]；静态扫描通过前勿 delegate/coordinate 或执行 MCP。"
        )
    _vnodes = nodes if isinstance(nodes, list) else []
    for n in _vnodes:
        if not isinstance(n, dict):
            continue
        ss = n.get("slot_schema")
        if isinstance(ss, list) and ss:
            try:
                sj = json.dumps(ss, ensure_ascii=False)
            except (TypeError, ValueError):
                sj = str(ss)
            if len(sj) > 500:
                sj = sj[:500] + "…"
            parts.append(f"【子意图 {n.get('id') or '?'}·槽位模式】{sj}")

    merged = bundle.extra.get("semantic_route_merged")
    if merged:
        try:
            s = json.dumps(merged, ensure_ascii=False)
        except (TypeError, ValueError):
            s = str(merged)
        if len(s) > 900:
            s = s[:900] + "…"
        parts.append(f"【语义路由参考（可偏离，以用户原意为准）】\n{s}")

    mm = bundle.extra.get("multimodal_route_head")
    if isinstance(mm, dict):
        parts.append(f"【附件路由头】{json.dumps(mm, ensure_ascii=False)}")

    return "\n".join(parts).strip()
