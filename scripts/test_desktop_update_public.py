#!/usr/bin/env python3
"""
公网桌面热更新接口探测（与 Tauri GET /api/v1/update/desktop 一致），无需打包桌面端。

成功条件（exit 0）：
  - HTTP 200 且 body 为合法 JSON（含 version / platforms）→ 有可用新版本
  - HTTP 204 → 已连上且鉴权通过，但当前版本已是最新或无产物

失败（exit 1）：网络错误、非预期状态码、JSON 形态不对。

用法：
  set DESKTOP_UPDATE_BEARER=与 Nexus 环境变量相同的密钥
  python scripts/test_desktop_update_public.py

  # 可选：公网 Nexus 根（默认与仓库 tauri.conf 一致）
  set JACHIN_NEXUS_PUBLIC_BASE=http://47.86.39.173:3000
  python scripts/test_desktop_update_public.py --current-version 0.0.1

pytest（需设置 DESKTOP_UPDATE_BEARER，否则跳过）：
  pytest scripts/test_desktop_update_public.py -v
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any
from pathlib import Path

# 与 clients/desktop/src-tauri/tauri.conf.json 默认 endpoint 主机一致（仅探测用，可覆盖）
DEFAULT_NEXUS_BASE = os.environ.get("JACHIN_NEXUS_PUBLIC_BASE", "http://47.86.39.173:3000").rstrip("/")


def _read_bearer_from_dotenv(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("DESKTOP_UPDATE_BEARER="):
            val = line.split("=", 1)[1].strip().strip('"').strip("'")
            return val or None
    return None


def resolve_bearer(cli_token: str | None) -> str | None:
    if cli_token and cli_token.strip():
        return cli_token.strip()
    for key in ("DESKTOP_UPDATE_BEARER", "DESKTOP_UPDATE_TOKEN"):
        v = os.environ.get(key, "").strip()
        if v:
            return v
    repo = Path(__file__).resolve().parents[1]
    for rel in ("cloud/nexus/.env.local", "cloud/nexus/.env"):
        t = _read_bearer_from_dotenv(repo / rel)
        if t:
            return t
    return None


def build_url(
    base: str,
    target: str,
    arch: str,
    current_version: str,
) -> str:
    from urllib.parse import urlencode, urlsplit, urlunsplit

    base = base.rstrip("/")
    path = "/api/v1/update/desktop"
    q = urlencode(
        {
            "target": target,
            "arch": arch,
            "current_version": current_version,
        }
    )
    parts = urlsplit(base)
    return urlunsplit((parts.scheme, parts.netloc, path, q, ""))


def probe_public_desktop_update(
    *,
    base_url: str = DEFAULT_NEXUS_BASE,
    bearer: str,
    target: str = "windows",
    arch: str = "x86_64",
    current_version: str = "0.0.1",
    timeout_sec: float = 30.0,
) -> tuple[int, dict[str, Any] | None, str]:
    """
    返回 (http_status, json_or_none, raw_body_or_error_hint)。
    204 时 json 为 None，raw 为空字符串。
    """
    url = build_url(base_url, target, arch, current_version)
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {bearer}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            status = resp.getcode()
            body = resp.read()
    except urllib.error.HTTPError as e:
        status = e.code
        body = e.read() if e.fp else b""
    except urllib.error.URLError as e:
        return -1, None, f"network: {e.reason!s}"

    text = body.decode("utf-8", errors="replace") if body else ""

    if status == 204:
        return 204, None, ""

    if status == 401:
        return 401, None, text[:500]

    if status != 200:
        return status, None, text[:2000]

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return status, None, f"invalid json: {e}; body_prefix={text[:300]!r}"

    if not isinstance(data, dict):
        return status, None, "body is not a JSON object"

    if "version" not in data or "platforms" not in data:
        return (
            status,
            None,
            f"missing version/platforms keys; keys={list(data.keys())[:20]}",
        )

    return status, data, text


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--base-url",
        default=DEFAULT_NEXUS_BASE,
        help=f"Nexus 根 URL（默认 {DEFAULT_NEXUS_BASE} 或环境变量 JACHIN_NEXUS_PUBLIC_BASE）",
    )
    p.add_argument("--target", default="windows")
    p.add_argument("--arch", default="x86_64")
    p.add_argument(
        "--current-version",
        default="0.0.1",
        help="故意设低易触发 200；设成已发布最新则多为 204",
    )
    p.add_argument(
        "--token",
        default=None,
        help="Bearer token（默认环境变量 DESKTOP_UPDATE_BEARER 或 cloud/nexus/.env.local）",
    )
    p.add_argument("--timeout", type=float, default=30.0)
    args = p.parse_args(argv)

    bearer = resolve_bearer(args.token)
    if not bearer:
        print(
            "错误：未找到 DESKTOP_UPDATE_BEARER。请设置环境变量，或使用 --token，"
            "或在 cloud/nexus/.env.local 中配置 DESKTOP_UPDATE_BEARER=...",
            file=sys.stderr,
        )
        return 2

    url = build_url(args.base_url, args.target, args.arch, args.current_version)
    print(f"GET {url}")
    print(f"Authorization: Bearer *** (len={len(bearer)})")

    status, data, hint = probe_public_desktop_update(
        base_url=args.base_url,
        bearer=bearer,
        target=args.target,
        arch=args.arch,
        current_version=args.current_version,
        timeout_sec=args.timeout,
    )

    if status == -1:
        print(f"FAIL {hint}", file=sys.stderr)
        return 1

    if status == 401:
        print(f"HTTP 401 — 凭证与 Nexus DESKTOP_UPDATE_BEARER 不一致或未配置。")
        print(hint[:800])
        return 1

    if status == 204:
        print("HTTP 204 No Content — 鉴权通过，但无新版本（或库中无更高 semver / 无该平台产物 / 未配置 S3）。")
        return 0

    if status != 200:
        print(f"HTTP {status}", file=sys.stderr)
        print(hint[:2000], file=sys.stderr)
        return 1

    if data is None:
        print(f"FAIL {hint}", file=sys.stderr)
        return 1

    print("HTTP 200 — 收到 Tauri 风格更新 JSON：")
    print(json.dumps(data, ensure_ascii=False, indent=2)[:8000])
    return 0


# --- pytest：不设 DESKTOP_UPDATE_BEARER 则跳过，避免 CI 无密钥失败 ---

def test_public_desktop_ota_reachable():
    import pytest

    bearer = resolve_bearer(None)
    if not bearer:
        pytest.skip("set DESKTOP_UPDATE_BEARER or place it in cloud/nexus/.env.local")

    status, data, hint = probe_public_desktop_update(
        bearer=bearer,
        current_version="0.0.1",
    )
    assert status != -1, hint
    assert status != 401, f"auth failed: {hint}"
    assert status in (200, 204), f"unexpected {status}: {hint}"
    if status == 200:
        assert data is not None and "version" in data and "platforms" in data


if __name__ == "__main__":
    raise SystemExit(main())
