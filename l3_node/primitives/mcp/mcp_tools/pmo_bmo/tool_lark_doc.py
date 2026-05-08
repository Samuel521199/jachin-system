"""
PMO Lark 文档同步 — mcp:atom_pmo_lark_doc

从配置的 Wiki 种子链接拉取 PRD、需求评审、排期等多维表/文档（与 BI 项目上下文同源实现），
默认同步 **K11 四张核心表**（需求主线、需求细分、项目进度、美术/设计任务），并按日落盘到
`docs/pmo_bmo_plugin/project_progress_daily/YYYY-MM-DD/`；亦支持仅浏览用的节点列表与单节点正文读取。

核心动作：
- operation=sync：全量同步（内部调用 sync_bi_project_context，配置键与 atom_bi_project_context 兼容）
- operation=list_nodes：列出某父节点下子节点（需 space_id + 可选 parent_node_token）
- operation=read_doc：读取单个 node_token 对应的节点信息与 docx 正文（若类型支持）
- operation=export_pmo_tables：按固定 **4** 张 K11 多维表拉取 JSON→~/.jachin/client_volumes/PMO/raw/{date}_{slug}.json、MD→docs/pmo_bmo_plugin/raw/{slug}.md（固定名覆盖）、写入 pmo.duckdb

配置: config/mcps/atom_pmo_lark_doc/config.yaml

同步相关键：
- use_k11_default_tables：默认 true；为 true 且未配置 wiki_urls 时使用内置四条 K11 Wiki 链接
- daily_snapshot：默认 true；为 true 时输出目录为 project_progress_daily/{snapshot_date}
- snapshot_date：可选，YYYY-MM-DD，默认当天（用于补跑历史日）
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

# pmo_bmo → mcp_tools → mcp → primitives → l3_node → 仓库根
_root = Path(__file__).resolve().parents[4]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_REL = "docs/pmo_bmo_plugin/synced"

# K11：需求主线、需求细分、项目进度、美术/设计（与 BI 默认种子一致，便于 PMO 开箱）
PMO_K11_DEFAULT_WIKI_URLS: list[str] = [
    "https://ssgkm409t6q5.sg.larksuite.com/wiki/ZItbw4omRi6Sbsksb6jlwYq8gYq?table=ldxeuHgiN5L2gXBH",
    "https://ssgkm409t6q5.sg.larksuite.com/wiki/ZItbw4omRi6Sbsksb6jlwYq8gYq?table=tblNdv7DIlycuqxp&view=vew8TxMcSh",
    "https://ssgkm409t6q5.sg.larksuite.com/wiki/B19Iww8tBiXZqfky1hhlIZ6kg0P?table=tblfK9gk6vTQpJtB&view=vewpI8lyYw",
    "https://ssgkm409t6q5.sg.larksuite.com/wiki/DiSnwVB1OiDvPWkk0W9lzx6AgLd?table=tblDw87UlhddFIoY&view=vew5taB9H1",
]

PROJECT_PROGRESS_DAILY_ROOT = "docs/pmo_bmo_plugin/project_progress_daily"

# 与种子 URL 片段对应，用于生成 00_K11_TABLES_INDEX.md
PMO_K11_TABLE_INDEX: list[tuple[str, str, str]] = [
    ("ZItbw4omRi6Sbsksb6jlwYq8gYq", "K11 需求主线（大表）", "与 Wiki table=ldxeu… 同源；仪表盘/大需求对齐主线锚点"),
    ("ZItbw4omRi6Sbsksb6jlwYq8gYq", "K11 需求池细分", "需求描述、Sprint、交付件、优先级、发起人/责任人、需求状态、开发状态、执行人等"),
    ("B19Iww8tBiXZqfky1hhlIZ6kg0P", "K11 项目进度", "任务树、优先级、Sprint、任务执行人、开始/交付日期、预计人天、状态等"),
    ("DiSnwVB1OiDvPWkk0W9lzx6AgLd", "美术/设计任务", "任务、需求人、优先级、Sprint、设计责任人、起止日期、预计人天等"),
]


def _parse_bool(val: Any, default: bool = True) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    if s in ("0", "false", "no", "off"):
        return False
    if s in ("1", "true", "yes", "on"):
        return True
    return default


def _resolve_snapshot_date(cfg: dict[str, Any]) -> str:
    raw = (cfg.get("snapshot_date") or "").strip()
    if raw:
        m = re.match(r"^(\d{4}-\d{2}-\d{2})", raw)
        if m:
            return m.group(1)
        try:
            return datetime.strptime(raw[:10], "%Y-%m-%d").date().isoformat()
        except ValueError:
            logger.warning("[atom_pmo_lark_doc] snapshot_date 无效 %s，使用今天", raw)
    return date.today().isoformat()


def _apply_pmo_sync_defaults(cfg: dict[str, Any]) -> dict[str, Any]:
    """合并 PMO 默认种子链接与按日落盘目录。"""
    sync_cfg = dict(cfg)
    use_k11 = _parse_bool(sync_cfg.get("use_k11_default_tables"), True)
    urls = sync_cfg.get("wiki_urls")
    if use_k11 and (not urls or not isinstance(urls, list) or len(urls) == 0):
        sync_cfg["wiki_urls"] = list(PMO_K11_DEFAULT_WIKI_URLS)

    daily = _parse_bool(sync_cfg.get("daily_snapshot"), True)
    if daily:
        snap = _resolve_snapshot_date(sync_cfg)
        sync_cfg["output_dir_relative"] = f"{PROJECT_PROGRESS_DAILY_ROOT}/{snap}"
    else:
        sync_cfg.setdefault("output_dir_relative", DEFAULT_OUTPUT_REL)
    return sync_cfg


def _write_k11_tables_index(out_dir: Path, wiki_urls: list[str], snapshot_label: str) -> Path | None:
    """在当日目录写入 00_K11_TABLES_INDEX.md，说明三张表含义与种子链接。"""
    try:
        lines = [
            f"# K11 项目进度快照说明",
            "",
            f"- **快照日期**: {snapshot_label}",
            f"- **生成时间**: {datetime.now().isoformat(timespec='seconds')}",
            "",
            "## 多维表与字段概览",
            "",
        ]
        for url in wiki_urls:
            label = "（未命名表）"
            desc = ""
            for token, lab, dsc in PMO_K11_TABLE_INDEX:
                if token in url:
                    label = lab
                    desc = dsc
                    break
            lines.append(f"### {label}")
            lines.append("")
            lines.append(f"- **种子链接**: `{url}`")
            if desc:
                lines.append(f"- **主要字段/用途**: {desc}")
            lines.append("")
        lines.append("---\n\n同步产物还包括同目录下带 `## 同步元数据` 的 `.md` 及 `00_SYNC_MANIFEST.json`。")
        path = out_dir / "00_K11_TABLES_INDEX.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        return path
    except Exception as e:
        logger.warning("[atom_pmo_lark_doc] 写入 K11 索引失败: %s", e)
        return None


def _load_pmo_config(runtime: dict[str, Any] | None, project_root: Path) -> dict[str, Any]:
    from l3_node.jachin_config import load_mcp_config

    base = load_mcp_config("atom_pmo_lark_doc", project_root=project_root)
    merged = dict(base)
    if runtime and isinstance(runtime, dict):
        for k, v in runtime.items():
            if v is not None:
                merged[k] = v
    return merged


def _lark_creds_from_cfg_and_env(cfg: dict[str, Any]) -> tuple[str, str]:
    aid = (cfg.get("app_id") or os.environ.get("LARK_APP_ID") or os.environ.get("FEISHU_APP_ID") or "").strip()
    sec = (cfg.get("app_secret") or os.environ.get("LARK_APP_SECRET") or os.environ.get("FEISHU_APP_SECRET") or "").strip()
    return aid, sec


def _lark_creds_placeholder(aid: str, sec: str) -> bool:
    if not aid or not sec:
        return True
    if aid.startswith("${") or (isinstance(sec, str) and sec.startswith("${")):
        return True
    return False


def _merge_lark_from_pmo_skill_yaml_if_needed(cfg: dict[str, Any], project_root: Path) -> dict[str, Any]:
    """MCP 未配置或为 ${VAR} 占位时，用 com.jachin.pmo.bmo 的 pmo_bmo.yaml 中 lark 块补全。"""
    aid, sec = _lark_creds_from_cfg_and_env(cfg)
    if not _lark_creds_placeholder(aid, sec):
        return cfg

    import yaml

    candidates = [
        project_root / "config" / "skills" / "com.jachin.pmo.bmo" / "pmo_bmo.yaml",
        Path.home() / ".jachin" / "config" / "skills" / "com.jachin.pmo.bmo" / "pmo_bmo.yaml",
    ]
    skill: dict[str, Any] = {}
    for p in candidates:
        if p.is_file():
            try:
                skill = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                break
            except Exception as e:
                logger.warning("[atom_pmo_lark_doc] 读取 PMO 技能 YAML 失败 %s: %s", p, e)
    lk = skill.get("lark")
    if not isinstance(lk, dict):
        return cfg

    out = dict(cfg)
    for k in ("app_id", "app_secret"):
        if k in lk and lk[k] is not None and str(lk[k]).strip() != "":
            cur = str(out.get(k) or "").strip()
            if not cur or cur.startswith("${"):
                out[k] = lk[k]
    if out.get("lark_use_feishu") is None and "lark_use_feishu" in lk:
        out["lark_use_feishu"] = lk["lark_use_feishu"]
    return out


def _lark_get(api_base: str, token: str, path: str, params: dict | None = None) -> dict[str, Any]:
    import requests

    url = f"{api_base.rstrip('/')}{path}" if path.startswith("/") else f"{api_base}/{path}"
    r = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params=params or {},
        timeout=60,
    )
    try:
        return r.json()
    except Exception:
        return {"code": -1, "msg": r.text[:500]}


def fetch_wiki_nodes(
    *,
    space_id: str,
    parent_node_token: str | None,
    tenant_token: str,
    api_base: str,
) -> dict[str, Any]:
    """列出知识空间下某父节点的子节点（分页拉全）。"""
    import requests

    items: list[dict] = []
    page_token = None
    while True:
        path = f"/wiki/v2/spaces/{space_id}/nodes"
        params: dict[str, Any] = {"page_size": 50}
        if parent_node_token:
            params["parent_node_token"] = parent_node_token
        if page_token:
            params["page_token"] = page_token
        url = f"{api_base.rstrip('/')}{path}"
        r = requests.get(
            url,
            headers={"Authorization": f"Bearer {tenant_token}"},
            params=params,
            timeout=60,
        )
        data = r.json()
        if data.get("code") != 0:
            return {"status": "error", "error": data.get("msg", str(data)), "items": []}
        chunk = data.get("data", {}).get("items", [])
        items.extend(chunk)
        page_token = data.get("data", {}).get("page_token")
        if not data.get("data", {}).get("has_more") or not chunk:
            break
    return {"status": "success", "items": items, "count": len(items)}


def read_lark_doc_content(
    *,
    node_token: str,
    tenant_token: str,
    api_base: str,
) -> dict[str, Any]:
    """读取 Wiki 节点元信息；若为 docx 则拉 raw_content。"""
    g = _lark_get(api_base, tenant_token, "/wiki/v2/spaces/get_node", {"token": node_token})
    if g.get("code") != 0:
        return {"status": "error", "error": g.get("msg", str(g)), "node": None}
    raw_d = g.get("data") or {}
    node = raw_d.get("node") if isinstance(raw_d.get("node"), dict) else raw_d
    if not isinstance(node, dict):
        return {"status": "error", "error": "invalid node payload", "node": None}
    obj_type = (node.get("obj_type") or "").lower()
    obj_token = node.get("obj_token") or ""
    out: dict[str, Any] = {
        "status": "success",
        "title": node.get("title"),
        "node_token": node_token,
        "obj_type": obj_type,
        "obj_token": obj_token,
        "space_id": node.get("space_id"),
        "raw_node": node,
    }
    if obj_type == "docx" and obj_token:
        dc = _lark_get(api_base, tenant_token, f"/docx/v1/documents/{obj_token}/raw_content", {})
        if dc.get("code") == 0:
            content = dc.get("data", {}).get("content")
            if content is None:
                content = dc.get("data", {}).get("text", "")
            out["docx_content"] = str(content) if content else ""
        else:
            out["docx_error"] = dc.get("msg", str(dc))
    return out


def run_pmo_lark_doc(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    MCP 统一入口。

    operation:
      - sync（默认）：调用 sync_bi_project_context，配置来自 atom_pmo_lark_doc
      - list_nodes：需要 space_id；可选 parent_node_token
      - read_doc：需要 node_token
    """
    from l3_node.paths import get_app_root
    from l3_node.channels.lark.client import get_lark_api_base, get_tenant_access_token
    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import sync_bi_project_context

    args = dict(arguments or {})
    op = (args.pop("operation", None) or "sync").strip().lower()
    root = get_app_root()
    cfg = _load_pmo_config(args, root)
    cfg = _merge_lark_from_pmo_skill_yaml_if_needed(cfg, root)

    app_id = (cfg.get("app_id") or os.environ.get("LARK_APP_ID") or os.environ.get("FEISHU_APP_ID") or "").strip()
    app_secret = (cfg.get("app_secret") or os.environ.get("LARK_APP_SECRET") or os.environ.get("FEISHU_APP_SECRET") or "").strip()
    if cfg.get("lark_use_feishu") in (True, "true", "1", "yes"):
        os.environ["LARK_USE_FEISHU"] = "1"
    elif cfg.get("lark_use_feishu") in (False, "false", "0", "no"):
        os.environ.pop("LARK_USE_FEISHU", None)

    if op == "export_pmo_tables":
        from l3_node.primitives.mcp.mcp_tools.pmo_bmo.tool_pmo_bitable_export import run_pmo_bitable_export

        return run_pmo_bitable_export(dict(cfg), root)

    if op == "sync":
        sync_cfg = _apply_pmo_sync_defaults(dict(cfg))
        result = sync_bi_project_context(config=sync_cfg, project_root=root)
        if (result.get("status") or "").lower() == "success" and _parse_bool(sync_cfg.get("daily_snapshot"), True):
            od = result.get("output_dir")
            if od:
                snap = _resolve_snapshot_date(sync_cfg)
                urls = sync_cfg.get("wiki_urls")
                if not isinstance(urls, list):
                    urls = []
                idx = _write_k11_tables_index(Path(od), urls, snap)
                if idx and isinstance(result.get("files"), list):
                    try:
                        rel = str(idx.resolve().relative_to(root))
                        if rel not in result["files"]:
                            result["files"].append(rel)
                    except ValueError:
                        pass
                result["k11_index_md"] = str(idx.relative_to(root)) if idx else None
        return result

    if not app_id or not app_secret or app_id.startswith("${"):
        return {"status": "error", "error": "未配置 app_id/app_secret（atom_pmo_lark_doc 或环境变量）"}

    api_base = get_lark_api_base()
    try:
        token = get_tenant_access_token(app_id=app_id, app_secret=app_secret, api_base=api_base)
    except Exception as e:
        return {"status": "error", "error": f"token: {e}"}

    if op == "list_nodes":
        space_id = (args.get("space_id") or cfg.get("space_id") or "").strip()
        parent = (args.get("parent_node_token") or cfg.get("parent_node_token") or "").strip() or None
        if not space_id:
            return {"status": "error", "error": "list_nodes 需要 space_id（参数或配置）"}
        r = fetch_wiki_nodes(space_id=space_id, parent_node_token=parent, tenant_token=token, api_base=api_base)
        return r

    if op == "read_doc":
        node_token = (args.get("node_token") or "").strip()
        if not node_token:
            return {"status": "error", "error": "read_doc 需要 node_token"}
        return read_lark_doc_content(node_token=node_token, tenant_token=token, api_base=api_base)

    return {"status": "error", "error": f"未知 operation: {op}（支持 sync | export_pmo_tables | list_nodes | read_doc）"}


def atom_pmo_lark_doc(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """兼容直接传入整包 config 的调用方式。"""
    return run_pmo_lark_doc(config)
