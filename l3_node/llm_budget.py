"""LLM Token 预算（主/子 Agent，usage 累计）。见 docs/L3_LIMITATIONS_AND_REMEDIATION_ROADMAP.md §〇、§2。"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path


class BudgetExhaustedError(Exception):
    """累计 token 超过硬顶。"""

    def __init__(self, used: int, limit: int, *, message: str = "") -> None:
        self.used = used
        self.limit = limit
        msg = message or f"Token 预算已用尽（累计 {used} > 上限 {limit}）。"
        super().__init__(msg)


def extract_usage_tokens(response: object) -> tuple[int, int]:
    """从 litellm 响应对象取 (prompt_tokens, completion_tokens)。"""
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0
    pt = getattr(usage, "prompt_tokens", None)
    if pt is None:
        pt = getattr(usage, "input_tokens", None)
    ct = getattr(usage, "completion_tokens", None)
    if ct is None:
        ct = getattr(usage, "output_tokens", None)
    try:
        pi = int(pt or 0)
    except (TypeError, ValueError):
        pi = 0
    try:
        ci = int(ct or 0)
    except (TypeError, ValueError):
        ci = 0
    return pi, ci


def accumulate_and_check(
    accumulator: dict[str, int],
    prompt_delta: int,
    completion_delta: int,
    max_total: int | None,
) -> None:
    accumulator["prompt"] = int(accumulator.get("prompt", 0)) + int(prompt_delta)
    accumulator["completion"] = int(accumulator.get("completion", 0)) + int(completion_delta)
    tot = accumulator["prompt"] + accumulator["completion"]
    if max_total is not None and tot > int(max_total):
        raise BudgetExhaustedError(tot, int(max_total))


# ---------------------------------------------------------------------------
# 进程级「今日」累计（供 ProactiveReporter / AwarenessLoop / autonomy 面板）
# 文件：$JACHIN_HOME/workspace/llm_token_daily.json
# ---------------------------------------------------------------------------

_daily_lock = threading.RLock()


def _daily_token_path() -> Path:
    home = Path(os.environ.get("JACHIN_HOME", "~/.jachin")).expanduser()
    workspace = home / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace / "llm_token_daily.json"


def _today_ymd() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def record_daily_llm_usage(prompt_tokens: int, completion_tokens: int) -> None:
    """在每次 LLM 响应落盘后累加今日 prompt/completion（线程安全）。"""
    pt = max(0, int(prompt_tokens or 0))
    ct = max(0, int(completion_tokens or 0))
    if pt == 0 and ct == 0:
        return
    today = _today_ymd()
    path = _daily_token_path()
    with _daily_lock:
        data: dict[str, int | str] = {"date": today, "prompt": 0, "completion": 0}
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    if raw.get("date") != today:
                        raw = {"date": today, "prompt": 0, "completion": 0}
                    data = {
                        "date": str(raw.get("date", today)),
                        "prompt": int(raw.get("prompt", 0) or 0),
                        "completion": int(raw.get("completion", 0) or 0),
                    }
            except Exception:
                data = {"date": today, "prompt": 0, "completion": 0}
        data["prompt"] = int(data["prompt"]) + pt
        data["completion"] = int(data["completion"]) + ct
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass


def get_today_token_usage() -> int:
    """今日累计总 token（prompt+completion），按本地日期切日。"""
    today = _today_ymd()
    path = _daily_token_path()
    with _daily_lock:
        if not path.exists():
            return 0
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("date") != today:
                return 0
            return int(raw.get("prompt", 0) or 0) + int(raw.get("completion", 0) or 0)
        except Exception:
            return 0


def get_token_day_budget() -> int:
    """日预算软上限（与 AwarenessLoop 的 JACHIN_TOKEN_DAY_BUDGET 一致）。"""
    raw = (os.environ.get("JACHIN_TOKEN_DAY_BUDGET") or "200000").strip()
    try:
        return max(1000, int(raw))
    except ValueError:
        return 200000
