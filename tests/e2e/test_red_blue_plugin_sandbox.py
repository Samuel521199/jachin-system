"""
全链路实弹演习：红蓝对抗与端到端集成测试

演习一：红队渗透测试 - 恶意插件应被 validator 拦截
演习二：蓝队业务闭环 - 合法插件应通过审查并成功加载
"""

import shutil
import tempfile
import zipfile
from pathlib import Path

import pytest

from core.plugin.validator import extract_and_validate, scan_python_code, SecurityViolationError
from core.plugin.sandbox import PluginSandbox


FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "plugins"


def _build_jmp(plugin_dir: Path, output_path: Path) -> None:
    """将插件目录打包为 .jmp (ZIP)"""
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in plugin_dir.rglob("*"):
            if f.is_file():
                arcname = f.relative_to(plugin_dir)
                zf.write(f, arcname)


class TestRedTeamRansomwareMock:
    """演习一：红队渗透测试"""

    def test_validator_blocks_subprocess_import(self):
        """恶意插件：import subprocess 未声明权限 -> 应被拦截"""
        ransomware_dir = FIXTURES_DIR / "ransomware_mock"
        if not ransomware_dir.exists():
            pytest.skip("fixtures/plugins/ransomware_mock 不存在")

        with tempfile.TemporaryDirectory() as tmp:
            jmp_path = Path(tmp) / "ransomware_mock.jmp"
            extract_dir = Path(tmp) / "extract"
            _build_jmp(ransomware_dir, jmp_path)

            with pytest.raises(SecurityViolationError) as exc_info:
                extract_and_validate(str(jmp_path), str(extract_dir))

            assert "subprocess" in str(exc_info.value).lower() or exc_info.value.module == "subprocess"
            assert not extract_dir.exists() or not list(extract_dir.iterdir()), "临时目录应被清理"

    def test_scan_python_code_blocks_os_without_permission(self):
        """main.py 含 import os 且未声明 system.power -> 应拦截"""
        code = "import os\nos.getcwd()"
        with pytest.raises(SecurityViolationError):
            scan_python_code(code, [])

    def test_scan_python_code_allows_os_with_system_power(self):
        """声明 system.power 后 import os -> 应放行"""
        code = "import os\nx = os.getcwd()"
        assert scan_python_code(code, ["system.power"]) is True


class TestBlueTeamLocalTimeWeather:
    """演习二：蓝队业务闭环"""

    def test_validator_passes_with_internet_access(self):
        """合法插件：声明 internet.access，使用 requests -> 应通过"""
        weather_dir = FIXTURES_DIR / "local_time_weather"
        if not weather_dir.exists():
            pytest.skip("fixtures/plugins/local_time_weather 不存在")

        with tempfile.TemporaryDirectory() as tmp:
            jmp_path = Path(tmp) / "local_time_weather.jmp"
            extract_dir = Path(tmp) / "extract"
            _build_jmp(weather_dir, jmp_path)

            manifest = extract_and_validate(str(jmp_path), str(extract_dir))
            assert manifest["id"] == "com.blueteam.local-time-weather"
            assert "internet.access" in manifest.get("permissions", [])
            assert (extract_dir / "main.py").exists()

    def test_sandbox_loads_plugin_and_extracts_setup(self):
        """沙箱应成功加载插件并提取 setup 入口"""
        weather_dir = FIXTURES_DIR / "local_time_weather"
        if not weather_dir.exists():
            pytest.skip("fixtures/plugins/local_time_weather 不存在")

        manifest_path = weather_dir / "manifest.json"
        import json
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        sandbox = PluginSandbox(allow_file_ops=False)
        entry = sandbox.load_plugin(str(weather_dir), manifest)

        assert entry is not None
        assert callable(entry)
        result = entry({})
        assert "capabilities" in result
        assert len(result["capabilities"]) >= 1
        assert "get_time" in [c["name"] for c in result["capabilities"]]

    def test_plugin_get_time_returns_valid_response(self):
        """插件 handle_get_time 应返回正确格式"""
        weather_dir = FIXTURES_DIR / "local_time_weather"
        if not weather_dir.exists():
            pytest.skip("fixtures/plugins/local_time_weather 不存在")

        import sys
        sys.path.insert(0, str(weather_dir))
        try:
            from main import handle_get_time
            resp = handle_get_time({})
            assert resp["success"] is True
            assert "text" in resp
            assert "现在是" in resp["text"] or ":" in resp["text"]
        finally:
            sys.path.pop(0)


class TestSandboxRestrictsEval:
    """沙箱应拦截 eval/exec 等危险内建"""

    def test_sandbox_blocks_eval(self):
        """插件在模块加载时调用 eval() 应被沙箱拦截"""
        with tempfile.TemporaryDirectory() as tmp:
            main_py = Path(tmp) / "main.py"
            # eval 在模块顶层执行，load_plugin 的 exec() 会立即触发
            main_py.write_text("""
x = eval("1+1")
def setup(ctx):
    return {}
""", encoding="utf-8")
            manifest = {"id": "test", "permissions": [], "entry": "main.py"}

            sandbox = PluginSandbox(allow_file_ops=False)
            with pytest.raises(PermissionError) as exc_info:
                sandbox.load_plugin(str(tmp), manifest)

            assert "eval" in str(exc_info.value).lower()
