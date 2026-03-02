"""
Updater Agent - 部署指令拉取与执行

流程：
1. 用户在 Layer 1 网页点击「部署到我的家庭服务器」
2. 云端生成带临时 Token 的 deploy_commands
3. Layer 2 Updater Agent 轮询 GET /api/v1/deploy/poll?instance_id=xxx
4. 收到指令后，用 Token 下载 .jmp 包
5. 安全流：validator 静态审查 -> sandbox 加载 -> 注册到 PluginManager
"""

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Optional

import httpx

from core.config import settings
from core.plugin.validator import extract_and_validate, SecurityViolationError
from core.plugin.sandbox_engine import SandboxEngine

logger = logging.getLogger(__name__)


def load_sandbox_plugin_from_dir(plugin_dir: Path) -> bool:
    """
    从本地目录加载沙箱插件（开发/测试用）。
    目录需包含 manifest.json 和 main.py。

    Returns:
        是否加载成功
    """
    plugin_dir = Path(plugin_dir)
    manifest_path = plugin_dir / "manifest.json"
    main_path = plugin_dir / "main.py"
    if not manifest_path.exists() or not main_path.exists():
        return False

    try:
        import json
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("load_sandbox_plugin_from_dir: manifest 解析失败 %s", e)
        return False

    try:
        from core.plugin.validator import scan_python_code, SecurityViolationError
        perms = manifest.get("permissions", [])
        perm_strs = [p.get("scope", p) if isinstance(p, dict) else p for p in perms]
        scan_python_code(main_path.read_text(encoding="utf-8"), perm_strs)
    except SecurityViolationError as e:
        logger.warning("load_sandbox_plugin_from_dir: 安全审查未通过 %s", e)
        return False

    perms = manifest.get("permissions", [])
    perm_strs = [p.get("scope", p) if isinstance(p, dict) else p for p in perms]
    allow_file = "file.read" in perm_strs or "file.write" in perm_strs

    sandbox = PluginSandbox(allow_file_ops=allow_file)
    entry_point = sandbox.load_plugin(str(plugin_dir), manifest)
    if not entry_point:
        return False

    from core.system.plugin_manager import get_plugin_manager
    from common.schemas.manifest import PluginManifest, PriceInfo, PriceType, Permission

    pm = get_plugin_manager()
    plugin_id = manifest.get("id", plugin_dir.name)
    if not hasattr(pm, "_sandbox_plugins"):
        pm._sandbox_plugins = {}
    pm._sandbox_plugins[plugin_id] = entry_point
    pm.installed_plugins[plugin_id] = PluginManifest(
        id=plugin_id,
        version=manifest.get("version", "1.0.0"),
        name=manifest.get("name", "Unknown"),
        description=manifest.get("description"),
        author=manifest.get("author"),
        price=PriceInfo(amount=0, type=PriceType.FREE),
        permissions=[Permission(scope=p) for p in perm_strs],
        requirements=manifest.get("requirements", []),
    )
    logger.info("Dev plugin loaded: %s", plugin_id)
    return True


class UpdaterAgent:
    """
    Updater Agent - 端云握手执行器

    轮询 Layer 1 的部署指令，下载并安装插件。
    经 validator 静态审查与 sandbox 加载后，注册到 PluginManager。
    """

    def __init__(
        self,
        instance_id: str,
        base_url: Optional[str] = None,
        poll_interval_sec: int = 30,
    ):
        """
        Args:
            instance_id: Layer 2 实例标识（家庭服务器唯一 ID）
            base_url: Layer 1 Nexus API 基地址
            poll_interval_sec: 轮询间隔（秒）
        """
        self.instance_id = instance_id
        self.base_url = (base_url or getattr(settings, "NEXUS_BASE_URL", "http://localhost:3000")).rstrip("/")
        self.poll_interval = poll_interval_sec
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def poll_deploy_commands(self) -> list[dict]:
        """
        轮询获取待执行的部署指令

        Returns:
            部署指令列表，每项含 temp_token, download_url, plugin_id
        """
        url = f"{self.base_url}/api/v1/deploy/poll"
        params = {"instance_id": self.instance_id}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
                if data.get("success") and "data" in data:
                    return data["data"].get("commands", [])
        except Exception as e:
            logger.warning(f"Poll deploy commands failed: {e}")
        return []

    async def execute_deploy(self, command: dict) -> bool:
        """
        执行单条部署指令：下载 -> 静态审查 -> 沙箱加载 -> 注册。

        Args:
            command: { temp_token, download_url, plugin_id, resource_type }

        Returns:
            是否成功
        """
        temp_token = command.get("temp_token")
        download_url = command.get("download_url")
        plugin_id = command.get("plugin_id")

        if not all([temp_token, download_url, plugin_id]):
            logger.error("Invalid deploy command: missing fields")
            return False

        jmp_path: Optional[Path] = None
        extract_dir: Optional[Path] = None

        try:
            # 1. 下载 .jmp 包到临时文件
            jmp_path = await self._download_package(download_url, temp_token)
            if not jmp_path:
                return False

            # 2. 解压并静态安全审查
            extract_dir = Path(tempfile.mkdtemp(prefix="jmp_extract_"))
            try:
                manifest = extract_and_validate(str(jmp_path), str(extract_dir))
            except SecurityViolationError as e:
                logger.warning(
                    "插件安全审查未通过，已阻断加载: %s (module=%s)",
                    e,
                    getattr(e, "module", "?"),
                )
                return False
            except ValueError as e:
                logger.warning("插件格式校验失败: %s", e)
                return False

            # 3. 沙箱装载（含 resource_mount 分流，统一由 SandboxEngine 处理）
            try:
                entry_point = SandboxEngine.load(str(extract_dir), manifest)
            except NotImplementedError as e:
                logger.warning("沙箱装载跳过（未实现）: %s", e)
                return False

            plugin_id_from_manifest = manifest.get("id", plugin_id)
            perm_strs = [p.get("scope", p) if isinstance(p, dict) else p for p in manifest.get("permissions", [])]

            # 3a. resource_mount：SandboxEngine 已挂载，仅注册 manifest，不复制到 skills_repo
            if getattr(entry_point, "_IS_RESOURCE_MOUNT", False):
                from core.system.plugin_manager import get_plugin_manager
                from common.schemas.manifest import PluginManifest, PriceInfo, PriceType, Permission

                pm = get_plugin_manager()
                pm.installed_plugins[plugin_id_from_manifest] = PluginManifest(
                    id=plugin_id_from_manifest,
                    version=manifest.get("version", "1.0.0"),
                    name=manifest.get("name", "Unknown"),
                    description=manifest.get("description"),
                    author=manifest.get("author"),
                    price=PriceInfo(amount=0, type=PriceType.FREE),
                    permissions=[Permission(scope=p) for p in perm_strs],
                    requirements=manifest.get("requirements", []),
                )
                logger.info(
                    "Resource '%s' 已挂载。core-llm-intent 等 Skill 可通过 JACHIN_VOL_* 环境变量读取。",
                    plugin_id_from_manifest,
                )
                return True

            # 4. 可执行插件：注册到 PluginManager（复制到 skills_repo）
            from core.system.plugin_manager import get_plugin_manager

            pm = get_plugin_manager()
            plugin_id_from_manifest = manifest.get("id", plugin_id)
            target_dir = pm.skills_repo_dir / plugin_id_from_manifest

            if target_dir.exists():
                shutil.rmtree(target_dir)
            shutil.copytree(extract_dir, target_dir)

            # 写入 manifest.yaml 供 PluginManager 兼容
            import yaml
            manifest_yaml = {
                "id": plugin_id_from_manifest,
                "version": manifest.get("version", "1.0.0"),
                "name": manifest.get("name", "Unknown"),
                "description": manifest.get("description"),
                "author": manifest.get("author"),
                "permissions": [{"scope": p} if isinstance(p, str) else p for p in perm_strs],
                "price": {"amount": 0, "currency": "USD", "type": "free"},
                "runtime": {"type": "ray", "python_version": "3.10", "resources": {}},
                "requirements": manifest.get("requirements", []),
            }
            (target_dir / "manifest.yaml").write_text(
                yaml.dump(manifest_yaml, allow_unicode=True),
                encoding="utf-8",
            )

            # 注册到 installed_plugins（简化：仅 manifest）
            from common.schemas.manifest import PluginManifest, PriceInfo, PriceType, Permission
            pm.installed_plugins[plugin_id_from_manifest] = PluginManifest(
                id=plugin_id_from_manifest,
                version=manifest.get("version", "1.0.0"),
                name=manifest.get("name", "Unknown"),
                description=manifest.get("description"),
                author=manifest.get("author"),
                price=PriceInfo(amount=0, type=PriceType.FREE),
                permissions=[Permission(scope=p) for p in perm_strs],
                requirements=manifest.get("requirements", []),
            )

            if entry_point:
                if not hasattr(pm, "_sandbox_plugins"):
                    pm._sandbox_plugins = {}
                pm._sandbox_plugins[plugin_id_from_manifest] = entry_point

            logger.info(
                "Plugin '%s' deployed successfully. 主人，我已经学会新技能了。",
                plugin_id_from_manifest,
            )
            return True

        except Exception as e:
            logger.error("Deploy failed: %s", e, exc_info=True)
            return False

        finally:
            # 5. 清理临时文件
            if jmp_path and jmp_path.exists():
                try:
                    jmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            if extract_dir and extract_dir.exists():
                try:
                    shutil.rmtree(extract_dir)
                except OSError:
                    pass

    async def _download_package(self, url: str, token: str) -> Optional[Path]:
        """下载 .jmp 包到临时文件（支持 ipfs://{cid}，CIDv0/Qm 与 CIDv1/bafy 均可）"""
        if url.startswith("ipfs://"):
            import os
            gateway = os.environ.get("IPFS_GATEWAY", "https://ipfs.io/ipfs/").rstrip("/") + "/"
            cid = url.replace("ipfs://", "").strip()
            url = gateway + cid

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                headers = {"Authorization": f"Bearer {token}"} if token else {}
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()

                with tempfile.NamedTemporaryFile(suffix=".jmp", delete=False) as f:
                    f.write(resp.content)
                    return Path(f.name)
        except Exception as e:
            logger.error("Download failed: %s", e)
            return None

    async def run_once(self) -> int:
        """
        执行一次轮询并处理所有待执行指令

        Returns:
            成功执行的指令数量
        """
        commands = await self.poll_deploy_commands()
        count = 0
        for cmd in commands:
            try:
                if await self.execute_deploy(cmd):
                    count += 1
            except Exception as e:
                logger.error("Execute deploy error: %s", e)
        return count

    async def run_loop(self) -> None:
        """后台轮询循环"""
        self._running = True
        logger.info(
            "UpdaterAgent started. instance_id=%s, poll_interval=%ds",
            self.instance_id,
            self.poll_interval,
        )
        while self._running:
            try:
                await self.run_once()
            except Exception as e:
                logger.error("UpdaterAgent loop error: %s", e)
            await asyncio.sleep(self.poll_interval)

    def start(self) -> None:
        """启动后台轮询"""
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self.run_loop())

    def stop(self) -> None:
        """停止轮询"""
        self._running = False
        if self._task:
            self._task.cancel()
