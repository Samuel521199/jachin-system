"""
L3 最早阶段调试日志 - 仅用 stdlib，不依赖 dotenv

每次 exe 启动时清空日志，便于排查打包问题。
日志路径（按优先级）:
  - JACHIN_LOG_DIR/l3_debug.log（便携包内 logs/ 子目录）
  - PyInstaller 时 cwd/logs/l3_debug.log 或 cwd/l3_debug.log
  - ~/.jachin/l3_debug.log
  - cwd/l3_debug.log
  - TEMP/l3_debug.log

详细诊断：
  - 设置环境变量 ``JACHIN_L3_DEBUG=1`` 或 ``L3_VERBOSE_LOG=1``：根日志级别 DEBUG，
    并抬高 ``l3_node.*``、``l3_node.ws_server``、``l3_node.llm_client`` 等输出；
    仍抑制 ``httpcore``/``websockets`` 等第三方刷屏。

异步落盘（默认开启）：
  - ``JACHIN_L3_LOG_ASYNC=0``：禁用 QueueListener，改回同步 ``FileHandler``（极端排障）。
  - ``JACHIN_L3_LOG_ASYNC_BATCH_CHARS`` / ``JACHIN_L3_LOG_ASYNC_FLUSH_SEC``：批量写阈值与时间窗。
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_LOG_PATH: str | None = None
_FILE_HANDLER: logging.FileHandler | None = None
_WS_HANDSHAKE_NOISE_FILTER_INSTALLED: bool = False


class _UTCFormatter(logging.Formatter):
    """与 __main__ 一致：文件与控制台均可用 UTC 时间戳。"""

    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _env_truthy(name: str) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def is_l3_verbose() -> bool:
    """是否开启 L3 超详细诊断（WS/LLM 流式逐条等）。"""
    return _env_truthy("JACHIN_L3_DEBUG") or _env_truthy("L3_VERBOSE_LOG")


class _WebsocketHandshakeNoiseFilter(logging.Filter):
    """
    抑制 websockets 库在「握手未完成即断开」时打的 ERROR + 堆栈（常见于端口探测、HTTP 误连、
    浏览器预检、客户端快速刷新）。与业务无关，避免污染 l3_debug.log。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if is_l3_verbose():
            return True
        name = str(record.name or "")
        if not name.startswith("websockets"):
            return True
        msg = (record.getMessage() or "").lower()
        if "opening handshake failed" in msg:
            return False
        if "connectionclosederror" in msg.replace(" ", "") and "no close frame" in msg:
            return False
        return True


def install_websocket_handshake_noise_filters() -> None:
    """幂等：为 websockets 相关 logger 挂载 Filter，并压低非致命噪声级别依赖。"""
    global _WS_HANDSHAKE_NOISE_FILTER_INSTALLED
    if _WS_HANDSHAKE_NOISE_FILTER_INSTALLED:
        return
    _WS_HANDSHAKE_NOISE_FILTER_INSTALLED = True
    flt = _WebsocketHandshakeNoiseFilter()
    for ln in ("websockets", "websockets.server", "websockets.asyncio.server", "websockets.client"):
        logging.getLogger(ln).addFilter(flt)


def _resolve_log_path() -> str:
    """解析日志路径。JACHIN_LOG_DIR 优先（便携包 logs/），否则 PyInstaller 时 cwd"""
    candidates = []
    log_dir = os.environ.get("JACHIN_LOG_DIR")
    if log_dir:
        p = Path(log_dir) / "l3_debug.log"
        candidates.append(p)
    if getattr(sys, "frozen", False):
        cwd = Path.cwd()
        # 便携包：优先 logs/ 子目录
        candidates.append(cwd / "logs" / "l3_debug.log")
        candidates.append(cwd / "l3_debug.log")
    jachin = Path.home() / ".jachin"
    candidates.append(jachin / "l3_debug.log")
    candidates.append(Path.cwd() / "l3_debug.log")
    candidates.append(Path(os.environ.get("TEMP", os.environ.get("TMP", "/tmp"))) / "l3_debug.log")
    candidates = list(dict.fromkeys(candidates))  # 去重
    for p in candidates:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            return str(p)
        except OSError:
            continue
    return str(candidates[-1])


def setup_early_logging() -> str:
    """
    最早阶段日志初始化。每次启动清空日志。
    返回日志文件路径。
    """
    global _LOG_PATH, _FILE_HANDLER
    _LOG_PATH = _resolve_log_path()

    # 每次启动清空（强制覆盖，确保日志一定更新）
    try:
        with open(_LOG_PATH, "w", encoding="utf-8") as f:
            utc = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            f.write(f"{utc} [L3 DEBUG] === L3 调试日志（每次启动清空）=== START\n")
            f.write(f"{utc} [L3 DEBUG] log_file={_LOG_PATH}\n")
            f.write(f"{utc} [L3 DEBUG] Python {sys.version.split()[0]} | {sys.version.split('|')[1].strip() if '|' in sys.version else ''}\n")
            f.write(f"{utc} [L3 DEBUG] platform={sys.platform}\n")
            f.write(f"{utc} [L3 DEBUG] executable={getattr(sys, 'executable', '')}\n")
            f.write(f"{utc} [L3 DEBUG] cwd={Path.cwd()}\n")
            f.write(f"{utc} [L3 DEBUG] __file__={getattr(sys.modules.get('__main__'), '__file__', '')}\n")
            frozen = getattr(sys, "frozen", False)
            f.write(f"{utc} [L3 DEBUG] PyInstaller frozen={frozen}, _MEIPASS={os.environ.get('_MEIPASS', '')}\n")
            f.write(f"{utc} [L3 DEBUG] sys.path[:3]={sys.path[:3]}\n")
            f.write(f"{utc} [L3 DEBUG] LOG_LEVEL={os.environ.get('LOG_LEVEL', '')}\n")
            f.write(f"{utc} [L3 DEBUG] JACHIN_L3_DEBUG={os.environ.get('JACHIN_L3_DEBUG', '')} L3_VERBOSE_LOG={os.environ.get('L3_VERBOSE_LOG', '')}\n")
            f.write(f"{utc} [L3 DEBUG] JACHIN_APP_ROOT={os.environ.get('JACHIN_APP_ROOT', '')}\n")
            f.write(f"{utc} [L3 DEBUG] L2_BASE_URL={os.environ.get('L2_BASE_URL', '')}\n")
            f.write(f"{utc} [L3 DEBUG] LLM_MODEL={os.environ.get('LLM_MODEL', '')} L3_MODEL={os.environ.get('L3_MODEL', '')}\n")
            f.write(f"{utc} [L3 DEBUG] JACHIN_LOG_DIR={os.environ.get('JACHIN_LOG_DIR', '')}\n")
            f.write(
                f"{utc} [L3 DEBUG] JACHIN_L3_DEEP_LOG={os.environ.get('JACHIN_L3_DEEP_LOG', '(default=on)')} "
                f"(0/false=关闭 jachin.deep 全量轨迹)\n"
            )
    except OSError:
        pass

    # 挂载文件日志：默认 QueueHandler + 后台批量写盘（见 l3_node.async_file_log），避免主线程同步写阻塞
    try:
        _fmt = _UTCFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        use_async = False
        install_async_file_sink = None
        try:
            from l3_node.async_file_log import async_file_log_enabled, install_async_file_sink

            use_async = bool(async_file_log_enabled() and install_async_file_sink is not None)
        except ImportError:
            pass

        if use_async:
            _FILE_HANDLER = install_async_file_sink(_LOG_PATH, fmt=_fmt)
            _FILE_HANDLER.setLevel(logging.DEBUG)
            logging.getLogger().addHandler(_FILE_HANDLER)
            with open(_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(
                    f"{datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')} "
                    f"[L3 DEBUG] Async file log (QueueHandler+BatchedFile); "
                    f"JACHIN_L3_LOG_ASYNC=0 sync FileHandler; set JACHIN_L3_DEBUG=1 for maximum detail\n"
                )
        else:
            _FILE_HANDLER = logging.FileHandler(_LOG_PATH, mode="a", encoding="utf-8")
            _FILE_HANDLER.setLevel(logging.DEBUG)
            _FILE_HANDLER.setFormatter(_fmt)
            logging.getLogger().addHandler(_FILE_HANDLER)
            with open(_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(
                    f"{datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')} "
                    f"[L3 DEBUG] FileHandler attached sync (UTC format); set JACHIN_L3_DEBUG=1 for maximum detail\n"
                )
    except OSError:
        pass

    install_websocket_handshake_noise_filters()

    return _LOG_PATH


def trace(fmt: str, *args) -> None:
    """写入 TRACE 级别日志"""
    if _LOG_PATH:
        try:
            utc = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            try:
                msg = fmt % args if args else fmt
            except (TypeError, ValueError):
                msg = fmt + " " + " ".join(str(a) for a in args)
            with open(_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"{utc} [TRACE] {msg}\n")
        except OSError:
            pass


def get_log_path() -> str | None:
    """返回当前日志路径"""
    return _LOG_PATH


def reattach_file_handler() -> None:
    """basicConfig(force=True) 会清除 handlers，需重新挂载 FileHandler"""
    global _FILE_HANDLER
    if _LOG_PATH and _FILE_HANDLER:
        root = logging.getLogger()
        if _FILE_HANDLER not in root.handlers:
            root.addHandler(_FILE_HANDLER)


def configure_l3_runtime_diagnostics() -> None:
    """
    在 basicConfig + reattach_file_handler 之后调用。

    - 无 JACHIN_L3_DEBUG：保持 LOG_LEVEL / INFO，文件已含完整格式。
    - JACHIN_L3_DEBUG=1：根与子 logger DEBUG，WS/LLM 流式可逐条落盘；抑制 urllib3/httpcore 等刷屏。
    """
    root = logging.getLogger()
    verbose = is_l3_verbose()

    if verbose:
        root.setLevel(logging.DEBUG)
        for name in (
            "l3_node",
            "l3_node.ws_server",
            "l3_node.llm_client",
            "l3_node.agent_core",
            "l3_node.bootstrap",
            "core.mcp_client",
            "core.inventory_scanner",
        ):
            logging.getLogger(name).setLevel(logging.DEBUG)
        # 第三方：避免拖垮日志体积
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.INFO)
        logging.getLogger("websockets").setLevel(logging.WARNING)
        logging.getLogger("websockets.server").setLevel(logging.WARNING)
        logging.getLogger("asyncio").setLevel(logging.WARNING)
        logging.getLogger("LiteLLM").setLevel(logging.INFO)
        logging.getLogger("litellm").setLevel(logging.INFO)
        try:
            trace("configure_l3_runtime_diagnostics: JACHIN_L3_DEBUG/L3_VERBOSE_LOG enabled -> DEBUG for l3_node.*")
        except Exception:
            pass
    else:
        # 非 verbose 也保证 l3_node 子树至少 INFO 进文件
        logging.getLogger("l3_node").setLevel(logging.INFO)
        logging.getLogger("l3_node.ws_server").setLevel(logging.INFO)
        logging.getLogger("l3_node.llm_client").setLevel(logging.INFO)

    install_websocket_handshake_noise_filters()
    suppress_third_party_llm_client_noise()


def suppress_third_party_llm_client_noise() -> None:
    """
    抑制 OpenAI Python SDK / LiteLLM 在 DEBUG 下打印「Request options」全量 json_data（含 tools schema），
    避免同步写 stderr/管道阻塞主线程，及打包后无控制台时管道背压。
    不影响 Jachin 自有 logger（l3_node、jachin.deep）。
    """
    for name in (
        "openai",
        "openai._base_client",
        "openai.resources",
        "openai.resources.chat",
        "openai.resources.completions",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)


def attach_file_handler_to_logger(lg: logging.Logger) -> None:
    """将 l3_debug FileHandler 挂到指定 logger（与 root 共用同一实例，避免重复打开文件）。"""
    global _FILE_HANDLER
    if _LOG_PATH and _FILE_HANDLER and _FILE_HANDLER not in lg.handlers:
        lg.addHandler(_FILE_HANDLER)
