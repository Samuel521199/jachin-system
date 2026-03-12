"""
P0-3 沙箱装载引擎单元测试
"""
import shutil
import tempfile
from pathlib import Path

import pytest

from core.plugin.sandbox_engine import SandboxEngine

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "plugins" / "heavy_process_demo"


@pytest.fixture
def extract_dir():
    """构建符合 JMP 2.0 的 extract 目录"""
    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp)
        # 复制 manifest
        shutil.copy(FIXTURES_DIR / "manifest.json", dst / "manifest.json")
        # 复制 payload
        shutil.copytree(FIXTURES_DIR / "payload", dst / "payload")
        yield str(dst)


def test_sandbox_engine_load_heavy_process(extract_dir):
    """heavy_process 分流：应返回带 execute 的句柄"""
    import json
    manifest = json.loads((Path(extract_dir) / "manifest.json").read_text(encoding="utf-8"))
    handle = SandboxEngine.load(extract_dir, manifest)
    assert handle is not None
    assert hasattr(handle, "execute")
    assert callable(getattr(handle, "execute", None))
    # 调用 ping
    result = handle.execute("ping", {})
    assert result.get("status_code") == 200
    assert result.get("payload", {}).get("message") == "pong"
    # 调用 echo
    result = handle.execute("echo", {"text": "hello"})
    assert result.get("status_code") == 200
    assert result.get("payload", {}).get("echo") == "hello"
    # 关闭
    if hasattr(handle, "shutdown"):
        handle.shutdown()


def test_sandbox_engine_wasm():
    """wasm 分流：wasmtime 可用时返回句柄，否则 NotImplementedError"""
    import json
    wasm_fixture = Path(__file__).parent.parent / "fixtures" / "plugins" / "wasm_minimal"
    if not (wasm_fixture / "payload" / "module.wasm").exists():
        pytest.skip("WASM fixture not built (run: python -c \"from wasmtime import wat2wasm; ...\")")

    manifest = json.loads((wasm_fixture / "manifest.json").read_text(encoding="utf-8"))
    try:
        handle = SandboxEngine.load(str(wasm_fixture), manifest)
    except NotImplementedError:
        pytest.skip("wasmtime not installed")
        return

    assert handle is not None
    assert hasattr(handle, "execute")
    result = handle.execute("ping", {})
    assert result.get("status_code") == 200
    if hasattr(handle, "shutdown"):
        handle.shutdown()


def test_sandbox_engine_default_fallback():
    """默认分流：回退到 PluginSandbox，返回 entry_point"""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "manifest.json").write_text(
            '{"id":"test","entry":"main.py","permissions":[]}',
            encoding="utf-8",
        )
        (d / "main.py").write_text(
            "def setup(ctx): return {'capabilities':[]}",
            encoding="utf-8",
        )
        manifest = {"id": "test", "entry": "main.py", "permissions": []}
        entry = SandboxEngine.load(str(d), manifest)
        assert entry is not None
        assert callable(entry)
        assert entry.__name__ == "setup"
