"""
Sandbox Engine - P0-3 混合动力沙箱装载引擎

根据 manifest.execution_model 进行物理分流：
- wasm: 轻量级 WASM 沙箱（预留接口，后续接入 Wasmtime）
- heavy_process: 重型独立进程，subprocess + UDS 通信
- resource_mount: 只读卷挂载（Persona/Memory 静态资产，供 VAD/RAG 读取）

符合 docs/HYBRID_SANDBOX_ARCHITECTURE.md
"""

import logging
import shutil
from pathlib import Path
from typing import Any, Protocol

from core.plugin.heavy_process import HeavyProcessClient, HeavyProcessRunner
from core.plugin.resource_mount import get_resources_dir, register_mount
from core.plugin.sandbox import PluginSandbox

logger = logging.getLogger(__name__)

# 全局注册表：plugin_id -> HeavyProcessRunner，供 TelemetryAgent 采集 Supervisor 状态
_runners: dict[str, HeavyProcessRunner] = {}


def get_plugin_runners() -> dict[str, HeavyProcessRunner]:
    """供遥测雷达读取插件状态"""
    return dict(_runners)


class SandboxHandle(Protocol):
    """沙箱句柄协议：统一的调用接口"""

    def execute(self, capability: str, payload: dict | bytes | None) -> dict[str, Any]:
        ...

    def shutdown(self) -> None:
        ...


class WasmSandboxHandle:
    """
    WASM 沙箱句柄（Wasmtime 驱动）
    微秒级冷启动、绝对内存安全，适用于低功耗 IoT 轻量级 Skill
    """


class SandboxEngine:
    """
    沙箱装载引擎：根据 manifest 分流到 WASM 或 heavy_process
    """

    @staticmethod
    def _get_payload_dir(extract_dir: Path, manifest: dict[str, Any]) -> Path:
        """
        解析 payload 目录
        JMP 2.0: extract_dir/payload/
        旧结构: extract_dir/ (main.py 在根目录)
        """
        payload = extract_dir / "payload"
        if payload.exists():
            return payload
        return extract_dir

    @staticmethod
    def load(
        extract_dir: str,
        manifest: dict[str, Any],
        socket_base_dir: str | None = None,
    ) -> SandboxHandle | Any:
        """
        根据 manifest.execution_model 装载沙箱

        Args:
            extract_dir: 已解压的 .jmp 目录（含 manifest.json、payload/）
            manifest: 解析后的 manifest
            socket_base_dir: UDS 路径基目录（仅 heavy_process）

        Returns:
            - heavy_process: HeavyProcessClient（可 execute）
            - wasm: 暂未实现，抛出 NotImplementedError
            - 默认: 回退到 PluginSandbox（轻量 Python AST 沙箱）
        """
        extract_path = Path(extract_dir)
        plugin_id = manifest.get("plugin_id") or manifest.get("id", "unknown")
        execution_model = manifest.get("execution_model", "sandbox")

        if execution_model == "wasm":
            # 分流 A：WASM 沙箱（Wasmtime 驱动）
            payload_dir = SandboxEngine._get_payload_dir(extract_path, manifest)
            wasm_path = payload_dir / "module.wasm"
            if not wasm_path.exists():
                wasm_path = payload_dir / "main.wasm"
            if not wasm_path.exists():
                raise FileNotFoundError(f"WASM 模式需要 payload/module.wasm 或 main.wasm，未找到")
            bytecode = wasm_path.read_bytes()
            try:
                from core.plugin.wasm_sandbox import _create_wasm_handle
                return _create_wasm_handle(bytecode, manifest, plugin_id)
            except ImportError as e:
                raise NotImplementedError(
                    "WASM 沙箱需要 wasmtime。请安装: pip install wasmtime。"
                    f" 当前错误: {e}"
                ) from e

        if execution_model == "heavy_process":
            # 分流 B：重型独立进程
            payload_dir = SandboxEngine._get_payload_dir(extract_path, manifest)
            runner = HeavyProcessRunner(
                plugin_id=plugin_id,
                payload_dir=str(payload_dir),
                manifest=manifest,
                socket_base_dir=socket_base_dir,
            )
            client = runner.start()
            _runners[plugin_id] = runner
            return _HeavyProcessHandle(plugin_id, runner, client)

        if execution_model == "resource_mount":
            # 分流 C：只读卷挂载（Persona 语音包、Memory 向量库等静态资产）
            # 解压到 resources_repo，施加只读保护，设置 JACHIN_VOL_xxx 环境变量
            resources_dir = get_resources_dir()
            safe_id = plugin_id.replace(".", "_").replace("-", "_")
            target_dir = resources_dir / safe_id

            if target_dir.exists():
                shutil.rmtree(target_dir)
            shutil.copytree(extract_path, target_dir)

            register_mount(plugin_id, str(target_dir), make_readonly=True)

            logger.info(
                "Resource '%s' mounted at %s. core-llm-intent 等 Skill 可通过 JACHIN_VOL_* 环境变量读取。",
                plugin_id,
                target_dir,
            )
            return _ResourceMountHandle(plugin_id, str(target_dir))

        # 默认：回退到原有 PluginSandbox（轻量 Python AST 沙箱）
        payload_dir = SandboxEngine._get_payload_dir(extract_path, manifest)
        perms = manifest.get("permissions", [])
        perm_strs = [p.get("scope", p) if isinstance(p, dict) else p for p in perms]
        allow_file = "file.read" in perm_strs or "file.write" in perm_strs
        sandbox = PluginSandbox(allow_file_ops=allow_file)
        entry_point = sandbox.load_plugin(str(payload_dir), manifest)
        # 保持与 UpdaterAgent 兼容：返回原始 entry_point（setup/Plugin 类）
        return entry_point


class _HeavyProcessHandle:
    """heavy_process 沙箱句柄：封装 Runner + Client"""

    def __init__(self, plugin_id: str, runner: HeavyProcessRunner, client: HeavyProcessClient) -> None:
        self._plugin_id = plugin_id
        self._runner = runner
        self._client = client

    def execute(
        self, capability: str, payload: dict[str, Any] | bytes | None = None
    ) -> dict[str, Any]:
        return self._client.execute(capability, payload)

    def shutdown(self) -> None:
        self._runner.stop()
        _runners.pop(self._plugin_id, None)


class _ResourceMountHandle:
    """
    resource_mount 句柄：只读卷，无 execute
    Skill 插件通过 os.environ["JACHIN_VOL_xxx"] 或 get_mount_path(plugin_id) 读取
    """

    _IS_RESOURCE_MOUNT = True  # 供 UpdaterAgent 识别

    def __init__(self, plugin_id: str, mount_path: str) -> None:
        self._plugin_id = plugin_id
        self._mount_path = mount_path

    def get_path(self) -> str:
        """返回挂载路径"""
        return self._mount_path

    def execute(
        self, capability: str, payload: dict[str, Any] | bytes | None = None
    ) -> dict[str, Any]:
        """静态资源无 execute，返回提示"""
        return {
            "status_code": 400,
            "error_message": "resource_mount 型插件不可执行，请通过 JACHIN_VOL_* 环境变量读取",
        }

    def shutdown(self) -> None:
        """无进程可关闭"""
        pass
