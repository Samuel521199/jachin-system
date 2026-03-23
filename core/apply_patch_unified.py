"""
阶段 C：unified diff → ~/.jachin/workspace/（完整 MVP：备份、清单、回滚）。

审计：~/.jachin/logs/apply_patch_audit.jsonl
备份：workspace/.patch_backups/<backup_id>/manifest.json + 各文件副本
"""
from __future__ import annotations

import ast
import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any

from core.intelligence_workspace import emit_intelligence_event, get_jachin_home

logger = logging.getLogger(__name__)

_HUNK_HEADER = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@")


def _audit(event: dict[str, Any]) -> None:
    try:
        p = get_jachin_home() / "logs"
        p.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"ts": time.time(), **event}, ensure_ascii=False)
        with (p / "apply_patch_audit.jsonl").open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        logger.debug("[apply_patch] audit 失败: %s", e)


def _strip_path_prefix(raw: str) -> str:
    s = (raw or "").strip().replace("\\", "/")
    for pref in ("a/", "b/", "i/", "w/"):
        if s.startswith(pref):
            s = s[len(pref) :]
            break
    if s in ("/dev/null", "dev/null"):
        return ""
    return s.lstrip("/")


def _resolve_workspace_path(rel: str) -> Path:
    ws = (get_jachin_home() / "workspace").resolve()
    if not rel or ".." in rel.split("/"):
        raise ValueError(f"非法相对路径: {rel!r}")
    p = (ws / rel).resolve()
    if not str(p).startswith(str(ws)):
        raise ValueError(f"路径越界 workspace: {rel}")
    return p


def _parse_hunk_body(raw_lines: list[str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for line in raw_lines:
        if not line:
            continue
        if line.startswith("\\"):
            out.append(("\\", line))
            continue
        c0 = line[0]
        if c0 not in (" ", "-", "+"):
            continue
        out.append((c0, line[1:].rstrip("\n")))
    return out


def _apply_hunk(lines: list[str], hunk: list[tuple[str, str]], old_start: int) -> list[str]:
    res = lines[: old_start - 1]
    i = old_start - 1
    hi = 0
    while hi < len(hunk):
        tag, content = hunk[hi]
        if tag == " ":
            if i >= len(lines):
                raise ValueError(f"context 越界: 期望行 {content!r}")
            if lines[i] != content:
                raise ValueError(f"context 不匹配 at {i+1}: 文件={lines[i]!r} patch={content!r}")
            res.append(lines[i])
            i += 1
            hi += 1
        elif tag == "-":
            if i >= len(lines):
                raise ValueError(f"删除行越界: {content!r}")
            if lines[i] != content:
                raise ValueError(f"删除不匹配 at {i+1}: 文件={lines[i]!r} patch={content!r}")
            i += 1
            hi += 1
        elif tag == "+":
            res.append(content)
            hi += 1
        else:
            hi += 1
    res.extend(lines[i:])
    return res


def _parse_patch_to_operations(patch_text: str) -> list[dict[str, Any]] | dict[str, Any]:
    """返回 [{rel, is_new, lines}, ...] 或 {error: str}"""
    text = (patch_text or "").replace("\r\n", "\n")
    if not text.strip():
        return {"error": "patch 为空"}
    chunks = re.split(r"(?=^--- )", text, flags=re.MULTILINE)
    ops: list[dict[str, Any]] = []

    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk.startswith("--- "):
            continue
        lines = chunk.split("\n")
        minus_line = lines[0][4:].strip() if lines else ""
        plus_line = ""
        idx = 1
        if idx < len(lines) and lines[idx].startswith("+++ "):
            plus_line = lines[idx][4:].strip()
            idx += 1
        rel = _strip_path_prefix(plus_line) or _strip_path_prefix(minus_line)
        if not rel:
            rel = _strip_path_prefix(plus_line)
        if not rel:
            return {"error": "无法解析 patch 目标路径"}
        try:
            _resolve_workspace_path(rel)
        except ValueError as e:
            return {"error": str(e)}
        is_new = minus_line.endswith("/dev/null") or minus_line.strip().endswith("dev/null")

        hunks_data: list[tuple[int, list[tuple[str, str]]]] = []
        while idx < len(lines):
            m = _HUNK_HEADER.match(lines[idx])
            if not m:
                idx += 1
                continue
            old_start = int(m.group(1))
            hunk_lines: list[str] = []
            idx += 1
            while idx < len(lines) and not _HUNK_HEADER.match(lines[idx]) and not lines[idx].startswith("--- "):
                hunk_lines.append(lines[idx])
                idx += 1
            hunks_data.append((old_start, _parse_hunk_body(hunk_lines)))

        if not hunks_data:
            return {"error": f"未找到有效 hunk: {rel}"}

        if is_new:
            new_lines: list[str] = []
            for _old_s, h_body in hunks_data:
                for tag, content in h_body:
                    if tag == "+":
                        new_lines.append(content)
                    elif tag == " ":
                        new_lines.append(content)
                    elif tag == "-":
                        return {"error": f"创建新文件 {rel} 时不应含删除行 (-)"}
            file_lines = new_lines
        else:
            target = _resolve_workspace_path(rel)
            if not target.exists():
                return {"error": f"目标不存在: {rel}"}
            file_lines = target.read_text(encoding="utf-8", errors="replace").split("\n")
            if file_lines and file_lines[-1] == "":
                file_lines.pop()
            for old_s, h_body in sorted(hunks_data, key=lambda x: x[0], reverse=True):
                start = max(1, old_s)
                file_lines = _apply_hunk(file_lines, h_body, start)

        ops.append({"rel": rel, "is_new": is_new, "lines": file_lines})

    if not ops:
        return {"error": "未解析到任何 --- 文件块（非合法 unified diff）"}
    return ops


def _workspace_root() -> Path:
    return (get_jachin_home() / "workspace").resolve()


def _backup_safe_name(rel: str) -> str:
    return rel.replace("\\", "/").replace("/", "__")


def _apply_patch_python_ast_validate_effective(explicit: bool | None) -> bool:
    if explicit is not None:
        return bool(explicit)
    try:
        p = get_jachin_home() / "nexus_config.json"
        if not p.exists():
            return False
        cfg = json.loads(p.read_text(encoding="utf-8"))
        sec = cfg.get("apply_patch")
        return bool(isinstance(sec, dict) and sec.get("python_ast_validate") is True)
    except Exception:
        return False


def _validate_python_ast_ops(ops: list[dict[str, Any]]) -> str | None:
    """对即将写入的 .py 全文做 ast.parse；失败返回错误文案。"""
    for op in ops:
        rel = str(op.get("rel", "") or "")
        if not rel.lower().endswith(".py"):
            continue
        lines = op.get("lines") or []
        body = "\n".join(lines)
        if body and not body.endswith("\n"):
            body += "\n"
        try:
            ast.parse(body, filename=rel)
        except SyntaxError as e:
            return f"Python AST 校验失败 {rel}: {e}"
    return None


def apply_unified_diff_to_workspace(
    patch_text: str,
    *,
    session_hint: str = "",
    backup: bool = True,
    python_ast_validate: bool | None = None,
) -> dict[str, Any]:
    """
    应用 patch；默认先备份再写入，失败则整批回滚。
    成功返回 ok, files, backup_id（若 backup=True）。
    """
    parsed = _parse_patch_to_operations(patch_text)
    if isinstance(parsed, dict) and "error" in parsed and "rel" not in parsed:
        return {"ok": False, "error": parsed["error"]}

    ops: list[dict[str, Any]] = parsed  # type: ignore[assignment]
    if _apply_patch_python_ast_validate_effective(python_ast_validate):
        verr = _validate_python_ast_ops(ops)
        if verr:
            _audit({"kind": "apply_patch_ast_reject", "error": verr, "session": session_hint[:200]})
            return {"ok": False, "error": verr}

    ws = _workspace_root()
    ws.mkdir(parents=True, exist_ok=True)

    targets: list[tuple[Path, str | None]] = []
    for op in ops:
        p = _resolve_workspace_path(op["rel"])
        prev: str | None
        if p.exists():
            prev = p.read_text(encoding="utf-8", errors="replace")
        else:
            prev = None
        targets.append((p, prev))

    backup_id = uuid.uuid4().hex[:12]
    br = ws / ".patch_backups" / backup_id
    if backup:
        br.mkdir(parents=True, exist_ok=True)

    try:
        manifest_files: list[dict[str, Any]] = []
        for op, (path, prev) in zip(ops, targets, strict=True):
            rel = op["rel"]
            if backup:
                meta = {"rel": rel, "is_new": prev is None}
                if prev is not None:
                    bfile = br / _backup_safe_name(rel)
                    bfile.parent.mkdir(parents=True, exist_ok=True)
                    bfile.write_text(prev, encoding="utf-8")
                    meta["backup_file"] = bfile.name
                manifest_files.append(meta)

        for op, (path, prev) in zip(ops, targets, strict=True):
            path.parent.mkdir(parents=True, exist_ok=True)
            body = "\n".join(op["lines"])
            if body and not body.endswith("\n"):
                body += "\n"
            path.write_text(body, encoding="utf-8")

        touched = [op["rel"] for op in ops]
        if backup:
            manifest = {
                "backup_id": backup_id,
                "ts": time.time(),
                "session_hint": (session_hint or "")[:200],
                "files": manifest_files,
            }
            (br / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            last = ws / ".patch_backups" / "last_success.json"
            last.write_text(
                json.dumps({"backup_id": backup_id, "ts": time.time(), "files": touched}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        _audit({"kind": "apply_patch", "files": touched, "backup_id": backup_id if backup else None, "session": session_hint[:200]})
        emit_intelligence_event("apply_patch", {"files": touched, "backup_id": backup_id if backup else None})
        out: dict[str, Any] = {"ok": True, "files": touched}
        if backup:
            out["backup_id"] = backup_id
        return out
    except Exception as e:
        logger.warning("[apply_patch] 应用失败，回滚: %s", e)
        for op, (path, prev) in zip(ops, targets, strict=True):
            try:
                if prev is None:
                    if path.exists():
                        path.unlink()
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(prev, encoding="utf-8")
            except OSError as oe:
                logger.error("[apply_patch] 回滚单文件失败 %s: %s", path, oe)
        if backup and br.exists():
            try:
                import shutil

                shutil.rmtree(br, ignore_errors=True)
            except Exception:
                pass
        emit_intelligence_event("apply_patch_rollback", {"error": str(e)})
        return {"ok": False, "error": str(e)}


def rollback_patch_backup(backup_id: str | None = None) -> dict[str, Any]:
    """
    从 workspace/.patch_backups/<id>/ 恢复。backup_id 缺省时读 last_success.json。
    """
    ws = _workspace_root()
    bid = (backup_id or "").strip()
    if not bid:
        last = ws / ".patch_backups" / "last_success.json"
        if not last.exists():
            return {"ok": False, "error": "无 last_success.json，请显式传入 backup_id"}
        try:
            data = json.loads(last.read_text(encoding="utf-8"))
            bid = str(data.get("backup_id") or "").strip()
        except Exception as e:
            return {"ok": False, "error": f"读取 last_success 失败: {e}"}
        if not bid:
            return {"ok": False, "error": "last_success 中无 backup_id"}

    br = ws / ".patch_backups" / bid
    mf = br / "manifest.json"
    if not mf.exists():
        return {"ok": False, "error": f"备份不存在: {bid}"}
    try:
        manifest = json.loads(mf.read_text(encoding="utf-8"))
        files = manifest.get("files") if isinstance(manifest, dict) else None
        if not isinstance(files, list):
            return {"ok": False, "error": "manifest 无效"}
        restored: list[str] = []
        for item in files:
            if not isinstance(item, dict):
                continue
            rel = str(item.get("rel") or "")
            if not rel:
                continue
            path = _resolve_workspace_path(rel)
            if item.get("is_new"):
                if path.exists():
                    path.unlink()
                restored.append(rel + " (已删除新建文件)")
                continue
            bf = item.get("backup_file")
            if not isinstance(bf, str):
                return {"ok": False, "error": f"缺少 backup_file: {rel}"}
            src = br / bf
            if not src.exists():
                return {"ok": False, "error": f"备份文件缺失: {bf}"}
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(src.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
            restored.append(rel)
        _audit({"kind": "apply_patch_rollback", "backup_id": bid, "restored": restored})
        emit_intelligence_event("apply_patch_restored", {"backup_id": bid, "files": restored})
        return {"ok": True, "backup_id": bid, "restored": restored}
    except Exception as e:
        return {"ok": False, "error": str(e)}
