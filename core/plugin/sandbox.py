"""
Plugin Sandbox - 受限执行作用域加载器
战役五：隔离区与代码沙箱 - Step 2

将审查通过的插件加载到隔离的命名空间中运行，防止污染全局变量。
"""

import logging
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# 危险内建函数：默认禁止，除非 manifest 声明 file.read / file.write
DANGEROUS_BUILTINS = frozenset({
    "eval",       # 可执行任意表达式
    "exec",       # 可执行任意代码
    "compile",    # 可编译任意代码
    "open",       # 文件读写，需 file.read/file.write 权限
    # __import__ 由白名单式 _safe_import 替代，见 load_plugin
    "getattr",    # 可绕过限制访问对象
    "setattr",
    "delattr",
    "input",      # 交互输入
    "breakpoint", # 调试断点
    "memoryview", # 底层内存
    "bytes",      # 部分场景可构造恶意数据
    "bytearray",
})

# file.read / file.write 权限下允许恢复的内建
FILE_PERMISSION_BUILTINS = frozenset({"open"})

# 始终允许的安全模块（无权限要求）
SAFE_MODULES = frozenset({
    "json", "datetime", "time", "re", "random", "math",
    "collections", "itertools", "functools", "typing", "decimal",
    "dataclasses", "enum", "copy", "hashlib", "base64", "uuid",
})

# 权限 -> 允许的模块（与 validator 的 PERMISSION_ALLOWED_MODULES 对齐）
PERMISSION_MODULES: dict[str, frozenset[str]] = {
    "internet.access": frozenset({"requests", "aiohttp", "urllib", "urllib.request", "urllib.parse", "urllib.error", "httpx", "http", "http.client"}),
    "file.read": frozenset({"pathlib", "os.path"}),
    "file.write": frozenset({"pathlib", "os.path"}),
    "system.power": frozenset({"os", "subprocess", "sys", "shutil"}),
}


def _make_safe_import(permissions: list):
    """根据 manifest 权限生成白名单式 __import__ 替代函数"""
    allowed = set(SAFE_MODULES)
    perm_strs = []
    for p in permissions or []:
        s = p.get("scope", p.get("name", p)) if isinstance(p, dict) else p
        if isinstance(s, str):
            perm_strs.append(s)
            if s in PERMISSION_MODULES:
                allowed.update(PERMISSION_MODULES[s])

    real_import = __import__

    def _safe_import(name: str, globals_d=None, locals_d=None, fromlist=(), level=0):
        base = name.split(".")[0] if name else ""
        if name not in allowed and base not in allowed:
            if name == "os.path" and ("file.read" in perm_strs or "file.write" in perm_strs):
                import os as _os
                return _os.path
            raise PermissionError(f"沙箱禁止导入: {name}（未声明对应权限）")
        return real_import(name, globals_d or {}, locals_d or {}, fromlist, level)

    return _safe_import


class PluginSandbox:
    """
    沙箱加载器：在受限命名空间中执行插件代码。

    通过限制 __builtins__ 和创建干净的 globals，防止插件：
    - 执行 eval/exec 注入
    - 随意读写文件（除非声明权限）
    - 动态导入任意模块
    """

    def __init__(self, allow_file_ops: bool = False):
        """
        Args:
            allow_file_ops: 是否允许 open()，由 manifest 的 file.read/file.write 决定
        """
        self.allow_file_ops = allow_file_ops

    def load_plugin(self, plugin_dir: str, manifest: dict[str, Any]) -> Callable | type | None:
        """
        动态加载插件到沙箱环境，并提取入口点。

        Args:
            plugin_dir: 已解压的插件目录路径
            manifest: manifest.json 解析后的字典

        Returns:
            setup(agent_context) 函数或 Plugin 类，供主循环调用；
            若未找到标准入口则返回 None
        """
        plugin_path = Path(plugin_dir)
        entry_file = manifest.get("entry", "main.py")
        main_path = plugin_path / entry_file

        if not main_path.exists():
            logger.error(f"入口文件不存在: {main_path}")
            return None

        code_str = main_path.read_text(encoding="utf-8")

        # 解析权限（与 validator 一致）
        perms = manifest.get("permissions", [])
        if perms and isinstance(perms[0], dict):
            perm_list = [p.get("scope", p.get("name", "")) for p in perms if isinstance(p, dict)]
        else:
            perm_list = [p for p in perms if isinstance(p, str)]

        # 创建受限的 __builtins__
        import builtins
        safe_builtins = {}
        for name, obj in vars(builtins).items():
            if name == "__import__":
                safe_builtins[name] = _make_safe_import(manifest.get("permissions", []))
                continue
            if name in DANGEROUS_BUILTINS:
                if name in FILE_PERMISSION_BUILTINS and self.allow_file_ops:
                    safe_builtins[name] = obj
                else:
                    # 替换为占位，调用时抛出（闭包捕获 n）
                    def _make_forbidden(n: str):
                        def _forbidden(*_args, **_kwargs):
                            raise PermissionError(f"沙箱禁止使用 {n}")
                        return _forbidden
                    safe_builtins[name] = _make_forbidden(name)
            else:
                safe_builtins[name] = obj

        restricted_globals: dict[str, Any] = {
            "__builtins__": safe_builtins,
            "__name__": "__plugin__",
            "__file__": str(main_path),
        }

        try:
            code_obj = compile(code_str, str(main_path), "exec")
            exec(code_obj, restricted_globals)
        except Exception as e:
            logger.error(f"插件执行失败: {e}", exc_info=True)
            raise

        # 提取标准入口点
        # 1. setup(agent_context) 函数
        if "setup" in restricted_globals:
            entry = restricted_globals["setup"]
            if callable(entry):
                logger.info("已提取插件入口: setup(agent_context)")
                return entry

        # 2. Plugin 类（约定名称）
        for name in ("Plugin", "Skill", "Agent"):
            if name in restricted_globals:
                entry = restricted_globals[name]
                if isinstance(entry, type):
                    logger.info(f"已提取插件入口: {name} 类")
                    return entry

        # 3. 任意可调用对象
        for name, obj in restricted_globals.items():
            if not name.startswith("_") and callable(obj):
                logger.info(f"已提取插件入口: {name}")
                return obj

        logger.warning("未找到标准入口 (setup/Plugin/Skill)，插件已加载但无导出")
        return None
