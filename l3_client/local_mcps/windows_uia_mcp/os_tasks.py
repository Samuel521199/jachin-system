#!/usr/bin/env python3
"""Reusable Windows OS automation tasks for Jachin.

Layer order:
1. UIA when controls expose accessibility names/patterns.
2. Keyboard/clipboard for deterministic native shortcuts.
3. PyAutoGUI physical input as the last local fallback.

The functions here are intentionally usable both by the MCP server and by
scripts/os_vision_smoke.py.
"""
from __future__ import annotations

import ast
import base64
import csv
import ctypes
import ctypes.wintypes
import html
import io
import json
import logging
import mimetypes
import operator
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("windows_os_tasks")

_DANGEROUS_FILE_ENV = "JACHIN_OS_FILE_DANGEROUS_NO_CONFIRM"
_DANGEROUS_BITABLE_ENV = "JACHIN_LARK_BITABLE_WRITE_NO_CONFIRM"


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


APP_PROFILES: dict[str, dict[str, Any]] = {
    "lark": {
        "aliases": ("lark", "feishu", "\u98de\u4e66"),
        "keywords": ("lark", "feishu", "\u98de\u4e66"),
        "env": "JACHIN_APP_LARK_EXE",
        "exe_names": ("Lark.exe", "Feishu.exe"),
        "candidate_paths": (
            r"%LOCALAPPDATA%\Lark\Lark.exe",
            r"%LOCALAPPDATA%\Programs\Lark\Lark.exe",
            r"%LOCALAPPDATA%\Feishu\Feishu.exe",
            r"%LOCALAPPDATA%\Programs\Feishu\Feishu.exe",
            r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Lark.lnk",
            r"%PROGRAMDATA%\Microsoft\Windows\Start Menu\Programs\Lark.lnk",
            r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Feishu.lnk",
            r"%PROGRAMDATA%\Microsoft\Windows\Start Menu\Programs\Feishu.lnk",
            r"%PROGRAMFILES%\Lark\Lark.exe",
            r"%PROGRAMFILES(X86)%\Lark\Lark.exe",
            r"%PROGRAMFILES%\Feishu\Feishu.exe",
            r"%PROGRAMFILES(X86)%\Feishu\Feishu.exe",
        ),
    },
    "notepad": {
        "aliases": ("notepad", "\u8bb0\u4e8b\u672c"),
        "keywords": ("notepad", "\u8bb0\u4e8b\u672c"),
        "exe_names": ("notepad.exe",),
        "candidate_paths": ("notepad.exe",),
    },
    "calculator": {
        "aliases": ("calculator", "calc", "\u8ba1\u7b97\u5668"),
        "keywords": ("calculator", "calc", "\u8ba1\u7b97\u5668"),
        "exe_names": ("calc.exe", "calculatorapp.exe", "applicationframehost.exe"),
        "candidate_paths": ("calc.exe",),
    },
    "explorer": {
        "aliases": ("explorer", "file explorer", "files"),
        "keywords": ("explorer", "file explorer", "\u6587\u4ef6\u8d44\u6e90\u7ba1\u7406\u5668"),
        "exe_names": ("explorer.exe",),
        "candidate_paths": ("explorer.exe",),
    },
    "browser": {
        "aliases": ("browser", "edge", "chrome", "web"),
        "keywords": ("edge", "chrome", "browser"),
        "exe_names": ("msedge.exe", "chrome.exe"),
        "candidate_paths": ("msedge.exe", "chrome.exe"),
    },
    "terminal": {
        "aliases": ("terminal", "powershell", "cmd", "shell"),
        "keywords": ("terminal", "powershell", "cmd"),
        "exe_names": ("wt.exe", "powershell.exe", "cmd.exe"),
        "candidate_paths": ("wt.exe", "powershell.exe", "cmd.exe"),
    },
    "wps": {
        "aliases": ("wps", "office"),
        "keywords": ("wps", "word", "excel", "powerpoint"),
        "exe_names": ("wps.exe", "et.exe", "wpp.exe", "winword.exe", "excel.exe", "powerpnt.exe"),
        "candidate_paths": ("wps.exe", "winword.exe", "excel.exe", "powerpnt.exe"),
    },
    "codex": {
        "aliases": ("codex", "openai codex"),
        "keywords": ("codex", "openai codex"),
        "env": "JACHIN_APP_CODEX_EXE",
        "exe_names": ("Codex.exe", "codex.exe"),
        "candidate_paths": (
            r"%LOCALAPPDATA%\Programs\Codex\Codex.exe",
            r"%LOCALAPPDATA%\Codex\Codex.exe",
            r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Codex.lnk",
            r"%PROGRAMDATA%\Microsoft\Windows\Start Menu\Programs\Codex.lnk",
            "Codex.exe",
            "codex.exe",
        ),
    },
}


@dataclass
class TaskResult:
    task: str
    ok: bool
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


@dataclass(frozen=True)
class ExecutionContract:
    """Generic target-environment contract for OS workflows.

    This contract intentionally describes the environment, not app-specific UI
    steps. Any workflow that is about to type, click, paste, or press keys can
    ask the verifier whether the current foreground environment still matches
    the user's target.
    """

    target_app: str
    app_key: str
    expected_keywords: tuple[str, ...] = ()
    expected_processes: tuple[str, ...] = ()
    goal: str = ""
    require_foreground: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_app": self.target_app,
            "app_key": self.app_key,
            "expected_keywords": list(self.expected_keywords),
            "expected_processes": list(self.expected_processes),
            "goal": self.goal,
            "require_foreground": self.require_foreground,
        }


@dataclass(frozen=True)
class EnvironmentVerification:
    ok: bool
    detail: str
    contract: ExecutionContract
    active: dict[str, Any] = field(default_factory=dict)
    checks: dict[str, Any] = field(default_factory=dict)
    stage: str = ""
    action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "detail": self.detail,
            "contract": self.contract.to_dict(),
            "active": self.active,
            "checks": self.checks,
            "stage": self.stage,
            "action": self.action,
        }


def normalize_text(text: str) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def normalize_number(text: str) -> str:
    raw = (text or "").strip().replace(",", "").replace(" ", "")
    match = re.search(r"-?\d+(?:\.\d+)?", raw)
    if not match:
        return raw
    value = match.group(0)
    return value[:-2] if value.endswith(".0") else value


def normalize_calculator_expression(text: str) -> str:
    raw = (text or "").strip()
    raw = raw.replace("×", "*").replace("x", "*").replace("X", "*")
    raw = raw.replace("÷", "/").replace("−", "-").replace("—", "-")
    raw = raw.replace("=", "")
    return re.sub(r"[^0-9+\-*/().]", "", raw)


def normalize_app_name(app_name: str) -> str:
    raw = (app_name or "").strip().lower()
    for key, profile in APP_PROFILES.items():
        aliases = tuple(str(x).lower() for x in profile.get("aliases", ()))
        if raw == key or raw in aliases:
            return key
    return raw


def _app_contract(app_name: str, goal: str = "") -> ExecutionContract:
    app_key = normalize_app_name(app_name)
    profile = APP_PROFILES.get(app_key, {})
    keywords = tuple(str(x).lower() for x in profile.get("keywords", ()) if str(x).strip()) or (app_key,)
    processes = tuple(str(x).lower() for x in profile.get("exe_names", ()) if str(x).strip())
    return ExecutionContract(
        target_app=str(app_name or app_key),
        app_key=app_key,
        expected_keywords=keywords,
        expected_processes=processes,
        goal=goal,
    )


def _expand_candidate_path(raw: str) -> str:
    s = os.path.expandvars(raw or "").strip()
    if not s:
        return ""
    return s


def _find_app_executable(profile: dict[str, Any]) -> tuple[str, str]:
    env_name = str(profile.get("env") or "")
    if env_name:
        env_path = _expand_candidate_path(os.environ.get(env_name) or "")
        if env_path and Path(env_path).is_file():
            return env_path, env_name

    for raw in profile.get("candidate_paths", ()):
        candidate = _expand_candidate_path(str(raw))
        if not candidate:
            continue
        suffix = Path(candidate).suffix.lower()
        if suffix in (".exe", ".lnk") and Path(candidate).is_file():
            return candidate, "candidate_path"
        if candidate.lower().endswith(".exe") and "\\" not in candidate and "/" not in candidate:
            resolved = shutil.which(candidate)
            if resolved:
                return resolved, "path_lookup"

    search_roots = [
        Path(os.environ.get("LOCALAPPDATA") or ""),
        Path(os.environ.get("PROGRAMFILES") or ""),
        Path(os.environ.get("PROGRAMFILES(X86)") or ""),
    ]
    exe_names = {str(x).lower() for x in profile.get("exe_names", ()) if str(x).strip()}
    for root in search_roots:
        if not root or not root.exists():
            continue
        for folder_hint in profile.get("aliases", ()):
            base = root / str(folder_hint)
            if not base.exists():
                continue
            for exe in base.rglob("*.exe"):
                if exe.name.lower() in exe_names:
                    return str(exe), "recursive_hint"
    return "", "not_found"


def _desktop_settings_candidates() -> list[Path]:
    out: list[Path] = []
    explicit = os.environ.get("JACHIN_DESKTOP_SETTINGS_PATH")
    if explicit:
        out.append(Path(explicit).expanduser())
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        out.extend(
            [
                Path(local) / "com.jachin.desktop" / "settings.json",
                Path(local) / "jachin" / "desktop" / "settings.json",
                Path(local) / "Jachin" / "desktop" / "settings.json",
            ]
        )
    try:
        exe_dir = Path(sys.executable).resolve().parent
        out.append(exe_dir / "_portable_data" / "settings.json")
    except Exception:
        pass
    return out


def os_file_dangerous_without_confirm_enabled(explicit: bool = False) -> bool:
    if explicit or _truthy(os.environ.get(_DANGEROUS_FILE_ENV)):
        return True
    for path in _desktop_settings_candidates():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if _truthy(data.get("os_file_dangerous_without_confirm")):
            return True
    return False


def os_lark_bitable_write_without_confirm_enabled(explicit: bool = False) -> bool:
    if explicit or _truthy(os.environ.get(_DANGEROUS_BITABLE_ENV)):
        return True
    for path in _desktop_settings_candidates():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if _truthy(data.get("lark_bitable_write_without_confirm")):
            return True
    return False


def _file_stat(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    try:
        st = p.stat()
        return {
            "path": str(p),
            "exists": True,
            "is_file": p.is_file(),
            "is_dir": p.is_dir(),
            "size": st.st_size,
            "mtime": st.st_mtime,
        }
    except Exception:
        return {"path": str(p), "exists": False}


def _user_known_folder(name: str) -> Path:
    home = Path.home()
    if name == "desktop":
        return Path(os.environ.get("USERPROFILE") or home) / "Desktop"
    if name == "downloads":
        return Path(os.environ.get("USERPROFILE") or home) / "Downloads"
    if name == "documents":
        return Path(os.environ.get("USERPROFILE") or home) / "Documents"
    return home


def _file_category(path: str | Path) -> str:
    ext = Path(path).suffix.lower()
    if ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico"}:
        return "image"
    if ext in {".doc", ".docx", ".pdf", ".ppt", ".pptx", ".xls", ".xlsx", ".csv", ".txt", ".md", ".rtf"}:
        return "document"
    if ext in {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2"}:
        return "archive"
    if ext in {".py", ".js", ".ts", ".tsx", ".jsx", ".rs", ".go", ".java", ".cs", ".cpp", ".c", ".h", ".html", ".css", ".json", ".yaml", ".yml", ".toml", ".sql"}:
        return "code"
    if ext in {".log", ".trace"}:
        return "log"
    if ext in {".exe", ".msi", ".bat", ".cmd", ".ps1", ".lnk"}:
        return "app_or_shortcut"
    return "other"


def _parse_tasklist() -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    if sys.platform != "win32":
        return out
    try:
        raw = subprocess.check_output(["tasklist", "/fo", "csv", "/nh"], text=True, encoding="utf-8", errors="replace", timeout=8)
        for row in csv.reader(raw.splitlines()):
            if len(row) < 5:
                continue
            try:
                pid = int(row[1])
            except ValueError:
                continue
            out[pid] = {
                "image_name": row[0],
                "session_name": row[2],
                "session_number": row[3],
                "memory": row[4],
            }
    except Exception:
        pass
    return out


def _run_powershell_json(script: str, timeout: int = 12) -> Any:
    try:
        raw = subprocess.check_output(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        raw = raw.strip()
        if not raw:
            return None
        return json.loads(raw)
    except Exception as e:
        return {"error": f"powershell_failed:{e!r}"}


def _json_string_list(value: str, fallback: list[str] | None = None) -> list[str]:
    if not str(value or "").strip():
        return list(fallback or [])
    try:
        raw = json.loads(value)
    except Exception:
        raw = value
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return list(fallback or [])
    return [str(x).strip() for x in raw if str(x).strip()]


def _json_object_list(value: str) -> list[dict[str, Any]]:
    if not str(value or "").strip():
        return []
    try:
        raw = json.loads(value)
    except Exception:
        return []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [x for x in raw if isinstance(x, dict)]


def _brief_file_line(row: dict[str, Any]) -> str:
    path = str(row.get("path") or "")
    category = str(row.get("category") or "other")
    size = int(row.get("size") or 0)
    return f"- [{category}] {path} ({round(size / 1024, 1)} KB)"


def _confirmation_required_result(task: str, operation: str, evidence: dict[str, Any]) -> TaskResult:
    ev = {
        **evidence,
        "operation": operation,
        "dangerous": True,
        "confirmation_required": True,
        "bypass_options": {
            "tool_arg": "allow_dangerous=true",
            "env": f"{_DANGEROUS_FILE_ENV}=1",
            "desktop_setting": "os_file_dangerous_without_confirm=true",
        },
    }
    return TaskResult(task, False, "confirmation_required", ev)


def _bitable_confirmation_required_result(task: str, operation: str, evidence: dict[str, Any]) -> TaskResult:
    ev = {
        **evidence,
        "operation": operation,
        "dangerous": True,
        "confirmation_required": True,
        "bypass_options": {
            "tool_arg": "allow_dangerous=true",
            "env": f"{_DANGEROUS_BITABLE_ENV}=1",
            "desktop_setting": "lark_bitable_write_without_confirm=true",
        },
    }
    return TaskResult(task, False, "confirmation_required", ev)


def _parse_fields_json(fields_json: str | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(fields_json, dict):
        return {str(k).strip(): v for k, v in fields_json.items() if str(k).strip()}
    raw = str(fields_json or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k).strip(): v for k, v in data.items() if str(k).strip()}


def _resolve_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _jachin_os_data_dir() -> Path:
    base = os.environ.get("JACHIN_OS_DATA_DIR") or os.environ.get("LOCALAPPDATA") or str(Path.home() / ".jachin")
    path = Path(base).expanduser()
    if path.name.lower() not in ("jachin", ".jachin"):
        path = path / "Jachin"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _project_memory_path() -> Path:
    return Path(os.environ.get("JACHIN_OS_PROJECT_MEMORY_PATH") or (_jachin_os_data_dir() / "os_project_memory.json")).expanduser()


def _project_key(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "").strip().lower())


def _load_project_memory() -> dict[str, Any]:
    path = _project_memory_path()
    if not path.exists():
        return {"projects": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"projects": {}}
    if not isinstance(data, dict):
        return {"projects": {}}
    projects = data.get("projects")
    if not isinstance(projects, dict):
        data["projects"] = {}
    return data


def _save_project_memory(data: dict[str, Any]) -> Path:
    path = _project_memory_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _remember_project_path(project_name: str, project_path: str | Path) -> dict[str, Any]:
    name = str(project_name or "").strip() or Path(project_path).name
    key = _project_key(name)
    root = Path(project_path).expanduser().resolve()
    data = _load_project_memory()
    data.setdefault("projects", {})[key] = {
        "name": name,
        "path": str(root),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    path = _save_project_memory(data)
    return {"name": name, "key": key, "path": str(root), "memory_path": str(path)}


def _resolve_remembered_project(project_name: str, project_path: str = "", remember: bool = True) -> tuple[Path | None, dict[str, Any]]:
    name = str(project_name or "").strip()
    if project_path.strip():
        root = Path(project_path).expanduser().resolve()
        ev = {"project_name": name or root.name, "provided_path": str(root), "memory_path": str(_project_memory_path())}
        if root.exists() and root.is_dir():
            if remember:
                ev["remembered"] = _remember_project_path(name or root.name, root)
            return root, ev
        ev["error"] = "provided_project_path_not_found"
        return None, ev

    data = _load_project_memory()
    projects = data.get("projects") or {}
    key = _project_key(name)
    row = projects.get(key) if key else None
    if not row and name:
        compact = key.replace(" ", "")
        for candidate_key, candidate in projects.items():
            if compact and compact in str(candidate_key).replace(" ", ""):
                row = candidate
                key = str(candidate_key)
                break
    if isinstance(row, dict):
        root = Path(str(row.get("path") or "")).expanduser().resolve()
        ev = {"project_name": name, "memory_key": key, "memory_entry": row, "memory_path": str(_project_memory_path())}
        if root.exists() and root.is_dir():
            return root, ev
        ev["error"] = "remembered_project_path_not_found"
        return None, ev

    return None, {
        "project_name": name,
        "memory_path": str(_project_memory_path()),
        "error": "project_path_required_first_time",
        "known_projects": list(projects.keys())[:30],
    }


def _run_cmd(cwd: Path, args: list[str], timeout: int = 12) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        return {"ok": proc.returncode == 0, "returncode": proc.returncode, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip(), "args": args}
    except Exception as e:
        return {"ok": False, "error": repr(e), "args": args}


def _git_lines(root: Path, args: list[str], timeout: int = 12) -> list[str]:
    res = _run_cmd(root, ["git", *args], timeout=timeout)
    if not res.get("ok"):
        return []
    return [line.strip() for line in str(res.get("stdout") or "").splitlines() if line.strip()]


def _git_text(root: Path, args: list[str], timeout: int = 12, max_chars: int = 12000) -> str:
    res = _run_cmd(root, ["git", *args], timeout=timeout)
    text = str(res.get("stdout") or res.get("stderr") or "")
    return text[:max_chars]


def _project_recent_files(root: Path, since_days: int = 3, max_results: int = 80) -> list[dict[str, Any]]:
    excluded = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", "target", "model"}
    since = time.time() - max(1, int(since_days or 3)) * 86400
    rows: list[dict[str, Any]] = []
    try:
        for p in root.rglob("*"):
            if len(rows) >= max(1, min(int(max_results or 80), 1000)):
                break
            try:
                rel_parts = p.relative_to(root).parts
                if any(part in excluded for part in rel_parts):
                    continue
                if not p.is_file():
                    continue
                st = p.stat()
            except Exception:
                continue
            if st.st_mtime < since and st.st_ctime < since:
                continue
            rows.append(
                {
                    "path": str(p),
                    "relative_path": str(p.relative_to(root)),
                    "category": _file_category(p),
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                    "ctime": st.st_ctime,
                }
            )
    except Exception:
        pass
    rows.sort(key=lambda r: max(float(r.get("mtime") or 0), float(r.get("ctime") or 0)), reverse=True)
    return rows[:max_results]


def _read_project_snippets(root: Path, rel_paths: list[str], max_files: int = 8, max_chars_per_file: int = 1600) -> list[dict[str, Any]]:
    snippets: list[dict[str, Any]] = []
    for rel in rel_paths:
        if len(snippets) >= max_files:
            break
        try:
            path = (root / rel).resolve()
            if not path.is_file() or root not in path.parents:
                continue
            if path.stat().st_size > 512_000:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        snippets.append({"relative_path": rel, "category": _file_category(path), "text": text[:max_chars_per_file]})
    return snippets


def _load_local_env_for_qwen(root: Path) -> None:
    candidates = [root / ".env", Path.cwd() / ".env", Path.cwd().parent / ".env", Path.home() / ".jachin" / ".env"]
    for env_path in candidates:
        try:
            if not env_path.exists() or not env_path.is_file():
                continue
            for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and v and not os.environ.get(k):
                    os.environ[k] = v
        except Exception:
            continue


def _qwen_credentials() -> tuple[str, str]:
    try:
        from core.brain.llm.dashscope_regional import get_dashscope_regional_credentials

        key, base = get_dashscope_regional_credentials()
        return key or "", (base or "https://dashscope.aliyuncs.com/compatible-mode/v1")
    except Exception:
        key = os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("QWEN_API_KEY") or os.environ.get("QWEN_AI_API_KEY") or ""
        base = os.environ.get("DASHSCOPE_API_BASE") or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        return key.strip(), base.strip()


def _call_qwen_coder(prompt: str, model: str = "", timeout: int = 45) -> dict[str, Any]:
    model_name = model or os.environ.get("JACHIN_PROJECT_BRIEFING_MODEL") or "qwen-coder"
    key, base = _qwen_credentials()
    if not key:
        return {"ok": False, "detail": "qwen_api_key_missing", "model": model_name}
    url = base.rstrip("/") + "/chat/completions"
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": "你是资深工程负责人和代码审阅助手。请基于证据总结项目最新进展、风险和下一步，不要编造证据外的信息。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 1600,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        return {"ok": bool(content), "detail": "qwen_summary_ready" if content else "qwen_empty_response", "model": model_name, "api_base": base, "content": content}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:1000]
        return {"ok": False, "detail": f"qwen_http_error:{e.code}", "model": model_name, "api_base": base, "error": body}
    except Exception as e:
        return {"ok": False, "detail": f"qwen_failed:{e!r}", "model": model_name, "api_base": base}


def _image_file_data_url(image_path: str | Path, max_bytes: int = 4_000_000) -> tuple[str, dict[str, Any]]:
    path = Path(image_path)
    raw = path.read_bytes()
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    meta: dict[str, Any] = {"path": str(path), "original_bytes": len(raw), "mime": mime, "resized": False}
    max_bytes = max(256_000, int(max_bytes or 4_000_000))
    if len(raw) > max_bytes:
        try:
            from PIL import Image

            with Image.open(path) as img:
                img = img.convert("RGB")
                max_side = max(900, int(os.environ.get("JACHIN_CODEX_VISION_MAX_IMAGE_SIDE") or "1800"))
                img.thumbnail((max_side, max_side))
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=max(55, min(95, int(os.environ.get("JACHIN_CODEX_VISION_JPEG_QUALITY") or "85"))))
                raw = buf.getvalue()
                mime = "image/jpeg"
                meta.update({"resized": True, "resized_bytes": len(raw), "mime": mime, "max_side": max_side})
        except Exception as e:
            meta["resize_error"] = repr(e)
    data = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{data}", meta


def _call_qwen_vision_codex_extract(
    screenshot_path: str | Path,
    project_name: str,
    feature_query: str = "",
    model: str = "",
    timeout: int | None = None,
) -> dict[str, Any]:
    model_name = (
        model
        or os.environ.get("JACHIN_CODEX_VISION_EXTRACT_MODEL")
        or os.environ.get("JACHIN_MULTIMODAL_MODEL")
        or "qwen3.7-plus"
    )
    key, base = _qwen_credentials()
    if not key:
        return {"ok": False, "detail": "qwen_api_key_missing", "model": model_name}
    try:
        data_url, image_meta = _image_file_data_url(
            screenshot_path,
            max_bytes=int(os.environ.get("JACHIN_CODEX_VISION_MAX_IMAGE_BYTES") or "4000000"),
        )
    except Exception as e:
        return {"ok": False, "detail": f"image_encode_failed:{e!r}", "model": model_name}

    prompt = (
        "你是一个可靠的屏幕截图文本抽取器。请只根据截图内容，提取 Codex 对话区中最新一条助手回复的正文。\n"
        "要求：\n"
        "1. 不要总结、不要改写、不要补充截图里没有的内容。\n"
        "2. 排除用户输入框、侧边栏、按钮、时间、工具栏和旧消息。\n"
        "3. 如果最新助手回复还没有完成，尽量抽取已经可见的完整段落。\n"
        "4. 只输出可直接发送到 Lark 的中文正文，不要输出 JSON、Markdown 代码块或解释。\n"
        f"项目名：{project_name}\n"
        f"用户关注点：{feature_query or '未指定'}"
    )
    url = base.rstrip("/") + "/chat/completions"
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": "你只做截图可见文本抽取。任何看不见的内容都不能编造。"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        "temperature": 0,
        "max_tokens": int(os.environ.get("JACHIN_CODEX_VISION_EXTRACT_MAX_TOKENS") or "1800"),
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout or int(os.environ.get("JACHIN_CODEX_VISION_EXTRACT_TIMEOUT") or "90")) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        content = re.sub(r"^```(?:\w+)?\s*", "", content).strip()
        content = re.sub(r"\s*```$", "", content).strip()
        return {
            "ok": bool(content),
            "detail": "qwen_vision_extract_ready" if content else "qwen_vision_empty_response",
            "model": model_name,
            "api_base": base,
            "image_meta": image_meta,
            "content": content,
        }
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:1000]
        return {"ok": False, "detail": f"qwen_vision_http_error:{e.code}", "model": model_name, "api_base": base, "image_meta": image_meta, "error": body}
    except Exception as e:
        return {"ok": False, "detail": f"qwen_vision_failed:{e!r}", "model": model_name, "api_base": base, "image_meta": image_meta}


def _build_codex_project_prompt(project_name: str, project_path: str, feature_query: str = "", since_days: int = 3) -> str:
    feature = str(feature_query or "").strip() or "整体最新修改"
    return (
        f"请总结 Windows 本机项目 `{project_name}` 的最新进展，并输出一段适合发送到 Lark 的中文简报。\n\n"
        f"项目路径：{project_path}\n"
        f"时间范围：最近 {max(1, int(since_days or 3))} 天\n"
        f"关注功能/主题：{feature}\n\n"
        "请你读取项目的 Git 状态、最近提交、未提交 diff、相关文件内容，给出：\n"
        "1. 最新完成/修改了什么\n"
        "2. 涉及哪些模块和关键文件\n"
        "3. 当前风险或未完成点\n"
        "4. 下一步建议\n"
        "5. 最后附一段可直接发给同事的短版消息\n\n"
        "要求：不要编造，明确基于你看到的文件、diff 或提交。输出请控制在 800 字以内。"
    )


def _looks_like_codex_project_prompt_echo(text: str) -> bool:
    content = str(text or "").strip()
    if not content:
        return False
    markers = (
        "请总结 Windows 本机项目",
        "适合发送到 Lark 的中文简报",
        "项目路径：",
        "时间范围：最近",
        "关注功能/主题：",
        "请你读取项目的 Git 状态",
        "未提交 diff",
        "相关文件内容，给出",
        "最新完成/修改了什么",
        "涉及哪些模块和关键文件",
        "当前风险或未完成点",
        "最后附一段可直接发给同事的短版消息",
        "要求：不要编造",
        "输出请控制在 800 字以内",
    )
    hits = sum(1 for marker in markers if marker in content)
    return hits >= 4


def _build_static_codex_project_prompt(project_name: str, project_path: str, feature_query: str = "", since_days: int = 3) -> str:
    feature = str(feature_query or "").strip() or "整体最新修改"
    days = max(1, int(since_days or 3))
    return (
        f"请总结 Windows 本机项目 `{project_name}` 的最新进展，并输出一段适合发送到 Lark 的中文简报。\n\n"
        f"项目路径：{project_path}\n"
        f"时间范围：最近 {days} 天\n"
        f"关注功能/主题：{feature}\n\n"
        "请你读取项目的 Git 状态、最近提交、未提交 diff、相关文件内容，给出：\n"
        "1. 最新完成/修改了什么\n"
        "2. 涉及哪些模块和关键文件\n"
        "3. 当前风险或未完成点\n"
        "4. 下一步建议\n"
        "5. 最后附一段可直接发给同事的短版消息\n\n"
        "要求：不要编造，明确基于你看到的文件、diff 或提交。输出请控制在 800 字以内。"
    )


def _strip_markdown_fence(text: str) -> str:
    content = str(text or "").strip()
    if content.startswith("```"):
        content = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    return content.strip()


def _polish_codex_project_prompt(
    *,
    project_name: str,
    project_path: str,
    feature_query: str = "",
    since_days: int = 3,
    original_user_input: str = "",
    fallback_prompt: str = "",
) -> dict[str, Any]:
    user_text = str(original_user_input or "").strip()
    if not user_text:
        return {"ok": False, "detail": "original_user_input_empty", "content": ""}
    request = f"""
You are writing the exact prompt that Jachin will paste into the Codex desktop app.

Goal:
- Convert the user's natural-language request into a richer, task-specific Codex prompt.
- Preserve the user's actual intent, wording constraints, audience, requested format, and focus.
- Do not use a fixed template mechanically. The final prompt should adapt to the user's request.
- Still require Codex to inspect real local evidence: git status, recent commits, uncommitted diff, and relevant files.
- Ask Codex not to fabricate.
- Ask Codex to produce a concise Chinese result suitable for Lark delivery.
- Output only the final prompt text, no explanation.

Known slots:
- project_name: {project_name}
- project_path: {project_path}
- since_days: {max(1, int(since_days or 3))}
- extracted_focus: {feature_query or "overall"}

User original request:
{user_text}

Fallback structure, only for reference:
{fallback_prompt}
""".strip()
    result = _call_qwen_coder(
        request,
        model=os.environ.get("JACHIN_CODEX_PROMPT_POLISH_MODEL") or "qwen-plus",
        timeout=30,
    )
    content = _strip_markdown_fence(str(result.get("content") or ""))
    checks = {
        "non_empty": len(content) >= 180,
        "mentions_project": (str(project_name or "").strip() in content) if project_name else True,
        "mentions_path": (str(project_path or "").strip() in content) if project_path else True,
        "mentions_evidence": bool(re.search(r"(git|diff|commit|提交|状态|文件|证据)", content, re.I)),
    }
    ok = bool(result.get("ok")) and all(checks.values())
    return {
        "ok": ok,
        "detail": "prompt_polished" if ok else str(result.get("detail") or "prompt_polish_invalid"),
        "model": result.get("model"),
        "api_base": result.get("api_base"),
        "checks": checks,
        "content": content if ok else "",
        "raw_len": len(content),
    }


def _build_codex_project_prompt_with_meta(
    project_name: str,
    project_path: str,
    feature_query: str = "",
    since_days: int = 3,
    original_user_input: str = "",
) -> tuple[str, dict[str, Any]]:
    fallback = _build_static_codex_project_prompt(project_name, project_path, feature_query=feature_query, since_days=since_days)
    polish = _polish_codex_project_prompt(
        project_name=project_name,
        project_path=project_path,
        feature_query=feature_query,
        since_days=since_days,
        original_user_input=original_user_input,
        fallback_prompt=fallback,
    )
    if polish.get("ok") and str(polish.get("content") or "").strip():
        prompt = str(polish["content"]).strip()
        strategy = "llm_polished_from_user_input"
    else:
        extra = ""
        if str(original_user_input or "").strip():
            extra = "\n\n用户原始请求（请优先保持这个意图、语气、格式要求和关注点）：\n" + str(original_user_input).strip()
        prompt = (fallback + extra).strip()
        strategy = "static_fallback_with_user_input" if extra else "static_fallback"
    meta = {
        "strategy": strategy,
        "original_user_input_len": len(str(original_user_input or "")),
        "feature_query": feature_query,
        "since_days": max(1, int(since_days or 3)),
        "polish": {k: v for k, v in polish.items() if k != "content"},
    }
    return prompt[:32000], meta


def _build_codex_project_prompt(
    project_name: str,
    project_path: str,
    feature_query: str = "",
    since_days: int = 3,
    original_user_input: str = "",
) -> str:
    return _build_codex_project_prompt_with_meta(
        project_name,
        project_path,
        feature_query=feature_query,
        since_days=since_days,
        original_user_input=original_user_input,
    )[0]


def _looks_like_codex_project_prompt_echo(text: str) -> bool:
    content = str(text or "").strip()
    if not content:
        return False
    compact = _compact_match_text(content)
    markers = (
        "请总结 Windows 本机项目",
        "项目路径",
        "时间范围",
        "关注功能/主题",
        "请你读取项目的 Git 状态",
        "未提交 diff",
        "不要编造",
        "输出请控制",
        "User original request:",
        "Known slots:",
        "You are writing the exact prompt",
        "Fallback structure",
        "project_path:",
        "git status",
        "uncommitted diff",
    )
    hits = sum(1 for marker in markers if marker and marker in content)
    compact_hits = sum(
        1
        for marker in ("请总结windows本机项目", "项目路径", "时间范围", "用户原始请求", "donotuseafixedtemplate")
        if marker in compact
    )
    return hits >= 3 or compact_hits >= 2


def _codex_response_valid(text: str, project_name: str, feature_query: str = "") -> dict[str, Any]:
    content = str(text or "").strip()
    compact = _compact_match_text(content)
    checks = {
        "non_empty": len(content) >= 80,
        "mentions_project": _compact_match_text(project_name) in compact if project_name else True,
        "has_file_or_module_signal": bool(re.search(r"(\.py|\.ts|\.tsx|\.rs|\.md|文件|模块|diff|commit|提交|路径|变更|差异|证据)", content, re.I)),
        "has_conclusion_signal": bool(re.search(r"(总结|进展|风险|下一步|建议|完成|修改|新增|开发|功能|结论|问题)", content)),
        "not_prompt_echo": not _looks_like_codex_project_prompt_echo(content),
    }
    if feature_query:
        topic_key = _compact_match_text(feature_query)
        topic_tokens = [t for t in re.split(r"[^a-zA-Z0-9\u4e00-\u9fff]+", str(feature_query or "").lower()) if len(t) >= 3]
        token_hits = sum(1 for token in topic_tokens if token in compact)
        checks["mentions_feature_or_topic"] = (
            topic_key in compact
            or token_hits >= max(1, min(2, len(topic_tokens)))
            or bool(re.search(r"(功能|主题|相关|工作流|调度|流程|助手|发送|交付)", content))
        )
    ok = all(checks.values())
    return {"ok": ok, "checks": checks, "length": len(content)}


def _extract_codex_brief_from_ocr(ocr_text: str, project_name: str) -> str:
    lines = []
    noisy_exact = {
        "口",
        "个",
        "×",
        "√",
        "q搜索",
        "@插件",
        "自动化",
        "项目",
        "对话",
        "设置",
        "codex移动版",
        "新对话",
    }
    noisy_patterns = (
        r"^\d+\s*(天|小时|分钟|分|周)$",
        r"^已处理\d+s",
        r"^文件编辑视图帮助",
        r"^要求后续变更",
        r"^完全访问",
        r"^5\.5",
        r"^s[丨|].*ai$",
        r"^打开计算器$",
        r"^了解我的习惯$",
        r"^了解项目用途$",
        r"^评估windowsmcp实现$",
        r"^workflow$",
        r"^清理d盘垃圾$",
        r"^为codex安装这些cli$",
        r"^codex有三种配置",
        r"^更新happyhorse",
        r"^yolo现在是",
    )
    for raw in str(ocr_text or "").splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        line_l = line.lower()
        if "ask for follow-up changes" in line_l or "ask for approval" in line_l:
            break
        if not line:
            continue
        key = line.lower().replace(" ", "")
        if key in noisy_exact:
            continue
        if any(re.search(pat, key, re.I) for pat in noisy_patterns):
            continue
        line = re.sub(r"^[o0]13_", "l3_", line)
        line = re.sub(r"^[o0](scripts|tests)/", r"\1/", line)
        lines.append(line)

    start = 0
    project_key = _compact_match_text(project_name)
    for idx, line in enumerate(lines):
        compact = _compact_match_text(line)
        if project_key and project_key in compact and re.search(r"(最近|最新|进展|正式提交|工作区)", line):
            start = idx
            break
    selected = lines[start:]

    # Keep the visible answer portion and drop composer/footer leftovers.
    stop_markers = ("要求后续变更", "完全访问", "设置")
    trimmed: list[str] = []
    for line in selected:
        if any(marker in line for marker in stop_markers):
            break
        trimmed.append(line)
        if len("\n".join(trimmed)) > 2200:
            break
    text = "\n".join(trimmed).strip()
    if len(text) > 1800:
        text = text[:1750].rstrip() + "\n...[OCR 摘要已截断]"
    return text


def _choose_codex_brief_message(
    copied_text: str,
    ocr_text: str,
    project_name: str,
    feature_query: str = "",
    vision_text: str = "",
) -> dict[str, Any]:
    copied = str(copied_text or "").strip()
    vision = str(vision_text or "").strip()
    ocr_fallback = _extract_codex_brief_from_ocr(ocr_text, project_name)
    vision_validation = _codex_response_valid(vision, project_name, feature_query=feature_query)
    copied_validation = _codex_response_valid(copied, project_name, feature_query=feature_query)
    ocr_validation = _codex_response_valid(ocr_fallback, project_name, feature_query=feature_query)

    if vision_validation.get("ok"):
        return {
            "message_text": vision,
            "message_source": "qwen_vision",
            "validation": vision_validation,
            "vision_text": vision,
            "ocr_fallback_text": ocr_fallback,
            "vision_validation": vision_validation,
            "copied_validation": copied_validation,
            "ocr_validation": ocr_validation,
        }
    if copied_validation.get("ok"):
        return {
            "message_text": copied,
            "message_source": "clipboard",
            "validation": copied_validation,
            "vision_text": vision,
            "ocr_fallback_text": ocr_fallback,
            "vision_validation": vision_validation,
            "copied_validation": copied_validation,
            "ocr_validation": ocr_validation,
        }
    if ocr_validation.get("ok"):
        return {
            "message_text": ocr_fallback,
            "message_source": "ocr_fallback",
            "validation": ocr_validation,
            "vision_text": vision,
            "ocr_fallback_text": ocr_fallback,
            "vision_validation": vision_validation,
            "copied_validation": copied_validation,
            "ocr_validation": ocr_validation,
        }

    # Preserve the richer candidate for evidence, but keep validation failed.
    candidates = [
        ("qwen_vision_unverified", vision, vision_validation),
        ("ocr_fallback_unverified", ocr_fallback, ocr_validation),
        ("clipboard_unverified", copied, copied_validation),
    ]
    message_source, message_text, validation = max(candidates, key=lambda item: len(item[1]))
    return {
        "message_text": message_text,
        "message_source": message_source,
        "validation": validation,
        "vision_text": vision,
        "ocr_fallback_text": ocr_fallback,
        "vision_validation": vision_validation,
        "copied_validation": copied_validation,
        "ocr_validation": ocr_validation,
    }


def _extract_codex_generic_from_ocr(ocr_text: str, question: str = "") -> str:
    lines: list[str] = []
    question_key = _compact_match_text(question)
    noisy_exact = {
        "file",
        "edit",
        "view",
        "help",
        "newchat",
        "qsearch",
        "projects",
        "chats",
        "scheduled",
        "showmore",
        "askforfollowupchanges",
        "askforapproval",
        "samuelthoreau",
        "update",
        "pro",
    }
    noisy_patterns = (
        r"^\d+(s|m|h|d)?$",
        r"^workingfor",
        r"^workedfor",
        r"^running\$",
        r"^ran\d+commands?$",
        r"^5\.5",
        r"^document[- ]?md$",
        r"^openin$",
    )
    for raw in str(ocr_text or "").splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        line_l = line.lower()
        if "ask for follow-up changes" in line_l or "ask for approval" in line_l:
            break
        if not line:
            continue
        compact = _compact_match_text(line)
        if not compact or compact in noisy_exact:
            continue
        if any(re.search(pat, compact, re.I) for pat in noisy_patterns):
            continue
        if question_key and compact == question_key:
            continue
        lines.append(line)

    if not lines:
        return ""

    start = 0
    if question_key:
        for idx, line in enumerate(lines):
            if question_key and question_key in _compact_match_text(line):
                start = min(idx + 1, len(lines))
    selected = lines[start:]
    trimmed: list[str] = []
    for line in selected:
        compact = _compact_match_text(line)
        if compact in {"askforfollowupchanges", "askforapproval", "update", "pro"}:
            break
        trimmed.append(line)
        if len("\n".join(trimmed)) > 2400:
            break
    text = "\n".join(trimmed).strip()
    if len(text) > 2200:
        text = text[:2150].rstrip() + "\n...[OCR excerpt truncated]"
    return text


def _codex_generic_response_valid(text: str, question: str = "") -> dict[str, Any]:
    content = str(text or "").strip()
    compact = _compact_match_text(content)
    question_text = str(question or "").strip()
    question_key = _compact_match_text(question_text)
    question_tokens = [
        token
        for token in re.split(r"[^a-zA-Z0-9\u4e00-\u9fff]+", question_text.lower())
        if len(token) >= 2
    ]
    token_hits = sum(1 for token in question_tokens if token and token in compact)
    prompt_echo = False
    if question_key:
        prompt_echo = compact == question_key or (question_key in compact and len(compact) <= max(80, len(question_key) * 2))
    checks = {
        "non_empty": len(content) >= 40,
        "not_prompt_echo": not prompt_echo and not _looks_like_codex_project_prompt_echo(content),
        "has_answer_shape": bool("\n" in content or re.search(r"([:：。；;]|^[-*•\d]+[.)、])", content, re.M) or len(content) >= 120),
        "mentions_question_topic": True if not question_tokens else token_hits >= max(1, min(2, len(question_tokens))),
    }
    ok = all(checks.values())
    return {"ok": ok, "checks": checks, "length": len(content), "token_hits": token_hits}


def _choose_codex_generic_reply(
    copied_text: str,
    ocr_text: str,
    vision_text: str = "",
    question: str = "",
) -> dict[str, Any]:
    copied = str(copied_text or "").strip()
    vision = str(vision_text or "").strip()
    ocr_fallback = _extract_codex_generic_from_ocr(ocr_text, question=question)
    vision_validation = _codex_generic_response_valid(vision, question=question)
    copied_validation = _codex_generic_response_valid(copied, question=question)
    ocr_validation = _codex_generic_response_valid(ocr_fallback, question=question)

    candidates = [
        ("qwen_vision", vision, vision_validation),
        ("clipboard", copied, copied_validation),
        ("ocr_fallback", ocr_fallback, ocr_validation),
    ]
    for source, message_text, validation in candidates:
        if validation.get("ok"):
            return {
                "message_text": message_text,
                "message_source": source,
                "validation": validation,
                "vision_text": vision,
                "ocr_fallback_text": ocr_fallback,
                "vision_validation": vision_validation,
                "copied_validation": copied_validation,
                "ocr_validation": ocr_validation,
            }

    source, message_text, validation = max(candidates, key=lambda item: len(item[1]))
    return {
        "message_text": message_text,
        "message_source": f"{source}_unverified",
        "validation": validation,
        "vision_text": vision,
        "ocr_fallback_text": ocr_fallback,
        "vision_validation": vision_validation,
        "copied_validation": copied_validation,
        "ocr_validation": ocr_validation,
    }

def calculator_visual_state(screenshot_path: str | Path, expected: str = "") -> dict[str, Any]:
    state: dict[str, Any] = {
        "ok": False,
        "ocr_text": "",
        "ocr_notes": "",
        "ocr_backend": "none",
        "expression": "",
        "expression_norm": "",
        "result": "",
        "result_norm": "",
    }
    try:
        from l3_client.local_mcps.gameqa_mcp.core.ocr_engine import ocr_png_bytes

        path = Path(screenshot_path)
        text, notes, backend = ocr_png_bytes(path.read_bytes())
        state.update({"ocr_text": text, "ocr_notes": notes, "ocr_backend": backend})
    except Exception as e:
        state["ocr_notes"] = f"ocr_failed:{e!r}"
        return state

    lines = [ln.strip() for ln in str(state["ocr_text"]).splitlines() if ln.strip()]
    expr_idx = -1
    for i, line in enumerate(lines):
        norm = normalize_calculator_expression(line)
        if re.search(r"\d", norm) and re.search(r"[+\-*/]", norm):
            state["expression"] = line
            state["expression_norm"] = norm
            expr_idx = i
            break
    expected_norm = normalize_number(expected)
    result_candidates: list[dict[str, str]] = []
    if expr_idx >= 0:
        for line in lines[expr_idx + 1 :]:
            norm_num = normalize_number(line)
            if re.fullmatch(r"-?\d+(?:\.\d+)?", norm_num or ""):
                result_candidates.append({"text": line, "norm": norm_num})
                if expected_norm and norm_num == expected_norm:
                    state["result"] = line
                    state["result_norm"] = norm_num
                    break
        if not state["result_norm"] and result_candidates:
            state["result"] = result_candidates[0]["text"]
            state["result_norm"] = result_candidates[0]["norm"]
    state["result_candidates"] = result_candidates
    state["ok"] = bool(state["expression_norm"] or state["result_norm"])
    return state


def ocr_image_state(screenshot_path: str | Path) -> dict[str, Any]:
    state: dict[str, Any] = {
        "ok": False,
        "ocr_text": "",
        "ocr_notes": "",
        "ocr_backend": "none",
    }
    try:
        from l3_client.local_mcps.gameqa_mcp.core.ocr_engine import ocr_png_bytes

        path = Path(screenshot_path)
        text, notes, backend = ocr_png_bytes(path.read_bytes())
        state.update({"ok": bool(str(text).strip()), "ocr_text": text, "ocr_notes": notes, "ocr_backend": backend})
    except Exception as e:
        state["ocr_notes"] = f"ocr_failed:{e!r}"
    return state


def now_tag() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _safe_label(text: str) -> str:
    label = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(text or "").strip())
    return label[:40] or "target"


def _dedupe_lines(texts: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for text in texts:
        for raw in str(text or "").splitlines():
            line = re.sub(r"\s+", " ", raw).strip()
            if not line or len(line) <= 1:
                continue
            key = line.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(line)
    return out


def _ocr_fingerprint(text: str) -> str:
    lines = []
    for raw in str(text or "").splitlines():
        line = re.sub(r"\s+", "", raw).strip().lower()
        if line:
            lines.append(line)
    return "|".join(lines)


def _codex_generation_active(text: str) -> bool:
    compact = _compact_match_text(text)
    active_markers = (
        "running",
        "reconnecting",
        "regenerating",
        "stopgenerating",
        "stopgeneration",
        "\u6b63\u5728\u8fd0\u884c",
        "\u6b63\u5728\u91cd\u65b0\u8fde\u63a5",
        "\u91cd\u65b0\u8fde\u63a5",
        "\u6b63\u5728\u751f\u6210",
        "\u505c\u6b62\u751f\u6210",
    )
    return any(marker in compact for marker in active_markers)


def _ocr_content_keys(text: str) -> list[str]:
    keys: list[str] = []
    for raw in str(text or "").splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if len(line) <= 1:
            continue
        compact = re.sub(r"\s+", "", line).lower()
        if compact in {"消息", "未读", "云文档", "工作台", "通讯录"}:
            continue
        if re.fullmatch(r"[口×一+?q0-9_\- ]+", compact):
            continue
        keys.append(compact)
    return keys


def _compact_match_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def _visual_has_any(text: str, needles: tuple[str, ...]) -> bool:
    compact = _compact_match_text(text)
    return any(_compact_match_text(n) in compact for n in needles if n)


def _lark_message_visible_match(message: str, visual_text: str) -> dict[str, Any]:
    msg = str(message or "").strip()
    text = str(visual_text or "")
    msg_key = _compact_match_text(msg)
    text_key = _compact_match_text(text)
    if not msg_key:
        return {"ok": False, "strategy": "empty_message", "hits": [], "required": 0}
    if msg_key in text_key:
        return {"ok": True, "strategy": "exact", "hits": [msg[:80]], "required": 1}

    if 4 <= len(msg_key) <= 20:
        edge_variants = [msg_key[1:], msg_key[:-1]]
        if len(msg_key) >= 6:
            edge_variants.extend([msg_key[2:], msg_key[:-2], msg_key[1:-1]])
        hits = [variant for variant in edge_variants if len(variant) >= 4 and variant in text_key]
        if hits:
            return {
                "ok": True,
                "strategy": "short_fuzzy_edge_drop",
                "hits": hits[:3],
                "required": 1,
                "message_len": len(msg_key),
            }

    # Long Lark drafts only show the bottom of the composer. OCR also drops
    # punctuation and can confuse file prefixes, so verify with distinctive
    # anchors from visible chunks instead of requiring the whole message.
    chunks: list[str] = []
    for raw_line in msg.splitlines():
        for part in re.split(r"[\uFF0C\u3002\uFF1B;\u3001,.!?\uFF01\uFF1F\s]+", raw_line):
            part = part.strip()
            key = _compact_match_text(part)
            if len(key) >= 5:
                chunks.append(part)
    tail_chunks = chunks[-16:] if len(chunks) > 16 else chunks
    seen: set[str] = set()
    anchors: list[str] = []
    for chunk in tail_chunks:
        key = _compact_match_text(chunk)
        if key in seen:
            continue
        seen.add(key)
        anchors.append(chunk)

    hits = [anchor for anchor in anchors if _compact_match_text(anchor) in text_key]
    if len(anchors) == 1 and len(_compact_match_text(anchors[0])) >= 4:
        required = 1
    else:
        required = max(2, min(3, len(anchors) // 3 if anchors else 0))
    ok = len(hits) >= required
    return {
        "ok": ok,
        "strategy": "anchor",
        "hits": hits[:12],
        "required": required,
        "anchor_count": len(anchors),
    }


def _lark_recipient_identity_check(target: str, visual_text: str) -> dict[str, Any]:
    """Verify the active Lark conversation, not just any full-screen text hit."""
    target_raw = str(target or "").strip()
    text = str(visual_text or "")
    target_key = _compact_match_text(target_raw)
    text_key = _compact_match_text(text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    result: dict[str, Any] = {
        "ok": False,
        "target": target_raw,
        "target_visible_fullscreen": bool(target_key and target_key in text_key),
        "title": "",
        "title_match": False,
        "send_target": "",
        "send_target_match": False,
        "negative_evidence": [],
        "reason": "",
    }
    if not target_key:
        result["reason"] = "target_empty"
        return result

    search_overlay = (
        ("\u641c\u7d22\u5386\u53f2" in text and ("\u9009\u62e9\u6761\u76ee" in text or "\u9000\u51fa\u641c\u7d22" in text))
        or ("search history" in text.lower() and ("select" in text.lower() or "esc" in text.lower()))
    )
    if search_overlay:
        result["negative_evidence"].append("search_overlay_still_open")

    for line in lines:
        if "\u53d1\u9001\u7ed9" in line or "send to" in line.lower():
            result["send_target"] = line
            result["send_target_match"] = target_key in _compact_match_text(line)
            if not result["send_target_match"]:
                result["negative_evidence"].append(f"wrong_send_target:{line[:80]}")
            break

    title = ""
    for idx, line in enumerate(lines[:18]):
        compact = _compact_match_text(line)
        if compact in {"\u6d88\u606f", "chats", "chat"}:
            for candidate in lines[idx + 1 : min(len(lines), idx + 6)]:
                candidate_compact = _compact_match_text(candidate)
                if not candidate_compact:
                    continue
                if candidate_compact in {"\u6d88\u606f", "chats", "chat"}:
                    continue
                if "\u641c\u7d22" in candidate or "search" in candidate.lower() or candidate in {"?", "Q"}:
                    continue
                title = candidate
                break
            if title:
                break
    if not title and lines:
        for candidate in lines[:10]:
            if "\u641c\u7d22" not in candidate and "search" not in candidate.lower() and candidate not in {"?", "Q", "\u53e3"}:
                title = candidate
                break
    result["title"] = title
    result["title_match"] = bool(title and target_key in _compact_match_text(title))

    wrong_assistant = any(
        marker in _compact_match_text(result.get("title") or "")
        for marker in ("\u90ae\u7bb1\u52a9\u624b", "\u90ae\u7bb1\u52a9\u624b\u673a\u5668\u4eba", "mailassistant", "emailassistant")
    )
    if wrong_assistant and not any(marker in target_key for marker in ("\u90ae\u7bb1\u52a9\u624b", "mailassistant", "emailassistant")):
        result["negative_evidence"].append(f"wrong_chat_title:{title[:80]}")

    if result["negative_evidence"]:
        result["reason"] = ",".join(result["negative_evidence"])
        return result
    if result["title_match"] or result["send_target_match"]:
        result["ok"] = True
        result["reason"] = "recipient_identity_verified"
        return result
    result["reason"] = "recipient_identity_not_verified"
    return result


def _collect_evidence_paths(value: Any) -> list[str]:
    paths: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                if isinstance(item, str):
                    lower_key = str(key).lower()
                    lower_item = item.lower()
                    if (
                        "path" in lower_key
                        or "screenshot" in lower_key
                        or lower_item.endswith((".png", ".jpg", ".jpeg", ".md", ".json", ".html"))
                    ):
                        paths.append(item)
                else:
                    walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(value)
    out: list[str] = []
    seen: set[str] = set()
    for path in paths:
        key = path.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def _timeline_event(stage: str, status: str = "done", detail: str = "", evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "stage": str(stage or "").strip() or "step",
        "status": str(status or "").strip() or "done",
        "detail": str(detail or "").strip(),
        "evidence": evidence or {},
    }


def _append_evidence_timeline(
    payload: dict[str, Any],
    evidence_path: Path,
    stage: str,
    status: str = "done",
    detail: str = "",
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload.setdefault("timeline", [])
    event = _timeline_event(stage, status=status, detail=detail, evidence=evidence)
    if isinstance(payload["timeline"], list):
        payload["timeline"].append(event)
    payload.setdefault("generated_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    payload.setdefault("evidence_path", str(evidence_path))
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return event


def _extract_timeline(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = [
        evidence.get("timeline"),
        ((evidence.get("run") or {}).get("evidence") or {}).get("timeline") if isinstance(evidence.get("run"), dict) else None,
    ]
    for value in candidates:
        if isinstance(value, list):
            rows = [dict(row) for row in value if isinstance(row, dict)]
            if rows:
                return rows
    return []


def _extract_evidence_panel_summary(evidence: dict[str, Any]) -> dict[str, Any]:
    apps: list[str] = []
    screenshots: list[str] = []
    files: list[str] = []
    recipients: list[str] = []
    validations: list[dict[str, Any]] = []

    def add_app(row: Any) -> None:
        if isinstance(row, dict):
            app = row.get("app") or row.get("app_key") or row.get("active_title") or row.get("title")
            if app:
                apps.append(str(app))

    add_app(evidence.get("codex_open"))
    add_app(evidence.get("open_result"))
    send_ev = (evidence.get("send_result") or {}).get("evidence") if isinstance(evidence.get("send_result"), dict) else {}
    if isinstance(send_ev, dict):
        add_app(send_ev.get("open_result", {}).get("evidence") if isinstance(send_ev.get("open_result"), dict) else send_ev.get("open_result"))
        recipients.extend(str(x) for x in (send_ev.get("recipients") or []) if str(x).strip())
    recipients.extend(str(x) for x in (evidence.get("recipients") or []) if str(x).strip())

    for key in ("validation", "copied_validation", "ocr_validation"):
        if isinstance(evidence.get(key), dict):
            validations.append({"name": key, **dict(evidence[key])})

    for path in _collect_evidence_paths(evidence):
        lower = path.lower()
        if lower.endswith((".png", ".jpg", ".jpeg")):
            screenshots.append(path)
        elif lower.endswith((".md", ".json", ".html")) or "\\" in path or "/" in path:
            files.append(path)

    codex_output = str(evidence.get("message_text") or evidence.get("summary") or evidence.get("message_preview") or "")
    if not codex_output and isinstance(send_ev, dict):
        codex_output = str(send_ev.get("message") or "")

    def uniq(items: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for item in items:
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out

    return {
        "apps": uniq(apps),
        "files": uniq(files)[:80],
        "screenshots": uniq(screenshots)[:80],
        "recipients": uniq(recipients),
        "validations": validations,
        "codex_output": codex_output,
        "timeline": _extract_timeline(evidence),
    }


def _write_evidence_panel(
    out_dir: Path,
    *,
    title: str,
    task: str,
    ok: bool,
    detail: str,
    evidence: dict[str, Any],
) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    panel_path = out_dir / f"jachin_evidence_panel_{_safe_label(task)}_{now_tag()}.html"
    summary = _extract_evidence_panel_summary(evidence)
    status = "PASS" if ok else "NEEDS ATTENTION"

    def esc(value: Any) -> str:
        return html.escape(str(value or ""), quote=True)

    def list_items(items: list[str]) -> str:
        if not items:
            return "<li class=\"muted\">None captured</li>"
        return "\n".join(f"<li><code>{esc(item)}</code></li>" for item in items)

    screenshot_cards = []
    for path in summary["screenshots"][:24]:
        src = Path(path).resolve().as_uri() if Path(path).exists() else path
        screenshot_cards.append(
            "<figure><img src=\""
            + esc(src)
            + "\" alt=\"screenshot\"><figcaption>"
            + esc(path)
            + "</figcaption></figure>"
        )
    validation_cards = []
    for row in summary["validations"]:
        checks = row.get("checks") if isinstance(row.get("checks"), dict) else {}
        checks_html = "".join(
            f"<span class=\"pill {'ok' if value else 'bad'}\">{esc(key)}: {esc(value)}</span>"
            for key, value in checks.items()
        )
        validation_cards.append(
            f"<div class=\"validation\"><strong>{esc(row.get('name'))}</strong> "
            f"<span class=\"pill {'ok' if row.get('ok') else 'bad'}\">ok={esc(row.get('ok'))}</span>"
            f"{checks_html}</div>"
        )
    timeline_rows = []
    for row in summary.get("timeline", [])[:80]:
        ev = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        ev_paths = _collect_evidence_paths(ev)[:4] if ev else []
        ev_html = "".join(f"<li><code>{esc(path)}</code></li>" for path in ev_paths)
        timeline_rows.append(
            "<li class=\"timeline-row\">"
            f"<div><strong>{esc(row.get('stage'))}</strong> "
            f"<span class=\"pill {'ok' if row.get('status') == 'done' else ('bad' if row.get('status') == 'failed' else '')}\">{esc(row.get('status'))}</span>"
            f"<span class=\"muted\">{esc(row.get('ts'))}</span></div>"
            f"<p>{esc(row.get('detail'))}</p>"
            + (f"<ul>{ev_html}</ul>" if ev_html else "")
            + "</li>"
        )

    html_text = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)}</title>
  <style>
    body {{ margin: 0; font-family: "Segoe UI", Arial, sans-serif; color: #17202a; background: #f6f7f9; }}
    header {{ padding: 24px 32px; background: #111827; color: white; }}
    main {{ padding: 24px 32px; display: grid; gap: 18px; }}
    h1 {{ margin: 0 0 8px; font-size: 24px; }}
    h2 {{ margin: 0 0 12px; font-size: 17px; }}
    section {{ background: white; border: 1px solid #e5e7eb; border-radius: 8px; padding: 18px; }}
    code, pre {{ font-family: Consolas, "Courier New", monospace; }}
    pre {{ white-space: pre-wrap; word-break: break-word; max-height: 360px; overflow: auto; background: #f3f4f6; padding: 12px; border-radius: 6px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; }}
    .pill {{ display: inline-block; margin: 4px 6px 4px 0; padding: 4px 8px; border-radius: 999px; background: #e5e7eb; font-size: 12px; }}
    .pill.ok {{ background: #dcfce7; color: #166534; }}
    .pill.bad {{ background: #fee2e2; color: #991b1b; }}
    .muted {{ color: #6b7280; }}
    figure {{ margin: 0; border: 1px solid #e5e7eb; border-radius: 8px; overflow: hidden; background: #f9fafb; }}
    img {{ display: block; max-width: 100%; height: auto; }}
    figcaption {{ padding: 8px; font-size: 12px; color: #4b5563; word-break: break-all; }}
    ul {{ margin: 0; padding-left: 20px; }}
    .timeline {{ list-style: none; padding-left: 0; display: grid; gap: 10px; }}
    .timeline-row {{ border-left: 3px solid #38bdf8; background: #f9fafb; padding: 10px 12px; border-radius: 6px; }}
    .timeline-row p {{ margin: 6px 0; color: #4b5563; }}
  </style>
</head>
<body>
  <header>
    <h1>{esc(title)}</h1>
    <div><span class="pill {'ok' if ok else 'bad'}">{status}</span> task=<code>{esc(task)}</code> detail=<code>{esc(detail)}</code></div>
  </header>
  <main>
    <section>
      <h2>Execution Summary</h2>
      <div class="grid">
        <div><strong>Opened Apps</strong><ul>{list_items(summary["apps"])}</ul></div>
        <div><strong>Recipients</strong><ul>{list_items(summary["recipients"])}</ul></div>
        <div><strong>Evidence Files</strong><ul>{list_items(summary["files"][:24])}</ul></div>
      </div>
    </section>
    <section>
      <h2>Codex / Message Output</h2>
      <pre>{esc(summary["codex_output"][:6000])}</pre>
    </section>
    <section>
      <h2>Validation</h2>
      {''.join(validation_cards) or '<p class="muted">No validation rows captured.</p>'}
    </section>
    <section>
      <h2>Execution Timeline</h2>
      <ol class="timeline">{''.join(timeline_rows) or '<li class="muted">No timeline captured.</li>'}</ol>
    </section>
    <section>
      <h2>Screenshots / OCR Evidence</h2>
      <div class="grid">{''.join(screenshot_cards) or '<p class="muted">No screenshots captured.</p>'}</div>
    </section>
  </main>
</body>
</html>
"""
    panel_path.write_text(html_text, encoding="utf-8")
    return str(panel_path)


def _title_matches_table(title: str, table_name: str) -> bool:
    title_key = _compact_match_text(title)
    name_key = _compact_match_text(table_name)
    if name_key and name_key in title_key:
        return True
    tokens = [t for t in re.findall(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]{2,}", str(table_name or "")) if len(t) >= 2]
    if not tokens:
        return False
    hits = sum(1 for token in tokens if _compact_match_text(token) in title_key)
    return hits >= min(2, len(tokens))


def _table_focus_keywords(table_name: str) -> tuple[str, ...]:
    raw = str(table_name or "").strip()
    tokens = [t for t in re.findall(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]{2,}", raw) if len(t) >= 2]
    out = [raw, re.sub(r"\s+", "", raw), *tokens]
    seen: set[str] = set()
    uniq: list[str] = []
    for item in out:
        key = _compact_match_text(item)
        if not key or key in seen:
            continue
        seen.add(key)
        uniq.append(item)
    return tuple(uniq)


def _line_overlap_ratio(previous: list[str], current: list[str]) -> float:
    prev = set(previous)
    cur = set(current)
    if not prev or not cur:
        return 0.0
    return len(prev & cur) / max(1, min(len(prev), len(cur)))


def _classify_lark_lines(lines: list[str]) -> dict[str, list[str]]:
    urgent_keywords = ("紧急", "尽快", "马上", "今天", "截止", "阻塞", "失败", "异常", "风险", "urgent", "asap", "blocked", "fail")
    mention_keywords = ("@我", "@", "我", "你")
    task_keywords = ("帮", "处理", "确认", "跟进", "修复", "整理", "发", "同步", "安排", "todo", "fix")
    return {
        "urgent_lines": [ln for ln in lines if any(k.lower() in ln.lower() for k in urgent_keywords)],
        "mention_like_lines": [ln for ln in lines if any(k.lower() in ln.lower() for k in mention_keywords)],
        "task_like_lines": [ln for ln in lines if any(k.lower() in ln.lower() for k in task_keywords)],
    }


def _group_lark_lines_by_time(lines: list[str]) -> list[dict[str, Any]]:
    time_re = re.compile(r"^(?:\d{1,2}:\d{2}|昨天|前天|今天|周[一二三四五六日天]|\d{1,2}月\d{1,2}日|\d{4}-\d{1,2}-\d{1,2})$")
    groups: list[dict[str, Any]] = []
    current: dict[str, Any] = {"time": "unknown", "lines": []}
    for line in lines:
        if time_re.match(line):
            if current["lines"]:
                groups.append(current)
            current = {"time": line, "lines": []}
            continue
        current["lines"].append(line)
    if current["lines"]:
        groups.append(current)
    return groups


def _history_window_labels(days: int, today: date | None = None) -> list[str]:
    base = today or date.today()
    span = max(1, int(days or 1))
    labels: list[str] = []
    for offset in range(span):
        d = base - timedelta(days=offset)
        if offset == 0:
            labels.append("今天")
        elif offset == 1:
            labels.append("昨天")
        elif offset == 2:
            labels.append("前天")
        labels.append(f"{d.month}月{d.day}日")
        labels.append(d.isoformat())
    return labels


def _build_lark_history_summary(lines: list[str], days: int) -> dict[str, Any]:
    labels = _history_window_labels(days)
    label_set = {x.lower() for x in labels}
    timeline = _group_lark_lines_by_time(lines)
    in_window_groups: list[dict[str, Any]] = []
    for group in timeline:
        t = str(group.get("time") or "")
        if t == "unknown" or t.lower() in label_set or re.fullmatch(r"\d{1,2}:\d{2}", t):
            in_window_groups.append(group)
    classified = _classify_lark_lines(lines)
    message_like = [
        ln
        for ln in lines
        if not re.fullmatch(r"\d{1,2}:\d{2}", ln)
        and ln not in {"消息", "未读", "云文档", "多维表格", "工作台", "通讯录", "审批", "日历", "群组"}
    ]
    return {
        "requested_days": max(1, int(days or 1)),
        "date_labels": labels,
        "timeline_groups": timeline,
        "in_window_groups": in_window_groups,
        "message_like_lines": message_like[:120],
        **classified,
    }


def _import_pyautogui():
    import pyautogui  # type: ignore

    pyautogui.FAILSAFE = True
    return pyautogui


class MouseFailSafeInterrupt(RuntimeError):
    """Raised when the pointer is in PyAutoGUI's fail-safe corner."""

    def __init__(
        self,
        action: str = "pyautogui_action",
        position: tuple[int, int] | None = None,
        screen: tuple[int, int] | None = None,
        margin: int = 8,
    ) -> None:
        super().__init__("mouse_failsafe_triggered")
        self.action = action
        self.position = position
        self.screen = screen
        self.margin = margin

    def to_evidence(self) -> dict[str, Any]:
        return {
            "detail": "mouse_failsafe_triggered",
            "action": self.action,
            "position": {"x": self.position[0], "y": self.position[1]} if self.position else {},
            "screen": {"width": self.screen[0], "height": self.screen[1]} if self.screen else {},
            "margin": self.margin,
        }


def _import_uia():
    try:
        import uiautomation as auto  # type: ignore

        return auto, ""
    except Exception as e:  # pragma: no cover - depends on host env
        return None, f"uiautomation_not_available:{e!r}"


def _safe_eval_arithmetic(expr: str) -> str:
    """Evaluate a small arithmetic expression used by Calculator smoke tests."""
    allowed: dict[type[ast.AST], Callable[..., Any]] = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
        ast.USub: operator.neg,
        ast.UAdd: operator.pos,
    }

    def walk(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in allowed:
            return allowed[type(node.op)](walk(node.left), walk(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in allowed:
            return allowed[type(node.op)](walk(node.operand))
        raise ValueError(f"unsupported expression: {expr!r}")

    value = walk(ast.parse(expr, mode="eval"))
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


class WindowTools:
    def __init__(self) -> None:
        self.enabled = sys.platform == "win32"

    def active_title(self) -> str:
        if not self.enabled:
            return ""
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        length = user32.GetWindowTextLengthW(hwnd) + 1
        buf = ctypes.create_unicode_buffer(length)
        user32.GetWindowTextW(hwnd, buf, length)
        return (buf.value or "").strip()

    def active_rect(self) -> tuple[str, int, int, int, int] | None:
        if not self.enabled:
            return None
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        length = user32.GetWindowTextLengthW(hwnd) + 1
        buf = ctypes.create_unicode_buffer(length)
        user32.GetWindowTextW(hwnd, buf, length)
        rect = ctypes.wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        left = int(rect.left)
        top = int(rect.top)
        width = max(0, int(rect.right - rect.left))
        height = max(0, int(rect.bottom - rect.top))
        if width <= 10 or height <= 10:
            return None
        return ((buf.value or "").strip(), left, top, width, height)

    def active_snapshot(self) -> dict[str, Any]:
        if not self.enabled:
            return {}
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return {}

        length = user32.GetWindowTextLengthW(hwnd) + 1
        buf = ctypes.create_unicode_buffer(length)
        user32.GetWindowTextW(hwnd, buf, length)
        title = (buf.value or "").strip()

        rect_data: dict[str, int] = {}
        rect = ctypes.wintypes.RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            left = int(rect.left)
            top = int(rect.top)
            rect_data = {
                "left": left,
                "top": top,
                "width": max(0, int(rect.right - rect.left)),
                "height": max(0, int(rect.bottom - rect.top)),
            }

        pid_i = 0
        try:
            pid = ctypes.wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pid_i = int(pid.value)
        except Exception:
            pid_i = 0

        proc = _parse_tasklist().get(pid_i, {})
        return {
            "hwnd": int(hwnd),
            "pid": pid_i,
            "process": proc.get("image_name") or "",
            "title": title,
            "rect": rect_data,
        }

    def list_windows(self, limit: int = 80) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        user32 = ctypes.windll.user32
        pid_map = _parse_tasklist()
        windows: list[dict[str, Any]] = []
        active_hwnd = user32.GetForegroundWindow()

        def callback(hwnd: int, _lparam: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd) + 1
            if length <= 1:
                return True
            buf = ctypes.create_unicode_buffer(length)
            user32.GetWindowTextW(hwnd, buf, length)
            title = (buf.value or "").strip()
            if not title:
                return True
            rect = ctypes.wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True
            left = int(rect.left)
            top = int(rect.top)
            width = max(0, int(rect.right - rect.left))
            height = max(0, int(rect.bottom - rect.top))
            if width <= 20 or height <= 20:
                return True
            pid = ctypes.wintypes.DWORD()
            try:
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                pid_i = int(pid.value)
            except Exception:
                pid_i = 0
            proc = pid_map.get(pid_i, {})
            title_l = title.lower()
            image_l = str(proc.get("image_name") or "").lower()
            office_kind = "other"
            if any(k in title_l or k in image_l for k in ("lark", "feishu", "飞书")):
                office_kind = "lark"
            elif any(k in title_l or k in image_l for k in ("edge", "chrome", "firefox", "browser", "larksuite", "feishu.cn")):
                office_kind = "browser"
            elif any(k in title_l or k in image_l for k in ("explorer", "文件资源管理器")):
                office_kind = "explorer"
            elif any(k in title_l or k in image_l for k in ("word", "excel", "powerpoint", "wps", ".doc", ".xls", ".ppt", ".pdf", "notepad")):
                office_kind = "document"
            elif any(k in title_l or k in image_l for k in ("powershell", "cmd", "terminal", "code", "cursor", "pycharm")):
                office_kind = "terminal_or_ide"
            windows.append(
                {
                    "hwnd": int(hwnd),
                    "pid": pid_i,
                    "process": proc.get("image_name") or "",
                    "title": title,
                    "rect": {"left": left, "top": top, "width": width, "height": height},
                    "area": width * height,
                    "active": int(hwnd) == int(active_hwnd),
                    "office_kind": office_kind,
                    "office_related": office_kind != "other",
                }
            )
            return len(windows) < max(1, int(limit or 80))

        enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        user32.EnumWindows(enum_proc(callback), 0)
        windows.sort(key=lambda row: (not row.get("active"), not row.get("office_related"), -int(row.get("area") or 0)))
        return windows[: max(1, int(limit or 80))]

    def find_window(self, keywords: tuple[str, ...], exclude_keywords: tuple[str, ...] = ()) -> tuple[int, str, int, int, int, int] | None:
        if not self.enabled:
            return None
        user32 = ctypes.windll.user32
        keys = [k.lower() for k in keywords if k]
        excludes = [k.lower() for k in exclude_keywords if k]
        matches: list[tuple[int, str, int, int, int, int, int]] = []

        def callback(hwnd: int, _lparam: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd) + 1
            if length <= 1:
                return True
            buf = ctypes.create_unicode_buffer(length)
            user32.GetWindowTextW(hwnd, buf, length)
            title = (buf.value or "").strip()
            if not title or (keys and not any(k in title.lower() for k in keys)):
                return True
            if excludes and any(k in title.lower() for k in excludes):
                return True
            rect = ctypes.wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True
            left = int(rect.left)
            top = int(rect.top)
            width = max(0, int(rect.right - rect.left))
            height = max(0, int(rect.bottom - rect.top))
            matches.append((hwnd, title, left, top, width, height, width * height))
            return True

        enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        user32.EnumWindows(enum_proc(callback), 0)
        if not matches:
            return None
        hwnd, title, left, top, width, height, _area = max(matches, key=lambda row: row[6])
        logger.info("[window] found title=%r rect=(%d,%d,%d,%d)", title, left, top, width, height)
        return hwnd, title, left, top, width, height

    def focus_by_keywords(self, keywords: tuple[str, ...], timeout: float = 8.0, exclude_keywords: tuple[str, ...] = ()) -> bool:
        if not self.enabled:
            return False
        deadline = time.time() + timeout
        while time.time() < deadline:
            match = self.find_window(keywords, exclude_keywords=exclude_keywords)
            if match:
                hwnd, title, left, top, width, height = match
                user32 = ctypes.windll.user32
                user32.ShowWindow(hwnd, 5)
                user32.SetForegroundWindow(hwnd)
                try:
                    pyautogui = _import_pyautogui()
                    pyautogui.click(x=int(left + width / 2), y=int(top + min(24, max(10, height / 12))))
                except Exception as e:
                    logger.debug("[window] focus click skipped: %r", e)
                time.sleep(0.25)
                logger.info("[window] focus requested title=%r active=%r", title, self.active_title())
                return True
            time.sleep(0.2)
        return False


class DesktopIO:
    def __init__(self, win: WindowTools) -> None:
        self.win = win
        self.pyautogui = _import_pyautogui()

    def _safe_mouse(self, action: str = "pyautogui_action", margin: int = 8) -> None:
        try:
            x, y = self.pyautogui.position()
            w, h = self.pyautogui.size()
        except Exception as exc:
            if type(exc).__name__ == "FailSafeException" or "fail-safe" in str(exc).lower():
                raise MouseFailSafeInterrupt(action=action) from exc
            return
        if x <= margin or y <= margin or x >= w - margin or y >= h - margin:
            raise MouseFailSafeInterrupt(action=action, position=(int(x), int(y)), screen=(int(w), int(h)), margin=margin)
    def launch_result(self, exe: str, keywords: tuple[str, ...], args: list[str] | None = None, wait: float = 1.2) -> dict[str, Any]:
        argv = [exe] + list(args or [])
        logger.info("[act] launch argv=%s", argv)
        try:
            if Path(exe).suffix.lower() == ".lnk":
                os.startfile(exe)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError as exc:
            logger.warning("[act] launch file not found argv=%s error=%r", argv, exc)
            return {
                "ok": False,
                "detail": "app_executable_not_found",
                "exe": exe,
                "argv": argv,
                "error_type": type(exc).__name__,
                "error": repr(exc),
            }
        except OSError as exc:
            logger.warning("[act] launch failed argv=%s error=%r", argv, exc)
            return {
                "ok": False,
                "detail": "app_launch_failed",
                "exe": exe,
                "argv": argv,
                "error_type": type(exc).__name__,
                "error": repr(exc),
            }
        time.sleep(wait)
        focused = self.win.focus_by_keywords(keywords, timeout=5.0)
        logger.info("[act] launched=%s focused=%s active=%r", exe, focused, self.win.active_title())
        return {"ok": True, "detail": "launch_invoked", "exe": exe, "argv": argv, "focused": bool(focused)}

    def launch(self, exe: str, keywords: tuple[str, ...], args: list[str] | None = None, wait: float = 1.2) -> bool:
        return bool(self.launch_result(exe, keywords, args=args, wait=wait).get("focused"))

    def hotkey(self, *keys: str, wait: float = 0.2) -> None:
        logger.info("[act] hotkey=%s", "+".join(keys))
        self._safe_mouse("hotkey")
        self.pyautogui.hotkey(*keys)
        time.sleep(wait)

    def press(self, key: str, presses: int = 1, wait: float = 0.15) -> None:
        logger.info("[act] press=%s x%s", key, presses)
        self._safe_mouse("press")
        self.pyautogui.press(key, presses=presses)
        time.sleep(wait)

    def write(self, text: str, interval: float = 0.02, wait: float = 0.2) -> None:
        logger.info("[act] write len=%d", len(text))
        self._safe_mouse("write")
        self.pyautogui.write(text, interval=interval)
        time.sleep(wait)

    def paste(self, text: str, wait: float = 0.2) -> None:
        import pyperclip  # type: ignore

        logger.info("[act] paste len=%d", len(text))
        pyperclip.copy(text)
        self._safe_mouse("paste")
        self.pyautogui.hotkey("ctrl", "v")
        time.sleep(wait)

    def click(self, x: int, y: int, wait: float = 0.2) -> None:
        logger.info("[act] click x=%s y=%s", x, y)
        self._safe_mouse("click")
        self.pyautogui.click(x=int(x), y=int(y))
        time.sleep(wait)

    def move_to(self, x: int, y: int, wait: float = 0.05) -> None:
        logger.info("[act] move_to x=%s y=%s", x, y)
        self._safe_mouse("move_to")
        self.pyautogui.moveTo(int(x), int(y), duration=0.05)
        time.sleep(wait)

    def drag_to(self, x: int, y: int, duration: float = 0.25, wait: float = 0.2) -> None:
        logger.info("[act] drag_to x=%s y=%s duration=%.2f", x, y, duration)
        self._safe_mouse("drag_to")
        self.pyautogui.dragTo(int(x), int(y), duration=float(duration), button="left")
        time.sleep(wait)

    def scroll(self, clicks: int, wait: float = 0.2) -> None:
        logger.info("[act] scroll clicks=%s", clicks)
        self._safe_mouse("scroll")
        self.pyautogui.scroll(int(clicks))
        time.sleep(wait)

    def screenshot(self, out_dir: Path, label: str) -> str:
        out_dir.mkdir(parents=True, exist_ok=True)
        self._safe_mouse("screenshot")
        img = self.pyautogui.screenshot()
        path = out_dir / f"{now_tag()}_{label}.png"
        img.save(path)
        logger.info("[observe] %s screenshot=%s active=%r", label, path, self.win.active_title())
        return str(path)

    def screenshot_active_window(self, out_dir: Path, label: str) -> str:
        out_dir.mkdir(parents=True, exist_ok=True)
        self._safe_mouse("screenshot_active_window")
        rect = self.win.active_rect()
        if rect:
            title, left, top, width, height = rect
            img = self.pyautogui.screenshot(region=(left, top, width, height))
            logger.info("[observe] %s active_window title=%r rect=(%d,%d,%d,%d)", label, title, left, top, width, height)
        else:
            img = self.pyautogui.screenshot()
        path = out_dir / f"{now_tag()}_{label}.png"
        img.save(path)
        logger.info("[observe] %s screenshot=%s active=%r", label, path, self.win.active_title())
        return str(path)


class EnvironmentVerifier:
    def __init__(self, win: WindowTools) -> None:
        self.win = win

    def verify(self, contract: ExecutionContract, stage: str = "", action: str = "") -> EnvironmentVerification:
        checks: dict[str, Any] = {}
        try:
            active = self.win.active_snapshot()
        except AttributeError as exc:
            title = ""
            try:
                title = self.win.active_title()
            except Exception:
                title = ""
            active = {"title": title, "process": ""}
            checks["active_snapshot_error"] = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            active = {}
            checks["active_snapshot_error"] = f"{type(exc).__name__}: {exc}"
        title = str(active.get("title") or "")
        process = str(active.get("process") or "")
        title_l = title.lower()
        process_l = process.lower()
        keywords = tuple(k.lower() for k in contract.expected_keywords if k)
        processes = tuple(p.lower() for p in contract.expected_processes if p)
        title_ok = bool(keywords and any(k in title_l for k in keywords))
        process_ok = bool(processes and any(process_l == p or p.replace(".exe", "") in process_l for p in processes))
        ok = bool((title_ok or process_ok) if contract.require_foreground else True)
        if ok:
            detail = "environment_verified"
        elif title or process:
            detail = "wrong_foreground_app"
        else:
            detail = "foreground_app_unknown"
        checks.update(
            {
                "title_ok": title_ok,
                "process_ok": process_ok,
                "expected_keywords": list(keywords),
                "expected_processes": list(processes),
            }
        )
        return EnvironmentVerification(ok=ok, detail=detail, contract=contract, active=active, checks=checks, stage=stage, action=action)


class DesktopIO:
    def __init__(self, win: WindowTools) -> None:
        self.win = win
        self.pyautogui = _import_pyautogui()

    def _safe_mouse(self, action: str = "pyautogui_action", margin: int = 8) -> None:
        try:
            x, y = self.pyautogui.position()
            w, h = self.pyautogui.size()
        except Exception as exc:
            if type(exc).__name__ == "FailSafeException" or "fail-safe" in str(exc).lower():
                raise MouseFailSafeInterrupt(action=action) from exc
            return
        if x <= margin or y <= margin or x >= w - margin or y >= h - margin:
            raise MouseFailSafeInterrupt(action=action, position=(int(x), int(y)), screen=(int(w), int(h)), margin=margin)
    def launch_result(self, exe: str, keywords: tuple[str, ...], args: list[str] | None = None, wait: float = 1.2) -> dict[str, Any]:
        argv = [exe] + list(args or [])
        logger.info("[act] launch argv=%s", argv)
        try:
            if Path(exe).suffix.lower() == ".lnk":
                os.startfile(exe)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError as exc:
            logger.warning("[act] launch file not found argv=%s error=%r", argv, exc)
            return {
                "ok": False,
                "detail": "app_executable_not_found",
                "exe": exe,
                "argv": argv,
                "error_type": type(exc).__name__,
                "error": repr(exc),
            }
        except OSError as exc:
            logger.warning("[act] launch failed argv=%s error=%r", argv, exc)
            return {
                "ok": False,
                "detail": "app_launch_failed",
                "exe": exe,
                "argv": argv,
                "error_type": type(exc).__name__,
                "error": repr(exc),
            }
        time.sleep(wait)
        focused = self.win.focus_by_keywords(keywords, timeout=5.0)
        logger.info("[act] launched=%s focused=%s active=%r", exe, focused, self.win.active_title())
        return {"ok": True, "detail": "launch_invoked", "exe": exe, "argv": argv, "focused": bool(focused)}

    def launch(self, exe: str, keywords: tuple[str, ...], args: list[str] | None = None, wait: float = 1.2) -> bool:
        return bool(self.launch_result(exe, keywords, args=args, wait=wait).get("focused"))

    def hotkey(self, *keys: str, wait: float = 0.2) -> None:
        logger.info("[act] hotkey=%s", "+".join(keys))
        self._safe_mouse("hotkey")
        self.pyautogui.hotkey(*keys)
        time.sleep(wait)

    def press(self, key: str, presses: int = 1, wait: float = 0.15) -> None:
        logger.info("[act] press=%s x%s", key, presses)
        self._safe_mouse("press")
        self.pyautogui.press(key, presses=presses)
        time.sleep(wait)

    def write(self, text: str, interval: float = 0.02, wait: float = 0.2) -> None:
        logger.info("[act] write len=%d", len(text))
        self._safe_mouse("write")
        self.pyautogui.write(text, interval=interval)
        time.sleep(wait)

    def paste(self, text: str, wait: float = 0.2) -> None:
        import pyperclip  # type: ignore

        logger.info("[act] paste len=%d", len(text))
        pyperclip.copy(text)
        self._safe_mouse("paste")
        self.pyautogui.hotkey("ctrl", "v")
        time.sleep(wait)

    def click(self, x: int, y: int, wait: float = 0.2) -> None:
        logger.info("[act] click x=%s y=%s", x, y)
        self._safe_mouse("click")
        self.pyautogui.click(x=int(x), y=int(y))
        time.sleep(wait)

    def move_to(self, x: int, y: int, wait: float = 0.05) -> None:
        logger.info("[act] move_to x=%s y=%s", x, y)
        self._safe_mouse("move_to")
        self.pyautogui.moveTo(int(x), int(y), duration=0.05)
        time.sleep(wait)

    def drag_to(self, x: int, y: int, duration: float = 0.25, wait: float = 0.2) -> None:
        logger.info("[act] drag_to x=%s y=%s duration=%.2f", x, y, duration)
        self._safe_mouse("drag_to")
        self.pyautogui.dragTo(int(x), int(y), duration=float(duration), button="left")
        time.sleep(wait)

    def scroll(self, clicks: int, wait: float = 0.2) -> None:
        logger.info("[act] scroll clicks=%s", clicks)
        self._safe_mouse("scroll")
        self.pyautogui.scroll(int(clicks))
        time.sleep(wait)

    def screenshot(self, out_dir: Path, label: str) -> str:
        out_dir.mkdir(parents=True, exist_ok=True)
        self._safe_mouse("screenshot")
        img = self.pyautogui.screenshot()
        path = out_dir / f"{now_tag()}_{label}.png"
        img.save(path)
        logger.info("[observe] %s screenshot=%s active=%r", label, path, self.win.active_title())
        return str(path)

    def screenshot_active_window(self, out_dir: Path, label: str) -> str:
        out_dir.mkdir(parents=True, exist_ok=True)
        self._safe_mouse("screenshot_active_window")
        rect = self.win.active_rect()
        if rect:
            title, left, top, width, height = rect
            img = self.pyautogui.screenshot(region=(left, top, width, height))
            logger.info("[observe] %s active_window title=%r rect=(%d,%d,%d,%d)", label, title, left, top, width, height)
        else:
            img = self.pyautogui.screenshot()
        path = out_dir / f"{now_tag()}_{label}.png"
        img.save(path)
        logger.info("[observe] %s screenshot=%s active=%r", label, path, self.win.active_title())
        return str(path)


class WindowsOSAutomation:
    def __init__(self, out_dir: str | Path | None = None) -> None:
        self.out_dir = Path(out_dir or Path.cwd() / "output" / "os_vision").resolve()
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.win = WindowTools()
        self.env = EnvironmentVerifier(self.win)
        self.io = DesktopIO(self.win)

    def _execution_contract(self, app_name: str, goal: str = "") -> ExecutionContract:
        return _app_contract(app_name, goal=goal)

    def _verify_environment(self, contract: ExecutionContract, stage: str = "", action: str = "") -> EnvironmentVerification:
        row = self.env.verify(contract, stage=stage, action=action)
        logger.info(
            "[env_guard] stage=%s action=%s target=%s ok=%s detail=%s active_title=%r process=%r",
            stage,
            action,
            contract.app_key,
            row.ok,
            row.detail,
            row.active.get("title"),
            row.active.get("process"),
        )
        return row

    def _unsafe_environment_result(self, task: str, contract: ExecutionContract, guard: EnvironmentVerification, evidence: dict[str, Any] | None = None) -> TaskResult:
        payload = dict(evidence or {})
        payload["execution_contract"] = contract.to_dict()
        payload["environment_guard"] = guard.to_dict()
        return TaskResult(task, False, guard.detail if guard.detail != "environment_verified" else "unsafe_environment", payload)

    def _recover_environment_if_needed(
        self,
        contract: ExecutionContract,
        guard: EnvironmentVerification,
        *,
        stage: str,
        action: str,
        launch_if_missing: bool = False,
        timeout: float = 3.0,
        max_attempts: int = 2,
    ) -> tuple[EnvironmentVerification, dict[str, Any] | None]:
        """Bring the target app back before treating foreground loss as fatal."""
        if guard.ok or guard.detail not in {"wrong_foreground_app", "foreground_app_unknown"}:
            return guard, None
        focus_result = self.focus_or_raise_app(
            contract.app_key,
            timeout=timeout,
            max_attempts=max_attempts,
            launch_if_missing=launch_if_missing,
            stage=f"{stage}_focus_recovery",
        )
        recovered = self._verify_environment(contract, stage=stage, action=f"{action}_after_focus_recovery")
        recovery = {
            "reason": guard.detail,
            "initial_guard": guard.to_dict(),
            "focus_result": asdict(focus_result),
            "recovered_guard": recovered.to_dict(),
            "ok": bool(recovered.ok),
        }
        return recovered, recovery

    def active_window(self) -> TaskResult:
        rect = self.win.active_rect()
        screenshot = self.io.screenshot_active_window(self.out_dir, "active_window")
        return TaskResult(
            "windows_active_window",
            bool(rect or self.win.active_title()),
            "active_window_captured",
            {"active_title": self.win.active_title(), "active_rect": rect, "screenshot": screenshot},
        )

    def window_list(self, limit: int = 80) -> TaskResult:
        windows = self.win.list_windows(limit=limit)
        active = next((w for w in windows if w.get("active")), None)
        return TaskResult(
            "windows_window_list",
            True,
            "windows_enumerated",
            {
                "count": len(windows),
                "active": active,
                "office_windows": [w for w in windows if w.get("office_related")],
                "windows": windows,
            },
        )

    def window_switch(self, keywords: str, exclude_keywords: str = "", timeout: float = 5.0) -> TaskResult:
        keys = tuple(k.strip() for k in re.split(r"[,锛寍]", str(keywords or "")) if k.strip())
        excludes = tuple(k.strip() for k in re.split(r"[,锛寍]", str(exclude_keywords or "")) if k.strip())
        if not keys:
            return TaskResult("windows_window_switch", False, "keywords_empty", {})
        before = self.win.active_title()
        focused = self.win.focus_by_keywords(keys, timeout=float(timeout or 5.0), exclude_keywords=excludes)
        after = self.win.active_title()
        screenshot = self.io.screenshot_active_window(self.out_dir, f"window_switch_{_safe_label('_'.join(keys))}")
        return TaskResult(
            "windows_window_switch",
            focused,
            "window_focused" if focused else "window_not_found",
            {"keywords": list(keys), "exclude_keywords": list(excludes), "before": before, "after": after, "screenshot": screenshot},
        )

    def window_close(self, keywords: str, exclude_keywords: str = "", timeout: float = 5.0) -> TaskResult:
        keys = tuple(k.strip() for k in re.split(r"[,閿涘瘝]", str(keywords or "")) if k.strip())
        excludes = tuple(k.strip() for k in re.split(r"[,閿涘瘝]", str(exclude_keywords or "")) if k.strip())
        if not keys:
            return TaskResult("windows_window_close", False, "keywords_empty", {})
        before = self.win.active_title()
        match = self.win.find_window(keys, exclude_keywords=excludes)
        if not match:
            return TaskResult(
                "windows_window_close",
                False,
                "window_not_found",
                {"keywords": list(keys), "exclude_keywords": list(excludes), "before": before},
            )
        hwnd, title, left, top, width, height = match
        close_sent = False
        if self.win.enabled:
            try:
                user32 = ctypes.windll.user32
                user32.ShowWindow(hwnd, 5)
                user32.SetForegroundWindow(hwnd)
                time.sleep(0.15)
                close_sent = bool(user32.PostMessageW(hwnd, 0x0010, 0, 0))
            except Exception as e:
                logger.warning("[window] close post failed title=%r err=%r", title, e)
        deadline = time.time() + float(timeout or 5.0)
        still_exists = True
        while time.time() < deadline:
            if not self.win.find_window(keys, exclude_keywords=excludes):
                still_exists = False
                break
            time.sleep(0.2)
        after = self.win.active_title()
        screenshot = self.io.screenshot_active_window(self.out_dir, f"window_close_{_safe_label('_'.join(keys))}")
        return TaskResult(
            "windows_window_close",
            close_sent and not still_exists,
            "window_closed" if close_sent and not still_exists else "window_close_unverified",
            {
                "keywords": list(keys),
                "exclude_keywords": list(excludes),
                "before": before,
                "target_title": title,
                "target_hwnd": int(hwnd),
                "target_rect": {"left": left, "top": top, "width": width, "height": height},
                "close_sent": close_sent,
                "still_exists": still_exists,
                "after": after,
                "screenshot": screenshot,
            },
        )

    def disk_snapshot(self) -> TaskResult:
        drives: list[dict[str, Any]] = []
        if sys.platform == "win32":
            for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                root = Path(f"{letter}:\\")
                if not root.exists():
                    continue
                try:
                    usage = shutil.disk_usage(str(root))
                except Exception:
                    continue
                drives.append(
                    {
                        "drive": str(root),
                        "total": usage.total,
                        "used": usage.used,
                        "free": usage.free,
                        "free_gb": round(usage.free / (1024 ** 3), 2),
                        "used_pct": round((usage.used / usage.total) * 100, 2) if usage.total else 0,
                    }
                )
        else:
            usage = shutil.disk_usage("/")
            drives.append({"drive": "/", "total": usage.total, "used": usage.used, "free": usage.free})
        return TaskResult("windows_disk_snapshot", True, "disk_snapshot_collected", {"drives": drives})

    def network_check(self, host: str = "www.baidu.com", port: int = 443, timeout: float = 3.0) -> TaskResult:
        started = time.time()
        ok = False
        error = ""
        try:
            with socket.create_connection((host, int(port or 443)), timeout=float(timeout or 3.0)):
                ok = True
        except Exception as e:
            error = repr(e)
        return TaskResult(
            "windows_network_check",
            ok,
            "network_reachable" if ok else "network_unreachable",
            {"ok": ok, "host": host, "port": int(port or 443), "latency_ms": round((time.time() - started) * 1000, 1), "error": error},
        )

    def power_status(self) -> TaskResult:
        data = _run_powershell_json(
            "$b=Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue; "
            "if($b){$b|Select-Object Name,EstimatedChargeRemaining,BatteryStatus|ConvertTo-Json -Depth 3} "
            "else {@{BatteryPresent=$false}|ConvertTo-Json}",
            timeout=8,
        )
        return TaskResult("windows_power_status", True, "power_status_collected", {"power": data})

    def process_snapshot(self, top: int = 10) -> TaskResult:
        top_n = max(1, min(int(top or 10), 50))
        data = _run_powershell_json(
            "$p=Get-Process | Sort-Object CPU -Descending | Select-Object -First "
            f"{top_n} Name,Id,CPU,WorkingSet64,PM,StartTime -ErrorAction SilentlyContinue; "
            "$p | ConvertTo-Json -Depth 4",
            timeout=12,
        )
        if isinstance(data, dict) and data.get("error"):
            data = []
        if isinstance(data, dict):
            data = [data]
        return TaskResult("windows_process_snapshot", True, "process_snapshot_collected", {"top": top_n, "processes": data or []})

    def system_status(self, network_host: str = "www.baidu.com") -> TaskResult:
        disk = self.disk_snapshot()
        network = self.network_check(host=network_host)
        power = self.power_status()
        processes = self.process_snapshot(top=8)
        return TaskResult(
            "windows_system_status",
            True,
            "system_status_collected",
            {
                "disk": disk.evidence,
                "network": network.evidence,
                "power": power.evidence,
                "processes": processes.evidence,
            },
        )

    def recent_files(self, paths_json: str = "", since_days: int = 1, max_results: int = 200) -> TaskResult:
        roots: list[Path] = []
        try:
            raw = json.loads(paths_json or "[]")
            if isinstance(raw, str):
                raw = [raw]
            if isinstance(raw, list):
                roots = [Path(str(x)).expanduser().resolve() for x in raw if str(x).strip()]
        except Exception:
            roots = []
        if not roots:
            roots = [
                _user_known_folder("desktop"),
                _user_known_folder("downloads"),
                _user_known_folder("documents"),
                Path.cwd(),
            ]
        roots = [p for p in roots if p.exists() and p.is_dir()]
        since = time.time() - max(1, int(since_days or 1)) * 86400
        limit = max(1, min(int(max_results or 200), 2000))
        rows: list[dict[str, Any]] = []
        by_category: dict[str, int] = {}
        for root in roots:
            try:
                iterator = root.rglob("*")
                for p in iterator:
                    if len(rows) >= limit:
                        break
                    try:
                        if not p.is_file():
                            continue
                        st = p.stat()
                    except Exception:
                        continue
                    if st.st_mtime < since and st.st_ctime < since:
                        continue
                    cat = _file_category(p)
                    by_category[cat] = by_category.get(cat, 0) + 1
                    rows.append(
                        {
                            "path": str(p),
                            "root": str(root),
                            "name": p.name,
                            "extension": p.suffix.lower(),
                            "category": cat,
                            "size": st.st_size,
                            "mtime": st.st_mtime,
                            "ctime": st.st_ctime,
                        }
                    )
            except Exception:
                continue
        rows.sort(key=lambda r: max(float(r.get("mtime") or 0), float(r.get("ctime") or 0)), reverse=True)
        return TaskResult(
            "windows_recent_files",
            True,
            "recent_files_collected",
            {"roots": [str(p) for p in roots], "since_days": max(1, int(since_days or 1)), "count": len(rows), "by_category": by_category, "files": rows[:limit]},
        )

    def folder_create(self, path: str) -> TaskResult:
        target = Path(path).expanduser().resolve()
        existed = target.exists()
        try:
            target.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return TaskResult("windows_folder_create", False, f"mkdir_failed:{e!r}", {"target": str(target)})
        return TaskResult("windows_folder_create", target.is_dir(), "folder_created" if not existed else "folder_already_exists", {"target": _file_stat(target), "existed": existed})

    def file_write_text(self, path: str, text: str, overwrite: bool = False, confirm: bool = False, allow_dangerous: bool = False) -> TaskResult:
        target = Path(path).expanduser().resolve()
        exists = target.exists()
        allow = os_file_dangerous_without_confirm_enabled(allow_dangerous)
        if exists and not (overwrite or confirm or allow):
            return _confirmation_required_result("windows_file_write_text", "overwrite_existing_file", {"target": _file_stat(target), "text_len": len(text)})
        if exists and overwrite and not (confirm or allow):
            return _confirmation_required_result("windows_file_write_text", "overwrite_existing_file", {"target": _file_stat(target), "text_len": len(text), "overwrite": overwrite})
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(text), encoding="utf-8")
        except Exception as e:
            return TaskResult("windows_file_write_text", False, f"write_failed:{e!r}", {"target": str(target)})
        return TaskResult("windows_file_write_text", True, "file_written", {"target": _file_stat(target), "text_len": len(text), "overwrote": exists, "dangerous_bypassed": bool(exists and allow and not confirm)})

    def workspace_report(self, output_path: str = "", since_days: int = 1, open_folder: bool = False) -> TaskResult:
        windows = self.window_list(limit=80)
        recent = self.recent_files(since_days=since_days, max_results=120)
        status = self.system_status()
        out = Path(output_path).expanduser().resolve() if output_path.strip() else (_user_known_folder("desktop") / f"Jachin_Windows_OS_Report_{time.strftime('%Y%m%d_%H%M%S')}.md")
        out.parent.mkdir(parents=True, exist_ok=True)
        ev = {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "active_window": windows.evidence.get("active"),
            "office_window_count": len(windows.evidence.get("office_windows") or []),
            "recent_file_count": recent.evidence.get("count"),
            "recent_by_category": recent.evidence.get("by_category"),
            "system": status.evidence,
        }
        lines = [
            "# Jachin Windows OS Report",
            "",
            f"- Generated: {ev['generated_at']}",
            f"- Active window: {(ev.get('active_window') or {}).get('title', '')}",
            f"- Office windows: {ev['office_window_count']}",
            f"- Recent files: {ev['recent_file_count']}",
            "",
            "## Office Windows",
        ]
        for w in (windows.evidence.get("office_windows") or [])[:20]:
            lines.append(f"- [{w.get('office_kind')}] {w.get('title')} ({w.get('process')}, pid={w.get('pid')})")
        lines.extend(["", "## Recent Files"])
        for f in (recent.evidence.get("files") or [])[:40]:
            lines.append(f"- [{f.get('category')}] {f.get('path')}")
        lines.extend(["", "## Disk"])
        for d in status.evidence.get("disk", {}).get("drives", []):
            lines.append(f"- {d.get('drive')}: free {d.get('free_gb', '')} GB, used {d.get('used_pct', '')}%")
        lines.extend(["", "## Network", f"- {status.evidence.get('network', {})}", "", "## Evidence JSON"])
        evidence_path = out.with_suffix(".evidence.json")
        evidence_payload = {"windows": windows.evidence, "recent": recent.evidence, "status": status.evidence, "report_path": str(out)}
        panel_path = _write_evidence_panel(
            self.out_dir,
            title="Jachin Windows Workspace Evidence",
            task="windows_workspace_report",
            ok=True,
            detail="report_written",
            evidence=evidence_payload,
        )
        evidence_payload["evidence_panel_path"] = panel_path
        evidence_path.write_text(json.dumps(evidence_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        lines.append(str(evidence_path))
        lines.extend(["", "## Evidence Panel", panel_path])
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        reveal: dict[str, Any] | None = None
        if open_folder:
            reveal = asdict(self.file_reveal_in_explorer(str(out)))
        return TaskResult(
            "windows_workspace_report",
            True,
            "report_written",
            {
                "report_path": str(out),
                "evidence_path": str(evidence_path),
                "evidence_panel_path": panel_path,
                "report": _file_stat(out),
                "evidence_json": _file_stat(evidence_path),
                "summary": ev,
                "reveal": reveal,
            },
        )

    def evidence_panel(self, evidence_path: str = "", title: str = "", open_panel: bool = False) -> TaskResult:
        path = Path(evidence_path).expanduser().resolve() if str(evidence_path or "").strip() else None
        if path is None or not path.exists() or not path.is_file():
            return TaskResult("windows_evidence_panel", False, "evidence_path_not_found", {"evidence_path": str(path or "")})
        try:
            evidence = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            return TaskResult("windows_evidence_panel", False, f"evidence_json_invalid:{e!r}", {"evidence_path": str(path)})
        if not isinstance(evidence, dict):
            return TaskResult("windows_evidence_panel", False, "evidence_json_not_object", {"evidence_path": str(path)})
        task = str(evidence.get("task") or path.stem)
        panel_path = _write_evidence_panel(
            self.out_dir,
            title=title or f"Jachin Evidence Panel - {task}",
            task=task,
            ok=bool(evidence.get("ok", True)),
            detail=str(evidence.get("detail") or "evidence_loaded"),
            evidence=evidence,
        )
        open_result: dict[str, Any] | None = None
        if open_panel:
            open_result = asdict(self.file_open(panel_path))
        return TaskResult(
            "windows_evidence_panel",
            True,
            "evidence_panel_ready",
            {"evidence_path": str(path), "evidence_panel_path": panel_path, "open_result": open_result},
        )

    def project_remember(self, project_name: str, project_path: str) -> TaskResult:
        root = Path(project_path).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            return TaskResult("windows_project_remember", False, "project_path_not_found", {"project_name": project_name, "project_path": str(root)})
        row = _remember_project_path(project_name or root.name, root)
        return TaskResult("windows_project_remember", True, "project_remembered", row)

    def project_latest_briefing(
        self,
        project_name: str,
        project_path: str = "",
        feature_query: str = "",
        recipients: list[str] | None = None,
        since_days: int = 3,
        send_summary: bool = False,
        open_report: bool = True,
        use_qwen: bool = True,
        remember: bool = True,
        max_files: int = 80,
    ) -> TaskResult:
        root, resolve_ev = _resolve_remembered_project(project_name, project_path, remember=remember)
        if root is None:
            return TaskResult("windows_project_latest_briefing", False, "project_path_required", resolve_ev)
        root = root.resolve()
        _load_local_env_for_qwen(root)

        git_root_res = _run_cmd(root, ["git", "rev-parse", "--show-toplevel"], timeout=8)
        git_root = Path(str(git_root_res.get("stdout") or root)).expanduser().resolve() if git_root_res.get("ok") else root
        branch = (_git_text(git_root, ["branch", "--show-current"], timeout=8, max_chars=200) or "").strip()
        head = (_git_text(git_root, ["rev-parse", "--short", "HEAD"], timeout=8, max_chars=80) or "").strip()
        status = _git_lines(git_root, ["status", "--short"], timeout=8)
        log = _git_text(git_root, ["log", f"--since={max(1, int(since_days or 3))} days ago", "--oneline", "--decorate", "--max-count=20"], timeout=8, max_chars=5000)
        diff_stat = _git_text(git_root, ["diff", "--stat", "HEAD"], timeout=12, max_chars=8000)
        diff_names = _git_lines(git_root, ["diff", "--name-only", "HEAD"], timeout=10)
        cached_names = _git_lines(git_root, ["diff", "--cached", "--name-only"], timeout=10)
        recent = _project_recent_files(git_root, since_days=since_days, max_results=max_files)

        changed_rel: list[str] = []
        for line in status:
            rel = line[3:].strip() if len(line) >= 4 else line.strip()
            if " -> " in rel:
                rel = rel.split(" -> ", 1)[1].strip()
            if rel and rel not in changed_rel:
                changed_rel.append(rel)
        for rel in [*diff_names, *cached_names, *[str(r.get("relative_path") or "") for r in recent]]:
            if rel and rel not in changed_rel:
                changed_rel.append(rel)

        feature_matches: list[str] = []
        fq = str(feature_query or "").strip()
        if fq:
            rg = _run_cmd(git_root, ["rg", "-n", "--glob", "!node_modules", "--glob", "!.git", "--glob", "!dist", "--glob", "!build", fq], timeout=12)
            if rg.get("ok") or rg.get("stdout"):
                feature_matches = str(rg.get("stdout") or "").splitlines()[:80]

        snippets = _read_project_snippets(git_root, changed_rel, max_files=8, max_chars_per_file=1600)
        evidence = {
            "project_name": project_name or root.name,
            "project_path": str(git_root),
            "resolve": resolve_ev,
            "git": {
                "is_git": bool(git_root_res.get("ok")),
                "branch": branch,
                "head": head,
                "status": status[:80],
                "recent_log": log,
                "diff_stat": diff_stat,
                "diff_names": diff_names[:80],
                "cached_names": cached_names[:80],
            },
            "feature_query": fq,
            "feature_matches": feature_matches,
            "recent_files": recent,
            "snippets": snippets,
        }

        prompt_parts = [
            f"项目名：{project_name or root.name}",
            f"项目路径：{git_root}",
            f"时间范围：最近 {max(1, int(since_days or 3))} 天",
            f"关注功能：{fq or '未指定，概括整体最新进展'}",
            "",
            "Git 分支/HEAD:",
            f"- branch: {branch}",
            f"- head: {head}",
            "",
            "Git status:",
            "\n".join(status[:80]) or "无",
            "",
            "最近提交:",
            log or "无",
            "",
            "未提交 diff stat:",
            diff_stat or "无",
            "",
            "功能关键词命中:",
            "\n".join(feature_matches[:60]) or "无",
            "",
            "最近文件:",
            "\n".join(_brief_file_line(r) for r in recent[:30]) or "无",
            "",
            "关键文件片段:",
            "\n\n".join(f"### {s['relative_path']}\n{s['text']}" for s in snippets) or "无",
            "",
            "请输出中文简报，包含：1. 最新进展 2. 涉及模块/文件 3. 风险/阻塞 4. 建议下一步 5. 可发送给同事的短版消息。",
        ]
        prompt = "\n".join(prompt_parts)[:28000]

        qwen_result: dict[str, Any] = {"ok": False, "detail": "qwen_disabled"}
        if use_qwen:
            qwen_result = _call_qwen_coder(prompt)
        fallback_summary = "\n".join(
            [
                f"项目 {project_name or root.name} 最新简报",
                f"- 路径：{git_root}",
                f"- 分支/HEAD：{branch or 'unknown'} / {head or 'unknown'}",
                f"- Git 状态条目：{len(status)}",
                f"- 最近文件：{len(recent)}",
                f"- 关注功能：{fq or '整体'}",
                "- 主要变更文件：",
                *[f"  - {rel}" for rel in changed_rel[:12]],
                "- 风险：模型总结未启用或失败时，请以 evidence JSON 中 Git/status/diff/snippet 为准。",
            ]
        )
        summary = str(qwen_result.get("content") or "").strip() or fallback_summary

        report = self.out_dir / f"jachin_project_briefing_{_safe_label(project_name or root.name)}_{now_tag()}.md"
        evidence_path = report.with_suffix(".evidence.json")
        report_lines = [
            f"# {project_name or root.name} Project Briefing",
            "",
            f"- Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- Project path: {git_root}",
            f"- Branch: {branch}",
            f"- HEAD: {head}",
            f"- Feature query: {fq or 'overall'}",
            f"- Qwen: {qwen_result.get('detail')}",
            "",
            "## Summary",
            "",
            summary,
            "",
            "## Evidence",
            "",
            f"- Evidence JSON: {evidence_path}",
        ]
        report.write_text("\n".join(report_lines), encoding="utf-8")
        evidence_payload = {**evidence, "qwen": {k: v for k, v in qwen_result.items() if k != "content"}, "summary": summary, "report_path": str(report)}

        open_result: dict[str, Any] | None = None
        if open_report:
            open_result = asdict(self.file_open(str(report)))

        clean_recipients = [str(x).strip() for x in (recipients or []) if str(x).strip()]
        send_result: dict[str, Any] | None = None
        if send_summary and clean_recipients:
            message = summary
            if len(message) > 1800:
                message = message[:1700].rstrip() + f"\n\n报告：{report}"
            send_result = asdict(self.lark_send_message(clean_recipients, message))

        ok = report.exists()
        if send_summary and clean_recipients:
            ok = bool(ok and send_result and send_result.get("ok"))
        evidence_payload.update({"open_result": open_result, "send_result": send_result, "recipients": clean_recipients})
        panel_path = _write_evidence_panel(
            self.out_dir,
            title=f"{project_name or root.name} Project Briefing Evidence",
            task="windows_project_latest_briefing",
            ok=bool(ok),
            detail="project_briefing_sent" if send_result and send_result.get("ok") else "project_briefing_ready",
            evidence=evidence_payload,
        )
        evidence_payload["evidence_panel_path"] = panel_path
        evidence_path.write_text(json.dumps(evidence_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return TaskResult(
            "windows_project_latest_briefing",
            bool(ok),
            "project_briefing_sent" if send_result and send_result.get("ok") else "project_briefing_ready",
            {
                "project_name": project_name or root.name,
                "project_path": str(git_root),
                "report_path": str(report),
                "evidence_path": str(evidence_path),
                "evidence_panel_path": panel_path,
                "summary": summary,
                "qwen": {k: v for k, v in qwen_result.items() if k != "content"},
                "resolve": resolve_ev,
                "git": evidence["git"],
                "recent_count": len(recent),
                "feature_match_count": len(feature_matches),
                "open_result": open_result,
                "send_result": send_result,
            },
        )

    def _codex_focus_input(self) -> dict[str, Any]:
        focus_guard = self._ensure_codex_foreground(timeout=2.0)
        if not focus_guard.get("ok"):
            return {"ok": False, "detail": "codex_focus_lost_before_input", "focus_guard": focus_guard}
        rect = self.win.active_rect()
        if not rect:
            return {"ok": False, "detail": "no_active_window_rect", "focus_guard": focus_guard}
        _title, left, top, width, height = rect
        # Codex desktop uses a bottom composer in the common chat layout.
        x = int(left + width * 0.50)
        y = int(top + height * 0.90)
        self.io.click(x, y, wait=0.2)
        return {"ok": True, "point": {"x": x, "y": y}, "active_title": self.win.active_title(), "focus_guard": focus_guard}

    def _codex_active_title_ok(self, title: str | None = None) -> bool:
        text = str(self.win.active_title() if title is None else title or "").lower()
        return any(keyword in text for keyword in APP_PROFILES.get("codex", {}).get("keywords", ("codex", "openai codex")))

    def _ensure_codex_foreground(self, timeout: float = 3.0) -> dict[str, Any]:
        before = ""
        try:
            before = self.win.active_title()
        except Exception as e:
            return {"ok": False, "detail": "codex_active_title_failed", "error": repr(e)}
        if self._codex_active_title_ok(before):
            return {"ok": True, "detail": "codex_already_foreground", "before": before, "after": before, "focused": False}
        try:
            focused = bool(self.win.focus_by_keywords(APP_PROFILES.get("codex", {}).get("keywords", ("codex", "openai codex")), timeout=float(timeout or 3.0)))
        except Exception as e:
            after = ""
            try:
                after = self.win.active_title()
            except Exception:
                pass
            return {"ok": False, "detail": "codex_focus_failed", "before": before, "after": after, "focused": False, "error": repr(e)}
        try:
            after = self.win.active_title()
        except Exception:
            after = ""
        ok = bool(focused and self._codex_active_title_ok(after))
        return {
            "ok": ok,
            "detail": "codex_foreground" if ok else "codex_focus_lost",
            "before": before,
            "after": after,
            "focused": focused,
        }

    def _codex_copy_latest_response(self, project_name: str = "", feature_query: str = "") -> dict[str, Any]:
        try:
            import pyperclip  # type: ignore
        except Exception as e:
            return {"ok": False, "detail": f"pyperclip_unavailable:{e!r}", "text": ""}

        attempts: list[dict[str, Any]] = []
        focus_guard = self._ensure_codex_foreground(timeout=2.0)
        attempts.append({"method": "codex_focus_guard", **focus_guard})
        if not focus_guard.get("ok"):
            return {"ok": False, "detail": "codex_focus_lost_before_copy", "text": "", "attempts": attempts, "focus_guard": focus_guard}

        def read_clipboard() -> str:
            try:
                return str(pyperclip.paste() or "").strip()
            except Exception:
                return ""

        def reset_clipboard() -> None:
            try:
                pyperclip.copy("")
            except Exception:
                pass

        def acceptable(text: str) -> bool:
            content = str(text or "").strip()
            if len(content) < 80:
                return False
            return not _looks_like_codex_project_prompt_echo(content)

        best_text = ""
        auto, uia_err = _import_uia()
        if auto is not None:
            try:
                active_rect = self.win.active_rect()
                root = auto.GetRootControl()
                candidates: list[tuple[int, int, Any, dict[str, Any]]] = []
                for ctrl, depth in _iter_uia_controls(root, max_depth=12):
                    name = (getattr(ctrl, "Name", "") or "").strip()
                    typ = (getattr(ctrl, "ControlTypeName", "") or "").strip()
                    if not name:
                        continue
                    lowered = name.lower()
                    if not any(token in lowered or token in name for token in ("copy", "复制", "拷贝")):
                        continue
                    rect = getattr(ctrl, "BoundingRectangle", None)
                    if not rect:
                        continue
                    left = int(getattr(rect, "left", 0))
                    top = int(getattr(rect, "top", 0))
                    right = int(getattr(rect, "right", 0))
                    bottom = int(getattr(rect, "bottom", 0))
                    if right <= left or bottom <= top:
                        continue
                    if active_rect:
                        _title, win_left, win_top, win_w, win_h = active_rect
                        if right < win_left or left > win_left + win_w or bottom < win_top or top > win_top + win_h:
                            continue
                    row = {
                        "name": name,
                        "control_type": typ,
                        "depth": depth,
                        "rect": [left, top, right, bottom],
                        "center": [int((left + right) / 2), int((top + bottom) / 2)],
                    }
                    candidates.append((bottom, left, ctrl, row))
                candidates.sort(key=lambda item: (item[0], -item[1]), reverse=True)
                attempts.append({"method": "uia_scan_copy_buttons", "count": len(candidates), "candidates": [row for *_rest, row in candidates[:8]]})
                for _bottom, _left, ctrl, row in candidates[:8]:
                    reset_clipboard()
                    try:
                        try:
                            ctrl.SetFocus()
                        except Exception:
                            pass
                        ctrl.Click()
                        time.sleep(0.45)
                        got = read_clipboard()
                        best_text = got if len(got) > len(best_text) else best_text
                        ok = acceptable(got)
                        attempts.append({"method": "uia_click_copy_button", "ok": ok, "copied_len": len(got), "button": row})
                        if ok:
                            return {"ok": True, "detail": "copied_by_codex_copy_button", "text": got, "attempts": attempts}
                    except Exception as e:
                        attempts.append({"method": "uia_click_copy_button", "ok": False, "error": repr(e), "button": row})
                    center = row.get("center") or [0, 0]
                    reset_clipboard()
                    try:
                        focus_guard = self._ensure_codex_foreground(timeout=1.0)
                        if not focus_guard.get("ok"):
                            attempts.append({"method": "coordinate_click_copy_button", "ok": False, "error": "codex_focus_lost", "button": row, "focus_guard": focus_guard})
                            continue
                        self.io.click(int(center[0]), int(center[1]), wait=0.45)
                        got = read_clipboard()
                        best_text = got if len(got) > len(best_text) else best_text
                        ok = acceptable(got)
                        attempts.append({"method": "coordinate_click_copy_button", "ok": ok, "copied_len": len(got), "button": row})
                        if ok:
                            return {"ok": True, "detail": "copied_by_codex_copy_button_coordinate", "text": got, "attempts": attempts}
                    except Exception as e:
                        attempts.append({"method": "coordinate_click_copy_button", "ok": False, "error": repr(e), "button": row})
            except Exception as e:
                attempts.append({"method": "uia_scan_copy_buttons", "ok": False, "error": repr(e)})
        else:
            attempts.append({"method": "uia_scan_copy_buttons", "ok": False, "error": uia_err})

        if _truthy(os.environ.get("JACHIN_CODEX_COPY_ALLOW_VISUAL_COORDINATE")):
            focus_guard = self._ensure_codex_foreground(timeout=1.0)
            active_rect = self.win.active_rect() if focus_guard.get("ok") else None
            if active_rect:
                _title, left, top, width, height = active_rect
                visual_points = [
                    (int(left + 36), int(top + height - 38)),
                    (int(left + max(44, width * 0.07)), int(top + height * 0.88)),
                    (int(left + 70), int(top + height - 38)),
                ]
                for x, y in visual_points:
                    reset_clipboard()
                    try:
                        focus_guard = self._ensure_codex_foreground(timeout=1.0)
                        if not focus_guard.get("ok"):
                            attempts.append({"method": "visual_coordinate_copy_button", "ok": False, "error": "codex_focus_lost", "point": {"x": x, "y": y}, "focus_guard": focus_guard})
                            continue
                        self.io.click(x, y, wait=0.45)
                        got = read_clipboard()
                        best_text = got if len(got) > len(best_text) else best_text
                        ok = acceptable(got)
                        attempts.append({"method": "visual_coordinate_copy_button", "ok": ok, "copied_len": len(got), "point": {"x": x, "y": y}})
                        if ok:
                            return {"ok": True, "detail": "copied_by_visual_copy_button_coordinate", "text": got, "attempts": attempts}
                    except Exception as e:
                        attempts.append({"method": "visual_coordinate_copy_button", "ok": False, "error": repr(e), "point": {"x": x, "y": y}})
        else:
            attempts.append({"method": "visual_coordinate_copy_button", "ok": False, "skipped": "disabled_by_default"})

        reset_clipboard()
        focus_guard = self._ensure_codex_foreground(timeout=1.0)
        if not focus_guard.get("ok"):
            attempts.append({"method": "ctrl_shift_c", "ok": False, "error": "codex_focus_lost", "focus_guard": focus_guard})
            return {"ok": False, "detail": "codex_focus_lost_before_shortcut_copy", "text": best_text, "attempts": attempts, "focus_guard": focus_guard}
        self.io.hotkey("ctrl", "shift", "c", wait=0.35)
        alt = read_clipboard()
        best_text = alt if len(alt) > len(best_text) else best_text
        attempts.append({"method": "ctrl_shift_c", "ok": acceptable(alt), "copied_len": len(alt)})
        if acceptable(alt):
            return {"ok": True, "detail": "copied_by_alt_shortcut", "text": alt, "attempts": attempts}

        if _truthy(os.environ.get("JACHIN_CODEX_COPY_ALLOW_SELECT_ALL")):
            # Last resort only: this often copies the composer/prompt, so it is opt-in.
            reset_clipboard()
            focus_guard = self._ensure_codex_foreground(timeout=1.0)
            if not focus_guard.get("ok"):
                attempts.append({"method": "ctrl_a_ctrl_c_last_resort", "ok": False, "error": "codex_focus_lost", "focus_guard": focus_guard})
                return {"ok": False, "detail": "codex_focus_lost_before_select_all_copy", "text": best_text, "attempts": attempts, "focus_guard": focus_guard}
            self.io.hotkey("ctrl", "a", wait=0.1)
            self.io.hotkey("ctrl", "c", wait=0.35)
            selected = read_clipboard()
            best_text = selected if len(selected) > len(best_text) else best_text
            attempts.append({"method": "ctrl_a_ctrl_c_last_resort", "ok": acceptable(selected), "copied_len": len(selected)})
            if acceptable(selected):
                return {"ok": True, "detail": "copied_by_select_all_last_resort", "text": selected, "attempts": attempts}
        else:
            attempts.append({"method": "ctrl_a_ctrl_c_last_resort", "ok": False, "skipped": "disabled_by_default"})
        return {"ok": False, "detail": "codex_copy_button_not_found_or_text_invalid", "text": best_text, "attempts": attempts}

    def codex_project_briefing_to_lark(
        self,
        project_name: str,
        project_path: str = "",
        feature_query: str = "",
        original_user_input: str = "",
        recipients: list[str] | None = None,
        since_days: int = 3,
        wait_seconds: int = 90,
        send_summary: bool = False,
        remember: bool = True,
    ) -> TaskResult:
        clean_recipients = [str(x).strip() for x in (recipients or []) if str(x).strip()]
        root, resolve_ev = _resolve_remembered_project(project_name, project_path, remember=remember)
        if root is None:
            return TaskResult("windows_codex_project_briefing_to_lark", False, "project_path_required", resolve_ev)

        report = self.out_dir / f"codex_project_briefing_{_safe_label(project_name or root.name)}_{now_tag()}.md"
        evidence_path = report.with_suffix(".evidence.json")
        evidence: dict[str, Any] = {
            "task": "windows_codex_project_briefing_to_lark",
            "ok": False,
            "detail": "running",
            "project_name": project_name or root.name,
            "project_path": str(root),
            "feature_query": feature_query,
            "original_user_input": original_user_input,
            "recipients": clean_recipients,
            "send_summary": send_summary,
            "resolve": resolve_ev,
            "report_path": str(report),
            "evidence_path": str(evidence_path),
            "timeline": [],
        }
        _append_evidence_timeline(
            evidence,
            evidence_path,
            "resolve_project",
            "done",
            "resolved remembered/provided project path",
            {"project_name": project_name or root.name, "project_path": str(root), "resolve": resolve_ev},
        )

        _load_local_env_for_qwen(root)
        prompt, prompt_meta = _build_codex_project_prompt_with_meta(
            project_name or root.name,
            str(root),
            feature_query=feature_query,
            since_days=since_days,
            original_user_input=original_user_input,
        )
        evidence["prompt"] = prompt
        evidence["prompt_meta"] = prompt_meta
        _append_evidence_timeline(
            evidence,
            evidence_path,
            "build_codex_prompt",
            "done",
            f"built prompt for last {since_days} days via {prompt_meta.get('strategy')}",
            {"prompt_len": len(prompt), "feature_query": feature_query, "since_days": since_days, "prompt_meta": prompt_meta},
        )
        codex_open = self.ensure_app("codex", timeout=4.0)
        evidence["codex_open"] = asdict(codex_open)
        _append_evidence_timeline(
            evidence,
            evidence_path,
            "open_codex",
            "done" if codex_open.ok else "failed",
            codex_open.detail,
            {"codex_open": asdict(codex_open)},
        )
        if not codex_open.ok:
            panel_path = _write_evidence_panel(
                self.out_dir,
                title=f"{project_name or root.name} Codex to Lark Evidence",
                task="windows_codex_project_briefing_to_lark",
                ok=False,
                detail="codex_open_failed",
                evidence=evidence,
            )
            evidence["detail"] = "codex_open_failed"
            evidence["evidence_panel_path"] = panel_path
            evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
            return TaskResult(
                "windows_codex_project_briefing_to_lark",
                False,
                "codex_open_failed",
                evidence,
            )

        codex_focus_before = self._ensure_codex_foreground(timeout=3.0)
        evidence["codex_focus_before"] = codex_focus_before
        before = self.io.screenshot_active_window(self.out_dir, f"codex_before_{_safe_label(project_name)}")
        focus_input = self._codex_focus_input()
        evidence["focus_input"] = focus_input
        evidence["screenshots"] = {"before": before}
        _append_evidence_timeline(
            evidence,
            evidence_path,
            "focus_codex_input",
            "done" if focus_input.get("ok") else "check",
            "focused Codex input area",
            {"focus_input": focus_input, "screenshot": before},
        )
        self.io.paste(prompt, wait=0.4)
        typed = self.io.screenshot_active_window(self.out_dir, f"codex_prompt_typed_{_safe_label(project_name)}")
        typed_visual = ocr_image_state(typed)
        evidence["screenshots"]["typed"] = typed
        evidence["typed_visual"] = typed_visual
        _append_evidence_timeline(
            evidence,
            evidence_path,
            "paste_codex_prompt",
            "done",
            "pasted project-summary prompt into Codex",
            {"screenshot": typed, "ocr_text_len": len(str(typed_visual.get("ocr_text") or ""))},
        )
        self.io.press("enter", wait=1.0)
        _append_evidence_timeline(evidence, evidence_path, "submit_codex_prompt", "done", "submitted prompt to Codex", {})

        wait_limit = max(10, min(int(wait_seconds or 90), 600))
        wait_start = time.time()
        deadline = time.time() + wait_limit
        last_fingerprint = ""
        stable_count = 0
        captures: list[dict[str, Any]] = []
        while time.time() < deadline:
            time.sleep(5.0)
            focus_guard = self._ensure_codex_foreground(timeout=2.0)
            if not focus_guard.get("ok"):
                elapsed = time.time() - wait_start
                captures.append({"focus_guard": focus_guard, "elapsed_seconds": round(elapsed, 1), "active": False, "stable_count": stable_count})
                evidence["wait_captures"] = captures
                _append_evidence_timeline(
                    evidence,
                    evidence_path,
                    "wait_codex_output",
                    "failed",
                    "Codex focus lost while waiting; stop visual actions",
                    {"focus_guard": focus_guard, "elapsed_seconds": round(elapsed, 1)},
                )
                break
            shot = self.io.screenshot_active_window(self.out_dir, f"codex_wait_{_safe_label(project_name)}_{len(captures)+1}")
            visual = ocr_image_state(shot)
            text = str(visual.get("ocr_text") or "")
            fp = _ocr_fingerprint(text)
            active = _codex_generation_active(text)
            if fp and fp == last_fingerprint:
                stable_count += 1
            else:
                stable_count = 0
                last_fingerprint = fp
            elapsed = time.time() - wait_start
            captures.append({"screenshot": shot, "visual": visual, "stable_count": stable_count, "active": active, "elapsed_seconds": round(elapsed, 1)})
            evidence["wait_captures"] = captures
            _append_evidence_timeline(
                evidence,
                evidence_path,
                "wait_codex_output",
                "running" if active or stable_count < 2 else "done",
                f"capture {len(captures)} stable_count={stable_count} active={active}",
                {"screenshot": shot, "ocr_text_len": len(text), "stable_count": stable_count, "active": active, "elapsed_seconds": round(elapsed, 1)},
            )
            min_wait = min(20, wait_limit)
            if stable_count >= 2 and len(text) > 80 and not active and elapsed >= min_wait:
                break

        final_focus_guard = self._ensure_codex_foreground(timeout=3.0)
        evidence["codex_final_focus_guard"] = final_focus_guard
        final_shot = self.io.screenshot_active_window(
            self.out_dir,
            f"codex_final_{_safe_label(project_name)}" if final_focus_guard.get("ok") else f"codex_final_focus_lost_{_safe_label(project_name)}",
        )
        final_visual = ocr_image_state(final_shot)
        vision_extract = _call_qwen_vision_codex_extract(final_shot, project_name or root.name, feature_query=feature_query)
        vision_text = str(vision_extract.get("content") or "").strip()
        evidence["vision_extract"] = {k: v for k, v in vision_extract.items() if k != "content"}
        evidence["vision_text"] = vision_text
        _append_evidence_timeline(
            evidence,
            evidence_path,
            "extract_codex_result_by_vision",
            "done" if vision_extract.get("ok") else "check",
            str(vision_extract.get("detail") or ""),
            {"screenshot": final_shot, "model": vision_extract.get("model"), "vision_text_len": len(vision_text), "image_meta": vision_extract.get("image_meta")},
        )
        if _truthy(os.environ.get("JACHIN_CODEX_COPY_FALLBACK_ENABLED")):
            copied = self._codex_copy_latest_response(project_name or root.name, feature_query=feature_query)
        else:
            copied = {"ok": False, "detail": "copy_fallback_disabled_visual_first", "text": "", "attempts": []}
        copied_text = str(copied.get("text") or "").strip()
        evidence["screenshots"]["final"] = final_shot
        evidence["final_visual"] = final_visual
        evidence["copied"] = {k: v for k, v in copied.items() if k != "text"}
        evidence["copied_text"] = copied_text
        _append_evidence_timeline(
            evidence,
            evidence_path,
            "copy_codex_result",
            "done" if copied.get("ok") else "check",
            str(copied.get("detail") or ""),
            {"screenshot": final_shot, "copied_len": len(copied_text), "ocr_text_len": len(str(final_visual.get("ocr_text") or ""))},
        )
        choice = _choose_codex_brief_message(
            copied_text,
            str(final_visual.get("ocr_text") or ""),
            project_name or root.name,
            feature_query=feature_query,
            vision_text=vision_text,
        )
        ocr_fallback_text = str(choice["ocr_fallback_text"])
        vision_validation = dict(choice["vision_validation"])
        copied_validation = dict(choice["copied_validation"])
        ocr_validation = dict(choice["ocr_validation"])
        message_text = str(choice["message_text"])
        message_source = str(choice["message_source"])
        validation = dict(choice["validation"])
        evidence.update(
            {
                "ocr_fallback_text": ocr_fallback_text,
                "message_text": message_text,
                "message_source": message_source,
                "vision_validation": vision_validation,
                "copied_validation": copied_validation,
                "ocr_validation": ocr_validation,
                "validation": validation,
            }
        )
        _append_evidence_timeline(
            evidence,
            evidence_path,
            "validate_codex_result",
            "done" if validation.get("ok") else "failed",
            f"message_source={message_source}",
            {"validation": validation, "vision_validation": vision_validation, "copied_validation": copied_validation, "ocr_validation": ocr_validation},
        )

        report.write_text(message_text or copied_text or str(final_visual.get("ocr_text") or ""), encoding="utf-8")
        _append_evidence_timeline(
            evidence,
            evidence_path,
            "write_report",
            "done",
            "wrote Markdown report for preview/delivery",
            {"report_path": str(report), "message_len": len(message_text)},
        )

        send_result: dict[str, Any] | None = None
        if send_summary:
            if not clean_recipients:
                send_result = {"ok": False, "detail": "recipients_empty"}
                _append_evidence_timeline(evidence, evidence_path, "send_lark", "failed", "recipients_empty", {})
            elif not validation.get("ok"):
                send_result = {"ok": False, "detail": "codex_response_validation_failed"}
                _append_evidence_timeline(evidence, evidence_path, "send_lark", "failed", "codex_response_validation_failed", {"validation": validation})
            else:
                def lark_event(stage: str, status: str, detail: str, row: dict[str, Any]) -> None:
                    _append_evidence_timeline(evidence, evidence_path, f"lark.{stage}", status, detail, row)

                send_result = asdict(self.lark_send_message(clean_recipients, message_text, timeline_cb=lark_event))
                _append_evidence_timeline(
                    evidence,
                    evidence_path,
                    "send_lark_complete",
                    "done" if send_result.get("ok") else "failed",
                    str(send_result.get("detail") or ""),
                    {"recipients": clean_recipients, "send_result": send_result},
                )

        ok = bool(validation.get("ok"))
        if send_summary:
            ok = ok and bool(send_result and send_result.get("ok"))
        evidence["ok"] = ok
        evidence["detail"] = "codex_briefing_sent" if send_result and send_result.get("ok") else ("codex_briefing_ready" if validation.get("ok") else "codex_briefing_not_verified")
        evidence["send_result"] = send_result
        panel_path = _write_evidence_panel(
            self.out_dir,
            title=f"{project_name or root.name} Codex to Lark Evidence",
            task="windows_codex_project_briefing_to_lark",
            ok=ok,
            detail=evidence["detail"],
            evidence=evidence,
        )
        evidence["evidence_panel_path"] = panel_path
        _append_evidence_timeline(
            evidence,
            evidence_path,
            "render_evidence_panel",
            "done",
            "rendered leadership evidence HTML panel",
            {"evidence_panel_path": panel_path},
        )
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        return TaskResult(
            "windows_codex_project_briefing_to_lark",
            ok,
            evidence["detail"],
            evidence,
        )

    def codex_ask_lark_send(
        self,
        question: str,
        recipients: list[str] | None = None,
        original_user_input: str = "",
        wait_seconds: int = 90,
    ) -> TaskResult:
        clean_question = str(question or "").strip()
        clean_recipients = [str(x).strip() for x in (recipients or []) if str(x).strip()]
        if not clean_question:
            return TaskResult("windows_codex_ask_lark_send", False, "question_empty", {"recipients": clean_recipients})
        if not clean_recipients:
            return TaskResult("windows_codex_ask_lark_send", False, "recipients_empty", {"question": clean_question})

        label = _safe_label(clean_question[:40] or "codex_question")
        report = self.out_dir / f"codex_ask_lark_{label}_{now_tag()}.md"
        evidence_path = report.with_suffix(".evidence.json")
        evidence: dict[str, Any] = {
            "task": "windows_codex_ask_lark_send",
            "ok": False,
            "detail": "running",
            "question": clean_question,
            "original_user_input": original_user_input,
            "recipients": clean_recipients,
            "report_path": str(report),
            "evidence_path": str(evidence_path),
            "artifact_contract": {
                "artifact": "codex_reply",
                "producer_step": "ask_codex",
                "consumer_step": "send_lark",
                "validation": ["non_empty", "not_prompt_echo", "has_answer_shape"],
            },
            "timeline": [],
        }
        _append_evidence_timeline(evidence, evidence_path, "build_mission_graph", "done", "codex_reply -> lark_message", {"recipients": clean_recipients})

        codex_open = self.ensure_app("codex", timeout=4.0)
        evidence["codex_open"] = asdict(codex_open)
        _append_evidence_timeline(evidence, evidence_path, "open_codex", "done" if codex_open.ok else "failed", codex_open.detail, {"codex_open": asdict(codex_open)})
        if not codex_open.ok:
            evidence["detail"] = "codex_open_failed"
            evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
            return TaskResult("windows_codex_ask_lark_send", False, "codex_open_failed", evidence)

        codex_focus_before = self._ensure_codex_foreground(timeout=3.0)
        evidence["codex_focus_before"] = codex_focus_before
        before = self.io.screenshot_active_window(self.out_dir, f"codex_ask_before_{label}")
        focus_input = self._codex_focus_input()
        evidence["focus_input"] = focus_input
        evidence["screenshots"] = {"before": before}
        _append_evidence_timeline(evidence, evidence_path, "focus_codex_input", "done" if focus_input.get("ok") else "failed", "focused Codex input area", {"focus_input": focus_input, "screenshot": before})
        if not focus_input.get("ok"):
            evidence["detail"] = "codex_input_focus_failed"
            evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
            return TaskResult("windows_codex_ask_lark_send", False, "codex_input_focus_failed", evidence)

        self.io.paste(clean_question, wait=0.4)
        typed = self.io.screenshot_active_window(self.out_dir, f"codex_ask_typed_{label}")
        evidence["screenshots"]["typed"] = typed
        evidence["typed_visual"] = ocr_image_state(typed)
        _append_evidence_timeline(evidence, evidence_path, "paste_codex_question", "done", "pasted user question into Codex", {"screenshot": typed, "question_len": len(clean_question)})
        self.io.press("enter", wait=1.0)
        _append_evidence_timeline(evidence, evidence_path, "submit_codex_question", "done", "submitted question to Codex", {})

        wait_limit = max(10, min(int(wait_seconds or 90), 600))
        wait_start = time.time()
        deadline = time.time() + wait_limit
        last_fingerprint = ""
        stable_count = 0
        captures: list[dict[str, Any]] = []
        while time.time() < deadline:
            time.sleep(5.0)
            focus_guard = self._ensure_codex_foreground(timeout=2.0)
            if not focus_guard.get("ok"):
                elapsed = time.time() - wait_start
                captures.append({"focus_guard": focus_guard, "elapsed_seconds": round(elapsed, 1), "active": False, "stable_count": stable_count})
                evidence["wait_captures"] = captures
                _append_evidence_timeline(evidence, evidence_path, "wait_codex_reply", "failed", "Codex focus lost while waiting", {"focus_guard": focus_guard, "elapsed_seconds": round(elapsed, 1)})
                break
            shot = self.io.screenshot_active_window(self.out_dir, f"codex_ask_wait_{label}_{len(captures)+1}")
            visual = ocr_image_state(shot)
            text = str(visual.get("ocr_text") or "")
            fp = _ocr_fingerprint(text)
            active = _codex_generation_active(text)
            if fp and fp == last_fingerprint:
                stable_count += 1
            else:
                stable_count = 0
                last_fingerprint = fp
            elapsed = time.time() - wait_start
            captures.append({"screenshot": shot, "visual": visual, "stable_count": stable_count, "active": active, "elapsed_seconds": round(elapsed, 1)})
            evidence["wait_captures"] = captures
            _append_evidence_timeline(evidence, evidence_path, "wait_codex_reply", "running" if active or stable_count < 2 else "done", f"capture {len(captures)} stable_count={stable_count} active={active}", {"screenshot": shot, "ocr_text_len": len(text), "stable_count": stable_count, "active": active, "elapsed_seconds": round(elapsed, 1)})
            min_wait = min(20, wait_limit)
            if stable_count >= 2 and len(text) > 80 and not active and elapsed >= min_wait:
                break

        final_focus_guard = self._ensure_codex_foreground(timeout=3.0)
        evidence["codex_final_focus_guard"] = final_focus_guard
        final_shot = self.io.screenshot_active_window(self.out_dir, f"codex_ask_final_{label}" if final_focus_guard.get("ok") else f"codex_ask_final_focus_lost_{label}")
        final_visual = ocr_image_state(final_shot)
        evidence["screenshots"]["final"] = final_shot
        evidence["final_visual"] = final_visual
        vision_extract = _call_qwen_vision_codex_extract(final_shot, "Codex answer", feature_query=clean_question)
        vision_text = str(vision_extract.get("content") or "").strip()
        evidence["vision_extract"] = {k: v for k, v in vision_extract.items() if k != "content"}
        evidence["vision_text"] = vision_text
        _append_evidence_timeline(evidence, evidence_path, "extract_codex_reply_by_vision", "done" if vision_extract.get("ok") else "check", str(vision_extract.get("detail") or ""), {"screenshot": final_shot, "vision_text_len": len(vision_text)})

        if _truthy(os.environ.get("JACHIN_CODEX_COPY_FALLBACK_ENABLED")):
            copied = self._codex_copy_latest_response("Codex answer", feature_query=clean_question)
        else:
            copied = {"ok": False, "detail": "copy_fallback_disabled_visual_first", "text": "", "attempts": []}
        copied_text = str(copied.get("text") or "").strip()
        evidence["copied"] = {k: v for k, v in copied.items() if k != "text"}
        evidence["copied_text"] = copied_text
        choice = _choose_codex_generic_reply(copied_text, str(final_visual.get("ocr_text") or ""), vision_text=vision_text, question=clean_question)
        message_text = str(choice.get("message_text") or "").strip()
        validation = dict(choice.get("validation") or {})
        evidence.update(
            {
                "codex_reply": message_text,
                "message_text": message_text,
                "message_source": str(choice.get("message_source") or ""),
                "reply_validation": validation,
                "vision_validation": choice.get("vision_validation"),
                "copied_validation": choice.get("copied_validation"),
                "ocr_validation": choice.get("ocr_validation"),
                "ocr_fallback_text": choice.get("ocr_fallback_text"),
            }
        )
        _append_evidence_timeline(evidence, evidence_path, "validate_codex_reply", "done" if validation.get("ok") else "failed", f"message_source={choice.get('message_source')}", {"validation": validation})
        report.write_text(message_text or str(final_visual.get("ocr_text") or ""), encoding="utf-8")
        _append_evidence_timeline(evidence, evidence_path, "write_report", "done", "wrote Codex reply report", {"report_path": str(report), "message_len": len(message_text)})

        send_result: dict[str, Any] | None = None
        if not validation.get("ok"):
            send_result = {"ok": False, "detail": "codex_reply_validation_failed"}
            _append_evidence_timeline(evidence, evidence_path, "send_lark", "failed", "codex_reply_validation_failed", {"validation": validation})
        else:
            def lark_event(stage: str, status: str, detail: str, row: dict[str, Any]) -> None:
                _append_evidence_timeline(evidence, evidence_path, f"lark.{stage}", status, detail, row)

            send_result = asdict(self.lark_send_message(clean_recipients, message_text, timeline_cb=lark_event))
            _append_evidence_timeline(evidence, evidence_path, "send_lark_complete", "done" if send_result.get("ok") else "failed", str(send_result.get("detail") or ""), {"recipients": clean_recipients, "send_result": send_result})

        ok = bool(validation.get("ok")) and bool(send_result and send_result.get("ok"))
        evidence["ok"] = ok
        evidence["detail"] = "codex_reply_sent" if ok else (str(send_result.get("detail") or "codex_reply_not_sent") if isinstance(send_result, dict) else "codex_reply_not_sent")
        evidence["send_result"] = send_result
        panel_path = _write_evidence_panel(
            self.out_dir,
            title="Codex Ask to Lark Evidence",
            task="windows_codex_ask_lark_send",
            ok=ok,
            detail=evidence["detail"],
            evidence=evidence,
        )
        evidence["evidence_panel_path"] = panel_path
        _append_evidence_timeline(evidence, evidence_path, "render_evidence_panel", "done", "rendered evidence HTML panel", {"evidence_panel_path": panel_path})
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        return TaskResult("windows_codex_ask_lark_send", ok, evidence["detail"], evidence)
    def codex_lark_workflow_template(
        self,
        project_name: str = "",
        project_path: str = "",
        directory_path: str = "",
        feature_query: str = "",
        bug_query: str = "",
        original_user_input: str = "",
        recipients: list[str] | None = None,
        since_days: int = 3,
        wait_seconds: int = 90,
        send_summary: bool = False,
        remember: bool = True,
    ) -> TaskResult:
        selected_path = str(project_path or directory_path or "").strip()
        selected_name = str(project_name or "").strip()
        if not selected_name and selected_path:
            selected_name = Path(selected_path).expanduser().resolve().name
        if not selected_name:
            return TaskResult(
                "windows_codex_lark_workflow_template",
                False,
                "project_name_required",
                {"project_path": selected_path, "directory_path": directory_path},
            )

        query_parts = [str(feature_query or "").strip()]
        if directory_path and not project_path:
            query_parts.append(f"directory briefing: {directory_path}")
        if bug_query:
            query_parts.append(f"bug analysis: {bug_query}")
        merged_query = " | ".join(part for part in query_parts if part) or "latest project progress"

        run = self.codex_project_briefing_to_lark(
            project_name=selected_name,
            project_path=selected_path,
            feature_query=merged_query,
            original_user_input=original_user_input,
            recipients=recipients or [],
            since_days=since_days,
            wait_seconds=wait_seconds,
            send_summary=send_summary,
            remember=remember,
        )
        evidence = {
            "template": {
                "project_name": selected_name,
                "project_path": selected_path,
                "directory_path": directory_path,
                "feature_query": feature_query,
                "bug_query": bug_query,
                "original_user_input": original_user_input,
                "merged_query": merged_query,
                "recipients": [str(x).strip() for x in (recipients or []) if str(x).strip()],
                "send_summary": send_summary,
            },
            "run": asdict(run),
            "report_path": run.evidence.get("report_path"),
            "evidence_path": run.evidence.get("evidence_path"),
            "send_result": run.evidence.get("send_result"),
            "message_text": run.evidence.get("message_text"),
        }
        panel_path = _write_evidence_panel(
            self.out_dir,
            title=f"{selected_name} Codex Lark Template Evidence",
            task="windows_codex_lark_workflow_template",
            ok=run.ok,
            detail=run.detail,
            evidence=evidence,
        )
        evidence["evidence_panel_path"] = panel_path
        return TaskResult(
            "windows_codex_lark_workflow_template",
            run.ok,
            "template_workflow_completed" if run.ok else run.detail,
            evidence,
        )

    def codex_lark_standard_demo(
        self,
        project_name: str = "Jachin",
        project_path: str = "",
        recipients: list[str] | None = None,
        since_days: int = 3,
        wait_seconds: int = 120,
        send_summary: bool = True,
        remember: bool = True,
    ) -> TaskResult:
        clean_project = str(project_name or "Jachin").strip() or "Jachin"
        clean_recipients = [str(x).strip() for x in (recipients or []) if str(x).strip()]
        return self.codex_lark_workflow_template(
            project_name=clean_project,
            project_path=project_path,
            feature_query="OS assistant Codex Lark workflow",
            recipients=clean_recipients,
            since_days=since_days,
            wait_seconds=wait_seconds,
            send_summary=send_summary,
            remember=remember,
        )

    def daily_office_briefing(
        self,
        recipients: list[str] | None = None,
        paths_json: str = "",
        since_days: int = 1,
        send_summary: bool = False,
        open_report: bool = True,
        reveal_key_file: bool = True,
        max_files: int = 60,
    ) -> TaskResult:
        clean_recipients = [str(x).strip() for x in (recipients or []) if str(x).strip()]
        windows = self.window_list(limit=80)
        status = self.system_status()
        recent = self.recent_files(paths_json=paths_json, since_days=since_days, max_results=max_files)
        files = list(recent.evidence.get("files") or [])
        by_category = dict(recent.evidence.get("by_category") or {})
        active_title = ((windows.evidence.get("active") or {}).get("title") or self.win.active_title())
        out = self.out_dir / f"jachin_daily_office_briefing_{now_tag()}.md"
        evidence_path = out.with_suffix(".evidence.json")
        roots = list(recent.evidence.get("roots") or [])
        lines = [
            "# Jachin Daily Office Briefing",
            "",
            f"- Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- Active window: {active_title}",
            f"- Office windows: {len(windows.evidence.get('office_windows') or [])}",
            f"- Recent file count: {recent.evidence.get('count')}",
            f"- Recent file categories: {by_category}",
            f"- Scan roots: {roots}",
            "",
            "## Changed Files",
        ]
        for row in files[:40]:
            lines.append(_brief_file_line(row))
        lines.extend(["", "## System", f"- Network: {status.evidence.get('network')}", ""])
        out.write_text("\n".join(lines), encoding="utf-8")
        evidence_payload = {"windows": windows.evidence, "status": status.evidence, "recent": recent.evidence, "report_path": str(out)}

        reveal_result: dict[str, Any] | None = None
        if reveal_key_file and files:
            reveal_result = asdict(self.file_reveal_in_explorer(str(files[0].get("path") or "")))

        open_result: dict[str, Any] | None = None
        if open_report:
            open_result = asdict(self.file_open(str(out)))

        message_lines = [
            "Jachin Daily Office Briefing",
            f"Recent files: {recent.evidence.get('count')} {by_category}",
            f"Active window: {active_title}",
            f"Report: {out}",
        ]
        if files:
            message_lines.append("Top changed files:")
            for row in files[:8]:
                message_lines.append(_brief_file_line(row))
        message = "\n".join(message_lines)
        send_result: dict[str, Any] | None = None
        if send_summary and clean_recipients:
            send_result = asdict(self.lark_send_message(clean_recipients, message))

        ok = bool(out.exists())
        if send_summary and clean_recipients:
            ok = ok and bool(send_result and send_result.get("ok"))
        evidence_payload.update(
            {
                "recipients": clean_recipients,
                "message_preview": message,
                "reveal_result": reveal_result,
                "open_result": open_result,
                "send_result": send_result,
            }
        )
        panel_path = _write_evidence_panel(
            self.out_dir,
            title="Jachin Daily Office Briefing Evidence",
            task="windows_daily_office_briefing",
            ok=ok,
            detail="daily_briefing_sent" if send_result and send_result.get("ok") else "daily_briefing_ready",
            evidence=evidence_payload,
        )
        evidence_payload["evidence_panel_path"] = panel_path
        evidence_path.write_text(json.dumps(evidence_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return TaskResult(
            "windows_daily_office_briefing",
            ok,
            "daily_briefing_sent" if send_result and send_result.get("ok") else "daily_briefing_ready",
            {
                "recipients": clean_recipients,
                "send_summary": send_summary,
                "report_path": str(out),
                "evidence_path": str(evidence_path),
                "evidence_panel_path": panel_path,
                "message_preview": message,
                "windows": windows.evidence,
                "system": status.evidence,
                "recent": recent.evidence,
                "reveal_result": reveal_result,
                "open_result": open_result,
                "send_result": send_result,
            },
        )

    def file_bridge_to_app(
        self,
        file_path: str = "",
        app_name: str = "",
        paths_json: str = "",
        since_days: int = 1,
        open_dialog_hotkey: str = "ctrl+o",
    ) -> TaskResult:
        selected = str(file_path or "").strip()
        recent: dict[str, Any] | None = None
        if not selected:
            recent_result = self.recent_files(paths_json=paths_json, since_days=since_days, max_results=20)
            recent = recent_result.evidence
            candidates = [f for f in recent_result.evidence.get("files", []) if Path(str(f.get("path") or "")).is_file()]
            if candidates:
                selected = str(candidates[0].get("path") or "")
        if not selected:
            return TaskResult("windows_file_bridge_to_app", False, "no_file_selected", {"recent": recent})
        reveal = self.file_reveal_in_explorer(selected)
        attach = self.file_attach_to_app(selected, app_name=app_name, open_dialog_hotkey=open_dialog_hotkey)
        return TaskResult(
            "windows_file_bridge_to_app",
            attach.ok,
            "file_bridged_to_app" if attach.ok else "file_bridge_failed",
            {"file": _file_stat(selected), "app_name": app_name, "recent": recent, "reveal": asdict(reveal), "attach": asdict(attach)},
        )

    def os_mission_execute(self, goal: str = "", steps_json: str = "", dry_run: bool = False, confirm_send: bool = False) -> TaskResult:
        steps = _json_object_list(steps_json)
        if not steps:
            steps = [
                {"action": "window_list"},
                {"action": "system_status"},
                {"action": "recent_files"},
                {"action": "daily_office_briefing", "send_summary": False},
            ]
        if dry_run:
            return TaskResult("windows_os_mission_execute", True, "mission_planned", {"goal": goal, "steps": steps, "dry_run": True})

        evidence_steps: list[dict[str, Any]] = []
        ok = True
        for index, step in enumerate(steps, start=1):
            action = str(step.get("action") or "").strip().lower()
            result: TaskResult
            if action in ("window_list", "windows"):
                result = self.window_list(limit=int(step.get("limit") or 80))
            elif action in ("active_window", "active"):
                result = self.active_window()
            elif action in ("system_status", "system"):
                result = self.system_status(network_host=str(step.get("network_host") or "www.baidu.com"))
            elif action in ("recent_files", "files"):
                result = self.recent_files(
                    paths_json=str(step.get("paths_json") or ""),
                    since_days=int(step.get("since_days") or 1),
                    max_results=int(step.get("max_results") or 120),
                )
            elif action in ("open_app", "ensure_app"):
                result = self.ensure_app(str(step.get("app_name") or step.get("app") or ""))
            elif action == "app_matrix":
                result = self.app_switch_matrix(apps_json=str(step.get("apps_json") or ""), timeout=float(step.get("timeout") or 4.0))
            elif action == "reveal_file":
                result = self.file_reveal_in_explorer(str(step.get("path") or ""))
            elif action == "open_file":
                result = self.file_open(str(step.get("path") or ""))
            elif action == "file_bridge_to_app":
                result = self.file_bridge_to_app(
                    file_path=str(step.get("file_path") or step.get("path") or ""),
                    app_name=str(step.get("app_name") or step.get("app") or ""),
                    paths_json=str(step.get("paths_json") or ""),
                    since_days=int(step.get("since_days") or 1),
                    open_dialog_hotkey=str(step.get("open_dialog_hotkey") or "ctrl+o"),
                )
            elif action == "daily_office_briefing":
                recipients = _json_string_list(str(step.get("recipients_json") or "[]"))
                result = self.daily_office_briefing(
                    recipients=recipients,
                    paths_json=str(step.get("paths_json") or ""),
                    since_days=int(step.get("since_days") or 1),
                    send_summary=_truthy(step.get("send_summary")),
                    open_report=_truthy(step.get("open_report", True)),
                    reveal_key_file=_truthy(step.get("reveal_key_file", True)),
                    max_files=int(step.get("max_files") or 60),
                )
            elif action == "lark_send_message":
                if not confirm_send:
                    result = _confirmation_required_result(
                        "windows_os_mission_execute",
                        "send_lark_message",
                        {"step": step, "reason": "confirm_send_required"},
                    )
                else:
                    recipients = _json_string_list(str(step.get("recipients_json") or "[]"))
                    result = self.lark_send_message(recipients, str(step.get("message") or ""))
            else:
                result = TaskResult("windows_os_mission_execute", False, "unknown_action", {"step": step})
            ok = ok and result.ok
            evidence_steps.append({"index": index, "action": action, "result": asdict(result)})
            if not result.ok and result.detail == "confirmation_required":
                break

        evidence_path = self.out_dir / f"os_mission_{now_tag()}.evidence.json"
        evidence_payload = {"goal": goal, "steps": evidence_steps}
        panel_path = _write_evidence_panel(
            self.out_dir,
            title="Jachin OS Mission Evidence",
            task="windows_os_mission_execute",
            ok=ok,
            detail="mission_completed" if ok else "mission_partial_or_blocked",
            evidence=evidence_payload,
        )
        evidence_payload["evidence_panel_path"] = panel_path
        evidence_path.write_text(json.dumps(evidence_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return TaskResult(
            "windows_os_mission_execute",
            ok,
            "mission_completed" if ok else "mission_partial_or_blocked",
            {"goal": goal, "steps": evidence_steps, "evidence_path": str(evidence_path), "evidence_panel_path": panel_path},
        )

    def _uia_set_any_text(self, text: str, prefer_bottom: bool = False) -> bool:
        auto, err = _import_uia()
        if auto is None:
            logger.info("[uia] set text skipped: %s", err)
            return False
        try:
            root = auto.GetRootControl()
            controls = []
            for ctrl, depth in _iter_uia_controls(root, max_depth=7):
                typ = getattr(ctrl, "ControlTypeName", "") or ""
                if typ not in ("EditControl", "ComboBoxControl", "DocumentControl"):
                    continue
                rect = getattr(ctrl, "BoundingRectangle", None)
                if not rect:
                    continue
                width = int(rect.right - rect.left)
                height = int(rect.bottom - rect.top)
                if width <= 40 or height <= 8:
                    continue
                controls.append((int(rect.top), ctrl))
            if not controls:
                return False
            controls.sort(key=lambda row: row[0], reverse=prefer_bottom)
            ctrl = controls[0][1]
            ctrl.SetFocus()
            try:
                ctrl.GetValuePattern().SetValue(text)
                logger.info("[uia] set text by ValuePattern control=%r", getattr(ctrl, "Name", ""))
            except Exception:
                self.io.hotkey("ctrl", "a", wait=0.05)
                self.io.paste(text, wait=0.05)
                logger.info("[uia] set text by clipboard fallback control=%r", getattr(ctrl, "Name", ""))
            return True
        except Exception as e:
            logger.info("[uia] set text failed: %r", e)
            return False

    def _uia_click_first(self, names: tuple[str, ...], timeout: float = 3.0) -> bool:
        auto, err = _import_uia()
        if auto is None:
            logger.info("[uia] click skipped: %s", err)
            return False
        wanted = tuple(n.lower() for n in names)
        deadline = time.time() + timeout
        while time.time() < deadline:
            root = auto.GetRootControl()
            for ctrl, _depth in _iter_uia_controls(root, max_depth=7):
                name = (getattr(ctrl, "Name", "") or "").strip()
                if name and name.lower() in wanted:
                    try:
                        ctrl.SetFocus()
                    except Exception:
                        pass
                    try:
                        ctrl.Click()
                        logger.info("[uia] clicked name=%r", name)
                        return True
                    except Exception as e:
                        logger.debug("[uia] click failed name=%r err=%r", name, e)
            time.sleep(0.2)
        return False

    def _ensure_active_title_contains(self, needle: str, max_tabs: int = 16) -> bool:
        needle_l = (needle or "").lower()
        for attempt in range(max(1, max_tabs)):
            active = self.win.active_title()
            if needle_l and needle_l in active.lower():
                logger.info("[window] active target confirmed title=%r", active)
                return True
            logger.info("[window] active title mismatch attempt=%d active=%r target=%r", attempt + 1, active, needle)
            self.io.hotkey("ctrl", "tab", wait=0.2)
        active = self.win.active_title()
        ok = bool(needle_l and needle_l in active.lower())
        logger.info("[window] active target final ok=%s title=%r target=%r", ok, active, needle)
        return ok

    def focus_or_raise_app(
        self,
        app_name: str,
        args: list[str] | None = None,
        timeout: float = 6.0,
        max_attempts: int = 3,
        launch_if_missing: bool = True,
        stage: str = "focus_or_raise_app",
    ) -> TaskResult:
        app_key = normalize_app_name(app_name)
        profile = APP_PROFILES.get(app_key)
        if not profile:
            profile = {
                "aliases": (app_key,),
                "keywords": (app_key,),
                "exe_names": (f"{app_key}.exe",),
                "candidate_paths": (f"{app_key}.exe",),
            }
        keywords = tuple(str(x) for x in profile.get("keywords", ()) if str(x).strip()) or (app_key,)
        contract = self._execution_contract(app_key, goal=f"focus_or_raise:{app_key}")
        if app_key == "browser":
            browser = _find_browser()
            exe, source = (browser, "detected_browser") if browser else ("", "not_found")
        else:
            exe, source = _find_app_executable(profile)
        if launch_if_missing and not exe:
            return TaskResult(
                "focus_or_raise_app",
                False,
                "app_executable_not_found",
                {
                    "app": app_name,
                    "app_key": app_key,
                    "path_source": source,
                    "env_hint": profile.get("env") or "",
                    "candidate_paths": list(profile.get("candidate_paths", ())),
                    "execution_contract": contract.to_dict(),
                },
            )

        attempts: list[dict[str, Any]] = []
        final_guard: EnvironmentVerification | None = None
        for attempt in range(1, max(1, int(max_attempts or 1)) + 1):
            launch_result: dict[str, Any] | None = None
            launched = False
            if launch_if_missing and exe and attempt == 1:
                launch_result = self.io.launch_result(exe, keywords, args=args or [], wait=1.0)
                launched = bool(launch_result.get("focused"))
                if not launch_result.get("ok"):
                    detail = str(launch_result.get("detail") or "app_launch_failed")
                    attempts.append(
                        {
                            "attempt": attempt,
                            "launched": False,
                            "focused": False,
                            "ok": False,
                            "detail": detail,
                            "launch_result": launch_result,
                        }
                    )
                    return TaskResult(
                        "focus_or_raise_app",
                        False,
                        detail,
                        {
                            "app": app_name,
                            "app_key": app_key,
                            "exe": exe,
                            "path_source": source,
                            "keywords": list(keywords),
                            "attempts": attempts,
                            "launch_result": launch_result,
                            "execution_contract": contract.to_dict(),
                        },
                    )
            focused = self.win.focus_by_keywords(keywords, timeout=float(timeout or 6.0))
            guard = self._verify_environment(contract, stage=stage, action=f"verify_foreground_attempt_{attempt}")
            final_guard = guard
            screenshot = self.io.screenshot_active_window(self.out_dir, f"focus_or_raise_{app_key}_attempt_{attempt}")
            attempts.append(
                {
                    "attempt": attempt,
                    "launched": launched,
                    "focused": focused,
                    "ok": guard.ok,
                    "detail": guard.detail,
                    "active": guard.active,
                    "environment_guard": guard.to_dict(),
                    "launch_result": launch_result,
                    "screenshot": screenshot,
                }
            )
            if guard.ok:
                return TaskResult(
                    "focus_or_raise_app",
                    True,
                    "app_focused_and_verified",
                    {
                        "app": app_name,
                        "app_key": app_key,
                        "exe": exe,
                        "path_source": source,
                        "keywords": list(keywords),
                        "attempts": attempts,
                        "execution_contract": contract.to_dict(),
                        "environment_guard": guard.to_dict(),
                        "screenshot": screenshot,
                    },
                )
            time.sleep(0.25)

        fallback_guard = final_guard or self._verify_environment(contract, stage=stage, action="verify_foreground_final")
        return TaskResult(
            "focus_or_raise_app",
            False,
            "app_focus_failed" if fallback_guard.detail in {"wrong_foreground_app", "foreground_app_unknown"} else fallback_guard.detail,
            {
                "app": app_name,
                "app_key": app_key,
                "exe": exe,
                "path_source": source,
                "keywords": list(keywords),
                "attempts": attempts,
                "execution_contract": contract.to_dict(),
                "environment_guard": fallback_guard.to_dict(),
            },
        )

    def open_app(self, app_name: str, args: list[str] | None = None) -> TaskResult:
        result = self.focus_or_raise_app(app_name, args=args or [], timeout=6.0, max_attempts=3, launch_if_missing=True, stage="open_app")
        evidence = dict(result.evidence)
        evidence["focused"] = bool(result.ok)
        return TaskResult(
            "open_app",
            result.ok,
            "app_opened_and_window_verified" if result.ok else result.detail,
            evidence,
        )


    def ensure_app(self, app_name: str, args: list[str] | None = None, timeout: float = 4.0) -> TaskResult:
        app_key = normalize_app_name(app_name)
        profile = APP_PROFILES.get(app_key, {"keywords": (app_key,)})
        keywords = tuple(str(x) for x in profile.get("keywords", ()) if str(x).strip()) or (app_key,)
        before = self.win.active_title()
        focused_existing = self.win.focus_by_keywords(keywords, timeout=float(timeout or 4.0))
        if focused_existing:
            screenshot = self.io.screenshot_active_window(self.out_dir, f"ensure_app_{app_key}_existing")
            contract = self._execution_contract(app_key, goal=f"ensure_app:{app_key}")
            guard = self._verify_environment(contract, stage="ensure_app", action="verify_existing_foreground")
            return TaskResult(
                "windows_ensure_app",
                bool(guard.ok),
                "existing_window_focused" if guard.ok else "app_focus_failed",
                {
                    "app": app_name,
                    "app_key": app_key,
                    "before": before,
                    "active_title": self.win.active_title(),
                    "started_new": False,
                    "keywords": list(keywords),
                    "environment_guard": guard.to_dict(),
                    "launch_result": launch_result,
                    "screenshot": screenshot,
                },
            )
        opened = self.open_app(app_name, args=args or [])
        ev = dict(opened.evidence)
        ev["before"] = before
        ev["started_new"] = True
        return TaskResult("windows_ensure_app", opened.ok, opened.detail, ev)

    def app_switch_matrix(self, apps_json: str = "", timeout: float = 4.0) -> TaskResult:
        apps = _json_string_list(apps_json, ["explorer", "calculator", "notepad", "browser", "lark"])
        results: list[dict[str, Any]] = []
        for app in apps:
            result = self.ensure_app(app, timeout=timeout)
            results.append(asdict(result))
        ok = all(bool(r.get("ok")) for r in results) if results else False
        return TaskResult(
            "windows_app_switch_matrix",
            ok,
            "app_matrix_verified" if ok else "app_matrix_partial",
            {"apps": apps, "results": results, "active_title": self.win.active_title()},
        )

    def _lark_open_target(self, target: str) -> dict[str, Any]:
        label = _safe_label(target)
        before = self.io.screenshot_active_window(self.out_dir, f"lark_before_{label}")
        self.io.hotkey("ctrl", "k", wait=0.4)
        rect = self.win.active_rect()
        if rect:
            _title, left, top, width, height = rect
            self.io.click(int(left + width * 0.48), int(top + height * 0.12), wait=0.1)
            self.io.hotkey("ctrl", "a", wait=0.05)
        self.io.paste(target, wait=0.8)
        search = self.io.screenshot_active_window(self.out_dir, f"lark_search_{label}")
        search_visual = ocr_image_state(search)
        rect = self.win.active_rect()
        if rect:
            _title, left, top, width, height = rect
            self.io.click(int(left + width * 0.20), int(top + height * 0.275), wait=1.4)
        else:
            self.io.press("enter", wait=1.4)
        opened = self.io.screenshot_active_window(self.out_dir, f"lark_opened_{label}")
        opened_visual = ocr_image_state(opened)
        opened_text = str(opened_visual.get("ocr_text") or "")
        identity = _lark_recipient_identity_check(target, opened_text)
        return {
            "target": target,
            "ok": bool(identity.get("ok")),
            "target_visible": bool(identity.get("target_visible_fullscreen")),
            "identity_verified": bool(identity.get("ok")),
            "identity_check": identity,
            "active_title": self.win.active_title(),
            "visual": opened_visual,
            "screenshots": {
                "before": before,
                "search": search,
                "opened": opened,
            },
            "search_visual": search_visual,
        }

    def _lark_verify_current_recipient_identity(self, recipient: str, label: str) -> dict[str, Any]:
        screenshot = self.io.screenshot_active_window(self.out_dir, label)
        visual = ocr_image_state(screenshot)
        text = str(visual.get("ocr_text") or "")
        identity = _lark_recipient_identity_check(recipient, text)
        return {
            "ok": bool(identity.get("ok")),
            "recipient": recipient,
            "screenshot": screenshot,
            "visual": visual,
            "identity_check": identity,
            "active_title": self.win.active_title(),
        }
    def _lark_focus_message_area(self) -> None:
        self._lark_ensure_focused()
        rect = self.win.active_rect()
        if not rect:
            return
        _title, left, top, width, height = rect
        self.io.click(int(left + width * 0.80), int(top + height * 0.55), wait=0.12)

    def _lark_ensure_focused(self) -> bool:
        active = self.win.active_title()
        active_l = active.lower()
        browser_title = any(k in active_l for k in ("edge", "chrome", "firefox", "browser"))
        if not browser_title and any(k in active_l for k in ("lark", "feishu", "椋炰功")):
            return True
        focused = self.win.focus_by_keywords(
            ("lark", "feishu", "椋炰功"),
            timeout=3.0,
            exclude_keywords=("edge", "chrome", "firefox", "browser"),
        )
        logger.info("[scenario:lark] focus guard active_before=%r focused=%s active_after=%r", active, focused, self.win.active_title())
        return focused

    def _lark_message_area_point(self) -> tuple[int, int] | None:
        self._lark_ensure_focused()
        rect = self.win.active_rect()
        if not rect:
            return None
        _title, left, top, width, height = rect
        return int(left + width * 0.80), int(top + height * 0.56)

    def _lark_scrollbar_point(self) -> tuple[int, int, int] | None:
        self._lark_ensure_focused()
        rect = self.win.active_rect()
        if not rect:
            return None
        _title, left, top, width, height = rect
        x = int(left + width * 0.965)
        y1 = int(top + height * 0.45)
        y2 = int(top + height * 0.30)
        return x, y1, y2

    def _lark_scroll_history_up(self, scroll_clicks: int, strategy: str = "wheel") -> dict[str, Any]:
        clicks = max(1, abs(int(scroll_clicks or 6)))
        point = self._lark_message_area_point()
        row: dict[str, Any] = {"strategy": strategy, "scroll_clicks": clicks}
        if point:
            x, y = point
            row["point"] = {"x": x, "y": y}
            self.io.move_to(x, y, wait=0.05)
        if strategy == "wheel":
            self.io.scroll(clicks, wait=0.85)
            return row
        if strategy == "pageup":
            if point:
                self.io.click(point[0], point[1], wait=0.05)
            self.io.press("pageup", presses=2, wait=0.85)
            return row
        if strategy == "drag_scrollbar":
            bar = self._lark_scrollbar_point()
            if bar:
                x, y1, y2 = bar
                row["drag"] = {"x": x, "from_y": y1, "to_y": y2}
                self.io.move_to(x, y1, wait=0.05)
                self.io.drag_to(x, y2, duration=0.28, wait=0.85)
            else:
                row["skipped"] = "no_active_window_rect"
            return row
        row["skipped"] = "unknown_strategy"
        return row

    def _lark_capture_page(self, chat: str, label: str, page: int) -> dict[str, Any]:
        focus_ok = self._lark_ensure_focused()
        active_title = self.win.active_title()
        screenshot = self.io.screenshot_active_window(self.out_dir, f"{label}_{_safe_label(chat)}_page_{page}")
        visual = ocr_image_state(screenshot)
        text = str(visual.get("ocr_text") or "")
        return {
            "page": page,
            "active_title": active_title,
            "focus_guard_ok": focus_ok,
            "screenshot": screenshot,
            "visual": visual,
            "ocr_fingerprint": _ocr_fingerprint(text),
            "ocr_content_keys": _ocr_content_keys(text),
        }

    def _lark_capture_next_scrolled_page(
        self,
        chat: str,
        label: str,
        page: int,
        previous_fingerprint: str,
        previous_content_keys: list[str],
        scroll_clicks: int,
    ) -> dict[str, Any]:
        attempts: list[dict[str, Any]] = []
        base_clicks = max(1, abs(int(scroll_clicks or 6)))
        wheel_clicks = [max(10, base_clicks * 2), max(14, base_clicks * 3)]
        plan: list[tuple[str, int]] = [("wheel", wheel_clicks[0]), ("wheel", wheel_clicks[1]), ("pageup", base_clicks), ("drag_scrollbar", base_clicks)]
        last_row: dict[str, Any] | None = None
        for strategy, clicks in plan:
            attempts.append(self._lark_scroll_history_up(clicks, strategy=strategy))
            row = self._lark_capture_page(chat, label, page)
            row["scroll_attempts"] = attempts
            fp = str(row.get("ocr_fingerprint") or "")
            overlap = _line_overlap_ratio(previous_content_keys, list(row.get("ocr_content_keys") or []))
            row["scroll_overlap_ratio"] = overlap
            last_row = row
            if fp and fp == previous_fingerprint:
                logger.info("[scenario:lark_history] scroll strategy=%s did not change page=%d target=%r", strategy, page, chat)
                continue
            if overlap >= 0.82:
                logger.info(
                    "[scenario:lark_history] scroll strategy=%s overlap too high %.2f page=%d target=%r",
                    strategy,
                    overlap,
                    page,
                    chat,
                )
                continue
            if overlap < 0.18 and previous_content_keys:
                row["scroll_gap_risk"] = True
            else:
                row["scroll_gap_risk"] = False
            if not previous_fingerprint or fp:
                row["scroll_verified_changed"] = True
                return row
        row = last_row or self._lark_capture_page(chat, label, page)
        row["scroll_attempts"] = attempts
        row["scroll_verified_changed"] = False
        return row

    def lark_send_message(
        self,
        recipients: list[str],
        message: str,
        max_attempts: int = 2,
        timeline_cb: Callable[[str, str, str, dict[str, Any]], None] | None = None,
    ) -> TaskResult:
        clean_recipients = [str(x).strip() for x in recipients if str(x).strip()]
        msg = str(message or "")
        if not clean_recipients:
            return TaskResult("lark_send_message", False, "recipients_empty", {"recipients": recipients})
        if not msg.strip():
            return TaskResult("lark_send_message", False, "message_empty", {"recipients": clean_recipients})

        contract = self._execution_contract("lark", goal="send_message")
        opened = self.open_app("lark")
        if timeline_cb:
            timeline_cb("open_lark", "done" if opened.ok else "failed", opened.detail, {"open_result": asdict(opened)})
        if not opened.ok:
            return TaskResult(
                "lark_send_message",
                False,
                "app_focus_failed" if opened.detail == "app_focus_failed" else "lark_open_failed",
                {"open_result": asdict(opened), "recipients": clean_recipients, "execution_contract": contract.to_dict()},
            )
        guard = self._verify_environment(contract, stage="before_recipient_loop", action="prepare_send_message")
        guard, focus_recovery = self._recover_environment_if_needed(
            contract,
            guard,
            stage="before_recipient_loop",
            action="prepare_send_message",
            launch_if_missing=False,
        )
        if not guard.ok:
            unsafe_evidence = {"open_result": asdict(opened), "recipients": clean_recipients}
            if focus_recovery:
                unsafe_evidence["focus_recovery"] = focus_recovery
            return self._unsafe_environment_result("lark_send_message", contract, guard, unsafe_evidence)

        deliveries: list[dict[str, Any]] = []
        for recipient in clean_recipients:
            logger.info("[scenario:lark] recipient=%r message_len=%d", recipient, len(msg))
            attempts: list[dict[str, Any]] = []
            delivery: dict[str, Any] | None = None
            guard = self._verify_environment(contract, stage="before_open_target", action="locate_target_object")
            guard, focus_recovery = self._recover_environment_if_needed(
                contract,
                guard,
                stage="before_open_target",
                action="locate_target_object",
                launch_if_missing=False,
            )
            if not guard.ok:
                row = {"recipient": recipient, "ok": False, "failure_stage": guard.detail, "environment_guard": guard.to_dict()}
                if focus_recovery:
                    row["focus_recovery"] = focus_recovery
                deliveries.append(row)
                continue
            opened_target = self._lark_open_target(recipient)
            guard = self._verify_environment(contract, stage="after_open_target", action="verify_target_environment")
            guard, focus_recovery = self._recover_environment_if_needed(
                contract,
                guard,
                stage="after_open_target",
                action="verify_target_environment",
                launch_if_missing=False,
            )
            if not guard.ok:
                opened_target["environment_guard"] = guard.to_dict()
                if focus_recovery:
                    opened_target["focus_recovery"] = focus_recovery
                deliveries.append({"recipient": recipient, "ok": False, "failure_stage": guard.detail, "opened_target": opened_target, "environment_guard": guard.to_dict()})
                continue
            if focus_recovery:
                opened_target["focus_recovery"] = focus_recovery
            if not opened_target.get("ok"):
                identity_reason = str((opened_target.get("identity_check") or {}).get("reason") or "")
                failure_stage = "wrong_recipient_opened" if "wrong_" in identity_reason or "search_overlay" in identity_reason else "recipient_not_verified"
                deliveries.append(
                    {
                        "recipient": recipient,
                        "ok": False,
                        "failure_stage": failure_stage,
                        "opened_target": opened_target,
                        "environment_guard": guard.to_dict(),
                    }
                )
                continue
            if timeline_cb:
                timeline_cb(
                    "open_recipient",
                    "done" if opened_target.get("ok") else "check",
                    f"target={recipient}",
                    {"recipient": recipient, "opened_target": opened_target},
                )
            for attempt in range(1, max(1, int(max_attempts)) + 1):
                if attempt > 1 and attempts and not attempts[-1].get("preview_recipient_visible"):
                    opened_target = self._lark_open_target(recipient)
                    if timeline_cb:
                        timeline_cb(
                            "reopen_recipient",
                            "done" if opened_target.get("ok") else "check",
                            f"target={recipient} attempt={attempt}",
                            {"recipient": recipient, "attempt": attempt, "opened_target": opened_target},
                        )
                guard = self._verify_environment(contract, stage="before_text_input", action="type_or_paste_message")
                guard, focus_recovery = self._recover_environment_if_needed(
                    contract,
                    guard,
                    stage="before_text_input",
                    action="type_or_paste_message",
                    launch_if_missing=False,
                )
                if not guard.ok:
                    row = {"attempt": attempt, "ok": False, "failure_stage": guard.detail, "environment_guard": guard.to_dict()}
                    if focus_recovery:
                        row["focus_recovery"] = focus_recovery
                    attempts.append(row)
                    break
                pre_identity = self._lark_verify_current_recipient_identity(
                    recipient,
                    f"lark_identity_before_input_{_safe_label(recipient)}_attempt_{attempt}",
                )
                if not pre_identity.get("ok"):
                    attempts.append(
                        {
                            "attempt": attempt,
                            "ok": False,
                            "failure_stage": "wrong_recipient_opened",
                            "opened_target": opened_target,
                            "recipient_identity": pre_identity,
                            "screenshots": {"identity": pre_identity.get("screenshot")},
                        }
                    )
                    logger.info(
                        "[scenario:lark] recipient identity failed before typing recipient=%r attempt=%d reason=%s",
                        recipient,
                        attempt,
                        (pre_identity.get("identity_check") or {}).get("reason"),
                    )
                    continue
                rect = self.win.active_rect()
                if rect:
                    _title, left, top, width, height = rect
                    self.io.click(int(left + width * 0.84), int(top + height * 0.94), wait=0.25)
                    self.io.hotkey("ctrl", "a", wait=0.05)
                else:
                    self.io.hotkey("ctrl", "end", wait=0.1)

                self.io.paste(msg, wait=0.25)
                typed = self.io.screenshot_active_window(self.out_dir, f"lark_typed_{_safe_label(recipient)}_attempt_{attempt}")
                preview = ocr_image_state(typed)
                preview_text = str(preview.get("ocr_text") or "")
                preview_identity = _lark_recipient_identity_check(recipient, preview_text)
                preview_recipient_ok = bool(preview_identity.get("ok"))
                preview_message_match = _lark_message_visible_match(msg, preview_text)
                preview_message_ok = bool(preview_message_match.get("ok"))
                attempt_row = {
                    "attempt": attempt,
                    "opened_target": opened_target,
                    "recipient_identity_before_input": pre_identity,
                    "preview": preview,
                    "preview_recipient_visible": preview_recipient_ok,
                    "preview_recipient_identity": preview_identity,
                    "preview_message_visible": preview_message_ok,
                    "preview_message_match": preview_message_match,
                    "screenshots": {"typed": typed},
                }
                attempts.append(attempt_row)
                if timeline_cb:
                    timeline_cb(
                        "preview_message",
                        "done" if (preview_recipient_ok and preview_message_ok) else "check",
                        f"target={recipient} attempt={attempt}",
                        {
                            "recipient": recipient,
                            "attempt": attempt,
                            "recipient_visible": preview_recipient_ok,
                            "message_visible": preview_message_ok,
                            "screenshot": typed,
                            "recipient_identity": preview_identity,
                            "message_match": preview_message_match,
                        },
                    )
                guard = self._verify_environment(contract, stage="after_text_input", action="verify_still_in_target_environment")
                guard, focus_recovery = self._recover_environment_if_needed(
                    contract,
                    guard,
                    stage="after_text_input",
                    action="verify_still_in_target_environment",
                    launch_if_missing=False,
                )
                if focus_recovery:
                    attempt_row["focus_recovery_after_text_input"] = focus_recovery
                if not guard.ok:
                    attempt_row["environment_guard"] = guard.to_dict()
                    logger.info("[scenario:lark] unsafe focus after typing recipient=%r attempt=%d detail=%s", recipient, attempt, guard.detail)
                    break
                if not (preview_recipient_ok and preview_message_ok):
                    logger.info(
                        "[scenario:lark] preview failed recipient=%r attempt=%d recipient_ok=%s msg_ok=%s",
                        recipient,
                        attempt,
                        preview_recipient_ok,
                        preview_message_ok,
                    )
                    continue
                guard = self._verify_environment(contract, stage="before_commit_action", action="press_enter_or_send")
                guard, focus_recovery = self._recover_environment_if_needed(
                    contract,
                    guard,
                    stage="before_commit_action",
                    action="press_enter_or_send",
                    launch_if_missing=False,
                )
                if focus_recovery:
                    attempt_row["focus_recovery_before_commit"] = focus_recovery
                if not guard.ok:
                    attempt_row["environment_guard"] = guard.to_dict()
                    break
                commit_identity = self._lark_verify_current_recipient_identity(
                    recipient,
                    f"lark_identity_before_send_{_safe_label(recipient)}_attempt_{attempt}",
                )
                attempt_row["recipient_identity_before_send"] = commit_identity
                if not commit_identity.get("ok"):
                    attempt_row["failure_stage"] = "wrong_recipient_before_send"
                    attempt_row.setdefault("screenshots", {})["identity_before_send"] = commit_identity.get("screenshot")
                    logger.info(
                        "[scenario:lark] recipient identity failed before send recipient=%r attempt=%d reason=%s",
                        recipient,
                        attempt,
                        (commit_identity.get("identity_check") or {}).get("reason"),
                    )
                    continue
                self.io.press("enter", wait=1.1)
                sent = self.io.screenshot_active_window(self.out_dir, f"lark_sent_{_safe_label(recipient)}_attempt_{attempt}")
                visual = ocr_image_state(sent)
                ocr_text = str(visual.get("ocr_text") or "")
                sent_identity = _lark_recipient_identity_check(recipient, ocr_text)
                recipient_ok = bool(sent_identity.get("ok"))
                message_match = _lark_message_visible_match(msg, ocr_text)
                message_ok = bool(message_match.get("ok"))
                delivery = {
                    "recipient": recipient,
                    "ok": bool(recipient_ok and message_ok),
                    "recipient_visible": recipient_ok,
                    "recipient_identity": sent_identity,
                    "message_visible": message_ok,
                    "message_match": message_match,
                    "preview_verified": True,
                    "failure_stage": "" if bool(recipient_ok and message_ok) else ("wrong_recipient_after_send" if not recipient_ok else "post_send_verification_failed"),
                    "active_title": self.win.active_title(),
                    "visual": visual,
                    "attempts": attempts,
                    "screenshots": {
                        **opened_target.get("screenshots", {}),
                        "typed": typed,
                        "sent": sent,
                    },
                }
                if timeline_cb:
                    timeline_cb(
                        "verify_sent",
                        "done" if delivery["ok"] else "failed",
                        f"target={recipient} attempt={attempt}",
                        {
                            "recipient": recipient,
                            "attempt": attempt,
                            "recipient_visible": recipient_ok,
                            "message_visible": message_ok,
                            "screenshot": sent,
                            "recipient_identity": sent_identity,
                            "message_match": message_match,
                        },
                    )
                break
            if delivery is None:
                unsafe_stage = next((str(a.get("failure_stage")) for a in attempts if str(a.get("failure_stage") or "") in {"wrong_foreground_app", "foreground_app_unknown"}), "")
                wrong_recipient_stage = next((str(a.get("failure_stage")) for a in attempts if str(a.get("failure_stage") or "").startswith("wrong_recipient")), "")
                any_recipient_seen = any(bool(a.get("preview_recipient_visible")) for a in attempts)
                any_message_seen = any(bool(a.get("preview_message_visible")) for a in attempts)
                if unsafe_stage:
                    failure_stage = unsafe_stage
                elif wrong_recipient_stage:
                    failure_stage = wrong_recipient_stage
                elif any_recipient_seen and not any_message_seen:
                    failure_stage = "message_preview_verification_failed"
                elif not any_recipient_seen:
                    failure_stage = "recipient_preview_verification_failed"
                else:
                    failure_stage = "preview_or_target_verification_failed"
                delivery = {
                    "recipient": recipient,
                    "ok": False,
                    "recipient_visible": any_recipient_seen,
                    "message_visible": any_message_seen,
                    "preview_verified": False,
                    "active_title": self.win.active_title(),
                    "attempts": attempts,
                    "failure_stage": failure_stage,
                }
                if timeline_cb:
                    timeline_cb(
                        "verify_sent",
                        "failed",
                        f"target={recipient} {failure_stage}",
                        {"recipient": recipient, "attempts": attempts},
                    )
            deliveries.append(delivery)

        ok = all(bool(d.get("ok")) for d in deliveries)
        if ok:
            detail = "sent_and_verified_with_visual"
        elif any(str(d.get("failure_stage") or "").startswith("wrong_recipient") for d in deliveries):
            detail = "wrong_recipient"
        elif any(str(d.get("failure_stage") or "") == "message_preview_verification_failed" for d in deliveries):
            detail = "draft_preview_verification_failed"
        elif any(str(d.get("failure_stage") or "") == "post_send_verification_failed" for d in deliveries):
            detail = "sent_but_post_verification_failed"
        elif any(str(d.get("failure_stage") or "") in {"wrong_foreground_app", "foreground_app_unknown"} for d in deliveries):
            detail = "wrong_foreground_app"
        else:
            detail = "lark_delivery_verification_failed"
        return TaskResult(
            "lark_send_message",
            ok,
            detail,
            {
                "recipients": clean_recipients,
                "message": msg,
                "open_result": asdict(opened),
                "deliveries": deliveries,
            },
        )

    def lark_read_recent_messages(self, target: str, pages: int = 3, scroll_clicks: int = 5) -> TaskResult:
        chat = str(target or "").strip()
        if not chat:
            return TaskResult("lark_read_recent_messages", False, "target_empty", {})
        opened = self.open_app("lark")
        if not opened.ok:
            return TaskResult(
                "lark_read_recent_messages",
                False,
                "lark_open_failed",
                {"open_result": asdict(opened), "target": chat},
            )
        opened_target = self._lark_open_target(chat)
        page_count = max(1, min(int(pages or 1), 8))
        all_texts: list[str] = []
        page_rows: list[dict[str, Any]] = []
        last_fingerprint = ""
        last_content_keys: list[str] = []
        for i in range(page_count):
            if i == 0:
                page_row = self._lark_capture_page(chat, "lark_read", i + 1)
            else:
                page_row = self._lark_capture_next_scrolled_page(
                    chat,
                    "lark_read",
                    i + 1,
                    last_fingerprint,
                    last_content_keys,
                    int(scroll_clicks or 5),
                )
            visual = page_row["visual"]
            text = str(visual.get("ocr_text") or "")
            last_fingerprint = str(page_row.get("ocr_fingerprint") or "")
            last_content_keys = list(page_row.get("ocr_content_keys") or [])
            all_texts.append(text)
            page_rows.append(page_row)
        lines = _dedupe_lines(all_texts)
        classified = _classify_lark_lines(lines)
        timeline_groups = _group_lark_lines_by_time(lines)
        target_visible = any(chat.lower() in str(row.get("visual", {}).get("ocr_text") or "").lower() for row in page_rows)
        return TaskResult(
            "lark_read_recent_messages",
            bool(target_visible and lines),
            "messages_read_with_visual_ocr" if target_visible and lines else "messages_read_but_target_or_text_not_verified",
            {
                "target": chat,
                "open_result": asdict(opened),
                "opened_target": opened_target,
                "pages": page_rows,
                "deduped_lines": lines,
                "line_count": len(lines),
                "timeline_groups": timeline_groups,
                **classified,
            },
        )

    def lark_read_history(self, target: str, days: int = 7, max_pages: int = 18, scroll_clicks: int = 6) -> TaskResult:
        chat = str(target or "").strip()
        if not chat:
            return TaskResult("lark_read_history", False, "target_empty", {})
        day_span = max(1, min(int(days or 1), 31))
        page_count = max(1, min(int(max_pages or 1), 60))
        opened = self.open_app("lark")
        if not opened.ok:
            return TaskResult(
                "lark_read_history",
                False,
                "lark_open_failed",
                {"open_result": asdict(opened), "target": chat, "days": day_span},
            )
        opened_target = self._lark_open_target(chat)
        all_texts: list[str] = []
        page_rows: list[dict[str, Any]] = []
        repeated_pages = 0
        last_fingerprint = ""
        last_content_keys: list[str] = []
        for i in range(page_count):
            if i == 0:
                page_row = self._lark_capture_page(chat, "lark_history", i + 1)
            else:
                page_row = self._lark_capture_next_scrolled_page(
                    chat,
                    "lark_history",
                    i + 1,
                    last_fingerprint,
                    last_content_keys,
                    int(scroll_clicks or 6),
                )
            visual = page_row["visual"]
            text = str(visual.get("ocr_text") or "")
            fingerprint = str(page_row.get("ocr_fingerprint") or "")
            if i > 0 and page_row.get("scroll_verified_changed") is False:
                repeated_pages += 1
            elif fingerprint and fingerprint == last_fingerprint:
                repeated_pages += 1
            else:
                repeated_pages = 0
            last_fingerprint = fingerprint
            last_content_keys = list(page_row.get("ocr_content_keys") or [])
            all_texts.append(text)
            page_rows.append(page_row)
            if repeated_pages >= 2:
                logger.info("[scenario:lark_history] stopping after repeated pages target=%r page=%d", chat, i + 1)
                break
        lines = _dedupe_lines(all_texts)
        summary = _build_lark_history_summary(lines, day_span)
        target_visible = any(chat.lower() in str(row.get("visual", {}).get("ocr_text") or "").lower() for row in page_rows)
        return TaskResult(
            "lark_read_history",
            bool(target_visible and lines),
            "history_read_with_visual_ocr" if target_visible and lines else "history_read_but_target_or_text_not_verified",
            {
                "target": chat,
                "days": day_span,
                "max_pages": page_count,
                "captured_pages": len(page_rows),
                "open_result": asdict(opened),
                "opened_target": opened_target,
                "pages": page_rows,
                "deduped_lines": lines,
                "line_count": len(lines),
                **summary,
            },
        )

    def lark_open_bitable(self, table_name: str, max_attempts: int = 2) -> TaskResult:
        name = str(table_name or "").strip()
        if not name:
            return TaskResult("lark_open_bitable", False, "table_name_empty", {})
        opened = self.open_app("lark")
        if not opened.ok:
            return TaskResult(
                "lark_open_bitable",
                False,
                "lark_open_failed",
                {"open_result": asdict(opened), "table_name": name},
            )

        attempts: list[dict[str, Any]] = []
        final_row: dict[str, Any] | None = None
        for attempt in range(1, max(1, int(max_attempts or 2)) + 1):
            self._lark_ensure_focused()
            rect = self.win.active_rect()
            if not rect:
                attempts.append({"attempt": attempt, "ok": False, "detail": "no_active_window_rect"})
                continue
            _title, left, top, width, height = rect

            self.io.click(int(left + width * 0.08), int(top + height * 0.34), wait=1.0)
            home_shot = self.io.screenshot_active_window(self.out_dir, f"lark_bitable_home_{_safe_label(name)}_{attempt}")
            home_visual = ocr_image_state(home_shot)

            self.io.click(int(left + width * 0.29), int(top + height * 0.13), wait=0.15)
            self.io.hotkey("ctrl", "a", wait=0.05)
            self.io.paste(name, wait=1.0)
            search_shot = self.io.screenshot_active_window(self.out_dir, f"lark_bitable_search_{_safe_label(name)}_{attempt}")
            search_visual = ocr_image_state(search_shot)
            search_text = str(search_visual.get("ocr_text") or "")
            name_key = _compact_match_text(name)
            search_visible = name_key in _compact_match_text(search_text)

            click_y_candidates = (0.43, 0.36, 0.50)
            opened_rows: list[dict[str, Any]] = []
            for idx, y_ratio in enumerate(click_y_candidates, start=1):
                self._lark_ensure_focused()
                self.io.click(int(left + width * 0.54), int(top + height * y_ratio), wait=2.2)
                time.sleep(1.0)
                browser_title = self.win.active_title()
                browser_shot = self.io.screenshot_active_window(
                    self.out_dir,
                    f"lark_bitable_opened_{_safe_label(name)}_{attempt}_{idx}",
                )
                browser_visual = ocr_image_state(browser_shot)
                browser_text = str(browser_visual.get("ocr_text") or "")
                title_ok = name_key in _compact_match_text(browser_title)
                visual_ok = name_key in _compact_match_text(browser_text)
                opened_row = {
                    "row_attempt": idx,
                    "y_ratio": y_ratio,
                    "active_title": browser_title,
                    "title_ok": title_ok,
                    "visual_ok": visual_ok,
                    "screenshot": browser_shot,
                    "visual": browser_visual,
                }
                opened_rows.append(opened_row)
                if title_ok or visual_ok:
                    final_row = {
                        "attempt": attempt,
                        "ok": True,
                        "search_visible": search_visible,
                        "home": {"screenshot": home_shot, "visual": home_visual},
                        "search": {"screenshot": search_shot, "visual": search_visual},
                        "opened_rows": opened_rows,
                    }
                    break
            attempts.append(
                final_row
                or {
                    "attempt": attempt,
                    "ok": False,
                    "search_visible": search_visible,
                    "home": {"screenshot": home_shot, "visual": home_visual},
                    "search": {"screenshot": search_shot, "visual": search_visual},
                    "opened_rows": opened_rows,
                }
            )
            if final_row:
                break

        ok = bool(final_row and final_row.get("ok"))
        return TaskResult(
            "lark_open_bitable",
            ok,
            "bitable_opened_and_verified" if ok else "bitable_open_not_verified",
            {
                "table_name": name,
                "open_result": asdict(opened),
                "attempts": attempts,
                "final": final_row,
            },
        )

    def _bitable_detail_field_points(self, rect: tuple[str, int, int, int, int]) -> dict[str, tuple[int, int]]:
        _title, left, top, width, height = rect
        x = int(left + width * 0.645)
        return {
            "任务": (x, int(top + height * 0.230)),
            "优先级": (x, int(top + height * 0.271)),
            "Sprint": (x, int(top + height * 0.312)),
            "AI版本": (x, int(top + height * 0.352)),
            "任务类型": (x, int(top + height * 0.391)),
            "任务提交人": (x, int(top + height * 0.432)),
            "任务执行人": (x, int(top + height * 0.472)),
            "开始日期": (x, int(top + height * 0.512)),
            "交付日期": (x, int(top + height * 0.552)),
            "预计人天": (x, int(top + height * 0.592)),
            "实际交付日期": (x, int(top + height * 0.633)),
            "实际人天": (x, int(top + height * 0.672)),
            "风险/问题/说明": (x, int(top + height * 0.712)),
            "开发交付端具体需求": (x, int(top + height * 0.752)),
        }

    def _focus_bitable_browser(self, table_name: str, timeout: float = 5.0) -> bool:
        name = str(table_name or "").strip()
        if not name:
            return False
        active = self.win.active_title()
        if _title_matches_table(active, name):
            return True
        focused = self.win.focus_by_keywords(_table_focus_keywords(name), timeout=timeout)
        if focused and _title_matches_table(self.win.active_title(), name):
            logger.info("[scenario:lark_bitable] browser focus table=%r before=%r focused=%s active=%r", name, active, focused, self.win.active_title())
            return True
        active_after = self.win.active_title()
        browser_active = any(k in active_after.lower() for k in ("edge", "chrome", "firefox", "browser"))
        if not browser_active:
            self.win.focus_by_keywords(("edge", "chrome", "firefox"), timeout=2.0)
        for idx in range(18):
            current = self.win.active_title()
            if _title_matches_table(current, name):
                logger.info("[scenario:lark_bitable] found table tab table=%r idx=%d active=%r", name, idx, current)
                return True
            self.io.hotkey("ctrl", "tab", wait=0.35)
        logger.info("[scenario:lark_bitable] browser focus table=%r before=%r focused=%s active=%r", name, active, focused, self.win.active_title())
        return _title_matches_table(self.win.active_title(), name)

    def _wait_bitable_page_ready(self, table_name: str, target_group: str = "", timeout: float = 18.0) -> dict[str, Any]:
        deadline = time.time() + max(2.0, float(timeout))
        last_row: dict[str, Any] = {}
        while time.time() < deadline:
            self._focus_bitable_browser(table_name, timeout=1.0)
            shot = self.io.screenshot_active_window(self.out_dir, f"lark_bitable_ready_{_safe_label(table_name)}")
            visual = ocr_image_state(shot)
            text = str(visual.get("ocr_text") or "")
            compact = _compact_match_text(text)
            has_add_record = _compact_match_text("添加记录") in compact
            has_table_controls = _visual_has_any(text, ("字段配置", "视图配置", "筛选", "分组"))
            header_hits = sum(
                1
                for needle in ("任务", "优先级", "Sprint", "交付日期", "预计人天", "风险/问题/说明")
                if _compact_match_text(needle) in compact
            )
            has_toolbar = bool(has_add_record and has_table_controls)
            has_headers = header_hits >= 3
            has_group = not target_group or _compact_match_text(target_group) in _compact_match_text(text)
            still_loading = _visual_has_any(text, ("数据加载中", "加载中", "计算中"))
            if not has_toolbar and _visual_has_any(text, ("仪表盘", "开发计划")):
                rect = self.win.active_rect()
                if rect:
                    _title, left, top, width, height = rect
                    self.io.click(int(left + width * 0.151), int(top + height * 0.176), wait=1.8)
                    continue
            last_row = {
                "ok": bool(has_toolbar and has_headers and has_group and not still_loading),
                "screenshot": shot,
                "visual": visual,
                "has_toolbar": has_toolbar,
                "has_headers": has_headers,
                "has_add_record": has_add_record,
                "has_table_controls": has_table_controls,
                "header_hits": header_hits,
                "has_group": has_group,
                "still_loading": still_loading,
            }
            if last_row["ok"]:
                return last_row
            time.sleep(1.0)
        return last_row or {"ok": False, "detail": "ready_timeout"}

    def lark_bitable_add_record(
        self,
        table_name: str,
        fields_json: str | dict[str, Any],
        confirm: bool = False,
        allow_dangerous: bool = False,
        max_attempts: int = 2,
    ) -> TaskResult:
        name = str(table_name or "").strip()
        fields = _parse_fields_json(fields_json)
        if not name:
            return TaskResult("lark_bitable_add_record", False, "table_name_empty", {"fields": fields})
        if not fields:
            return TaskResult("lark_bitable_add_record", False, "fields_empty", {"table_name": name})

        bypass = os_lark_bitable_write_without_confirm_enabled(allow_dangerous)
        if not (confirm or bypass):
            return _bitable_confirmation_required_result(
                "lark_bitable_add_record",
                "add_lark_bitable_record",
                {
                    "table_name": name,
                    "fields_preview": fields,
                    "note": "Adding a Lark Bitable record writes shared office data and needs confirmation.",
                },
            )

        open_result: TaskResult | None = None
        if not self._focus_bitable_browser(name, timeout=2.0):
            open_result = self.lark_open_bitable(name, max_attempts=max_attempts)
            if not open_result.ok:
                return TaskResult(
                    "lark_bitable_add_record",
                    False,
                    "table_open_failed",
                    {"table_name": name, "fields_preview": fields, "open_result": asdict(open_result)},
                )
            self._focus_bitable_browser(name, timeout=5.0)

        rect = self.win.active_rect()
        if not rect:
            return TaskResult(
                "lark_bitable_add_record",
                False,
                "no_active_browser_window",
                {"table_name": name, "open_result": asdict(open_result) if open_result else None, "fields_preview": fields},
            )
        _title, left, top, width, height = rect
        if not _title_matches_table(_title, name):
            return TaskResult(
                "lark_bitable_add_record",
                False,
                "bitable_browser_not_focused",
                {
                    "table_name": name,
                    "active_title": _title,
                    "open_result": asdict(open_result) if open_result else None,
                    "fields_preview": fields,
                },
            )
        self.io.press("esc", wait=0.2)
        self.io.press("esc", wait=0.2)
        self.io.click(int(left + width * 0.260), int(top + height * 0.222), wait=1.3)
        panel_shot = self.io.screenshot_active_window(self.out_dir, f"lark_bitable_add_panel_{_safe_label(name)}")
        panel_visual = ocr_image_state(panel_shot)
        panel_text = str(panel_visual.get("ocr_text") or "")
        panel_ok = ("未命名记录" in panel_text) or ("任务" in panel_text and "请输入内容" in panel_text)

        points = self._bitable_detail_field_points(rect)
        filled: list[dict[str, Any]] = []
        unsupported: list[dict[str, Any]] = []
        for field, value in fields.items():
            point = points.get(field)
            if point is None:
                unsupported.append({"field": field, "value": value, "reason": "field_not_in_current_mvp_coordinate_map"})
                continue
            text = str(value)
            self.io.click(point[0], point[1], wait=0.2)
            self.io.hotkey("ctrl", "a", wait=0.05)
            self.io.paste(text, wait=0.45)
            self.io.press("enter", wait=0.35)
            filled.append({"field": field, "value": text, "point": {"x": point[0], "y": point[1]}})

        time.sleep(1.0)
        final_shot = self.io.screenshot_active_window(self.out_dir, f"lark_bitable_add_record_final_{_safe_label(name)}")
        final_visual = ocr_image_state(final_shot)
        final_text = str(final_visual.get("ocr_text") or "")
        verified_fields = [
            row["field"]
            for row in filled
            if str(row.get("value") or "") and str(row.get("value") or "") in final_text
        ]
        ok = bool(filled and panel_ok and len(verified_fields) == len(filled))
        return TaskResult(
            "lark_bitable_add_record",
            ok,
            "record_added_and_verified" if ok else "record_added_but_visual_verification_incomplete",
            {
                "table_name": name,
                "open_result": asdict(open_result) if open_result else None,
                "confirm": confirm,
                "dangerous_bypassed": bool(bypass and not confirm),
                "fields_preview": fields,
                "filled_fields": filled,
                "unsupported_fields": unsupported,
                "verified_fields": verified_fields,
                "panel_verified": panel_ok,
                "screenshots": {"panel": panel_shot, "final": final_shot},
                "panel_visual": panel_visual,
                "final_visual": final_visual,
            },
        )

    def lark_bitable_ai_paste_records(
        self,
        table_name: str,
        records_text: str,
        target_group: str = "2026/6/22",
        confirm: bool = False,
        allow_dangerous: bool = False,
        max_attempts: int = 2,
    ) -> TaskResult:
        name = str(table_name or "").strip()
        text = str(records_text or "").strip()
        group = str(target_group or "").strip()
        if not name:
            return TaskResult("lark_bitable_ai_paste_records", False, "table_name_empty", {})
        if not text:
            return TaskResult("lark_bitable_ai_paste_records", False, "records_text_empty", {"table_name": name, "target_group": group})
        payload = (
            f"璇锋妸涓嬮潰鍐呭褰曞叆鍒板缁磋〃鏍硷紝鐩爣鍒嗙粍/鏃ユ湡涓猴細{group}銆俓n"
            "姣忎竴鏉″紑鍙戜簨椤圭敓鎴愪竴鏉¤褰曪紱浠诲姟瀛楁鍐欐竻妤氫簨椤规爣棰橈紱濡傛灉鍐呭閲屾湁璐熻矗浜恒€侀闄┿€佹棩鏈熴€侀浼颁汉澶╋紝璇峰敖閲忓～鍏ュ搴斿瓧娈碉紱"
            "娌℃湁鏄庣‘鍊肩殑瀛楁鐣欑┖锛屼笉瑕佺紪閫犮€俓n\n"
            f"{text}"
        )
        bypass = os_lark_bitable_write_without_confirm_enabled(allow_dangerous)
        if not (confirm or bypass):
            return _bitable_confirmation_required_result(
                "lark_bitable_ai_paste_records",
                "ai_paste_lark_bitable_records",
                {
                    "table_name": name,
                    "target_group": group,
                    "records_text_preview": text,
                    "payload_preview": payload,
                    "note": "AI paste creates shared Lark Bitable records and needs confirmation.",
                },
            )

        open_result: TaskResult | None = None
        if not self._focus_bitable_browser(name, timeout=2.0):
            open_result = self.lark_open_bitable(name, max_attempts=max_attempts)
            if not open_result.ok:
                return TaskResult(
                    "lark_bitable_ai_paste_records",
                    False,
                    "table_open_failed",
                    {"table_name": name, "target_group": group, "open_result": asdict(open_result)},
                )
            self._focus_bitable_browser(name, timeout=5.0)

        rect = self.win.active_rect()
        if not rect:
            return TaskResult(
                "lark_bitable_ai_paste_records",
                False,
                "no_active_browser_window",
                {"table_name": name, "target_group": group, "open_result": asdict(open_result) if open_result else None},
            )
        _title, left, top, width, height = rect
        if not _title_matches_table(_title, name):
            return TaskResult(
                "lark_bitable_ai_paste_records",
                False,
                "bitable_browser_not_focused",
                {"table_name": name, "target_group": group, "active_title": _title},
            )

        ready = self._wait_bitable_page_ready(name, group, timeout=20.0)
        if not ready.get("ok"):
            return TaskResult(
                "lark_bitable_ai_paste_records",
                False,
                "bitable_page_not_ready",
                {
                    "table_name": name,
                    "target_group": group,
                    "active_title": _title,
                    "ready": ready,
                    "guard": "Refusing to click AI paste before the Bitable page is fully loaded and target group is visible.",
                },
            )
        rect = self.win.active_rect()
        if not rect:
            return TaskResult(
                "lark_bitable_ai_paste_records",
                False,
                "no_active_browser_window_after_ready",
                {"table_name": name, "target_group": group, "ready": ready},
            )
        _title, left, top, width, height = rect
        self.io.press("esc", wait=0.2)
        self.io.press("esc", wait=0.2)
        self.io.click(int(left + width * 0.178), int(top + height * 0.196), wait=0.55)
        menu_shot = self.io.screenshot_active_window(self.out_dir, f"lark_bitable_ai_paste_menu_{_safe_label(name)}")
        menu_visual = ocr_image_state(menu_shot)
        menu_text = str(menu_visual.get("ocr_text") or "")
        menu_verified = _visual_has_any(menu_text, ("AI 绮樿创褰曞叆", "绮樿创褰曞叆"))
        if not menu_verified:
            return TaskResult(
                "lark_bitable_ai_paste_records",
                False,
                "ai_paste_menu_not_verified",
                {
                    "table_name": name,
                    "target_group": group,
                    "open_result": asdict(open_result) if open_result else None,
                    "ready": ready,
                    "menu_screenshot": menu_shot,
                    "menu_visual": menu_visual,
                    "records_text_preview": text,
                    "guard": "Refusing to paste because the AI paste menu item was not visually verified.",
                },
            )
        self.io.click(int(left + width * 0.222), int(top + height * 0.279), wait=1.2)
        modal_shot = self.io.screenshot_active_window(self.out_dir, f"lark_bitable_ai_paste_modal_{_safe_label(name)}")
        modal_visual = ocr_image_state(modal_shot)
        modal_text = str(modal_visual.get("ocr_text") or "")
        modal_verified = _visual_has_any(modal_text, ("AI 绮樿创褰曞叆", "鏀寔绮樿创鏂囨湰", "AI 鑷姩璇嗗埆鍐呭骞跺綍鍏ユ暟鎹〃", "AI鑷姩璇嗗埆鍐呭骞跺綍鍏ユ暟鎹〃"))
        if not modal_verified:
            return TaskResult(
                "lark_bitable_ai_paste_records",
                False,
                "ai_paste_modal_not_verified",
                {
                    "table_name": name,
                    "target_group": group,
                    "open_result": asdict(open_result) if open_result else None,
                    "ready": ready,
                    "menu_screenshot": menu_shot,
                    "modal_screenshot": modal_shot,
                    "menu_visual": menu_visual,
                    "modal_visual": modal_visual,
                    "records_text_preview": text,
                    "guard": "Refusing to paste because the AI paste modal/upload area was not visually verified.",
                },
            )
        self.io.click(int(left + width * 0.505), int(top + height * 0.560), wait=0.2)
        self.io.paste(payload, wait=1.3)
        pasted_shot = self.io.screenshot_active_window(self.out_dir, f"lark_bitable_ai_paste_pasted_{_safe_label(name)}")
        pasted_visual = ocr_image_state(pasted_shot)
        pasted_text = str(pasted_visual.get("ocr_text") or "")
        payload_visible = any(part and part in pasted_text for part in (group, text.splitlines()[0][:24] if text.splitlines() else ""))
        return TaskResult(
            "lark_bitable_ai_paste_records",
            bool(payload_visible),
            "ai_paste_payload_entered_for_review" if payload_visible else "ai_paste_payload_entered_but_not_verified",
            {
                "table_name": name,
                "target_group": group,
                "open_result": asdict(open_result) if open_result else None,
                "ready": ready,
                "confirm": confirm,
                "dangerous_bypassed": bool(bypass and not confirm),
                "records_text_preview": text,
                "payload_preview": payload,
                "payload_visible": payload_visible,
                "screenshots": {"menu": menu_shot, "modal": modal_shot, "pasted": pasted_shot},
                "menu_visual": menu_visual,
                "modal_visual": modal_visual,
                "pasted_visual": pasted_visual,
            },
        )

    def file_find(self, root: str, pattern: str = "*", max_results: int = 100, include_dirs: bool = True) -> TaskResult:
        base = _resolve_path(root)
        if not base.exists() or not base.is_dir():
            return TaskResult("file_find", False, "root_not_found_or_not_dir", {"root": str(base)})
        pat = pattern or "*"
        limit = max(1, min(int(max_results or 100), 1000))
        results: list[dict[str, Any]] = []
        try:
            for p in base.rglob(pat):
                if not include_dirs and p.is_dir():
                    continue
                results.append(_file_stat(p))
                if len(results) >= limit:
                    break
        except Exception as e:
            return TaskResult("file_find", False, f"find_failed:{e!r}", {"root": str(base), "pattern": pat})
        return TaskResult(
            "file_find",
            True,
            "files_found",
            {"root": str(base), "pattern": pat, "count": len(results), "truncated": len(results) >= limit, "results": results},
        )

    def file_copy(self, source: str, destination: str, overwrite: bool = False, confirm: bool = False, allow_dangerous: bool = False) -> TaskResult:
        src = _resolve_path(source)
        dst = _resolve_path(destination)
        if not src.exists() or not src.is_file():
            return TaskResult("file_copy", False, "source_not_found_or_not_file", {"source": str(src)})
        dst_exists = dst.exists()
        allow = os_file_dangerous_without_confirm_enabled(allow_dangerous)
        if dst_exists and not (confirm or allow):
            return _confirmation_required_result(
                "file_copy",
                "overwrite_existing_destination",
                {"source": _file_stat(src), "destination": _file_stat(dst), "overwrite": overwrite, "confirm": confirm},
            )
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        except Exception as e:
            return TaskResult("file_copy", False, f"copy_failed:{e!r}", {"source": str(src), "destination": str(dst)})
        return TaskResult(
            "file_copy",
            True,
            "copied",
            {"source": _file_stat(src), "destination": _file_stat(dst), "overwrote": dst_exists, "dangerous_bypassed": bool(dst_exists and allow and not confirm)},
        )

    def file_move(self, source: str, destination: str, overwrite: bool = False, confirm: bool = False, allow_dangerous: bool = False) -> TaskResult:
        src = _resolve_path(source)
        dst = _resolve_path(destination)
        if not src.exists():
            return TaskResult("file_move", False, "source_not_found", {"source": str(src)})
        dst_exists = dst.exists()
        allow = os_file_dangerous_without_confirm_enabled(allow_dangerous)
        if dst_exists and not (confirm or allow):
            return _confirmation_required_result(
                "file_move",
                "overwrite_existing_destination",
                {"source": _file_stat(src), "destination": _file_stat(dst), "overwrite": overwrite, "confirm": confirm},
            )
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst_exists and (confirm or allow):
                if dst.is_dir():
                    shutil.rmtree(dst)
                else:
                    dst.unlink()
            shutil.move(str(src), str(dst))
        except Exception as e:
            return TaskResult("file_move", False, f"move_failed:{e!r}", {"source": str(src), "destination": str(dst)})
        return TaskResult(
            "file_move",
            True,
            "moved",
            {"source": str(src), "destination": _file_stat(dst), "overwrote": dst_exists, "dangerous_bypassed": bool(dst_exists and allow and not confirm)},
        )

    def file_rename(self, path: str, new_name: str, overwrite: bool = False, confirm: bool = False, allow_dangerous: bool = False) -> TaskResult:
        src = _resolve_path(path)
        if not src.exists():
            return TaskResult("file_rename", False, "source_not_found", {"source": str(src)})
        safe_name = Path(new_name).name
        if not safe_name or safe_name in (".", ".."):
            return TaskResult("file_rename", False, "invalid_new_name", {"new_name": new_name})
        return self.file_move(str(src), str(src.with_name(safe_name)), overwrite=overwrite, confirm=confirm, allow_dangerous=allow_dangerous)

    def file_delete_with_confirm(self, path: str, confirm: bool = False, allow_dangerous: bool = False) -> TaskResult:
        target = _resolve_path(path)
        if not target.exists():
            return TaskResult("file_delete_with_confirm", False, "target_not_found", {"target": str(target)})
        allow = os_file_dangerous_without_confirm_enabled(allow_dangerous)
        if not (confirm or allow):
            return _confirmation_required_result(
                "file_delete_with_confirm",
                "delete",
                {"target": _file_stat(target), "confirm": confirm},
            )
        before = _file_stat(target)
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        except Exception as e:
            return TaskResult("file_delete_with_confirm", False, f"delete_failed:{e!r}", {"target": str(target), "before": before})
        return TaskResult(
            "file_delete_with_confirm",
            not target.exists(),
            "deleted" if not target.exists() else "delete_not_verified",
            {"target": str(target), "before": before, "after": _file_stat(target), "dangerous_bypassed": bool(allow and not confirm)},
        )

    def file_open(self, path: str) -> TaskResult:
        target = _resolve_path(path)
        if not target.exists():
            return TaskResult("file_open", False, "target_not_found", {"target": str(target)})
        try:
            os.startfile(str(target))  # type: ignore[attr-defined]
        except Exception as e:
            return TaskResult("file_open", False, f"open_failed:{e!r}", {"target": str(target)})
        time.sleep(0.8)
        screenshot = self.io.screenshot_active_window(self.out_dir, f"file_open_{_safe_label(target.name)}")
        return TaskResult("file_open", True, "opened", {"target": _file_stat(target), "active_title": self.win.active_title(), "screenshot": screenshot})

    def file_reveal_in_explorer(self, path: str) -> TaskResult:
        target = _resolve_path(path)
        if not target.exists():
            return TaskResult("file_reveal_in_explorer", False, "target_not_found", {"target": str(target)})
        try:
            subprocess.Popen(["explorer.exe", "/select,", str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            return TaskResult("file_reveal_in_explorer", False, f"reveal_failed:{e!r}", {"target": str(target)})
        time.sleep(1.0)
        screenshot = self.io.screenshot_active_window(self.out_dir, f"file_reveal_{_safe_label(target.name)}")
        return TaskResult(
            "file_reveal_in_explorer",
            True,
            "revealed",
            {"target": _file_stat(target), "active_title": self.win.active_title(), "screenshot": screenshot},
        )

    def file_attach_to_app(self, file_path: str, app_name: str = "", open_dialog_hotkey: str = "ctrl+o") -> TaskResult:
        target = _resolve_path(file_path)
        if not target.exists() or not target.is_file():
            return TaskResult("file_attach_to_app", False, "file_not_found_or_not_file", {"file": str(target)})
        open_result: dict[str, Any] | None = None
        if app_name.strip():
            opened = self.open_app(app_name.strip())
            open_result = asdict(opened)
            if not opened.ok:
                return TaskResult("file_attach_to_app", False, "app_open_failed", {"file": str(target), "open_result": open_result})
        keys = [k.strip().lower() for k in (open_dialog_hotkey or "ctrl+o").split("+") if k.strip()]
        if keys:
            self.io.hotkey(*keys, wait=0.8)
        before = self.io.screenshot_active_window(self.out_dir, f"file_attach_dialog_{_safe_label(target.name)}")
        self.io.paste(str(target), wait=0.2)
        self.io.press("enter", wait=1.0)
        after = self.io.screenshot_active_window(self.out_dir, f"file_attach_after_{_safe_label(target.name)}")
        return TaskResult(
            "file_attach_to_app",
            True,
            "path_submitted_to_active_file_dialog",
            {"file": _file_stat(target), "app_name": app_name, "open_result": open_result, "screenshots": {"before": before, "after": after}, "active_title": self.win.active_title()},
        )

    def folder_summarize(self, folder: str, max_depth: int = 2, max_entries: int = 300) -> TaskResult:
        root = _resolve_path(folder)
        if not root.exists() or not root.is_dir():
            return TaskResult("folder_summarize", False, "folder_not_found_or_not_dir", {"folder": str(root)})
        depth_limit = max(0, min(int(max_depth or 2), 8))
        entry_limit = max(1, min(int(max_entries or 300), 3000))
        entries: list[dict[str, Any]] = []
        total_files = 0
        total_dirs = 0
        total_size = 0
        by_ext: dict[str, int] = {}
        try:
            for p in root.rglob("*"):
                rel = p.relative_to(root)
                if len(rel.parts) > depth_limit:
                    continue
                stat = _file_stat(p)
                if p.is_dir():
                    total_dirs += 1
                else:
                    total_files += 1
                    size = int(stat.get("size") or 0)
                    total_size += size
                    by_ext[p.suffix.lower() or "<no_ext>"] = by_ext.get(p.suffix.lower() or "<no_ext>", 0) + 1
                if len(entries) < entry_limit:
                    entries.append({"relative": str(rel), **stat})
        except Exception as e:
            return TaskResult("folder_summarize", False, f"summarize_failed:{e!r}", {"folder": str(root)})
        return TaskResult(
            "folder_summarize",
            True,
            "folder_summarized",
            {
                "folder": str(root),
                "max_depth": depth_limit,
                "total_files": total_files,
                "total_dirs": total_dirs,
                "total_size": total_size,
                "by_extension": by_ext,
                "entry_count": len(entries),
                "entries": entries,
            },
        )

    def notepad_edit_save(self, text: str, target_path: str | Path) -> TaskResult:
        target = Path(target_path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
        logger.info("[scenario:notepad] target=%s text_len=%d", target, len(text))
        self.io.launch("notepad.exe", (target.name, "notepad"), args=[str(target)], wait=1.0)
        if not self._ensure_active_title_contains(target.name):
            return TaskResult(
                "notepad",
                False,
                "target_notepad_tab_not_active",
                {"target": str(target), "active_title": self.win.active_title()},
            )
        before = self.io.screenshot(self.out_dir, "notepad_before")
        if not self._uia_set_any_text(text):
            self.io.hotkey("ctrl", "a", wait=0.05)
            self.io.paste(text, wait=0.2)
        typed = self.io.screenshot(self.out_dir, "notepad_typed")
        self.io.hotkey("ctrl", "s", wait=0.8)

        saved = ""
        deadline = time.time() + 6
        while time.time() < deadline:
            try:
                saved = target.read_text(encoding="utf-8", errors="replace")
                if normalize_text(saved) == normalize_text(text):
                    break
            except Exception:
                pass
            time.sleep(0.2)
        after = self.io.screenshot(self.out_dir, "notepad_saved")
        ok = normalize_text(saved) == normalize_text(text)
        return TaskResult(
            "notepad",
            ok,
            "saved_and_verified" if ok else "saved_text_mismatch",
            {"target": str(target), "saved_len": len(saved), "expected_len": len(text), "screenshots": {"before": before, "typed": typed, "after": after}},
        )

    def calculator_calculate(self, expression: str, expected: str = "") -> TaskResult:
        import pyperclip  # type: ignore

        expr = expression.strip()
        expr_norm = normalize_calculator_expression(expr)
        expect = normalize_number(expected or _safe_eval_arithmetic(expr))
        contract = self._execution_contract("calculator", goal="calculate")
        logger.info("[scenario:calculator] expr=%r expect=%s", expr, expect)
        focus_result = self.focus_or_raise_app("calculator", timeout=4.0, max_attempts=3, stage="calculator_open_focus")
        before = self.io.screenshot_active_window(self.out_dir, "calculator_before")
        if not focus_result.ok:
            return TaskResult(
                "calculator",
                False,
                focus_result.detail,
                {
                    "expr": expr,
                    "expr_norm": expr_norm,
                    "expect": expect,
                    "focus_result": asdict(focus_result),
                    "execution_contract": contract.to_dict(),
                    "screenshots": {"before": before, "after": ""},
                },
            )

        attempts: list[dict[str, Any]] = []
        ok = False
        result_verified = False
        expression_verified = False
        raw = ""
        got = ""
        after = ""
        visual: dict[str, Any] = {}
        for attempt in range(1, 4):
            logger.info("[scenario:calculator] attempt=%d expr=%r", attempt, expr)
            guard = self._verify_environment(contract, stage="calculator_before_input", action="type_expression")
            refocus_result = None
            if not guard.ok:
                refocus_result = self.focus_or_raise_app("calculator", timeout=3.0, max_attempts=2, launch_if_missing=False, stage="calculator_refocus_before_input")
                guard = self._verify_environment(contract, stage="calculator_before_input", action="type_expression_after_refocus")
            if not guard.ok:
                attempts.append(
                    {
                        "attempt": attempt,
                        "ok": False,
                        "failure_stage": guard.detail,
                        "environment_guard": guard.to_dict(),
                        "refocus_result": asdict(refocus_result) if refocus_result else None,
                    }
                )
                return self._unsafe_environment_result(
                    "calculator",
                    contract,
                    guard,
                    {
                        "expr": expr,
                        "expr_norm": expr_norm,
                        "expect": expect,
                        "focus_result": asdict(focus_result),
                        "attempts": attempts,
                        "screenshots": {"before": before, "after": after},
                    },
                )
            self.io.press("esc", presses=3, wait=0.25)
            if attempt == 1:
                self.io.write(expr, interval=0.08, wait=0.25)
            else:
                self.io.paste(expr, wait=0.35)
            guard = self._verify_environment(contract, stage="calculator_before_submit", action="press_enter")
            if not guard.ok:
                attempts.append({"attempt": attempt, "ok": False, "failure_stage": guard.detail, "environment_guard": guard.to_dict()})
                return self._unsafe_environment_result(
                    "calculator",
                    contract,
                    guard,
                    {
                        "expr": expr,
                        "expr_norm": expr_norm,
                        "expect": expect,
                        "focus_result": asdict(focus_result),
                        "attempts": attempts,
                        "screenshots": {"before": before, "after": after},
                    },
                )
            self.io.press("enter", wait=0.9)
            after = self.io.screenshot_active_window(self.out_dir, f"calculator_after_attempt_{attempt}")
            visual = calculator_visual_state(after, expect)
            pyperclip.copy("")
            self.io.hotkey("ctrl", "c", wait=0.35)
            raw = pyperclip.paste()
            got = normalize_number(raw)
            expr_ok = visual.get("expression_norm") == expr_norm
            visual_result_ok = visual.get("result_norm") == expect
            clipboard_ok = got == expect
            result_verified = bool(clipboard_ok or visual_result_ok)
            expression_verified = bool(expr_ok)
            attempts.append(
                {
                    "attempt": attempt,
                    "clipboard_raw": raw,
                    "clipboard_norm": got,
                    "clipboard_ok": clipboard_ok,
                    "visual": visual,
                    "visual_expression_ok": expr_ok,
                    "visual_result_ok": visual_result_ok,
                    "screenshot": after,
                }
            )
            logger.info(
                "[scenario:calculator] attempt=%d clipboard_ok=%s visual_expr=%r visual_result=%r",
                attempt,
                clipboard_ok,
                visual.get("expression_norm"),
                visual.get("result_norm"),
            )
            if result_verified:
                ok = True
                break
        detail = (
            "result_verified_with_visual"
            if ok and expression_verified and visual.get("result_norm") == expect
            else "result_verified_expression_ocr_incomplete"
            if ok
            else "result_not_verified"
        )
        return TaskResult(
            "calculator",
            ok,
            detail,
            {
                "expr": expr,
                "expr_norm": expr_norm,
                "expect": expect,
                "clipboard_raw": raw,
                "clipboard_norm": got,
                "visual": visual,
                "result_verified": result_verified,
                "expression_verified": expression_verified,
                "attempts": attempts,
                "focus_result": asdict(focus_result),
                "execution_contract": contract.to_dict(),
                "screenshots": {"before": before, "after": after},
            },
        )


    def file_open_save_dialogs(self) -> TaskResult:
        source = self.out_dir / f"open_source_{now_tag()}.txt"
        save_as = self.out_dir / f"save_dialog_{now_tag()}.txt"
        source.write_text("Jachin file open dialog smoke.\r\n", encoding="utf-8")
        if save_as.exists():
            save_as.unlink()
        logger.info("[scenario:file_dialogs] source=%s save_as=%s", source, save_as)

        selected_open = self._run_file_dialog("open", source)
        open_after = self.io.screenshot(self.out_dir, "file_open_after")
        open_ok = bool(selected_open) and Path(selected_open).resolve() == source.resolve()

        selected_save = self._run_file_dialog("save", save_as)
        if selected_save:
            Path(selected_save).write_text("Jachin file save dialog smoke.\r\n", encoding="utf-8")
        save_after = self.io.screenshot(self.out_dir, "file_save_after")
        save_ok = bool(selected_save) and Path(selected_save).resolve() == save_as.resolve() and Path(selected_save).exists()

        return TaskResult(
            "file_dialogs",
            bool(open_ok and save_ok),
            "open_and_save_dialogs_verified" if open_ok and save_ok else "file_dialog_verification_failed",
            {
                "source": str(source),
                "save_as": str(save_as),
                "selected_open": selected_open,
                "selected_save": selected_save,
                "open_ok": open_ok,
                "save_ok": save_ok,
                "screenshots": {"open_after": open_after, "save_after": save_after},
            },
        )

    def _run_file_dialog(self, mode: str, path: Path) -> str:
        title = f"Jachin {mode.title()} File Dialog"
        klass = "OpenFileDialog" if mode == "open" else "SaveFileDialog"
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            f"$d=New-Object System.Windows.Forms.{klass}; "
            f"$d.Title='{title}'; "
            "$d.Filter='Text files (*.txt)|*.txt|All files (*.*)|*.*'; "
            "if($d.ShowDialog() -eq 'OK'){[Console]::Out.WriteLine($d.FileName)}"
        )
        proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-STA", "-Command", ps],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        focused = self.win.focus_by_keywords((title,), timeout=6.0)
        self.io.screenshot(self.out_dir, f"file_{mode}_dialog")
        if focused and not self._uia_set_any_text(str(path), prefer_bottom=True):
            self.io.paste(str(path), wait=0.2)
        elif not focused:
            self.io.paste(str(path), wait=0.2)
        self.io.press("enter", wait=0.8)
        try:
            selected = (proc.communicate(timeout=8)[0] or "").strip()
        except subprocess.TimeoutExpired:
            selected = ""
            proc.terminate()
        logger.info("[scenario:file_dialogs] mode=%s selected=%r", mode, selected)
        return selected

    def browser_address_download_prompt(self, url: str = "") -> TaskResult:
        browser = _find_browser()
        if not browser:
            return TaskResult("browser", False, "browser_exe_not_found", {})
        download_file = self.out_dir / "browser_download_source.txt"
        html = self.out_dir / "browser_smoke.html"
        download_file.write_text("download-ok", encoding="utf-8")
        if not url:
            html.write_text(
                "<!doctype html><title>Jachin Browser Vision Smoke</title>"
                "<h1>Jachin Browser Vision Smoke</h1>"
                f"<a href='{download_file.as_uri()}' download='jachin_os_vision_download.txt'>Download test file</a>"
                "<button onclick=\"Notification.requestPermission && Notification.requestPermission()\">Permission</button>",
                encoding="utf-8",
            )
            url = html.as_uri()
        logger.info("[scenario:browser] browser=%s url=%s", browser, url)
        self.io.launch(browser, ("chrome", "edge"), args=["about:blank"], wait=1.2)
        self.io.hotkey("ctrl", "l", wait=0.1)
        self.io.paste(url, wait=0.1)
        self.io.press("enter", wait=1.5)
        loaded = self.io.screenshot(self.out_dir, "browser_address_loaded")
        title = self.win.active_title()
        loaded_ok = "Jachin Browser Vision Smoke" in title or "browser_smoke" in title.lower() or bool(url)
        self.io.press("tab", wait=0.15)
        self.io.press("enter", wait=0.8)
        download_after = self.io.screenshot(self.out_dir, "browser_download_after_enter")
        self.io.press("esc", wait=0.15)
        return TaskResult(
            "browser",
            bool(loaded_ok),
            "browser_address_loaded_download_attempted" if loaded_ok else "browser_navigation_failed",
            {"browser": browser, "url": url, "active_title": title, "download_attempted": True, "screenshots": {"loaded": loaded, "download_after": download_after}},
        )

    def popup_action(self, action: str = "confirm") -> TaskResult:
        action = (action or "confirm").lower()
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "[System.Windows.Forms.MessageBox]::Show('Jachin popup smoke','Jachin Popup Smoke',"
            "'OKCancel','Information') | Out-Null"
        )
        logger.info("[scenario:popup] action=%s", action)
        proc = subprocess.Popen(["powershell", "-NoProfile", "-STA", "-Command", ps], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        focused = self.win.focus_by_keywords(("Jachin Popup Smoke",), timeout=6.0)
        before = self.io.screenshot(self.out_dir, "popup_before_action")
        clicked = False
        if action in ("confirm", "ok", "yes"):
            clicked = self._uia_click_first(("OK", "Yes", "确定", "是"), timeout=2.0)
            if not clicked:
                self.io.press("enter", wait=0.3)
        elif action in ("cancel", "no"):
            clicked = self._uia_click_first(("Cancel", "No", "取消", "否"), timeout=2.0)
            if not clicked:
                self.io.press("esc", wait=0.3)
        else:
            self.io.press("esc", wait=0.3)
        try:
            proc.wait(timeout=5)
            closed = True
        except subprocess.TimeoutExpired:
            closed = False
            proc.terminate()
        after = self.io.screenshot(self.out_dir, "popup_after_action")
        return TaskResult(
            "popup",
            bool(focused and closed),
            "popup_action_completed" if focused and closed else "popup_not_closed",
            {"action": action, "focused": focused, "closed": closed, "uia_clicked": clicked, "screenshots": {"before": before, "after": after}},
        )


def _iter_uia_controls(root: Any, max_depth: int = 6):
    stack: list[tuple[Any, int]] = [(root, 0)]
    while stack:
        ctrl, depth = stack.pop()
        yield ctrl, depth
        if depth >= max_depth:
            continue
        try:
            children = list(ctrl.GetChildren())
        except Exception:
            children = []
        for child in reversed(children):
            stack.append((child, depth + 1))


def _find_browser() -> str:
    candidates = [
        os.environ.get("OS_VISION_BROWSER_EXE") or "",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for raw in candidates:
        if raw and Path(raw).is_file():
            return raw
    return ""


def run_tasks(
    *,
    task: str = "all",
    out_dir: str | Path | None = None,
    notepad_text: str = "",
    notepad_file: str | Path | None = None,
    expression: str = "99*8+15",
    expected: str = "",
    browser_url: str = "",
) -> dict[str, Any]:
    auto = WindowsOSAutomation(out_dir=out_dir)
    out = Path(out_dir or auto.out_dir).resolve()
    text = notepad_text or (
        "Jachin OS vision smoke test.\r\n"
        "Task: open Notepad, type text, save file.\r\n"
        f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}\r\n"
    )
    target = Path(notepad_file).expanduser().resolve() if notepad_file else out / f"notepad_smoke_{now_tag()}.txt"
    runners: list[Callable[[], TaskResult]] = []
    if task in ("all", "file_dialogs"):
        runners.append(auto.file_open_save_dialogs)
    if task in ("all", "calculator"):
        runners.append(lambda: auto.calculator_calculate(expression, expected))
    if task in ("all", "notepad"):
        runners.append(lambda: auto.notepad_edit_save(text, target))
    if task in ("all", "browser"):
        runners.append(lambda: auto.browser_address_download_prompt(browser_url))
    if task in ("all", "popup"):
        runners.append(auto.popup_action)
    results = [r() for r in runners]
    report = {"ok": all(r.ok for r in results), "results": [asdict(r) for r in results], "out_dir": str(out)}
    out.mkdir(parents=True, exist_ok=True)
    report_path = out / f"os_vision_report_{now_tag()}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["report_path"] = str(report_path)
    return report











