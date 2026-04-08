"""
将 export_lark_wiki_bitable_to_json 产出的扁平 records 重组为：
  Sprint → 需求项（归纳）→ 架构与工程 | 业务逻辑

用法:
  python scripts/rebuild_bitable_json_hierarchy.py --src "D:\\zzz\\bitable_....json" [--dst ...]
  python scripts/rebuild_bitable_json_hierarchy.py --retitle-hierarchical "D:\\zzz\\树形结构.json"
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# 命中则归为「架构与工程」（其余归「业务逻辑」）；避免「服务」等过宽词单独匹配
_ARCH_PAT = re.compile(
    r"架构|框架|存储|数据库|SQLite|PostgreSQL|Redis|\bL1\b|\bL2\b|\bL3\b|部署|网关|"
    r"管道|安全|权限|密钥|沙箱|FastAPI|健康检查|gRPC|Nacos|心跳|Monitor|服务注册|"
    r"基础设施|向量|记忆|同步|白皮书|目录|零信任|生物学|LanceDB|Dream|"
    r"RBAC|CORS|引导|密钥对|加密|审计|合规|Mesh|通信|控制面|数据面|"
    r"Edge|算力|注册表|下载|调用链|策略|执行面|规范|"
    r"Go 服务|Python 服务|优雅关闭|panic|GetAllStatuses|仪表盘数据|知识库数量|"
    r"服务数量|Memory 服务|Character-Manager|协议错误|调试日志|MCP|Skill 注册|Skill 清单|"
    r"三轨道|调用链|控制面与数据|生物钟|主动感知|Jachin Mesh|Edge Mesh|算力协同",
    re.I,
)


def _sprint_display(s: str) -> str:
    s = (s or "").strip() or "未分配"
    if s.endswith("-Sprint"):
        return s
    # 2026/3/15 → 2026/03/15-Sprint
    m = re.match(r"^(\d{4})/(\d{1,2})/(\d{1,2})$", s)
    if m:
        y, mo, d = m.groups()
        return f"{y}/{int(mo):02d}/{int(d):02d}-Sprint"
    return f"{s}-Sprint"


def _first_line(task: str) -> str:
    if not isinstance(task, str):
        return ""
    return task.split("\n")[0].strip()


def _extract_major(first: str) -> tuple[int | None, str]:
    """
    返回 (major序号, 归类说明)。
    major 用于同一 Sprint 下分「需求项1/2/3…」。
    """
    first = first.strip()
    m = re.match(r"^(\d+)\.(\d+)\s+", first)
    if m:
        return int(m.group(1)), "numbered_M.N"
    m = re.match(r"^(\d+)\s+", first)
    if m:
        return int(m.group(1)), "numbered_M_"
    m = re.match(r"^(\d+)[、.]\s*", first)
    if m and not re.match(r"^\d+\.\d+", first):
        return int(m.group(1)), "numbered_M顿号"
    return None, "unnumbered"


# 无编号聚类（major 100–103）在无法从任务首行提炼标题时的兜底短名（不再加「需求项·」前缀）
_CLUSTER_FALLBACK: dict[int, str] = {
    100: "服务治理与可观测",
    101: "多媒体与美术管线",
    102: "BI与数据分析",
    103: "其他",
}


def _strip_task_prefixes(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"^\[架构\]\s*", "", s, flags=re.I)
    s = re.sub(r"^\[业务\]\s*", "", s, flags=re.I)
    return s.strip()


def _trim_item_count_suffix(title: str) -> str:
    """去掉首行末尾的「（5 项）」「（约 9 项）」等体量说明，便于作需求归纳标题。"""
    t = title.strip()
    t = re.sub(r"（\s*约?\s*\d+\s*项\s*）\s*$", "", t)
    t = re.sub(r"（\d+\s*项）\s*$", "", t)
    return t.strip()


def _core_from_first_line(first: str) -> str | None:
    """
    从任务首行提炼可读主题，覆盖：
    - 1.1 xxx（5 项）
    - 2. L3 Agent / …（约 9 项）
    - 1 Core / …（前导空格）
    """
    s = _strip_task_prefixes(first)
    if not s:
        return None
    # 1.1 标题…
    m = re.match(r"^(\d+)\.(\d+)\s+(.+)$", s)
    if m:
        return _trim_item_count_suffix(m.group(3)) or None
    # 2. 标题…  （注意：「2.」后为空格，不是 2.3）
    m = re.match(r"^(\d+)\.\s+(.+)$", s)
    if m:
        return _trim_item_count_suffix(m.group(2)) or None
    # 纯「整数 + 空格 + 标题」（避免与 1.1 冲突：要求第二位不是点）
    m = re.match(r"^(\d+)\s+(.+)$", s)
    if m and not re.match(r"^\d+\.\d", s):
        return _trim_item_count_suffix(m.group(2)) or None
    return None


def _best_title_from_samples(samples: list[str]) -> str | None:
    """在组内多条任务中，取首条能解析出主题的首行；否则用首条首行截断。"""
    for s in samples:
        fl = _first_line(s)
        core = _core_from_first_line(fl)
        if core:
            return core
    if samples:
        raw = _strip_task_prefixes(_first_line(samples[0]))
        raw = _trim_item_count_suffix(raw)
        if raw:
            return raw[:48] + ("…" if len(raw) > 48 else "")
    return None


def _cluster_needs_fallback(major: int, samples: list[str]) -> bool:
    """无编号聚类内若多条任务提炼出的主题前缀差异大，用领域兜底名比单条任务名更合适。"""
    if not (100 <= major <= 103) or len(samples) < 3:
        return False
    heads: list[str] = []
    for s in samples[:16]:
        fl = _first_line(s)
        c = _core_from_first_line(fl)
        if c:
            heads.append(c[:24])
        else:
            raw = _strip_task_prefixes(fl)
            if raw:
                heads.append(raw[:24])
    if len(heads) < 3:
        return False
    # 任意两条前缀无明显公共子串则视为杂乱「其他」类
    uniq = set(heads)
    return len(uniq) >= 3


def _req_name_for_major(_sprint: str, major: int, samples: list[str]) -> str:
    """
    需求项展示名：图2 风格「需求项{n}（从任务提炼的短标题）」；
    无编号聚类 major∈[100,103] 用短标题或领域兜底名，避免「需求项·xxx」固定废话。
    """
    core = _best_title_from_samples(samples)
    if 100 <= major <= 103:
        if _cluster_needs_fallback(major, samples):
            return _CLUSTER_FALLBACK.get(major, f"主题{major}")
        if core:
            if len(core) > 44:
                core = core[:41] + "…"
            return core
        return _CLUSTER_FALLBACK.get(major, f"主题{major}")
    if core:
        if len(core) > 44:
            core = core[:41] + "…"
        return f"需求项{major}（{core}）"
    return f"需求项{major}"


def _cluster_unnumbered(first: str) -> int:
    """无编号任务：用关键词聚类成虚拟 major 100+。"""
    if re.search(
        r"gRPC|Nacos|心跳|Monitor|服务|仪表盘|健康检查|Python 服务|Go 服务|"
        r"优雅关闭|panic|GetAllStatuses|知识库数量|Memory|Character|协议错误|调试日志",
        first,
        re.I,
    ):
        return 100
    if re.search(r"视频|分镜|片段|渲染|参考图|提示词|原型图|合并|拆分|设计", first):
        return 101
    if re.search(r"BI|战报|留存|分析|报表|MCP.*Skill|Skill.*构建", first, re.I):
        return 102
    return 103


def _bucket(task_full: str) -> str:
    first = _first_line(task_full)
    head = first + "\n" + (task_full[:2000] if isinstance(task_full, str) else "")
    # 明确偏「业务/交付表现」的先归业务逻辑
    if re.search(
        r"视频|分镜|片段|参考图|提示词|原型图|合并|拆分|总体视频|优化组件|渲染性能|"
        r"自然留存|战报|仪表盘分析|漏斗",
        first,
        re.I,
    ):
        return "业务逻辑"
    if _ARCH_PAT.search(first) or _ARCH_PAT.search(head):
        return "架构与工程"
    return "业务逻辑"


def rebuild(data: dict[str, Any]) -> dict[str, Any]:
    records: list[dict[str, Any]] = data.get("records") or []
    out_tree: list[dict[str, Any]] = []

    # sprint -> list of (record, first_line, major)
    by_sp: dict[str, list[tuple[dict[str, Any], str, int]]] = {}
    for r in records:
        f = r.get("fields") or {}
        sp = f.get("Sprint")
        if isinstance(sp, list) and sp:
            sk = sp[0] if isinstance(sp[0], str) else str(sp[0])
        else:
            sk = "未分配"
        task = f.get("任务") or ""
        first = _first_line(task)
        maj, _ = _extract_major(first)
        if maj is None:
            maj = _cluster_unnumbered(first)
        by_sp.setdefault(sk, []).append((r, first, maj))

    for sprint_key in sorted(by_sp.keys(), key=lambda x: (x == "未分配", x)):
        groups: dict[int, list[dict[str, Any]]] = {}
        samples: dict[int, list[str]] = {}
        for r, _fl, maj in by_sp[sprint_key]:
            groups.setdefault(maj, []).append(r)
            samples.setdefault(maj, []).append((r.get("fields") or {}).get("任务") or "")

        req_items: list[dict[str, Any]] = []
        for maj in sorted(groups.keys()):
            recs_g = groups[maj]
            name = _req_name_for_major(sprint_key, maj, samples.get(maj) or [])
            arch: list[dict[str, Any]] = []
            biz: list[dict[str, Any]] = []
            for r in recs_g:
                task = (r.get("fields") or {}).get("任务") or ""
                if _bucket(str(task)) == "架构与工程":
                    arch.append(r)
                else:
                    biz.append(r)
            req_items.append(
                {
                    "需求项": name,
                    "major_key": maj,
                    "架构与工程": arch,
                    "业务逻辑": biz,
                    "counts": {"架构与工程": len(arch), "业务逻辑": len(biz), "合计": len(arch) + len(biz)},
                }
            )

        total = sum(len(groups[m]) for m in groups)
        out_tree.append(
            {
                "Sprint": _sprint_display(sprint_key),
                "Sprint_raw": sprint_key,
                "需求项": req_items,
                "record_count": total,
            }
        )

    flat_ids = []
    for node in out_tree:
        for req in node["需求项"]:
            for r in req["架构与工程"] + req["业务逻辑"]:
                flat_ids.append(r.get("record_id") or r.get("id"))

    src_ids = [r.get("record_id") or r.get("id") for r in records]
    missing = set(src_ids) - set(flat_ids)
    extra = set(flat_ids) - set(src_ids)

    return {
        "format": "hierarchical_sprint_req_arch_biz_v1",
        "source_meta": {
            "exported_at": data.get("exported_at"),
            "wiki_url": data.get("wiki_url"),
            "table_id": data.get("table_id"),
            "fields": data.get("fields"),
        },
        "classification_notes": {
            "需求项": (
                "分组：任务首行主编号 M（M.N 或 M. 或 M 空格）；无编号按关键词聚类（100–103）。"
                "展示名：从组内任务首行提炼主题。"
            ),
            "架构与工程_业务逻辑": "任务首行命中架构/infra 关键词→架构与工程；视频/分镜/战报/留存等→业务逻辑；其余默认业务逻辑。",
        },
        "tree": out_tree,
        "validation": {
            "source_record_count": len(records),
            "tree_record_count": len(flat_ids),
            "missing_record_ids": list(missing),
            "extra_record_ids": list(extra),
            "ok": len(missing) == 0 and len(extra) == 0 and len(flat_ids) == len(records),
        },
    }


def retitle_hierarchical_doc(doc: dict[str, Any]) -> dict[str, Any]:
    """对已生成的 hierarchical JSON 仅刷新「需求项」展示名，不改分组与记录内容。"""
    for sprint_node in doc.get("tree") or []:
        for req in sprint_node.get("需求项") or []:
            samples: list[str] = []
            for bucket in ("架构与工程", "业务逻辑"):
                for r in req.get(bucket) or []:
                    t = (r.get("fields") or {}).get("任务") or ""
                    if isinstance(t, str) and t.strip():
                        samples.append(t)
            maj = int(req.get("major_key") or 0)
            sk = sprint_node.get("Sprint_raw") or sprint_node.get("Sprint") or ""
            req["需求项"] = _req_name_for_major(sk, maj, samples)
    notes = doc.get("classification_notes")
    if isinstance(notes, dict):
        notes["需求项"] = (
            "分组：任务首行主编号 M（M.N 或 M. 或 M 空格）；无编号按关键词聚类（100–103）。"
            "展示名：从组内任务首行提炼主题。"
        )
    return doc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="", help="原始 bitable JSON 路径（扁平 records）")
    ap.add_argument("--dst", default="", help="输出路径（默认同目录 _hierarchical.json 或覆盖 --retitle 目标）")
    ap.add_argument(
        "--retitle-hierarchical",
        default="",
        metavar="PATH",
        help="已有 hierarchical（如 树形结构.json），仅按任务重算「需求项」名称后写出",
    )
    args = ap.parse_args()
    if args.retitle_hierarchical.strip():
        src = Path(args.retitle_hierarchical)
        if not src.is_file():
            print(f"文件不存在: {src}", file=sys.stderr)
            return 2
        doc = json.loads(src.read_text(encoding="utf-8"))
        out = retitle_hierarchical_doc(doc)
        dst = Path(args.dst) if args.dst.strip() else src
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(str(dst))
        return 0
    if not args.src.strip():
        print("请指定 --src（扁平 bitable JSON）或 --retitle-hierarchical（树形 JSON）", file=sys.stderr)
        return 2
    src = Path(args.src)
    if not src.is_file():
        print(f"文件不存在: {src}", file=sys.stderr)
        return 2
    data = json.loads(src.read_text(encoding="utf-8"))
    out = rebuild(data)
    dst = Path(args.dst) if args.dst.strip() else src.with_name(src.stem + "_hierarchical.json")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(dst))
    v = out["validation"]
    print(
        f"records: {v['source_record_count']} -> tree: {v['tree_record_count']} ok={v['ok']}",
        file=sys.stderr,
    )
    if not v["ok"]:
        print(f"missing: {len(v['missing_record_ids'])} extra: {len(v['extra_record_ids'])}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
