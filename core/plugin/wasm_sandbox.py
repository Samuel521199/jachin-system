"""
WASM 沙箱 - Wasmtime 驱动的轻量级执行引擎

适用于低功耗 IoT 设备上的轻量级 Skill：数据清洗、文本格式化等。
微秒级冷启动、绝对内存安全、Default Deny 权限模型。

插件 ABI：WASM 模块需导出
- memory: 共享线性内存
- execute: (param i32 i32) (result i32) — 输入 ptr, len，返回输出长度（写入 memory 起始处）
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _record_wasm_telemetry(
    sub_account_id: str,
    item_id: str,
    action_name: str,
    status: str,
    latency_ms: float,
) -> None:
    """异步写入 Wasm 执行用量遥测（fire-and-forget）"""
    try:
        from core.usage_telemetry import record_usage

        record_usage(sub_account_id, item_id, action_name, status, latency_ms)
    except Exception as e:
        logger.debug("[Wasm Telemetry] 写入失败（可忽略）: %s", e)


def _create_wasm_handle(
    bytecode: bytes,
    manifest: dict[str, Any],
    plugin_id: str,
) -> "_WasmSandboxHandle":
    """工厂函数：加载 WASM 并返回句柄"""
    try:
        from wasmtime import Config, Engine, Linker, Module, Store
    except ImportError as e:
        raise ImportError(
            "WASM 沙箱需要 wasmtime。请安装: pip install wasmtime"
        ) from e

    config = Config()
    config.cache = True
    if hasattr(config, "consume_fuel"):
        config.consume_fuel = True

    engine = Engine(config)
    store = Store(engine)
    if hasattr(store, "add_fuel"):
        ram_mb = 64
        if isinstance(manifest.get("resource_footprint"), dict):
            ram_mb = manifest["resource_footprint"].get("ram_estimate_mb", 64)
        store.add_fuel(ram_mb * 1000)

    linker = Linker(engine)
    module = Module(engine, bytecode)
    instance = linker.instantiate(store, module)

    memory = instance.exports(store).get("memory")
    execute_fn = instance.exports(store).get("execute")

    if memory is None:
        raise ValueError("WASM 模块必须导出 memory")
    if execute_fn is None:
        execute_fn = instance.exports(store).get("run")

    return _WasmSandboxHandle(
        plugin_id=plugin_id,
        store=store,
        instance=instance,
        memory=memory,
        execute_fn=execute_fn,
    )


class _WasmSandboxHandle:
    """
    WASM 沙箱句柄：通过 Wasmtime 执行 .wasm 字节码
    """

    def __init__(
        self,
        plugin_id: str,
        store: Any,
        instance: Any,
        memory: Any,
        execute_fn: Any | None,
    ) -> None:
        self._plugin_id = plugin_id
        self._store = store
        self._instance = instance
        self._memory = memory
        self._execute_fn = execute_fn

    def execute(
        self,
        capability: str,
        payload: dict[str, Any] | bytes | None = None,
    ) -> dict[str, Any]:
        item_id = f"skill:{self._plugin_id}"
        payload_dict = dict(payload) if isinstance(payload, dict) else {}
        sub_account_id = str(payload_dict.pop("_telemetry_sub_account_id", "system"))

        if self._execute_fn is None:
            return {
                "status_code": 200,
                "payload": {"success": True, "text": "WASM 插件已加载（无 execute 导出）"},
            }

        payload_dict["capability"] = capability
        input_bytes = json.dumps(payload_dict, ensure_ascii=False).encode("utf-8")

        t0 = time.perf_counter()
        try:
            mem_size = self._memory.data_len(self._store)
            if mem_size < len(input_bytes) + 256:
                _record_wasm_telemetry(sub_account_id, item_id, capability, "failure", (time.perf_counter() - t0) * 1000)
                return {
                    "status_code": 500,
                    "error_message": "WASM memory 不足",
                }

            self._memory.write(self._store, input_bytes, 0)
            ptr = 0
            length = len(input_bytes)

            result = self._execute_fn(self._store, ptr, length)

            if result is None:
                _record_wasm_telemetry(sub_account_id, item_id, capability, "success", (time.perf_counter() - t0) * 1000)
                return {"status_code": 200, "payload": {"success": True}}

            out_len = int(result)
            if out_len <= 0:
                _record_wasm_telemetry(sub_account_id, item_id, capability, "success", (time.perf_counter() - t0) * 1000)
                return {"status_code": 200, "payload": {"success": True}}

            out_bytes = self._memory.read(self._store, 0, out_len)
            try:
                out_obj = json.loads(out_bytes.decode("utf-8"))
                _record_wasm_telemetry(sub_account_id, item_id, capability, "success", (time.perf_counter() - t0) * 1000)
                return {"status_code": 200, "payload": out_obj}
            except (json.JSONDecodeError, UnicodeDecodeError):
                _record_wasm_telemetry(sub_account_id, item_id, capability, "success", (time.perf_counter() - t0) * 1000)
                return {"status_code": 200, "payload": {"raw": out_bytes.decode("utf-8", errors="replace")}}

        except Exception as e:
            _record_wasm_telemetry(sub_account_id, item_id, capability, "failure", (time.perf_counter() - t0) * 1000)
            logger.warning("WASM execute failed: %s", e)
            return {"status_code": 500, "error_message": str(e)}

    def shutdown(self) -> None:
        pass
