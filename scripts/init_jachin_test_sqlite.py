#!/usr/bin/env python3
"""
在 ~/.jachin/workspace/test_db.sqlite 创建压测用 inventory 表及 3 条样例行。
stdio SQLite 使用 npm 包 ``mcp-sqlite``（``npx -y mcp-sqlite <db路径>``）。
勿使用 ``@modelcontextprotocol/server-sqlite``（npm 404）。

可选：
  --purge-memories         清理疑似压测相关的本地记忆条目
  --no-sync-local-config   仅重置数据库；默认会写入/迁移 mcp_servers.json 与 nexus merge_sqlite 项
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import time
from pathlib import Path


def _jachin_root() -> Path:
    return Path(os.environ.get("JACHIN_HOME", Path.home() / ".jachin")).expanduser().resolve()


_WORKSPACE_SQLITE_MCP_SERVER: dict = {
    "id": "official-sqlite-npx",
    "name": "MCP SQLite（npm mcp-sqlite，工作区 test_db）",
    "command": "npx",
    "args": ["-y", "mcp-sqlite", "__JACHIN_WORKSPACE__/test_db.sqlite"],
}


def _migrate_broken_npm_sqlite_entry(s: dict[str, object]) -> tuple[dict[str, object], bool]:
    """@modelcontextprotocol/server-sqlite → mcp-sqlite（前者 npm 404）。"""
    args = s.get("args") if isinstance(s.get("args"), list) else []
    flat = " ".join(str(a) for a in args)
    if "@modelcontextprotocol/server-sqlite" not in flat:
        return s, False
    db = "__JACHIN_WORKSPACE__/test_db.sqlite"
    for i, a in enumerate(args):
        if str(a) == "--db-path" and i + 1 < len(args):
            db = str(args[i + 1])
            break
    sid = str(s.get("id") or "official-sqlite-npx").strip() or "official-sqlite-npx"
    return {
        "id": sid,
        "name": s.get("name") or "MCP SQLite（npm mcp-sqlite，工作区 test_db）",
        "command": "npx",
        "args": ["-y", "mcp-sqlite", db],
    }, True


def _servers_include_mcp_sqlite_package(servers: list[object]) -> bool:
    for s in servers:
        if not isinstance(s, dict):
            continue
        args = s.get("args") if isinstance(s.get("args"), list) else []
        for a in args:
            if str(a).strip().lower() == "mcp-sqlite":
                return True
    return False


def sync_local_mcp_and_nexus_for_sqlite_read(root: Path | None = None) -> dict[str, str]:
    """
    开发兜底：mcp_servers.json 使用 npm「mcp-sqlite」；若仍存在已 404 的旧包名则自动迁移。
    并打开 nexus agent.merge_sqlite_read_into_tool_pool。
    """
    root = root or _jachin_root()
    root.mkdir(parents=True, exist_ok=True)
    out: dict[str, str] = {}

    mcp_path = root / "mcp_servers.json"
    try:
        data: dict = {}
        if mcp_path.exists():
            raw = json.loads(mcp_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        servers = data.get("mcp_servers")
        if not isinstance(servers, list):
            servers = []
        new_servers: list[dict] = []
        migrated = False
        for entry in servers:
            if not isinstance(entry, dict):
                continue
            fixed, did = _migrate_broken_npm_sqlite_entry(entry)
            new_servers.append(fixed)
            if did:
                migrated = True
        servers = new_servers
        appended = False
        if not _servers_include_mcp_sqlite_package(servers):
            servers.append(dict(_WORKSPACE_SQLITE_MCP_SERVER))
            appended = True
        if migrated or appended:
            data["mcp_servers"] = servers
            tmp = mcp_path.with_name(mcp_path.name + ".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(mcp_path)
            if migrated and appended:
                out["mcp_servers"] = "ok_migrated_and_appended"
            elif migrated:
                out["mcp_servers"] = "ok_migrated"
            else:
                out["mcp_servers"] = "ok_appended"
        else:
            out["mcp_servers"] = "skipped_ok"
    except Exception as e:
        out["mcp_servers"] = f"error:{e}"

    nexus_path = root / "nexus_config.json"
    try:
        cfg: dict = {}
        if nexus_path.exists():
            raw = json.loads(nexus_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cfg = raw
        agent = cfg.get("agent")
        if not isinstance(agent, dict):
            agent = {}
            cfg["agent"] = agent
        if agent.get("merge_sqlite_read_into_tool_pool") is True:
            out["nexus_config"] = "skipped_already_true"
        else:
            agent["merge_sqlite_read_into_tool_pool"] = True
            tmp = nexus_path.with_name(nexus_path.name + ".tmp")
            tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(nexus_path)
            out["nexus_config"] = "ok_written"
    except Exception as e:
        out["nexus_config"] = f"error:{e}"

    return out


def reset_test_sqlite_db() -> Path:
    root = _jachin_root()
    workspace = root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    db_path = workspace / "test_db.sqlite"

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item TEXT NOT NULL,
                quantity INTEGER NOT NULL
            )
            """
        )
        conn.execute("DELETE FROM inventory")
        conn.executemany(
            "INSERT INTO inventory (item, quantity) VALUES (?, ?)",
            [("苹果", 50), ("香蕉", 0), ("橙子", 20)],
        )
        conn.commit()
    finally:
        conn.close()

    return db_path


def _looks_like_workspace_sqlite_test_memory(text: str) -> bool:
    """与 test_db / workspace sqlite 压测、缺货问答相关的记忆，用于测试清理。"""
    t = text or ""
    if not t.strip():
        return False
    tl = t.lower()
    if "test_db.sqlite" in tl:
        return True
    if "test_db" in tl and "sqlite" in tl:
        return True
    if ".sqlite" in tl and re.search(r"inventory|缺货|库存|read_query|write_query", t, re.I):
        return True
    if re.search(r"工作区", t) and re.search(r"sqlite|\.sqlite|数据库", t, re.I):
        return True
    if re.search(r"查一下.*工作区", t) and re.search(r"sqlite|数据库|缺货|库存", t, re.I):
        return True
    return False


def purge_workspace_sqlite_test_memories() -> dict[str, int]:
    """
    从 l3_local.json、各 shard、l3_memory.json 删除疑似 SQLite 压测对话记忆。
    返回 {path_key: removed_count}。
    """
    root = _jachin_root()
    memory_dir = root / "memory"
    stats: dict[str, int] = {}

    def _filter_local_json(path: Path, key: str) -> None:
        if not path.exists():
            stats[key] = 0
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            stats[key] = 0
            return
        if not isinstance(raw, list):
            stats[key] = 0
            return
        before = len(raw)
        kept = [e for e in raw if isinstance(e, dict) and not _looks_like_workspace_sqlite_test_memory(str(e.get("content") or ""))]
        removed = before - len(kept)
        if removed:
            path.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
        stats[key] = removed

    _filter_local_json(memory_dir / "l3_local.json", "l3_local.json")
    shard_removed = 0
    if memory_dir.is_dir():
        for p in sorted(memory_dir.glob("l3_local_shard_*.json")):
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(raw, list):
                continue
            before = len(raw)
            kept = [
                e
                for e in raw
                if isinstance(e, dict) and not _looks_like_workspace_sqlite_test_memory(str(e.get("content") or ""))
            ]
            r = before - len(kept)
            if r:
                p.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
                shard_removed += r
    stats["l3_local_shard_*.json"] = shard_removed

    sync_path = root / "l3_memory.json"
    if not sync_path.exists():
        stats["l3_memory.json"] = 0
    else:
        try:
            data = json.loads(sync_path.read_text(encoding="utf-8"))
        except Exception:
            stats["l3_memory.json"] = 0
        else:
            entries = data.get("entries")
            if not isinstance(entries, list):
                stats["l3_memory.json"] = 0
            else:
                before = len(entries)
                kept = [
                    e
                    for e in entries
                    if isinstance(e, dict)
                    and not _looks_like_workspace_sqlite_test_memory(str(e.get("content") or ""))
                ]
                removed = before - len(kept)
                if removed:
                    data["entries"] = kept
                    data["last_sqlite_test_purge_ts"] = time.time()
                    sync_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                stats["l3_memory.json"] = removed

    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="Reset test_db.sqlite inventory rows; optionally purge test memories.")
    ap.add_argument(
        "--purge-memories",
        action="store_true",
        help="Remove ~/.jachin local entries that look like workspace SQLite / test_db test chatter",
    )
    ap.add_argument(
        "--no-sync-local-config",
        action="store_true",
        help="Do not modify mcp_servers.json or nexus_config.json (默认会同步 MCP + nexus 兜底项)",
    )
    args = ap.parse_args()

    db_path = reset_test_sqlite_db()
    print(f"OK: {db_path} (inventory: 苹果=50, 香蕉=0, 橙子=20)")

    if not args.no_sync_local_config:
        st = sync_local_mcp_and_nexus_for_sqlite_read()
        print(f"SYNC: mcp_servers.json → {st.get('mcp_servers')}")
        print(f"SYNC: nexus_config.json → {st.get('nexus_config')}")

    if args.purge_memories:
        st = purge_workspace_sqlite_test_memories()
        for k, v in st.items():
            print(f"PURGE: {k} removed={v}")


if __name__ == "__main__":
    main()
