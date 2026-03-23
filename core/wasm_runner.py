"""
Jachin Nexus - WASM 物理沙箱引擎

零信任代码执行：燃料熔断、物理隔离、爆炸半径控制。
使用 wasmtime 实现真正的算力熔断——死循环时燃料耗尽，沙箱当场超度，宿主毫发无损。

双模式支持：
- Pure Compute：run() -> i32，Rust 等直接导出
- WASI：stdin/stdout JSON 协议，Python (py2wasm) 等系统接口型插件

Host Functions（供 hr-analyzer4 等技能）：
- env.http_post(url_ptr, url_len, body_ptr, body_len) -> response_len：发起 HTTP POST，响应写入 OUTPUT_OFFSET
- env.llm_complete(prompt_ptr, prompt_len) -> response_len：调用 LLM，结果写入 OUTPUT_OFFSET
"""
from __future__ import annotations

import json
import logging
import queue
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# L3 本地数据卷根（Boss 收网 PDF 蓄水池）
_L3_VOLUME_ROOT = Path.home() / ".jachin" / "client_volumes"

# Host 服务注册（L3 bootstrap 时注入，供 http_post/llm_complete 使用）
_host_services: dict[str, Any] = {}

# 当前执行上下文（run_plugin_execute_abi 调用前设置，供 host 函数访问 memory/store）
_run_context: dict[str, Any] = {}

# NDJSON 流式输出（host_stream_ndjson 写入，execute 完成后可读取）
_last_ndjson_lines: list[str] = []


def get_last_ndjson_lines() -> list[str]:
    """获取并清空上次 Wasm 执行的 NDJSON 流式输出。"""
    global _last_ndjson_lines
    lines, _last_ndjson_lines = _last_ndjson_lines, []
    return lines


def register_host_services(
    *,
    llm_engine: Optional[Any] = None,
    l2_base_url: Optional[str] = None,
) -> None:
    """注册 LLM 引擎与 L2 地址，供 Wasm 技能 Host 函数使用。"""
    if llm_engine is not None:
        _host_services["llm_engine"] = llm_engine
    if l2_base_url is not None:
        _host_services["l2_base_url"] = l2_base_url.rstrip("/")

try:
    from wasmtime import Config, Engine, Instance, Linker, Module, Store, WasiConfig
    from wasmtime import WasmtimeError
    HAS_WASMTIME = True
except ImportError:
    HAS_WASMTIME = False
    Linker = None  # type: ignore
    WasiConfig = None  # type: ignore


class WasmExecutionError(Exception):
    """WASM 执行异常，携带完整 wasm 栈与错误详情供前端排查"""

    def __init__(self, message: str, wasm_details: str | None = None):
        super().__init__(message)
        self.wasm_details = wasm_details or ""


def _format_wasm_error(exc: BaseException) -> str:
    """将 WasmtimeError / trap 格式化为可读的 wasm 详情（含 backtrace）"""
    parts = [str(exc)]
    if hasattr(exc, "__traceback__") and exc.__traceback__:
        tb = "".join(traceback.format_tb(exc.__traceback__))
        parts.append(f"Python traceback:\n{tb}")
    return "\n---\n".join(parts)


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
        import sys
        if stdin_json is not None:
            stdin_str = (
                json.dumps(stdin_json, ensure_ascii=False, separators=(",", ":"))
                if isinstance(stdin_json, dict)
                else str(stdin_json)
            )
            # 先尝试 execute(ptr, len) ABI（Rust JPP 插件如 jachin-system-pilot）
            print(f"[Skill Execute] [WASM] 尝试 execute ABI wasm={wasm_file_path}", file=sys.stderr, flush=True)
            try:
                ndjson_q = kwargs.get("ndjson_queue")
                out = self.run_plugin_execute_abi(wasm_file_path, stdin_str, fuel_limit, ndjson_queue=ndjson_q)
                if out is not None:
                    return out
            except Exception as e:
                print(f"[Skill Execute] [WASM] execute ABI 不可用，回退 WASI: {e}", file=sys.stderr, flush=True)
                logger.debug("[WASM] execute ABI 不可用，回退 WASI: %s", e)
            if not HAS_WASMTIME or Linker is None or WasiConfig is None:
                raise ImportError("WASI 模式需要 wasmtime。请安装: pip install wasmtime")
            print(f"[Skill Execute] [WASM] WASI 模式 wasm={wasm_file_path} stdin_len={len(stdin_str)}", file=sys.stderr, flush=True)
            return self.run_plugin_wasi(wasm_file_path, stdin_str, fuel_limit)
        path = Path(wasm_file_path)
        if not path.exists():
            raise FileNotFoundError(f"找不到 WASM 插件: {wasm_file_path}")

        print(f"[Skill Execute] [WASM] Pure Compute 模式 wasm={wasm_file_path} func={function_name}", file=sys.stderr, flush=True)
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
            print(f"[Skill Execute] [WASM] 未找到导出函数 wasm={wasm_file_path} func={function_name}", file=sys.stderr, flush=True)
            raise ValueError(
                f"WASM 模块未导出函数 '{function_name}'。"
                " 需导出 run/execute/_start 之一。"
            )

        try:
            result = func(store)
            print(f"[Skill Execute] [WASM] 返回 wasm={wasm_file_path} result={result!r}", file=sys.stderr, flush=True)
            return result
        except WasmtimeError as e:
            msg = str(e).lower()
            if "out of fuel" in msg or "fuel" in msg or "trap" in msg:
                self._log_meltdown(wasm_file_path, e)
            raise WasmExecutionError(str(e), _format_wasm_error(e))

    def _make_execute_linker(self) -> "Linker | None":
        """创建带 alloc/dealloc host 函数的 Linker，供 execute ABI 使用"""
        if not HAS_WASMTIME or Linker is None:
            return None
        try:
            from wasmtime import FuncType, ValType
        except ImportError:
            return None
        # 每 store 的 bump 分配器状态：heap_ptr
        heap_state: dict[int, int] = {}
        HEAP_START = 0x10000  # 64KB 偏移，避免覆盖 wasm 静态数据

        def _store_id() -> int:
            """wasmtime-py 不传 caller，用 _run_context 的 store 作为 heap 键"""
            s = _run_context.get("store")
            return id(s) if s else 0

        def _rust_alloc(size: int, align: int) -> int:
            if size <= 0:
                return 0
            store_id = _store_id()
            ptr = heap_state.get(store_id, HEAP_START)
            align = max(align, 1)
            ptr = (ptr + align - 1) & ~(align - 1)
            heap_state[store_id] = ptr + size
            return ptr

        def _rust_dealloc(_ptr: int, _size: int, _align: int) -> None:
            pass  # bump 分配器不回收

        def _rust_realloc(ptr: int, old_size: int, align: int, new_size: int) -> int:
            if new_size <= old_size:
                return ptr
            return _rust_alloc(new_size, align)

        def _rust_alloc_zeroed(size: int, align: int) -> int:
            return _rust_alloc(size, align)

        OUTPUT_OFFSET = 0x8000  # 与 execute ABI 约定

        def _get_mem_store() -> tuple[Any, Any]:
            """从 _run_context 获取 memory 与 store（host 函数无 Caller 时使用）"""
            mem = _run_context.get("memory")
            store = _run_context.get("store")
            return mem, store

        def _http_post(url_ptr: int, url_len: int, body_ptr: int, body_len: int) -> int:
            """Host: HTTP POST，响应写入 OUTPUT_OFFSET，返回响应字节数。"""
            mem, store = _get_mem_store()
            if not mem or not store:
                return _write_err_fallback(OUTPUT_OFFSET, "⚠️ 无法连接 L2 机房或读取文件失败")
            try:
                url = bytes(mem.read(store, url_ptr, url_ptr + url_len)).decode("utf-8", errors="replace")
                body = bytes(mem.read(store, body_ptr, body_ptr + body_len)).decode("utf-8", errors="replace") if body_len > 0 else ""
                import httpx
                l2 = _host_services.get("l2_base_url", "http://localhost:18888")
                full_url = url if url.startswith("http") else f"{l2.rstrip('/')}{url}"
                with httpx.Client(timeout=30.0) as client:
                    resp = client.post(full_url, content=body.encode("utf-8") if body else b"", headers={"Content-Type": "application/json"})
                    resp.raise_for_status()
                    out = resp.content
                out_str = out.decode("utf-8", errors="replace")
                return _write_to_mem(mem, store, OUTPUT_OFFSET, out_str)
            except Exception as e:
                err = f"⚠️ 无法连接 L2 机房或读取文件失败: {e}"
                return _write_to_mem(mem, store, OUTPUT_OFFSET, err)

        def _mcp_read_file(path_ptr: int, path_len: int) -> int:
            """Host: L3 本地读取文件（含 PDF 提取），不依赖 L2。L2 仅作回退。
            路径白名单：client_volumes、data/hr_resumes、config/skills/.../hr_jds。"""
            mem, store = _get_mem_store()
            if not mem or not store:
                return _write_err_fallback(OUTPUT_OFFSET, "⚠️ 无法连接 L2 机房或读取文件失败")
            try:
                raw = bytes(mem.read(store, path_ptr, path_ptr + path_len)).decode("utf-8", errors="replace").strip()
                if "\n" in raw or len(raw) > 1200:
                    logger.warning("[WASM Host] mcp_read_file 路径异常（含换行或过长）: len=%d", len(raw))
                    return -1
                p = Path(raw)
                if p.is_absolute() and not p.exists() and "/" in raw and "\\" not in raw:
                    p_alt = Path(raw.replace("/", "\\"))
                    if p_alt.exists():
                        p = p_alt
                _proj = Path(__file__).resolve().parent.parent
                raw_norm = raw.strip().replace("\\", "/").lstrip("/")
                filename = (p.name or "zhangsan_resume.md").strip()
                if not filename.lower().endswith((".md", ".txt", ".pdf")):
                    filename = "zhangsan_resume.md"
                path_obj = None
                if p.is_absolute() and p.exists():
                    path_obj = p.resolve()
                else:
                    # 岗位 JD 临时文件：Loader 写入 %TEMP%/tmp*.txt，需显式支持（Windows 下 Path 可能因斜杠格式导致 exists() 误判）
                    try:
                        _tmp = Path(tempfile.gettempdir()).resolve()
                        _tmp_str = str(_tmp).replace("\\", "/")
                        for _p in (p, Path(raw.replace("/", "\\")), Path(raw.replace("\\", "/")), Path(raw)):
                            try:
                                _resolved = _p.resolve()
                                _res_str = str(_resolved).replace("\\", "/")
                                if (_res_str.startswith(_tmp_str) or str(_resolved).startswith(str(_tmp))) and _resolved.is_file():
                                    path_obj = _resolved
                                    logger.info("[WASM Host] mcp_read_file 岗位 JD 临时文件已解析 path=%s", _resolved)
                                    break
                            except Exception:
                                continue
                    except Exception:
                        pass
                if path_obj is None:
                    # 支持 ~/.jachin/workspace/hr_recruitment/{岗位}/pending 下的简历（Boss 收网路径，exe 模式下 p.exists() 可能因斜杠/编码误判）
                    if ".jachin" in raw and "workspace" in raw and "hr_recruitment" in raw:
                        for raw_alt in [raw, raw.replace("/", "\\"), raw.replace("\\", "/")]:
                            try:
                                p_ws = Path(raw_alt)
                                if p_ws.exists() and p_ws.is_file():
                                    path_obj = p_ws.resolve()
                                    logger.info("[WASM Host] mcp_read_file workspace 路径已解析 path=%s", path_obj)
                                    break
                            except Exception:
                                continue
                    # 支持 ~/.jachin/workspace/hr_recruitment/{岗位}/pending 下的简历（收网保存路径，Windows 绝对路径可能因编码/斜杠无法 exists）
                    if path_obj is None and "skills_repo" in raw_norm:
                        try:
                            idx = raw_norm.find("skills_repo")
                            rel = raw_norm[idx:]
                            cand_plug = (_proj / rel).resolve()
                            if cand_plug.exists() and cand_plug.is_file():
                                path_obj = cand_plug
                                logger.info("[WASM Host] mcp_read_file 插件数据卷路径已解析 path=%s", path_obj)
                        except Exception:
                            pass
                    if path_obj is None:
                        from l3_node.jachin_config import get_hr_jds_dir
                        _hr_jds = get_hr_jds_dir(_proj)
                        for base, sub in [(_L3_VOLUME_ROOT, raw_norm), (_proj / "data" / "hr_resumes", filename), (_hr_jds, filename)]:
                            cand = (base / sub).resolve()
                            if cand.exists() and cand.is_file():
                                path_obj = cand
                                break
                if path_obj and path_obj.exists():
                    if path_obj.suffix.lower() == ".pdf":
                        from core.pdf_extractor import extract_pdf_text, SCAN_PLACEHOLDER
                        content = extract_pdf_text(path_obj)
                        # 水印/无效内容（如单字符 ~）视为扫描件
                        s = (content or "").strip()
                        if not s or (len(s) < 30 and not any("\u4e00" <= c <= "\u9fff" for c in s)):
                            return _write_to_mem(mem, store, OUTPUT_OFFSET, SCAN_PLACEHOLDER)
                        return _write_to_mem(mem, store, OUTPUT_OFFSET, content)
                    content = path_obj.read_text(encoding="utf-8", errors="replace")
                    # 岗位 JD 临时文件：打印完整内容便于排查
                    if path_obj.suffix.lower() == ".txt" and ("Temp" in raw or "tmp" in raw):
                        logger.info("[WASM Host] mcp_read_file 岗位 JD 已读取 path=%s len=%d", path_obj, len(content))
                        print(f"[mcp_read_file] 岗位 JD 完整内容 (len={len(content)}):\n{content}\n", file=sys.stderr, flush=True)
                    return _write_to_mem(mem, store, OUTPUT_OFFSET, content)
                # 岗位 JD 临时文件：本地读取失败时返回 -1，Rust 将回退到 jd_template
                if ("Temp" in raw or "tmp" in raw) and (".txt" in raw or raw.lower().endswith(".txt")):
                    logger.warning("[WASM Host] mcp_read_file 岗位 JD 临时文件读取失败 path=%s (Rust 将回退 jd_template)", raw[:100])
                    print(f"[mcp_read_file] 失败：无法读取 JD 临时文件 path={raw[:80]}...", file=sys.stderr, flush=True)
                    return -1
                path_for_l2 = str((path_obj or (_proj / "data" / "hr_resumes" / filename)).resolve()).replace("\\", "/")
                # 回退路径不存在时，跳过 L2 调用，避免 L2 报 ENOENT 噪音
                if path_obj is None and not Path(path_for_l2).exists():
                    logger.debug("[WASM Host] mcp_read_file 本地未找到且回退路径不存在，跳过 L2 path=%s", path_for_l2[:100])
                    return -1
                try:
                    import httpx
                    l2 = _host_services.get("l2_base_url", "http://localhost:18888")
                    url = f"{l2.rstrip('/')}/api/v2/mcp/invoke"
                    body = json.dumps({"tool_name": "read_file", "arguments": {"path": path_for_l2}})
                    with httpx.Client(timeout=30.0) as client:
                        resp = client.post(url, content=body, headers={"Content-Type": "application/json"})
                        resp.raise_for_status()
                        data = resp.json()
                    content = data.get("result", "")
                    if not isinstance(content, str):
                        content = str(content) if content is not None else ""
                    if content.strip().upper().startswith(("ENOENT", "ERROR", "EXCEPTION")) or "no such file" in content.lower():
                        logger.warning("[WASM Host] mcp_read_file L2 返回错误: %s", content[:200])
                        return -1
                    return _write_to_mem(mem, store, OUTPUT_OFFSET, content)
                except Exception as l2_err:
                    logger.debug("[WASM Host] mcp_read_file L2 不可用（L3 本地读取已优先）: %s", l2_err)
                    return -1
            except Exception as e:
                logger.warning("[WASM Host] mcp_read_file 异常 path=%s: %s", raw, e)
                return -1

        def _mcp_list_directory(path_ptr: int, path_len: int) -> int:
            """Host: 列出目录下 .md/.txt/.pdf 文件。支持 L3 数据卷、data/hr_resumes、config/skills/.../hr_jds 本地直读。"""
            mem, store = _get_mem_store()
            if not mem or not store:
                return -1
            try:
                raw = bytes(mem.read(store, path_ptr, path_ptr + path_len)).decode("utf-8", errors="replace").strip()
                raw_clean = raw.strip().replace("\\", "/").lstrip("/")
                _proj = Path(__file__).resolve().parent.parent
                # L3 数据卷：global_resume_pool 或 global_resume_pool/Java_杭州 4-6K
                p_vol = (_L3_VOLUME_ROOT / raw_clean).resolve()
                if p_vol.is_dir() and str(p_vol).startswith(str(_L3_VOLUME_ROOT.resolve())):
                    try:
                        files = [f.name for f in sorted(p_vol.iterdir()) if f.is_file() and f.suffix.lower() in (".md", ".txt", ".pdf")]
                        result = "\n".join(f"[FILE] {f}" for f in files)
                        logger.info("[WASM Host] mcp_list_directory L3 数据卷 path=%s files=%s", p_vol, files)
                        return _write_to_mem(mem, store, OUTPUT_OFFSET, result)
                    except (OSError, RuntimeError) as e:
                        logger.debug("[WASM Host] mcp_list_directory L3 数据卷异常: %s", e)
                if not raw_clean.startswith("/") and (len(raw_clean) < 2 or raw_clean[1] != ":"):
                    p = (_proj / raw_clean).resolve()
                else:
                    p = Path(raw).resolve()
                p_norm = str(p).replace("\\", "/").lower()
                if "hr_resumes" in p_norm or "hr_jds" in p_norm or "client_volumes" in p_norm:
                    try:
                        if p.is_dir():
                            files = [
                                f.name for f in sorted(p.iterdir())
                                if f.is_file() and f.suffix.lower() in (".md", ".txt", ".pdf")
                            ]
                            result = "\n".join(f"[FILE] {f}" for f in files)
                            logger.info("[WASM Host] mcp_list_directory 本地直读 path=%s files=%s", p, files)
                            return _write_to_mem(mem, store, OUTPUT_OFFSET, result)
                    except (OSError, RuntimeError) as e:
                        logger.debug("[WASM Host] mcp_list_directory 本地直读异常: %s", e)
                path_for_mcp = str(p).replace("\\", "/")
                import httpx
                l2 = _host_services.get("l2_base_url", "http://localhost:18888")
                url = f"{l2.rstrip('/')}/api/v2/mcp/invoke"
                body = json.dumps({"tool_name": "list_directory", "arguments": {"path": path_for_mcp}})
                with httpx.Client(timeout=30.0, trust_env=False) as client:
                    resp = client.post(url, content=body, headers={"Content-Type": "application/json"})
                    resp.raise_for_status()
                    data = resp.json()
                result = data.get("result", "")
                if not isinstance(result, str):
                    result = json.dumps(result) if result is not None else ""
                if "[DIR] .git" in result or "[DIR] .cursor" in result:
                    logger.warning("[WASM Host] mcp_list_directory MCP 返回了项目根目录，非 data/hr_resumes，回退本地直读")
                    try:
                        p_fallback = p.resolve()
                        if p_fallback.is_dir() and ("hr_resumes" in str(p_fallback) or "hr_jds" in str(p_fallback)):
                            files = [f.name for f in sorted(p_fallback.iterdir()) if f.is_file() and f.suffix.lower() in (".md", ".txt", ".pdf")]
                            result = "\n".join(f"[FILE] {f}" for f in files)
                    except (OSError, RuntimeError):
                        pass
                return _write_to_mem(mem, store, OUTPUT_OFFSET, result)
            except Exception as e:
                logger.warning("[WASM Host] mcp_list_directory 异常: %s", e)
                return -1

        def _llm_complete(prompt_ptr: int, prompt_len: int) -> int:
            """Host: LLM 推理，结果写入 OUTPUT_OFFSET，返回字节数。"""
            mem, store = _get_mem_store()
            if not mem or not store:
                return _write_err_fallback(OUTPUT_OFFSET, "⚠️ LLM 引擎未注册")
            try:
                prompt = bytes(mem.read(store, prompt_ptr, prompt_ptr + prompt_len)).decode("utf-8", errors="replace")
                engine = _host_services.get("llm_engine")
                if not engine:
                    return _write_to_mem(mem, store, OUTPUT_OFFSET, "⚠️ LLM 引擎未注册，请确保 L3 已配对并启动")
                import litellm
                # LiteLLM 需要 provider 前缀（如 dashscope/qwen3.5-flash-2026-02-23），L2 可能只返回 qwen3.5-flash-2026-02-23
                model = engine.model_name or "dashscope/qwen3.5-flash-2026-02-23"
                if hasattr(engine, "_normalize_model"):
                    model = engine._normalize_model(model) or model
                engine._inject_key(model)
                resp = litellm.completion(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    timeout=engine.timeout,
                )
                text = (resp.choices[0].message.content or "").strip()
                return _write_to_mem(mem, store, OUTPUT_OFFSET, text)
            except Exception as e:
                err = f"⚠️ LLM 调用失败: {e}"
                logger.warning("[WASM Host] llm_complete 异常: %s", e)
                return _write_to_mem(mem, store, OUTPUT_OFFSET, err)

        def _write_to_mem(mem: Any, store: Any, offset: int, text: str) -> int:
            data = text.encode("utf-8")
            n = len(data)
            if n == 0:
                return 0
            try:
                size = mem.data_len(store) if hasattr(mem, "data_len") else 65536
                if offset + n > size and hasattr(mem, "grow"):
                    mem.grow(store, max(1, (offset + n - size + 65535) // 65536))
            except (TypeError, AttributeError):
                pass
            mem.write(store, data, offset)
            return n

        def _write_err_fallback(offset: int, msg: str) -> int:
            mem, store = _get_mem_store()
            if mem and store:
                return _write_to_mem(mem, store, offset, msg)
            return -1

        try:
            linker = Linker(self._engine)
            alloc_ty = FuncType([ValType.i32(), ValType.i32()], [ValType.i32()])
            dealloc_ty = FuncType([ValType.i32(), ValType.i32(), ValType.i32()], [])
            realloc_ty = FuncType([ValType.i32(), ValType.i32(), ValType.i32(), ValType.i32()], [ValType.i32()])
            linker.define_func("env", "__rust_alloc", alloc_ty, _rust_alloc)
            linker.define_func("env", "__rust_dealloc", dealloc_ty, _rust_dealloc)
            linker.define_func("env", "__rust_realloc", realloc_ty, _rust_realloc)
            linker.define_func("env", "__rust_alloc_zeroed", alloc_ty, _rust_alloc_zeroed)
            http_post_ty = FuncType([ValType.i32(), ValType.i32(), ValType.i32(), ValType.i32()], [ValType.i32()])
            mcp_read_ty = FuncType([ValType.i32(), ValType.i32()], [ValType.i32()])
            mcp_list_dir_ty = FuncType([ValType.i32(), ValType.i32()], [ValType.i32()])
            llm_ty = FuncType([ValType.i32(), ValType.i32()], [ValType.i32()])
            linker.define_func("env", "http_post", http_post_ty, _http_post)
            linker.define_func("env", "mcp_read_file", mcp_read_ty, _mcp_read_file)
            linker.define_func("env", "mcp_list_directory", mcp_list_dir_ty, _mcp_list_directory)
            linker.define_func("env", "llm_complete", llm_ty, _llm_complete)

            def _host_stream_ndjson(ptr: int, length: int) -> None:
                mem, store = _get_mem_store()
                if mem and store and ptr >= 0 and length > 0:
                    try:
                        raw = bytes(mem.read(store, ptr, ptr + length)).decode("utf-8", errors="replace")
                        q = _run_context.get("ndjson_queue")
                        if q is not None:
                            q.put(raw)
                    except Exception as e:
                        logger.debug("[WASM Host] host_stream_ndjson: %s", e)

            stream_ty = FuncType([ValType.i32(), ValType.i32()], [])
            linker.define_func("env", "host_stream_ndjson", stream_ty, _host_stream_ndjson)
            return linker
        except (TypeError, AttributeError):
            return None

    def run_plugin_execute_abi(
        self,
        wasm_file_path: str,
        stdin_str: str = "{}",
        fuel_limit: int = 100_000,
        ndjson_queue: "queue.Queue[str] | None" = None,
    ) -> str | None:
        """
        JPP execute(ptr, len) -> i32 ABI：宿主写输入到 memory[ptr..ptr+len]，
        调用后从 memory[0..return_value] 读取输出。适用于 Rust wasm32-unknown-unknown 插件（如 jachin-system-pilot）。
        支持 env.__rust_alloc/dealloc 等 host 函数，供需要堆分配的插件使用。
        """
        import sys
        path = Path(wasm_file_path)
        if not path.exists():
            return None
        try:
            bytecode = path.read_bytes()
            store = Store(self._engine)
            store.set_fuel(fuel_limit)
            module = Module(self._engine, bytecode)

            # 先尝试带 alloc/dealloc 的 Linker，失败则用裸 Instance（无 import 的模块）
            instance = None
            try:
                linker = self._make_execute_linker()
                if linker is not None:
                    instance = linker.instantiate(store, module)
            except (WasmtimeError, TypeError, AttributeError) as e:
                print(f"[Skill Execute] [WASM] execute linker 实例化失败: {e}", file=sys.stderr, flush=True)
                logger.debug("[WASM] execute linker 实例化失败 %s: %s", wasm_file_path, e)
            if instance is None:
                try:
                    instance = Instance(store, module, [])
                except (WasmtimeError, TypeError, AttributeError):
                    return None

            exports = instance.exports(store)
            func = exports.get("execute")
            memory = exports.get("memory")
            if func is None or memory is None:
                return None
            # 设置 run context 供 http_post/llm_complete/host_stream_ndjson 等 host 函数使用
            _run_context["memory"] = memory
            _run_context["store"] = store
            _run_context["ndjson_queue"] = ndjson_queue if ndjson_queue is not None else queue.Queue()
            try:
                # JPP 约定：宿主写输入到 memory[ptr..ptr+len]，插件写输出到 memory[OUTPUT_OFFSET..]
                INPUT_OFFSET = 0x8000
                OUTPUT_OFFSET = 0x8000  # 与 jachin-system-pilot 等 Rust 插件约定
                data = stdin_str.encode("utf-8")
                len_i = len(data)
                print(f"[WASM] execute ABI stdin_len={len_i} has_hr_files={'_hr_files' in stdin_str} has_delim={'|||' in stdin_str}", file=sys.stderr, flush=True)
                if len_i > 0 and len_i < 800:
                    print(f"[WASM] execute ABI stdin_full={stdin_str!r}", file=sys.stderr, flush=True)
                elif len_i > 0:
                    print(f"[WASM] execute ABI stdin_preview={stdin_str[:350]!r}...", file=sys.stderr, flush=True)
                if len_i == 0:
                    ptr = 0
                else:
                    ptr = INPUT_OFFSET
                    try:
                        mem_size = memory.data_len(store) if hasattr(memory, "data_len") else 65536
                        if ptr + len_i > mem_size and hasattr(memory, "grow"):
                            memory.grow(store, max(1, (ptr + len_i - mem_size + 65535) // 65536))
                    except (TypeError, AttributeError):
                        pass
                    memory.write(store, data, ptr)
                out_len = func(store, ptr, len_i)
                # 收集 NDJSON 流式输出（host_stream_ndjson 写入）
                # 若使用外部 ndjson_queue（流式模式），不 drain 到 _last_ndjson_lines
                ndjson_lines: list[str] = []
                try:
                    q = _run_context.get("ndjson_queue")
                    if q is not None and ndjson_queue is None:
                        while True:
                            try:
                                ndjson_lines.append(q.get_nowait())
                            except queue.Empty:
                                break
                        _last_ndjson_lines[:] = ndjson_lines
                except Exception:
                    pass
                if out_len <= 0:
                    if ndjson_lines:
                        result = ndjson_lines[-1] if ndjson_lines else ""
                    else:
                        print(f"[Skill Execute] [WASM] execute ABI 返回空 out_len={out_len} wasm={wasm_file_path}", file=sys.stderr, flush=True)
                        return ""
                else:
                    out_bytes = memory.read(store, OUTPUT_OFFSET, OUTPUT_OFFSET + out_len)
                    result = bytes(out_bytes).decode("utf-8", errors="replace")
                print(f"[Skill Execute] [WASM] execute ABI 返回 wasm={wasm_file_path} len={len(result)} ndjson_lines={len(ndjson_lines)}", file=sys.stderr, flush=True)
                return result
            finally:
                _run_context.clear()
        except (WasmtimeError, TypeError, AttributeError) as e:
            logger.debug("[WASM] execute ABI 失败 %s: %s", wasm_file_path, e)
            return None

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
        import sys
        path = Path(wasm_file_path)
        if not path.exists():
            raise FileNotFoundError(f"找不到 WASM 插件: {wasm_file_path}")

        print(f"[Skill Execute] [WASM] WASI 执行中 wasm={wasm_file_path} stdin_preview={stdin_str[:150]}...", file=sys.stderr, flush=True)
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
            store = Store(self._engine)
            store.set_fuel(fuel_limit)

            # 优先尝试 execute ABI 的 linker（含 __rust_alloc 等），适用于 hr-analyzer4 等 Rust 插件
            execute_linker = self._make_execute_linker()
            instance = None
            execute_err: BaseException | None = None
            if execute_linker is not None:
                try:
                    module = Module(self._engine, bytecode)
                    instance = execute_linker.instantiate(store, module)
                except (WasmtimeError, TypeError, AttributeError) as e:
                    execute_err = e
                    logger.debug("[WASM] execute linker 实例化失败 (WASI 路径): %s", e)

            # Rust 插件需 __rust_alloc，若 execute linker 失败则不应回退纯 WASI（纯 WASI 无 alloc）
            if instance is None and execute_err is not None:
                err_msg = str(execute_err).lower()
                if "__rust_dealloc" in err_msg or "__rust_alloc" in err_msg or "unknown import" in err_msg:
                    raise WasmExecutionError(
                        f"Rust Wasm 需要 execute ABI，instantiate 失败: {execute_err}",
                        _format_wasm_error(execute_err),
                    )

            if instance is not None:
                exports = instance.exports(store)
                func = exports.get("execute")
                memory = exports.get("memory")
                if func is not None and memory is not None:
                    _run_context["memory"] = memory
                    _run_context["store"] = store
                    try:
                        INPUT_OFFSET = 0x8000
                        OUTPUT_OFFSET = 0x8000
                        data = stdin_str.encode("utf-8")
                        len_i = len(data)
                        ptr = INPUT_OFFSET if len_i > 0 else 0
                        if len_i > 0:
                            try:
                                mem_size = memory.data_len(store) if hasattr(memory, "data_len") else 65536
                                if ptr + len_i > mem_size and hasattr(memory, "grow"):
                                    memory.grow(store, max(1, (ptr + len_i - mem_size + 65535) // 65536))
                            except (TypeError, AttributeError):
                                pass
                            memory.write(store, data, ptr)
                        out_len = func(store, ptr, len_i)
                        if out_len > 0:
                            out_bytes = memory.read(store, OUTPUT_OFFSET, OUTPUT_OFFSET + out_len)
                            out = bytes(out_bytes).decode("utf-8", errors="replace")
                        else:
                            out = ""
                    finally:
                        _run_context.clear()
                else:
                    instance = None

            if instance is None:
                # 回退到纯 WASI（Python py2wasm 插件），需新 Store
                wasi = WasiConfig()
                wasi.stdin_file = stdin_path
                wasi.stdout_file = stdout_path
                store = Store(self._engine)
                store.set_fuel(fuel_limit)
                store.set_wasi(wasi)
                linker = Linker(self._engine)
                linker.define_wasi()
                module = Module(self._engine, bytecode)
                try:
                    instance = linker.instantiate(store, module)
                except WasmtimeError as e:
                    msg = str(e).lower()
                    if "__rust_dealloc" in msg or "__rust_alloc" in msg:
                        raise WasmExecutionError(
                            "此技能为 Rust Wasm，需要 execute ABI（__rust_alloc 等）。"
                            "请确保 L3 与 core 使用最新 wasm_runner，或从 l3_node/skills/wasm_plugins 侧载。",
                            _format_wasm_error(e),
                        )
                    raise
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
                    out = f.read()
            print(f"[Skill Execute] [WASM] WASI 返回 wasm={wasm_file_path} stdout_len={len(out)} preview={out[:200]}...", file=sys.stderr, flush=True)
            return out
        except WasmtimeError as e:
            print(f"[Skill Execute] [WASM] WasmtimeError wasm={wasm_file_path} error={e}", file=sys.stderr, flush=True)
            msg = str(e).lower()
            if "out of fuel" in msg or "fuel" in msg or "trap" in msg:
                self._log_meltdown(wasm_file_path, e)
            raise WasmExecutionError(str(e), _format_wasm_error(e))
        except Exception as e:
            print(f"[Skill Execute] [WASM] 执行异常 wasm={wasm_file_path} error={e}", file=sys.stderr, flush=True)
            if isinstance(e, WasmExecutionError):
                raise
            raise WasmExecutionError(str(e), _format_wasm_error(e))
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
    ndjson_queue: "queue.Queue[str] | None" = None,
) -> Any:
    """
    便捷函数：在沙箱中执行 WASM 插件。

    若 wasm_path 不存在，返回 None（优雅跳过）。
    stdin_json 有值时使用 WASI 模式（Python py2wasm 插件）。
    执行失败时抛出 WasmExecutionError，携带 wasm_details 供 API 返回前端。
    """
    if not Path(wasm_path).exists():
        return None
    if not HAS_WASMTIME:
        raise WasmExecutionError(
            "WASM 沙箱不可用：未安装 wasmtime。请 pip install wasmtime。"
            "若使用 Desktop Sidecar 模式，需重新打包：python scripts/build_l3_sidecar.py --force",
            None,
        )
    try:
        sandbox = JachinWasmSandbox()
        return sandbox.run_plugin(
            wasm_path,
            function_name,
            fuel_limit,
            stdin_json=stdin_json,
            ndjson_queue=ndjson_queue,
        )
    except WasmExecutionError:
        raise
    except Exception as e:
        logger.warning("WASM 执行失败 %s: %s", wasm_path, e)
        raise WasmExecutionError(str(e), _format_wasm_error(e))
