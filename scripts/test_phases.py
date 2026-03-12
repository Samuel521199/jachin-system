#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
阶段 1-6 联调验证脚本
在启动后端后运行，检查各 API 是否正常响应
用法: python scripts/test_phases.py
       python scripts/test_phases.py 18888   # 指定端口
前置: 后端需已启动 (start.bat 或 python core/main.py)，默认端口 18888
"""

import sys
import json
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from core.config import settings

try:
    import urllib.request
    import urllib.error
except ImportError:
    import urllib2 as urllib_request  # type: ignore
    urllib.request = urllib_request  # type: ignore

_PORT = int(sys.argv[1]) if len(sys.argv) > 1 else settings.effective_port
BASE = f"http://localhost:{_PORT}"


def fetch(path: str, method: str = "GET", data: dict = None, parse_json: bool = True) -> tuple[int, dict | list | str | None]:
    """GET/POST 请求。parse_json=False 时仅返回状态码，不解析 JSON（用于 /hive/ 等 HTML 页面）"""
    url = BASE + path
    req = urllib.request.Request(url, method=method)
    if data and method != "GET":
        req.data = json.dumps(data).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            body = r.read().decode()
            if not parse_json:
                return r.status, body
            return r.status, json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:
        print(f"  [FAIL] {e}")
        return -1, None


def main():
    print("=" * 60)
    print("Jachin 阶段 1-6 联调验证")
    print("=" * 60)
    print(f"后端: {BASE} (请确保已启动 start.bat 或 python core/main.py)\n")

    ok = 0
    total = 0

    # 阶段 1: 模型
    print("[阶段 1] 模型热切换")
    total += 1
    code, r = fetch("/api/v3/models")
    if code == 200 and r and "models" in r:
        print(f"  [OK] GET /api/v3/models: current={r.get('current', '?')}")
        ok += 1
    else:
        print(f"  [FAIL] GET /api/v3/models: {code}")

    code, r = fetch("/api/v3/models/current", "POST", {"model_id": "qwen-turbo"})
    if code == 200 and r and r.get("ok"):
        print(f"  [OK] POST /api/v3/models/current")
        ok += 1
    else:
        print(f"  [FAIL] POST /api/v3/models/current: {code}")
    total += 1

    # 阶段 2: 推理策略
    print("\n[阶段 2] 推理策略")
    total += 1
    code, r = fetch("/api/v3/inference/strategy")
    if code == 200 and r and "mode" in r:
        print(f"  [OK] GET /api/v3/inference/strategy: mode={r.get('mode')}")
        ok += 1
    else:
        print(f"  [FAIL] GET /api/v3/inference/strategy: {code}")

    code, r = fetch("/api/v3/inference/strategy", "POST", {"mode": "default"})
    if code == 200 and r and r.get("ok"):
        print(f"  [OK] POST /api/v3/inference/strategy")
        ok += 1
    else:
        print(f"  [FAIL] POST: {code}")
    total += 1

    # 阶段 3: 日志
    print("\n[阶段 3] 思维流日志")
    total += 1
    code, r = fetch("/api/v3/logs/recent?limit=5")
    if code == 200 and r and "lines" in r:
        print(f"  [OK] GET /api/v3/logs/recent: {len(r.get('lines', []))} lines")
        ok += 1
    else:
        print(f"  [FAIL] GET /api/v3/logs/recent: {code}")

    # 阶段 4/5/6: 技能、技能状态
    print("\n[阶段 6] 技能与状态")
    total += 1
    code, r = fetch("/api/v3/skills")
    if code == 200 and isinstance(r, list):
        print(f"  [OK] GET /api/v3/skills: {len(r)} skills")
        ok += 1
        if r:
            sid = r[0].get("skill_id", "")
            if sid:
                code2, r2 = fetch(f"/api/v3/skills/{sid}/status")
                if code2 == 200 and r2:
                    print(f"  [OK] GET /api/v3/skills/{{id}}/status: executions={r2.get('executions', 0)}")
                    ok += 1
                total += 1
    else:
        print(f"  [FAIL] GET /api/v3/skills: {code}")

    # Hive
    print("\n[Hive] Dashboard")
    total += 1
    code, _ = fetch("/hive/", parse_json=False)
    if code == 200:
        print(f"  [OK] GET /hive/ 可访问")
        ok += 1
    else:
        print(f"  [FAIL] GET /hive/: {code}")

    # 阶段 7: 建议 API + 日历 API
    print("\n[阶段 7] 建议 API")
    total += 1
    code, r = fetch("/api/v3/suggestions")
    if code == 200 and r and "items" in r:
        items = r.get("items", [])
        print(f"  [OK] GET /api/v3/suggestions: {len(items)} 条建议")
        ok += 1
    else:
        print(f"  [FAIL] GET /api/v3/suggestions: {code}")

    print("\n[阶段 7] 日历 API")
    total += 1
    code, r = fetch("/api/v3/calendar/items?days=7")
    if code == 200 and r and "items" in r:
        print(f"  [OK] GET /api/v3/calendar/items: {len(r.get('items', []))} 条")
        ok += 1
    else:
        print(f"  [FAIL] GET /api/v3/calendar/items: {code}")

    print("\n" + "=" * 60)
    print(f"结果: {ok}/{total} 通过")
    print("=" * 60)
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
