"""
Jachin Nexus - WASM 物理沙箱引擎

零信任代码执行：燃料熔断、物理隔离、爆炸半径控制。
使用 wasmtime 实现真正的算力熔断——死循环时燃料耗尽，沙箱当场超度，宿主毫发无损。

双模式支持：
- Pure Compute：run() -> i32，Rust 等直接导出
- WASI：stdin/stdout JSON 协议，Python (py2wasm) 等系统接口型插件
"""
from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from wasmtime import Config, Engine, Instance, Linker, Module, Store, WasiConfig
    from wasmtime import WasmtimeError
    HAS_WASMTIME = True
except ImportError:
    HAS_WASMTIME = False
    Linker = None  # type: ignore
    WasiConfig = None  # type: ignore


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
        stdin_json: dict | str | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """
        在受限沙箱中执行 WASM 插件。

        支持两种模式：
        - stdin_json=None：Pure Compute，调用 run() -> i32（Rust 插件）
        - stdin_json 有值：WASI 模式，通过 stdin/stdout 传递 JSON（Python 插件）

        Args:
            wasm_file_path: .wasm 文件路径
            function_name: 导出的函数名，默认 "run"
            fuel_limit: 燃料上限，耗尽即熔断
            stdin_json: 可选，WASI 模式下的 stdin 内容（dict 或 JSON 字符串）

        Returns:
            Pure Compute 模式：导出函数的返回值
            WASI 模式：stdout 字符串（通常为 JSON）
        """
        if stdin_json is not None:
            if not HAS_WASMTIME or Linker is None or WasiConfig is None:
                raise ImportError("WASI 模式需要 wasmtime。请安装: pip install wasmtime")
            stdin_str = (
                json.dumps(stdin_json, ensure_ascii=False)
                if isinstance(stdin_json, dict)
                else str(stdin_json)
            )
            return self.run_plugin_wasi(wasm_file_path, stdin_str, fuel_limit)
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

    def run_plugin_wasi(
        self,
        wasm_file_path: str,
        stdin_str: str = "{}",
        fuel_limit: int = 100_000,
    ) -> str:
        """
        在 WASI 沙箱中执行插件，通过 stdin/stdout 传递 JSON。

        适用于 py2wasm 编译的 Python 插件（@jachin_plugin、stdin/stdout 协议）。

        Args:
            wasm_file_path: .wasm 文件路径
            stdin_str: 写入 stdin 的 JSON 字符串
            fuel_limit: 燃料上限

        Returns:
            stdout 内容（通常为 JSON 字符串）
        """
        path = Path(wasm_file_path)
        if not path.exists():
            raise FileNotFoundError(f"找不到 WASM 插件: {wasm_file_path}")

        bytecode = path.read_bytes()

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            delete=False,
            encoding="utf-8",
        ) as f_in:
            f_in.write(stdin_str)
            stdin_path = f_in.name
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            delete=False,
            encoding="utf-8",
        ) as f_out:
            stdout_path = f_out.name

        try:
            wasi = WasiConfig()
            wasi.stdin_file = stdin_path
            wasi.stdout_file = stdout_path

            store = Store(self._engine)
            store.set_fuel(fuel_limit)
            store.set_wasi(wasi)

            linker = Linker(self._engine)
            linker.define_wasi()

            module = Module(self._engine, bytecode)
            instance = linker.instantiate(store, module)

            exports = instance.exports(store)
            start = exports.get("_start")
            if start is not None:
                start(store)
            else:
                for name in ("run", "main", "_initialize"):
                    fn = exports.get(name)
                    if fn is not None:
                        fn(store)
                        break

            with open(stdout_path, encoding="utf-8") as f:
                return f.read()
        except WasmtimeError as e:
            msg = str(e).lower()
            if "out of fuel" in msg or "fuel" in msg or "trap" in msg:
                self._log_meltdown(wasm_file_path, e)
            raise
        finally:
            Path(stdin_path).unlink(missing_ok=True)
            Path(stdout_path).unlink(missing_ok=True)

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
    stdin_json: dict | str | None = None,
) -> Any:
    """
    便捷函数：在沙箱中执行 WASM 插件。

    若 wasm_path 不存在，返回 None（优雅跳过）。
    stdin_json 有值时使用 WASI 模式（Python py2wasm 插件）。
    """
    if not Path(wasm_path).exists():
        return None
    if not HAS_WASMTIME:
        return None
    try:
        sandbox = JachinWasmSandbox()
        return sandbox.run_plugin(
            wasm_path,
            function_name,
            fuel_limit,
            stdin_json=stdin_json,
        )
    except Exception as e:
        logger.warning("WASM 执行失败 %s: %s", wasm_path, e)
        raise
