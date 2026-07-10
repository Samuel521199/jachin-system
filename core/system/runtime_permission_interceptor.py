"""
Runtime Permission Interceptor - 运行时权限拦截器
在插件运行时拦截系统调用并检查权限

职责：
- 拦截文件系统调用（open, write, delete）
- 拦截网络调用（requests, urllib）
- 拦截数据库调用（psycopg2, sqlite3）
- 在调用前检查权限，无权限则抛出异常
"""

import logging
import sys
import os
from typing import Optional, Callable, Any
from pathlib import Path

logger = logging.getLogger(__name__)


class RuntimePermissionInterceptor:
    """
    运行时权限拦截器

    通过 Python 的导入钩子（import hook）和猴子补丁（monkey patching）
    拦截系统调用并检查权限。
    """

    def __init__(self, plugin_id: str, permission_checker: Callable[[str], bool]):
        """
        初始化拦截器

        Args:
            plugin_id: 插件 ID
            permission_checker: 权限检查函数，接受权限 scope，返回是否有权限
        """
        self.plugin_id = plugin_id
        self.check_permission = permission_checker
        self._original_functions = {}
        self._interceptors_installed = False

    def install(self):
        """安装拦截器"""
        if self._interceptors_installed:
            logger.warning(f"Interceptors already installed for plugin '{self.plugin_id}'")
            return

        try:
            self._intercept_filesystem()
            self._intercept_network()
            self._intercept_database()
            self._interceptors_installed = True
            logger.info(f"Runtime permission interceptors installed for plugin '{self.plugin_id}'")
        except Exception as e:
            logger.error(f"Failed to install interceptors: {e}", exc_info=True)
            raise

    def uninstall(self):
        """卸载拦截器，恢复原始函数"""
        if not self._interceptors_installed:
            return

        try:
            # 恢复原始函数
            if 'builtins.open' in self._original_functions:
                import builtins
                builtins.open = self._original_functions['builtins.open']

            if 'os.remove' in self._original_functions:
                os.remove = self._original_functions['os.remove']

            if 'os.unlink' in self._original_functions:
                os.unlink = self._original_functions['os.unlink']

            if 'os.rmdir' in self._original_functions:
                os.rmdir = self._original_functions['os.rmdir']

            # 恢复网络库（如果已拦截）
            self._restore_network_modules()

            # 恢复数据库库（如果已拦截）
            self._restore_database_modules()

            self._interceptors_installed = False
            logger.info(f"Runtime permission interceptors uninstalled for plugin '{self.plugin_id}'")
        except Exception as e:
            logger.error(f"Failed to uninstall interceptors: {e}", exc_info=True)

    def _intercept_filesystem(self):
        """拦截文件系统调用"""
        import builtins

        # 保存原始函数
        self._original_functions['builtins.open'] = builtins.open

        # 拦截 open() 函数
        def intercepted_open(file, mode='r', *args, **kwargs):
            # 检查文件操作权限
            if 'r' in mode or 'a' in mode:
                # 读取操作
                if not self.check_permission("file.read"):
                    raise PermissionError(
                        f"Plugin '{self.plugin_id}' does not have 'file.read' permission"
                    )
            elif 'w' in mode or 'x' in mode or 'a' in mode:
                # 写入操作
                if not self.check_permission("file.write"):
                    raise PermissionError(
                        f"Plugin '{self.plugin_id}' does not have 'file.write' permission"
                    )

            # 调用原始函数
            return self._original_functions['builtins.open'](file, mode, *args, **kwargs)

        builtins.open = intercepted_open

        # 拦截 os.remove() 和 os.unlink()
        self._original_functions['os.remove'] = os.remove
        self._original_functions['os.unlink'] = os.unlink

        def intercepted_remove(path):
            if not self.check_permission("file.delete"):
                raise PermissionError(
                    f"Plugin '{self.plugin_id}' does not have 'file.delete' permission"
                )
            return self._original_functions['os.remove'](path)

        os.remove = intercepted_remove
        os.unlink = intercepted_remove

        # 拦截 os.rmdir()
        self._original_functions['os.rmdir'] = os.rmdir

        def intercepted_rmdir(path):
            if not self.check_permission("file.delete"):
                raise PermissionError(
                    f"Plugin '{self.plugin_id}' does not have 'file.delete' permission"
                )
            return self._original_functions['os.rmdir'](path)

        os.rmdir = intercepted_rmdir

        logger.debug(f"File system interceptors installed for plugin '{self.plugin_id}'")

    def _intercept_network(self):
        """拦截网络调用"""
        # 尝试拦截 requests 库
        try:
            import requests
            self._intercept_requests(requests)
        except ImportError:
            logger.debug("requests module not available, skipping interception")

        # 尝试拦截 urllib
        try:
            import urllib.request
            self._intercept_urllib(urllib.request)
        except ImportError:
            logger.debug("urllib.request module not available, skipping interception")

    def _intercept_requests(self, requests_module):
        """拦截 requests 库"""
        # 保存原始函数
        original_get = requests_module.get
        original_post = requests_module.post
        original_request = requests_module.request

        self._original_functions['requests.get'] = original_get
        self._original_functions['requests.post'] = original_post
        self._original_functions['requests.request'] = original_request

        def check_network_permission(url: str):
            """检查网络权限"""
            # 检查是否有网络访问权限
            if not self.check_permission("internet.access"):
                raise PermissionError(
                    f"Plugin '{self.plugin_id}' does not have 'internet.access' permission"
                )

            # 检查 HTTPS-only 限制
            if self.check_permission("internet.https_only"):
                if not url.startswith("https://"):
                    raise PermissionError(
                        f"Plugin '{self.plugin_id}' can only access HTTPS URLs"
                    )

        def intercepted_get(url, **kwargs):
            check_network_permission(url)
            return original_get(url, **kwargs)

        def intercepted_post(url, **kwargs):
            check_network_permission(url)
            return original_post(url, **kwargs)

        def intercepted_request(method, url, **kwargs):
            check_network_permission(url)
            return original_request(method, url, **kwargs)

        requests_module.get = intercepted_get
        requests_module.post = intercepted_post
        requests_module.request = intercepted_request

        logger.debug(f"requests interceptors installed for plugin '{self.plugin_id}'")

    def _intercept_urllib(self, urllib_request_module):
        """拦截 urllib.request"""
        original_urlopen = urllib_request_module.urlopen

        self._original_functions['urllib.request.urlopen'] = original_urlopen

        def intercepted_urlopen(url, *args, **kwargs):
            # 检查网络权限
            if not self.check_permission("internet.access"):
                raise PermissionError(
                    f"Plugin '{self.plugin_id}' does not have 'internet.access' permission"
                )

            # 检查 HTTPS-only 限制
            url_str = str(url)
            if self.check_permission("internet.https_only"):
                if not url_str.startswith("https://"):
                    raise PermissionError(
                        f"Plugin '{self.plugin_id}' can only access HTTPS URLs"
                    )

            return original_urlopen(url, *args, **kwargs)

        urllib_request_module.urlopen = intercepted_urlopen

        logger.debug(f"urllib interceptors installed for plugin '{self.plugin_id}'")

    def _intercept_database(self):
        """拦截数据库调用"""
        # 尝试拦截 psycopg2（PostgreSQL）
        try:
            import psycopg2
            self._intercept_psycopg2(psycopg2)
        except ImportError:
            logger.debug("psycopg2 module not available, skipping interception")

        # 尝试拦截 sqlite3
        try:
            import sqlite3
            self._intercept_sqlite3(sqlite3)
        except ImportError:
            logger.debug("sqlite3 module not available, skipping interception")

    def _intercept_psycopg2(self, psycopg2_module):
        """拦截 psycopg2 库"""
        original_connect = psycopg2_module.connect

        self._original_functions['psycopg2.connect'] = original_connect

        def intercepted_connect(*args, **kwargs):
            # 检查数据库权限
            if not self.check_permission("database.query") and not self.check_permission("database.write"):
                raise PermissionError(
                    f"Plugin '{self.plugin_id}' does not have database access permission"
                )
            return original_connect(*args, **kwargs)

        psycopg2_module.connect = intercepted_connect

        logger.debug(f"psycopg2 interceptors installed for plugin '{self.plugin_id}'")

    def _intercept_sqlite3(self, sqlite3_module):
        """拦截 sqlite3 库"""
        original_connect = sqlite3_module.connect

        self._original_functions['sqlite3.connect'] = original_connect

        def intercepted_connect(database, *args, **kwargs):
            # 检查数据库权限
            if not self.check_permission("database.query") and not self.check_permission("database.write"):
                raise PermissionError(
                    f"Plugin '{self.plugin_id}' does not have database access permission"
                )
            return original_connect(database, *args, **kwargs)

        sqlite3_module.connect = intercepted_connect

        logger.debug(f"sqlite3 interceptors installed for plugin '{self.plugin_id}'")

    def _restore_network_modules(self):
        """恢复网络模块的原始函数"""
        if 'requests.get' in self._original_functions:
            try:
                import requests
                requests.get = self._original_functions['requests.get']
                requests.post = self._original_functions['requests.post']
                requests.request = self._original_functions['requests.request']
            except ImportError:
                pass

        if 'urllib.request.urlopen' in self._original_functions:
            try:
                import urllib.request
                urllib.request.urlopen = self._original_functions['urllib.request.urlopen']
            except ImportError:
                pass

    def _restore_database_modules(self):
        """恢复数据库模块的原始函数"""
        if 'psycopg2.connect' in self._original_functions:
            try:
                import psycopg2
                psycopg2.connect = self._original_functions['psycopg2.connect']
            except ImportError:
                pass

        if 'sqlite3.connect' in self._original_functions:
            try:
                import sqlite3
                sqlite3.connect = self._original_functions['sqlite3.connect']
            except ImportError:
                pass


def install_interceptor_for_plugin(plugin_id: str, permission_checker: Callable[[str], bool]):
    """
    为插件安装权限拦截器（在 Actor 内部调用）

    Args:
        plugin_id: 插件 ID
        permission_checker: 权限检查函数
    """
    interceptor = RuntimePermissionInterceptor(plugin_id, permission_checker)
    interceptor.install()
    return interceptor


def create_permission_interceptor_init_script(plugin_id: str) -> str:
    """
    创建权限拦截器初始化脚本

    这个脚本会在 Ray RuntimeEnv 中执行，在插件 Actor 启动时安装拦截器。

    Args:
        plugin_id: 插件 ID

    Returns:
        Python 脚本内容（字符串）
    """
    script = f"""
# 权限拦截器初始化脚本
# 在插件 Actor 启动时自动执行

import sys
import os
import logging

# 添加项目根目录到 Python 路径（如果不在 Ray RuntimeEnv 中）
project_root = os.environ.get("JACHIN_PROJECT_ROOT")
if project_root and project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    from core.system.runtime_permission_interceptor import RuntimePermissionInterceptor
    from core.system.permission_enforcer import get_permission_enforcer

    # 获取权限执行器
    enforcer = get_permission_enforcer()

    # 创建权限检查函数
    def check_permission(scope: str) -> bool:
        return enforcer.check_permission("{plugin_id}", scope)

    # 创建并安装拦截器
    interceptor = RuntimePermissionInterceptor("{plugin_id}", check_permission)
    interceptor.install()

    logging.info(f"Runtime permission interceptor installed for plugin '{{plugin_id}}'")
except Exception as e:
    logging.error(f"Failed to install runtime permission interceptor: {{e}}", exc_info=True)
    # 不抛出异常，避免阻止插件启动
"""
    return script
