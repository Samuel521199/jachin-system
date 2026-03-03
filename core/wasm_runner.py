"""
Jachin Nexus - WASM 物理沙箱引擎

零信任代码执行：燃料熔断、物理隔离、爆炸半径控制。
使用 wasmtime 实现真正的算力熔断——死循环时燃料耗尽，沙箱当场超度，宿主毫发无损。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from wasmtime import Config, Engine, Instance, Module, Store
    from wasmtime import WasmtimeError
    HAS_WASMTIME = True
except ImportError:
    HAS_WASMTIME = False


class JachinWasmSandbox:
    """
    WASM 物理沙箱：燃料熔断 + 物理隔离

    使用 wasmtime 的 fuel 机制限制执行算力，防止死循环击穿宿主。
    """

    def __init__(self) -> None:
        if not HAS_WASMTIME:
            raise ImportError("WASM 沙箱需要 wasmtime。请安装: pip install wasmtime")
        config = Config()
        config.consume_fuel = True
        self._engine = Engine(config)

    def run_plugin(
        self,
        wasm_file_path: str,
        function_name: str = "run",
        fuel_limit: int = 100_000,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """
        在受限沙箱中执行 WASM 插件。

        Args:
            wasm_file_path: .wasm 文件路径
            function_name: 导出的函数名，默认 "run"
            fuel_limit: 燃料上限，耗尽即熔断

        Returns:
            导出函数的返回值，若无则 None

        Raises:
            FileNotFoundError: wasm 文件不存在
            WasmtimeError: 燃料耗尽、内存越界等（会先打印熔断警告）
        """
        path = Path(wasm_file_path)
        if not path.exists():
            raise FileNotFoundError(f"找不到 WASM 插件: {wasm_file_path}")

        bytecode = path.read_bytes()
        store = Store(self._engine)
        store.set_fuel(fuel_limit)

        module = Module(self._engine, bytecode)
        instance = Instance(store, module, [])

        exports = instance.exports(store)
        func = exports.get(function_name)
        if func is None:
            # 尝试常见导出名
            for name in ("execute", "_start", "main"):
                func = exports.get(name)
                if func is not None:
                    break
        if func is None:
            raise ValueError(
                f"WASM 模块未导出函数 '{function_name}'。"
                " 需导出 run/execute/_start 之一。"
            )

        try:
            result = func(store)
            return result
        except WasmtimeError as e:
            msg = str(e).lower()
            if "out of fuel" in msg or "fuel" in msg or "trap" in msg:
                self._log_meltdown(wasm_file_path, e)
            raise

    def _log_meltdown(self, wasm_path: str, error: Exception) -> None:
        """熔断时打印夺目警告"""
        try:
            from rich.console import Console
            from rich.theme import Theme
            console = Console(theme=Theme({"red": "#ef4444", "bold": "bold"}))
            console.print(
                "[bold red]🚨 [熔断机制触发] WASM 插件执行超时/死循环，已物理超度！宿主安全。[/bold red]"
            )
            console.print(f"[red]  插件: {wasm_path}[/red]")
            console.print(f"[red]  原因: {error}[/red]")
        except ImportError:
            logger.warning(
                "🚨 [熔断机制触发] WASM 插件 %s 执行超时/死循环，已物理超度！宿主安全。%s",
                wasm_path,
                error,
            )


def run_wasm_plugin(
    wasm_path: str,
    function_name: str = "run",
    fuel_limit: int = 100_000,
) -> Any:
    """
    便捷函数：在沙箱中执行 WASM 插件。

    若 wasm_path 不存在，返回 None（优雅跳过）。
    """
    if not Path(wasm_path).exists():
        return None
    if not HAS_WASMTIME:
        return None
    try:
        sandbox = JachinWasmSandbox()
        return sandbox.run_plugin(wasm_path, function_name, fuel_limit)
    except Exception as e:
        logger.warning("WASM 执行失败 %s: %s", wasm_path, e)
        raise
