"""
Plugin Validator - 静态安全审查
战役五：隔离区与代码沙箱 - Step 1

在加载执行未知插件代码之前，进行严格的静态安全审查。
"""

import ast
import json
import logging
import shutil
import zipfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 高危模块黑名单（未声明权限时一律拦截）
BLACKLIST_MODULES = frozenset({
    "os",           # os.system("rm -rf /"), os.popen, os.exec*
    "subprocess",   # subprocess.run, Popen
    "sys",          # sys.modules 污染, sys.exit
    "socket",       # 原始网络访问
    "shutil",       # shutil.rmtree, copy
    "ctypes",       # 底层 C 调用
    "pickle",       # 反序列化攻击
    "marshal",      # 序列化
    "builtins",     # 修改内建
    "importlib",    # 动态加载任意模块
})

# 权限 -> 允许的模块映射
PERMISSION_ALLOWED_MODULES: dict[str, frozenset[str]] = {
    "system.power": frozenset({"os"}),  # 关机/重启等需 os
    "internet.access": frozenset({"requests", "aiohttp", "urllib", "urllib.request", "http", "httpx"}),
    "file.read": frozenset({"pathlib", "os.path"}),
    "file.write": frozenset({"pathlib", "os.path"}),
}


class SecurityViolationError(Exception):
    """插件安全审查未通过"""

    def __init__(self, message: str, module: str | None = None):
        super().__init__(message)
        self.module = module


def extract_and_validate(jmp_filepath: str, extract_dir: str) -> dict[str, Any]:
    """
    解压 JMP 包并进行基础校验与静态安全扫描。

    Args:
        jmp_filepath: .jmp 文件路径（ZIP 格式）
        extract_dir: 解压目标目录

    Returns:
        解析后的 manifest 字典

    Raises:
        SecurityViolationError: 静态扫描发现高危操作
        ValueError: 包格式错误（缺少必需文件）
    """
    jmp_path = Path(jmp_filepath)
    out_dir = Path(extract_dir)

    if not jmp_path.exists():
        raise ValueError(f"JMP 文件不存在: {jmp_path}")

    try:
        with zipfile.ZipFile(jmp_path, "r") as zf:
            namelist = zf.namelist()

            if "manifest.json" not in namelist:
                raise ValueError("JMP 包必须包含 manifest.json")

            if "main.py" not in namelist:
                raise ValueError("JMP 包必须包含 main.py")

            # 解压
            out_dir.mkdir(parents=True, exist_ok=True)
            zf.extractall(out_dir)

        manifest_path = out_dir / "manifest.json"
        main_path = out_dir / "main.py"

        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)

        permissions = manifest.get("permissions", [])
        if permissions and isinstance(permissions[0], dict):
            allowed = [p.get("scope", p) for p in permissions if isinstance(p, dict)]
        else:
            allowed = [p for p in permissions if isinstance(p, str)]

        main_code = main_path.read_text(encoding="utf-8")

        if not scan_python_code(main_code, allowed):
            raise SecurityViolationError(
                "main.py 静态扫描发现未授权的高危模块导入",
                module="(见日志)",
            )

        logger.info(f"插件 {manifest.get('id', 'unknown')} 静态审查通过")
        return manifest

    except (ValueError, SecurityViolationError):
        _cleanup_extract_dir(out_dir)
        raise
    except Exception as e:
        _cleanup_extract_dir(out_dir)
        logger.error(f"解压或校验失败: {e}", exc_info=True)
        raise


def scan_python_code(code_str: str, allowed_permissions: list[str]) -> bool:
    """
    AST 静态安全扫描：检查 main.py 是否导入了黑名单模块且未声明权限。

    Args:
        code_str: main.py 源码
        allowed_permissions: manifest 中声明的权限列表，如 ["internet.access"]

    Returns:
        True 表示通过，False 表示不通过（会抛出 SecurityViolationError）

    Raises:
        SecurityViolationError: 发现未授权的高危导入
    """
    try:
        tree = ast.parse(code_str)
    except SyntaxError as e:
        raise SecurityViolationError(f"main.py 语法错误: {e}") from e

    # 根据权限构建允许的模块集合
    allowed_modules: set[str] = set()
    for perm in allowed_permissions:
        if perm in PERMISSION_ALLOWED_MODULES:
            allowed_modules.update(PERMISSION_ALLOWED_MODULES[perm])

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name.split(".")[0]
                if mod in BLACKLIST_MODULES and mod not in allowed_modules:
                    logger.warning(f"安全违规: 导入了黑名单模块 '{mod}'，未在 permissions 中声明")
                    raise SecurityViolationError(
                        f"禁止导入高危模块 '{mod}'，请在 manifest.json 的 permissions 中声明相应权限",
                        module=mod,
                    )
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            top_mod = mod.split(".")[0]
            if top_mod in BLACKLIST_MODULES and top_mod not in allowed_modules:
                logger.warning(f"安全违规: 从黑名单模块 '{top_mod}' 导入")
                raise SecurityViolationError(
                    f"禁止从高危模块 '{top_mod}' 导入，请在 manifest.json 中声明权限",
                    module=top_mod,
                )

    return True


def _cleanup_extract_dir(path: Path) -> None:
    """静态扫描失败时彻底删除已解压的临时目录"""
    try:
        if path.exists():
            shutil.rmtree(path)
            logger.info(f"已清理临时目录: {path}")
    except OSError as e:
        logger.error(f"清理临时目录失败: {path}, {e}")
