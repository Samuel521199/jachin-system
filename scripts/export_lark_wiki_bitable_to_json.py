"""
从飞书/Lark 知识库 Wiki 链接拉取嵌入的多维表（Bitable）子表记录，写入 JSON。

依赖：与项目一致（requests）；凭证：LARK_APP_ID + LARK_APP_SECRET（.env 或 ~/.jachin/config/im_channels.yaml）。
国际版 Lark（larksuite.com）勿设 LARK_USE_FEISHU=1。

用法:
  python scripts/export_lark_wiki_bitable_to_json.py ^
    --url "https://xxx.sg.larksuite.com/wiki/NODE?table=tblXXX&view=vewYYY" ^
    --out-dir "D:\\zzz"

Wiki 节点须为「多维表」类型（get_node 返回 obj_type=bitable）；table= / view= 来自链接 query。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# 项目根
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from l3_node.channels.lark.client import get_lark_api_base, get_tenant_access_token  # noqa: E402
from l3_node.mcp_tools.bi.tool_bi_project_context import parse_wiki_url, sanitize_wiki_url  # noqa: E402
from l3_node.primitives.mcp.mcp_tools.pmo_bmo.tool_pmo_bitable_export import (  # noqa: E402
    _bitable_list_fields,
    _bitable_list_records,
    _lark_get,
)


def main() -> int:
    p = argparse.ArgumentParser(description="Wiki 多维表 → JSON 文件")
    p.add_argument(
        "--url",
        required=True,
        help="含 wiki 节点与 table=、可选 view= 的完整链接",
    )
    p.add_argument(
        "--out-dir",
        default=r"D:\zzz",
        help=r"输出目录（默认 D:\zzz）",
    )
    p.add_argument(
        "--max-records",
        type=int,
        default=8000,
        help="最多拉取记录条数（分页）",
    )
    args = p.parse_args()

    url = sanitize_wiki_url((args.url or "").strip())
    parsed = parse_wiki_url(url)
    node_token = (parsed.get("node_token") or "").strip()
    table_id = (parsed.get("table_id") or "").strip()
    view_id = (parsed.get("view_id") or "").strip() or None
    if not node_token:
        print("错误：无法从 URL 解析 wiki 节点 token（path 中 /wiki/<token>）", file=sys.stderr)
        return 2
    if not table_id:
        print("错误：URL 中缺少 table= 子表 ID（tbl...）", file=sys.stderr)
        return 2

    api_base = get_lark_api_base().rstrip("/")
    token = get_tenant_access_token()

    g = _lark_get(api_base, token, "/wiki/v2/spaces/get_node", {"token": node_token})
    if g.get("code") != 0:
        print(f"错误：get_node 失败: {g.get('msg', g)}", file=sys.stderr)
        return 3

    raw_d = g.get("data") or {}
    node = raw_d.get("node") if isinstance(raw_d.get("node"), dict) else raw_d
    if not isinstance(node, dict):
        print("错误：get_node 返回无效 node", file=sys.stderr)
        return 3

    obj_type = (node.get("obj_type") or "").lower()
    app_token = (node.get("obj_token") or "").strip()
    if obj_type != "bitable" or not app_token:
        print(
            f"错误：该 Wiki 节点不是多维表或缺少 obj_token（obj_type={obj_type!r}）。"
            f"请确认链接打开的是「多维表」节点。",
            file=sys.stderr,
        )
        return 4

    fields, ferr = _bitable_list_fields(api_base, token, app_token, table_id)
    if ferr:
        print(f"警告：列字段拉取失败（仍继续拉记录）: {ferr}", file=sys.stderr)

    records, rerr = _bitable_list_records(
        api_base, token, app_token, table_id, max_records=args.max_records, view_id=view_id
    )
    if rerr:
        print(f"错误：拉取记录失败: {rerr}", file=sys.stderr)
        return 5

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_table = "".join(c if c.isalnum() or c in "._-" else "_" for c in table_id)[:80]
    out_path = out_dir / f"bitable_{safe_table}_{ts}.json"

    payload = {
        "exported_at": ts,
        "wiki_url": url,
        "node_token": node_token,
        "app_token": app_token,
        "table_id": table_id,
        "view_id": view_id,
        "field_count": len(fields),
        "record_count": len(records),
        "fields": fields,
        "records": records,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
