#!/usr/bin/env python3
"""
对 hierarchical_sprint_req_arch_biz_v1 树形 JSON 做需求项层级均衡：

- 过大桶（如「其他」99 条、或单组任务数超过阈值）：按任务首行语义拆成多组；
- 过细桶（同一 Sprint 下连续多条「仅 1 条任务」的编号需求项 2–11）：按固定主题合并为少量父组；
- 含 1.1 / 1.2 / 1.Core / 1.3 等多子标题的编号组：按子编号拆成多条需求项。

记录不增删，仅重组「需求项」分组与 架构/业务 列表；仍用 rebuild 脚本中的 _bucket 划分架构与工程。

用法:
  python scripts/rebalance_tree_json.py --path D:\\zzz\\树形结构.json [--dry-run]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _load_rebuild():
    p = Path(__file__).resolve().parent / "rebuild_bitable_json_hierarchy.py"
    spec = importlib.util.spec_from_file_location("rbh", p)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rbh = _load_rebuild()
_first_line = rbh._first_line
_strip = rbh._strip_task_prefixes
_trim_cnt = rbh._trim_item_count_suffix
_bucket = rbh._bucket

# 单组超过该数量则尝试拆分（「其他」类更积极）
SPLIT_THRESHOLD = 14
SPLIT_THRESHOLD_OTHER = 12

# 过细合并： majors 2–11 且每组仅 1 条任务时，按包合并
MERGE_PACKS: list[tuple[list[int], str]] = [
    ([2, 3, 4], "L3 编排 · 能力目录 · HR 飞书"),
    ([5, 6, 7, 8], "Skill · MCP · 插件与仓库"),
    ([9, 10, 11], "配置 · 脚本 · 文档"),
]

# 语义桶（过大「其他」用），顺序优先匹配
_SEMANTIC_RULES: list[tuple[str, str]] = [
    (r"角色|权限|租户|RBAC|分配|子账号|配额|观看|鉴权", "角色与权限"),
    (r"知识库|记忆|向量|上下文", "知识库与记忆"),
    (r"Dashboard|仪表盘|页面|前端|组件|界面|UI", "前端与界面"),
    (r"脚本|自动化|启动|CLI", "脚本与自动化"),
    (r"Admin|Gateway|Backend|gRPC|数据库|表|中间件|API\s*测试", "后端与数据"),
    (r"Auth|账号|登录|验证", "账号与认证"),
    (r"测试|单元测试|集成|用例", "测试与质量"),
    (r"部署|文档|README|运维", "部署与文档"),
]


def _task_line(rec: dict[str, Any]) -> str:
    t = (rec.get("fields") or {}).get("任务") or ""
    return t if isinstance(t, str) else ""


def _make_req(title: str, recs: list[dict[str, Any]], major_key: int) -> dict[str, Any]:
    arch: list[dict[str, Any]] = []
    biz: list[dict[str, Any]] = []
    for r in recs:
        t = _task_line(r)
        if _bucket(str(t)) == "架构与工程":
            arch.append(r)
        else:
            biz.append(r)
    n = len(recs)
    return {
        "需求项": title,
        "major_key": major_key,
        "架构与工程": arch,
        "业务逻辑": biz,
        "counts": {"架构与工程": len(arch), "业务逻辑": len(biz), "合计": n},
    }


def _short_title_from_line(fl: str, max_len: int = 46) -> str:
    fl0 = fl.split("\n")[0] if fl else ""
    t = _trim_cnt(_strip(fl0))
    if len(t) > max_len:
        return t[: max_len - 1] + "…"
    return t


def _subnumber_key(first_line: str) -> str | None:
    s = first_line.strip()
    m = re.match(r"^(\d+\.\d+)\s+", s)
    if m:
        return m.group(1)
    m = re.match(r"^(\d+)\.\s*Core", s, re.I)
    if m:
        return f"{m.group(1)}.Core"
    return None


def _distinct_sub_keys(recs: list[dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for r in recs:
        k = _subnumber_key(_first_line(_task_line(r)))
        if k:
            keys.add(k)
    return keys


def _split_by_subnumber(recs: list[dict[str, Any]], base_major: int) -> list[dict[str, Any]] | None:
    buckets: dict[str, list[dict[str, Any]]] = {}
    unkey: list[dict[str, Any]] = []
    for r in recs:
        fl = _first_line(_task_line(r))
        k = _subnumber_key(fl)
        if k:
            buckets.setdefault(k, []).append(r)
        else:
            unkey.append(r)
    if len(buckets) < 2:
        return None
    out: list[dict[str, Any]] = []
    sub_i = 0
    for k in sorted(buckets.keys(), key=lambda x: (len(x), x)):
        sub_recs = buckets[k]
        fl = _first_line(_task_line(sub_recs[0]))
        title = _short_title_from_line(fl)
        mk = base_major * 100 + sub_i + 11  # e.g. 1 -> 111,112,...
        out.append(_make_req(title, sub_recs, mk))
        sub_i += 1
    if unkey:
        mk = base_major * 100 + sub_i + 11
        title = _short_title_from_line(_first_line(_task_line(unkey[0])))
        out.append(_make_req(title or "同组未子编号任务", unkey, mk))
    return out


def _semantic_bucket(line: str) -> str:
    for pat, name in _SEMANTIC_RULES:
        if re.search(pat, line, re.I):
            return name
    return "其他（补充）"


def _split_oversized(recs: list[dict[str, Any]], _title_hint: str) -> list[tuple[str, list[dict[str, Any]]]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for r in recs:
        fl = _first_line(_task_line(r))
        b = _semantic_bucket(fl)
        buckets.setdefault(b, []).append(r)
    parts: list[tuple[str, list[dict[str, Any]]]] = []
    for name, group in sorted(buckets.items(), key=lambda x: (-len(x[1]), x[0])):
        group = sorted(group, key=lambda x: _first_line(_task_line(x)))
        if len(group) <= SPLIT_THRESHOLD:
            parts.append((name, group))
            continue
        for j in range(0, len(group), SPLIT_THRESHOLD):
            chunk = group[j : j + SPLIT_THRESHOLD]
            # 块序号由外层 ·s{n} 统一标注，此处不再加 ·1 ·2 避免与 ·s 重复
            parts.append((name, chunk))
    return parts


def _should_try_subnumber_split(recs: list[dict[str, Any]]) -> bool:
    if len(recs) < 2:
        return False
    ks = _distinct_sub_keys(recs)
    return len(ks) >= 2


def _rebalance_req_list(reqs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """单 Sprint 内需求项列表均衡。"""
    phase1: list[dict[str, Any]] = []
    split_serial = [0]  # 同一 Sprint 内语义拆分标题全局序号，避免「其他（补充）·1」重复

    for req in reqs:
        recs = (req.get("架构与工程") or []) + (req.get("业务逻辑") or [])
        n = len(recs)
        maj = int(req.get("major_key") or 0)
        title = req.get("需求项") or ""

        # A) 按 1.1 / 1.Core / 1.2 拆编号组（避免「需求项1」下堆四种不同子项）
        if _should_try_subnumber_split(recs):
            spl = _split_by_subnumber(recs, maj)
            if spl and len(spl) > 1:
                phase1.extend(spl)
                continue

        # B) 「其他」或过大的桶按语义拆
        thr = SPLIT_THRESHOLD_OTHER if (maj == 103 or "其他" in title) else SPLIT_THRESHOLD
        if n > thr:
            parts = _split_oversized(recs, title)
            for sub_title, sub_recs in parts:
                split_serial[0] += 1
                uniq_title = f"{sub_title}·s{split_serial[0]}"
                mk = 10300 + split_serial[0] if maj == 103 else maj * 1000 + split_serial[0]
                phase1.append(_make_req(uniq_title, sub_recs, mk))
            continue

        phase1.append(req)

    # C) 合并过细：连续单任务且 major 为 2–11 且命中整包
    merged = _merge_singleton_packs(phase1)
    return merged


def _merge_singleton_packs(reqs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    i = 0
    while i < len(reqs):
        matched = False
        for majors, label in MERGE_PACKS:
            if i + len(majors) > len(reqs):
                continue
            chunk = reqs[i : i + len(majors)]
            if not all(
                len(c.get("架构与工程") or []) + len(c.get("业务逻辑") or []) == 1
                for c in chunk
            ):
                continue
            if [c.get("major_key") for c in chunk] != majors:
                continue
            recs: list[dict[str, Any]] = []
            for c in chunk:
                recs.extend((c.get("架构与工程") or []) + (c.get("业务逻辑") or []))
            out.append(_make_req(label, recs, majors[0]))
            i += len(majors)
            matched = True
            break
        if not matched:
            out.append(reqs[i])
            i += 1
    return out


def rebalance_doc(doc: dict[str, Any]) -> dict[str, Any]:
    tree = doc.get("tree") or []
    total_before = 0
    for sp in tree:
        for req in sp.get("需求项") or []:
            total_before += len((req.get("架构与工程") or []) + (req.get("业务逻辑") or []))

    new_tree: list[dict[str, Any]] = []
    for sp in tree:
        reqs = sp.get("需求项") or []
        new_reqs = _rebalance_req_list(list(reqs))
        total_sp = sum(
            len((r.get("架构与工程") or []) + (r.get("业务逻辑") or [])) for r in new_reqs
        )
        node = dict(sp)
        node["需求项"] = new_reqs
        node["record_count"] = total_sp
        new_tree.append(node)

    total_after = 0
    all_ids: list[str] = []
    for sp in new_tree:
        for req in sp.get("需求项") or []:
            for r in (req.get("架构与工程") or []) + (req.get("业务逻辑") or []):
                total_after += 1
                rid = r.get("record_id") or r.get("id")
                if rid:
                    all_ids.append(rid)

    doc = dict(doc)
    doc["tree"] = new_tree
    v = dict(doc.get("validation") or {})
    v["tree_record_count"] = total_after
    v["ok"] = total_before == total_after
    v["rebalanced"] = True
    doc["validation"] = v
    cn = dict(doc.get("classification_notes") or {})
    cn["需求项"] = (
        cn.get("需求项", "")
        + " [rebalance: 过大语义拆分、过细编号组合并、多子编号拆分]"
    ).strip()
    doc["classification_notes"] = cn
    return doc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", required=True, help="树形结构.json 路径")
    ap.add_argument("--dry-run", action="store_true", help="只打印统计，不写文件")
    args = ap.parse_args()
    path = Path(args.path)
    if not path.is_file():
        print(f"文件不存在: {path}", file=sys.stderr)
        return 2
    raw = path.read_text(encoding="utf-8")
    doc = json.loads(raw)
    before = sum(
        len((req.get("架构与工程") or []) + (req.get("业务逻辑") or []))
        for sp in doc.get("tree", [])
        for req in sp.get("需求项") or []
    )
    out = rebalance_doc(doc)
    after = sum(
        len((req.get("架构与工程") or []) + (req.get("业务逻辑") or []))
        for sp in out.get("tree", [])
        for req in sp.get("需求项") or []
    )
    nreq = sum(len(sp.get("需求项") or []) for sp in out.get("tree", []))
    print(f"records: {before} -> {after}, 需求项节点数: {nreq}, ok={before == after}", file=sys.stderr)
    if not out["validation"].get("ok"):
        print("错误: 记录数不一致", file=sys.stderr)
        return 1
    if args.dry_run:
        return 0
    bak = path.with_suffix(path.suffix + ".bak")
    bak.write_text(raw, encoding="utf-8")
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(path))
    print(f"备份: {bak}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
