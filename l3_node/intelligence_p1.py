"""P1 safety policy gates.

In the memory-first architecture this module is intentionally narrow. It no
longer owns preference prompt injection, legacy auxiliary parsing, clarification queues, or
memory snapshots. Durable user knowledge is handled by Memory Growth agents.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_NEXUS_CONFIG = Path.home() / ".jachin" / "nexus_config.json"

_DEFAULT_SHELL_BLOCKLIST = [
    "rm -rf",
    "mkfs",
    "dd if=",
    ":(){:|:&};:",
    "powershell -enc",
    "> /dev/sd",
    "format c:",
    "diskpart",
]


def get_intel_p1_config() -> dict[str, Any]:
    """Read the intelligence_p1 section from the local Nexus config."""
    try:
        if not _NEXUS_CONFIG.exists():
            return {}
        cfg = json.loads(_NEXUS_CONFIG.read_text(encoding="utf-8"))
        sec = cfg.get("intelligence_p1")
        return sec if isinstance(sec, dict) else {}
    except Exception as e:
        logger.debug("[P1] failed to read intelligence_p1 config: %s", e)
        return {}


def _command_matches_destructive_task_plan_gate(command: str) -> bool:
    """Return true for destructive shell commands that require a task plan."""
    c = (command or "").strip().lower()
    if not c:
        return False
    if re.search(r"docker\s+.{0,240}\bprune\b", c, re.DOTALL):
        return True
    if re.search(r"\bpodman\s+.{0,240}\bprune\b", c, re.DOTALL):
        return True
    if re.search(r"\btruncate\s+table\b", c):
        return True
    if re.search(r"\bdrop\s+(?:database|table|schema)\b", c):
        return True
    return False


def assert_shell_exec_allowed(command: str) -> None:
    """Validate a shell command against local P1 safety policy."""
    cmd = (command or "").strip()
    if not cmd:
        raise ValueError("shell command is empty")

    cfg = get_intel_p1_config()
    blocklist = cfg.get("shell_exec_blocklist_patterns")
    if not isinstance(blocklist, list) or not blocklist:
        blocklist = _DEFAULT_SHELL_BLOCKLIST

    lowered = cmd.lower()
    for pat in blocklist:
        if not isinstance(pat, str) or not pat.strip():
            continue
        if pat.lower() in lowered:
            raise ValueError(f"shell_exec blocked dangerous pattern: {pat!r}")

    if bool(cfg.get("destructive_shell_requires_task_plan", False)):
        if _command_matches_destructive_task_plan_gate(cmd):
            try:
                from l3_node.task_planning import task_plan_is_substantial

                if not task_plan_is_substantial():
                    raise ValueError(
                        "high-risk cleanup/DDL shell command requires a substantial task_plan.md"
                    )
            except ValueError:
                raise
            except Exception as e:
                logger.debug("[P1] failed to check destructive-shell task plan gate: %s", e)

    mode = str(cfg.get("shell_exec_mode", "open") or "open").lower()
    if mode != "restricted":
        return

    prefixes = cfg.get("shell_exec_allowlist_prefixes")
    if not isinstance(prefixes, list) or not prefixes:
        raise ValueError(
            "shell_exec_mode=restricted but shell_exec_allowlist_prefixes is not configured"
        )

    for p in prefixes:
        if isinstance(p, str) and lowered.startswith(p.strip().lower()):
            return

    raise ValueError(
        "shell_exec is restricted; command must start with an allowlisted prefix. "
        f"current={cmd[:80]!r}"
    )
