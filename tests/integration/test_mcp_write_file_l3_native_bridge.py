"""
集成：mcp:write_file 须在 L3 走 Native 桥接（core:fs_write），不得先打 stdio filesystem（-32602）。

对应用户任务：工作区 scripts/system_monitor.py，每 2 秒打印 CPU/内存。
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path

import pytest

# 与 core.native_tools 一致：相对路径落在 ~/.jachin/workspace/
_WORKSPACE = Path.home() / ".jachin" / "workspace"

SYSTEM_MONITOR_PY = '''"""每约 2 秒打印 CPU 与内存占用（优先 psutil）。"""
import time

try:
    import psutil
except ImportError:
    psutil = None

def main() -> None:
    while True:
        if psutil is not None:
            cpu = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory().percent
            print(f"CPU: {cpu:.1f}%  MEM: {mem:.1f}%")
        else:
            print("psutil 未安装，请: pip install psutil")
        time.sleep(2)

if __name__ == "__main__":
    main()
'''


@pytest.fixture
def probe_rel_path() -> str:
    return f"scripts/_jachin_probe_sm_{uuid.uuid4().hex[:10]}.py"


def test_mcp_write_file_invokes_native_bridge_not_stdio_errors(probe_rel_path: str) -> None:
    """mcp:write_file + 合法 path/content 应返回 written 且文件落在 workspace。"""
    from l3_node.primitives.mcp.registry import MCPToolRegistry

    _WORKSPACE.mkdir(parents=True, exist_ok=True)
    reg = MCPToolRegistry(l2_base_url="http://127.0.0.1:9")
    payload = json.dumps(
        {"path": probe_rel_path, "content": "print('jachin_probe_ok')\n"},
        ensure_ascii=False,
    )
    out = asyncio.run(reg.invoke("mcp:write_file", payload))
    assert out
    assert "-32602" not in out
    data = json.loads(out)
    assert data.get("ok") is True or data.get("status") == "written"
    assert data.get("via") == "l3_native_bridge"
    fp = (_WORKSPACE / probe_rel_path).resolve()
    assert fp.is_file()
    txt = fp.read_text(encoding="utf-8")
    assert "jachin_probe_ok" in txt
    try:
        fp.unlink(missing_ok=True)  # type: ignore[call-arg]
    except TypeError:
        if fp.exists():
            fp.unlink()


def test_mcp_write_file_system_monitor_task_content(probe_rel_path: str) -> None:
    """完整 system_monitor.py 内容写入 scripts/ 下（与用户话术一致的路径形态）。"""
    from l3_node.primitives.mcp.registry import MCPToolRegistry

    _WORKSPACE.mkdir(parents=True, exist_ok=True)
    reg = MCPToolRegistry(l2_base_url="http://127.0.0.1:9")
    rel = f"scripts/system_monitor_{uuid.uuid4().hex[:8]}.py"
    payload = json.dumps({"path": rel, "content": SYSTEM_MONITOR_PY}, ensure_ascii=False)
    out = asyncio.run(reg.invoke("mcp:write_file", payload))
    assert out and "-32602" not in out
    data = json.loads(out)
    assert data.get("via") == "l3_native_bridge"
    fp = (_WORKSPACE / rel).resolve()
    assert fp.is_file()
    body = fp.read_text(encoding="utf-8")
    assert "psutil" in body and "time.sleep(2)" in body
    abs_msg = str(fp)
    assert os.path.isabs(abs_msg)
    try:
        fp.unlink(missing_ok=True)  # type: ignore[call-arg]
    except TypeError:
        if fp.exists():
            fp.unlink()


def test_bridge_missing_path_json_not_32602() -> None:
    from l3_node.primitives.mcp.registry import MCPToolRegistry

    reg = MCPToolRegistry(l2_base_url="http://127.0.0.1:9")
    out = asyncio.run(
        reg.invoke("mcp:write_file", json.dumps({"content": "only content"}, ensure_ascii=False))
    )
    assert "missing_path" in out
