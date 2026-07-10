"""
Plugin Manager - JSP Package Manager
插件管理器：负责 .jsp 插件的安装、卸载、验签

职责：
- 加载和验证 .jsp 插件包
- 检查 Tier 1 官方签名
- 管理插件运行时环境（Ray RuntimeEnv）
- 处理 License Key 验证和 DRM 心跳
"""

import inspect
import logging
import zipfile
import yaml
import base64
import json
import importlib.util
import sys
from pathlib import Path
from typing import Optional, Dict, List, Any

import ray

# 使用 common/schemas/manifest.py 中的 Pydantic 模型
from common.schemas.manifest import PluginManifest, PriceInfo, Permission, PriceType, SkillManifest
from common.schemas.skill import DeploymentStrategy

logger = logging.getLogger(__name__)


# 单例：全局 PluginManager 实例
_plugin_manager_instance: Optional["PluginManager"] = None


def get_plugin_manager(plugins_dir: Optional[Path] = None, skills_repo_dir: Optional[Path] = None) -> "PluginManager":
    """获取 PluginManager 单例"""
    global _plugin_manager_instance
    if _plugin_manager_instance is None:
        from core.config import settings
        import os
        base = Path(settings.SKILLS_REPO_PATH)
        if not base.is_absolute() and Path("skills_repo").exists():
            base = Path("skills_repo").resolve()
        plugins = plugins_dir or base.parent / "plugins"
        skills = skills_repo_dir or base
        _plugin_manager_instance = PluginManager(plugins, skills)
    return _plugin_manager_instance


class PluginManager:
    """
    插件管理器 - 技能生命周期唯一入口

    负责：
    - load_skills(): 扫描 skills_repo，为每个技能启动 Ray Actor
    - get_actor(skill_id): 返回 Ray Actor 句柄
    - list_capabilities(filter_tag): 遍历已加载 Actor，返回能力列表
    - .jsp 包的安装/卸载
    """

    def __init__(self, plugins_dir: Path, skills_repo_dir: Path):
        """
        初始化插件管理器

        Args:
            plugins_dir: 插件包存储目录（.jsp 文件）
            skills_repo_dir: 技能存储目录（含 _bundled）
        """
        self.plugins_dir = Path(plugins_dir)
        self.skills_repo_dir = Path(skills_repo_dir)

        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.skills_repo_dir.mkdir(parents=True, exist_ok=True)

        self.installed_plugins: Dict[str, PluginManifest] = {}
        self._actors: Dict[str, Any] = {}  # skill_id -> Ray Actor handle
        self._manifests: Dict[str, Dict[str, Any]] = {}  # skill_id -> manifest dict

    def install_plugin(self, jsp_path: Path, license_key: Optional[str] = None) -> bool:
        """
        安装插件

        Args:
            jsp_path: .jsp 文件路径
            license_key: License Key（如果插件需要）

        Returns:
            是否安装成功
        """
        try:
            # 1. 验证 .jsp 包格式
            if not jsp_path.suffix == ".jsp":
                logger.error(f"Invalid plugin format: {jsp_path}")
                return False

            # 2. 解压并读取 manifest.yaml
            with zipfile.ZipFile(jsp_path, 'r') as zip_ref:
                # 验证签名
                if not self._verify_signature(zip_ref):
                    logger.error("Plugin signature verification failed")
                    return False

                # 读取 manifest (v3.2 格式)
                manifest_data = yaml.safe_load(zip_ref.read("manifest.yaml"))

                # 使用 Pydantic 模型验证和解析
                # 将 YAML 数据转换为字典，然后创建 PluginManifest
                manifest_dict = {
                    "id": manifest_data.get("id", manifest_data.get("name", "unknown")),
                    "version": manifest_data.get("version", "1.0.0"),
                    "name": manifest_data.get("name", "Unknown Plugin"),
                    "description": manifest_data.get("description"),
                    "author": manifest_data.get("author"),
                    "author_email": manifest_data.get("author_email"),
                }

                # 处理价格信息
                price_data = manifest_data.get("price", {})
                if isinstance(price_data, dict):
                    manifest_dict["price"] = {
                        "amount": price_data.get("amount", 0.0),
                        "currency": price_data.get("currency", "USD"),
                        "type": price_data.get("type", "free")
                    }
                else:
                    manifest_dict["price"] = {"amount": 0.0, "currency": "USD", "type": "free"}

                # 处理权限列表
                permissions_data = manifest_data.get("permissions", [])
                manifest_dict["permissions"] = [
                    {"scope": p.get("scope") if isinstance(p, dict) else p}
                    for p in permissions_data
                ]

                # 处理运行时配置
                runtime_data = manifest_data.get("runtime", {})
                manifest_dict["runtime"] = {
                    "type": runtime_data.get("type", "ray"),
                    "python_version": runtime_data.get("python_version", "3.10"),
                    "resources": runtime_data.get("resources", {})
                }

                # 其他字段
                manifest_dict["developer_signature"] = manifest_data.get("developer_signature")
                manifest_dict["requirements"] = manifest_data.get("requirements", [])

                # 创建 PluginManifest 对象（Pydantic 会自动验证）
                manifest = PluginManifest(**manifest_dict)

                # 3. 验证 License（如果需要）
                if manifest.price.type != PriceType.FREE:
                    if not license_key:
                        logger.error("License key required for paid plugin")
                        return False
                    if not self._verify_license(manifest.id, license_key):
                        logger.error("Invalid license key")
                        return False
                    # 更新 License Key（Pydantic 模型需要重新创建）
                    manifest_dict = manifest.dict()
                    manifest_dict["license_key"] = license_key
                    manifest = PluginManifest(**manifest_dict)

                # 4. 解压到 skills_repo
                plugin_dir = self.skills_repo_dir / manifest.id
                zip_ref.extractall(plugin_dir)

                # 5. 注册插件
                self.installed_plugins[manifest.id] = manifest

                logger.info(f"Plugin '{manifest.name}' (id: {manifest.id}) installed successfully")
                return True

        except Exception as e:
            logger.error(f"Failed to install plugin: {e}", exc_info=True)
            return False

    def uninstall_plugin(self, plugin_id: str) -> bool:
        """
        卸载插件

        Args:
            plugin_id: 插件 ID（如 "com.developer.deep-research"）

        Returns:
            是否卸载成功
        """
        if plugin_id not in self.installed_plugins:
            logger.warning(f"Plugin '{plugin_id}' not installed")
            return False

        try:
            # 删除插件目录
            plugin_dir = self.skills_repo_dir / plugin_id
            if plugin_dir.exists():
                import shutil
                shutil.rmtree(plugin_dir)

            # 从注册表中移除
            del self.installed_plugins[plugin_id]

            logger.info(f"Plugin '{plugin_id}' uninstalled successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to uninstall plugin: {e}")
            return False

    def _verify_signature(self, zip_ref: zipfile.ZipFile) -> bool:
        """
        验证插件签名

        Args:
            zip_ref: ZIP 文件引用

        Returns:
            签名是否有效
        """
        try:
            # 读取 manifest.yaml
            manifest_data = zip_ref.read("manifest.yaml")
            developer_signature = yaml.safe_load(manifest_data).get("developer_signature")

            if not developer_signature:
                logger.warning("No developer signature found in manifest")
                # 开发环境允许无签名，生产环境应拒绝
                return True  # TODO: 生产环境应返回 False

            # 读取需要签名的文件
            files_to_verify = []
            for file_name in zip_ref.namelist():
                if file_name in ["manifest.yaml", "main.py", "requirements.txt"]:
                    files_to_verify.append((file_name, zip_ref.read(file_name)))

            # TODO: 实现签名验证逻辑
            # - 解码 base64 签名
            # - 使用 Tier 1 Market 的公钥验证
            # - 验证签名是否匹配文件内容

            # 临时实现：检查签名格式
            try:
                base64.b64decode(developer_signature)
                logger.info("Developer signature format valid")
                return True
            except Exception as e:
                logger.error(f"Invalid signature format: {e}")
                return False

        except KeyError as e:
            logger.error(f"Required file not found in package: {e}")
            return False
        except Exception as e:
            logger.error(f"Signature verification error: {e}")
            return False

    def _verify_license(self, plugin_id: str, license_key: str) -> bool:
        """
        验证 License Key

        Args:
            plugin_id: 插件 ID
            license_key: License Key

        Returns:
            License 是否有效
        """
        # TODO: 实现 License 验证逻辑
        # - 向 Tier 1 Market API 验证
        # - 检查 License 是否过期
        # - 检查 License 是否被撤销
        # - 检查 License 是否匹配插件 ID
        logger.info(f"Verifying license for plugin '{plugin_id}'...")

        # 临时实现：基本格式检查
        if not license_key or len(license_key) < 16:
            logger.error("Invalid license key format")
            return False

        return True

    def list_installed_plugins(self) -> List[str]:
        """列出已安装的插件 ID"""
        return list(self.installed_plugins.keys())

    def get_plugin_manifest(self, plugin_id: str) -> Optional[PluginManifest]:
        """
        获取插件清单

        首先从内存缓存中查找，如果不存在，则从 skills_repo_dir 读取 manifest.yaml
        """
        # 先从内存缓存中查找
        if plugin_id in self.installed_plugins:
            return self.installed_plugins[plugin_id]

        # 如果缓存中没有，尝试从文件系统读取（用于测试或手动安装的插件）
        plugin_dir = self.skills_repo_dir / plugin_id
        manifest_file = plugin_dir / "manifest.yaml"

        if manifest_file.exists():
            try:
                manifest_data = yaml.safe_load(manifest_file.read_text(encoding='utf-8'))

                # 转换为 PluginManifest
                manifest_dict = {
                    "id": manifest_data.get("id", manifest_data.get("name", "unknown")),
                    "version": manifest_data.get("version", "1.0.0"),
                    "name": manifest_data.get("name", "Unknown Plugin"),
                    "description": manifest_data.get("description"),
                    "author": manifest_data.get("author"),
                    "author_email": manifest_data.get("author_email"),
                }

                # 处理价格信息
                price_data = manifest_data.get("price", {})
                if isinstance(price_data, dict):
                    manifest_dict["price"] = {
                        "amount": price_data.get("amount", 0.0),
                        "currency": price_data.get("currency", "USD"),
                        "type": price_data.get("type", "free")
                    }
                else:
                    manifest_dict["price"] = {"amount": 0.0, "currency": "USD", "type": "free"}

                # 处理权限列表
                permissions_data = manifest_data.get("permissions", [])
                manifest_dict["permissions"] = [
                    {"scope": p.get("scope") if isinstance(p, dict) else p}
                    for p in permissions_data
                ]

                # 处理运行时配置
                runtime_data = manifest_data.get("runtime", {})
                manifest_dict["runtime"] = {
                    "type": runtime_data.get("type", "ray"),
                    "python_version": runtime_data.get("python_version", "3.10"),
                    "resources": runtime_data.get("resources", {"cpu": 1, "gpu": False})
                }

                # 处理依赖
                manifest_dict["requirements"] = manifest_data.get("requirements", [])

                # 创建 PluginManifest 对象
                manifest = PluginManifest(**manifest_dict)

                # 缓存到内存中
                self.installed_plugins[plugin_id] = manifest

                logger.debug(f"Loaded plugin manifest from file system: {plugin_id}")
                return manifest

            except Exception as e:
                logger.error(f"Failed to load manifest from file system for '{plugin_id}': {e}")
                return None

        return None

    def check_plugin_license(self, plugin_id: str) -> bool:
        """
        检查插件 License 是否有效（DRM 校验，分发前调用）

        优先使用 L1 sync_licenses 的本地缓存；若无则回退到 manifest.license_key 校验。

        Args:
            plugin_id: 插件 ID

        Returns:
            License 是否有效
        """
        manifest = self.get_plugin_manifest(plugin_id)
        if not manifest:
            return False

        # DRM 校验钩子：使用 L1 同步的 License 列表
        try:
            from core.cloud_client.drm import check_skill_license
            return check_skill_license(plugin_id, manifest)
        except ImportError:
            pass

        # 回退：免费插件始终有效
        if manifest.price.type == PriceType.FREE:
            return True
        if not manifest.license_key:
            return False
        return self._verify_license(plugin_id, manifest.license_key)

    def scan_bundled_skills(self) -> List[str]:
        """
        扫描 _bundled/ 目录，发现所有预装技能

        Returns:
            List[str]: 技能ID列表
        """
        bundled_dir = self.skills_repo_dir / "_bundled"
        if not bundled_dir.exists():
            logger.warning(f"Bundled skills directory not found: {bundled_dir}")
            return []

        skill_ids = []
        for skill_dir in bundled_dir.iterdir():
            if not skill_dir.is_dir():
                continue

            manifest_file = skill_dir / "manifest.yaml"
            if manifest_file.exists():
                try:
                    manifest_data = yaml.safe_load(manifest_file.read_text(encoding='utf-8'))
                    skill_id = manifest_data.get("id")
                    if skill_id:
                        skill_ids.append(skill_id)
                        logger.debug(f"Found bundled skill: {skill_id}")
                except Exception as e:
                    logger.warning(f"Failed to read manifest from {manifest_file}: {e}")

        logger.info(f"Scanned {len(skill_ids)} bundled skills")
        return skill_ids

    def _scan_dir_for_skills(self, base: Path) -> List[str]:
        """扫描指定目录下的技能（子目录需含 manifest.yaml）"""
        ids = []
        if not base.exists():
            return ids
        for d in base.iterdir():
            if d.is_dir() and not d.name.startswith("_"):
                mf = d / "manifest.yaml"
                if mf.exists():
                    try:
                        data = yaml.safe_load(mf.read_text(encoding="utf-8"))
                        sid = data.get("id") or data.get("name", d.name)
                        if sid:
                            ids.append(sid)
                    except Exception:
                        pass
        return ids

    def _scan_all_skills(self) -> List[str]:
        """扫描可自动加载的技能。

        默认只加载 _bundled 核心技能。业务/扩展技能必须通过 L1/L2 安装进入
        用户缓存；开发态如需直接扫描仓库 skills_repo，可显式打开
        JACHIN_DEV_LOAD_REPO_CAPABILITY_PACKAGES 或
        JACHIN_DEV_LOAD_BUSINESS_CAPABILITY_PACKAGES。
        """
        from core.capability_pack_policy import (
            is_core_package_id,
            should_scan_repo_skill_roots,
        )

        ids = set()
        for sid in self.scan_bundled_skills():
            if is_core_package_id(sid):
                ids.add(sid)
        if not should_scan_repo_skill_roots():
            return list(ids)
        for sub in ("drivers", "apps"):
            for sid in self._scan_dir_for_skills(self.skills_repo_dir / sub):
                ids.add(sid)
        root = self.skills_repo_dir
        if root.exists():
            for d in root.iterdir():
                if d.is_dir() and not d.name.startswith("_") and d.name not in ("drivers", "apps"):
                    mf = d / "manifest.yaml"
                    if mf.exists():
                        try:
                            data = yaml.safe_load(mf.read_text(encoding="utf-8"))
                            sid = data.get("id") or data.get("name", d.name)
                            ids.add(sid)
                        except Exception:
                            pass
        return list(ids)

    def load_skills(self) -> int:
        """
        扫描 skills_repo，为每个合法技能启动一个 Ray Actor。
        Returns:
            int: 成功加载的技能数量
        """
        skill_ids = self._scan_all_skills()
        loaded = 0
        for skill_id in skill_ids:
            try:
                actor = self._load_skill(skill_id)
                if actor:
                    self._actors[skill_id] = actor
                    loaded += 1
                    logger.info(f"Loaded skill actor: {skill_id}")
            except Exception as e:
                logger.warning(f"Failed to load skill {skill_id}: {e}", exc_info=True)
        logger.info(f"PluginManager: loaded {loaded}/{len(skill_ids)} skills")
        return loaded

    def get_actor(self, skill_id: str) -> Optional[Any]:
        """返回指定技能的 Ray Actor 句柄"""
        return self._actors.get(skill_id)

    async def get_skill(self, skill_id: str) -> Optional[Dict[str, Any]]:
        """获取技能 manifest（兼容 SkillRegistry.get_skill）"""
        return self._manifests.get(skill_id)

    async def load_skill(self, skill_id: str) -> bool:
        """异步加载技能（兼容 SkillRegistry.load_skill），返回是否成功"""
        actor = self._load_skill(skill_id)
        if actor:
            self._actors[skill_id] = actor
            return True
        return False

    def unload_skill(self, skill_id: str) -> bool:
        """从内存卸载技能 Actor（用于 uninstall 时清理）"""
        if skill_id in self._actors:
            del self._actors[skill_id]
        if skill_id in self._manifests:
            del self._manifests[skill_id]
        return True

    async def update_skill_status(self, skill_id: str, status: str) -> bool:
        """更新技能状态（兼容 SkillRegistry，PluginManager 无状态存储，始终返回 True）"""
        return skill_id in self._manifests

    async def reload_skills(self) -> Dict[str, Any]:
        """重新扫描并加载所有技能（兼容 SkillRegistry.reload_skills）"""
        self._actors.clear()
        self._manifests.clear()
        loaded = self.load_skills()
        return {"loaded": loaded, "total": loaded}

    def get_skill_path(self, skill_id: str) -> Optional[Path]:
        """获取技能目录路径（兼容 registry.loader.get_skill_path，v4.0 含 drivers/apps）"""
        for base in [
            self.skills_repo_dir / "_bundled",
            self.skills_repo_dir / "drivers",
            self.skills_repo_dir / "apps",
            self.skills_repo_dir,
        ]:
            p = base / skill_id
            if p.exists() and p.is_dir():
                return p
        return None

    @property
    def loader(self) -> "SkillLoader":
        """返回 SkillLoader 实例（兼容 registry.loader）"""
        from core.runtime.skill_loader import SkillLoader
        return SkillLoader(str(self.skills_repo_dir))

    async def list_skills(self) -> List[Dict[str, Any]]:
        """列出已加载技能（兼容 SkillRegistry API）"""
        result = []
        for skill_id, manifest in self._manifests.items():
            caps = manifest.get("capabilities", [])
            capabilities = [
                {"name": c.get("name") if isinstance(c, dict) else getattr(c, "name", ""), "description": c.get("description") if isinstance(c, dict) else ""}
                for c in caps
            ]
            result.append({
                "skill_id": skill_id,
                "name": manifest.get("name", skill_id),
                "version": manifest.get("version", "1.0.0"),
                "description": manifest.get("description"),
                "status": "installed",
                "capabilities": capabilities,
                "permissions": [{"id": p.get("scope", p) if isinstance(p, dict) else p, "label": p.get("scope", p) if isinstance(p, dict) else str(p)} for p in manifest.get("permissions", [])],
            })
        return result

    async def list_capabilities(self, filter_tag: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        遍历所有已加载 Actor，返回能力列表（供 Brain/Orchestrator 使用）。
        filter_tag: 如 'user.reach'，仅返回 capability 含该 tag 的技能
        """
        result = []
        for skill_id, actor in list(self._actors.items()):
            try:
                manifest_ref = actor.get_manifest.remote()
                manifest = ray.get(manifest_ref)
            except Exception as e:
                logger.warning(f"Failed to get manifest for {skill_id}: {e}")
                manifest = self._manifests.get(skill_id, {})
            caps = manifest.get("capabilities", [])
            for cap in caps:
                cap_name = cap.get("name") if isinstance(cap, dict) else getattr(cap, "name", "")
                cap_tag = cap.get("tag") if isinstance(cap, dict) else getattr(cap, "tag", None)
                if filter_tag:
                    if cap_tag != filter_tag and filter_tag not in str(cap_name):
                        continue
                result.append({
                    "skill_id": skill_id,
                    "capability_name": cap_name,
                    "description": cap.get("description") if isinstance(cap, dict) else getattr(cap, "description", ""),
                    "tag": cap_tag,
                })
        return result

    def _load_skill(self, skill_id: str) -> Optional[Any]:
        """
        加载技能并创建 Ray Actor

        步骤：
        1. 读取 manifest.yaml
        2. 检查权限（调用 permission.py）
        3. 动态加载 main.py 模块
        4. 使用 ray.remote 创建 Actor 实例
        5. 返回 Actor 句柄

        Args:
            skill_id: 技能ID（如 "com.jachin.sys-monitor"）

        Returns:
            Ray Actor Handle，如果加载失败则返回 None
        """
        try:
            # 1. 查找技能目录（v4.0: _bundled, drivers, apps, root）
            skill_dir = None
            for base in ("_bundled", "drivers", "apps", "."):
                cand = self.skills_repo_dir / base / skill_id if base != "." else self.skills_repo_dir / skill_id
                if cand.exists() and (cand / "manifest.yaml").exists():
                    skill_dir = cand
                    break
            if skill_dir is None:
                logger.error(f"Skill directory not found: {skill_id}")
                return None

            # 2. 读取 manifest.yaml
            manifest_file = skill_dir / "manifest.yaml"
            manifest_data = yaml.safe_load(manifest_file.read_text(encoding='utf-8'))

            # 转换为 SkillManifest（含 deployment_strategy，默认 cached）
            _ds_raw = manifest_data.get("deployment_strategy", "cached")
            try:
                _ds = DeploymentStrategy(_ds_raw) if isinstance(_ds_raw, str) else DeploymentStrategy.CACHED
            except ValueError:
                _ds = DeploymentStrategy.CACHED
            skill_manifest = SkillManifest(
                id=manifest_data.get("id", skill_id),
                version=manifest_data.get("version", "1.0.0"),
                name=manifest_data.get("name", skill_id),
                description=manifest_data.get("description"),
                author=manifest_data.get("author"),
                capabilities=manifest_data.get("capabilities", []),
                permissions=[
                    Permission(scope=p.get("scope") if isinstance(p, dict) else p)
                    for p in manifest_data.get("permissions", [])
                ],
                requirements=manifest_data.get("requirements", []),
                runtime=manifest_data.get("runtime", {"type": "ray", "python_version": "3.10"}),
                deployment_strategy=_ds,
            )

            logger.info(f"Loaded manifest for skill: {skill_id}")

            # 3. 检查权限
            from core.system.permission import get_permission_checker
            permission_checker = get_permission_checker()

            raw = skill_manifest.permissions
            # 提取 scope 字符串（Permission 对象不可哈希，不能作为 dict key）
            required_permissions = [
                p.scope if hasattr(p, "scope") else (p.get("scope", "") if isinstance(p, dict) else str(p))
                for p in raw
            ]
            if not permission_checker.validate_permissions(skill_id, required_permissions):
                logger.error(f"Permission check failed for skill: {skill_id}")
                return None

            logger.info(f"Permission check passed for skill: {skill_id}")

            # 4. 动态加载 main.py 模块
            main_file = skill_dir / "main.py"
            if not main_file.exists():
                logger.error(f"main.py not found: {main_file}")
                return None

            # 使用 importlib 动态加载模块
            module_name = f"skill_{skill_id.replace('.', '_')}"
            spec = importlib.util.spec_from_file_location(module_name, main_file)
            if spec is None or spec.loader is None:
                logger.error(f"Failed to create module spec for {skill_id}")
                return None

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            logger.info(f"Loaded module for skill: {skill_id}")

            # 5. 创建 Ray Actor
            # 检查是否有 execute 函数或继承 BaseSkill 的类
            from core.skills.base_skill import BaseSkill as _BaseSkill
            has_execute = hasattr(module, "execute")
            has_skill_class = any(
                inspect.isclass(obj) and issubclass(obj, _BaseSkill) and obj is not _BaseSkill
                for _, obj in inspect.getmembers(module)
            )
            if not has_execute and not has_skill_class:
                logger.error(f"Module {skill_id} needs 'execute' function or class(BaseSkill)")
                return None

            # 创建技能 Actor 包装类
            # 延迟导入避免循环依赖。Ray 不支持继承 actor 类，故 SkillActorWrapper 继承 BaseSkill（非 actor）
            from core.skills.base_skill import BaseSkill

            # 创建动态 Actor 类（继承 BaseSkill，然后应用 @ray.remote）
            # 注意：不序列化函数，而是序列化技能目录路径，在 Actor 内部重新加载模块
            class SkillActorWrapper(BaseSkill):
                """动态创建的技能 Actor 包装类（继承 BaseSkill，manifest 含 id）"""

                def __init__(self, skill_id: str, manifest: Dict[str, Any], skill_dir_path: str):
                    manifest = dict(manifest) if manifest else {}
                    manifest.setdefault("id", skill_id)
                    super().__init__(manifest)
                    self.skill_dir_path = skill_dir_path
                    self._module = None
                    self._execute_func = None
                    self._skill_class = None

                def _load_module(self):
                    """延迟加载模块（在 Actor 内部执行）。支持类继承 BaseSkill 或 execute 函数。"""
                    if self._module is None:
                        import importlib.util
                        import inspect
                        import sys
                        from pathlib import Path

                        main_file = Path(self.skill_dir_path) / "main.py"
                        if not main_file.exists():
                            raise FileNotFoundError(f"main.py not found: {main_file}")

                        module_name = f"skill_{self.skill_id.replace('.', '_')}"
                        spec = importlib.util.spec_from_file_location(module_name, main_file)
                        if spec is None or spec.loader is None:
                            raise ValueError(f"Failed to create module spec for {self.skill_id}")

                        self._module = importlib.util.module_from_spec(spec)
                        sys.modules[module_name] = self._module
                        spec.loader.exec_module(self._module)

                        # 优先查找继承 BaseSkill 的类（Ray 不支持继承 actor，技能应继承 BaseSkill）
                        self._skill_class = None
                        for _, obj in inspect.getmembers(self._module):
                            if inspect.isclass(obj) and issubclass(obj, BaseSkill) and obj is not BaseSkill:
                                self._skill_class = obj
                                break
                        if self._skill_class:
                            self._execute_func = None
                        elif hasattr(self._module, "execute"):
                            self._execute_func = self._module.execute
                        else:
                            raise AttributeError(f"Module {self.skill_id} needs class(BaseSkill) or execute()")

                async def execute(
                    self,
                    capability: str,
                    params: Dict[str, Any],
                    context: Optional[Dict[str, Any]] = None,
                ) -> Dict[str, Any]:
                    """执行技能能力（支持类或 execute 函数），v4.0 传入 SecurityContext"""
                    try:
                        if self._module is None:
                            self._load_module()
                        if self._skill_class:
                            instance = self._skill_class(self.manifest)
                            result = await instance.execute(capability, params, context)
                        else:
                            result = await self._execute_func(capability, params)
                        if isinstance(result, dict) and "success" in result:
                            return result
                        return {"success": True, "result": result}
                    except Exception as e:
                        logger.error(f"Error executing capability '{capability}': {e}", exc_info=True)
                        return {"success": False, "error": str(e)}

            # 应用 @ray.remote 装饰器创建 Ray Actor 类
            SkillActorClass = ray.remote(SkillActorWrapper)

            manifest_dict = skill_manifest.model_dump() if hasattr(skill_manifest, "model_dump") else dict(skill_manifest)
            self._manifests[skill_id] = manifest_dict

            # 创建 Actor 实例（传递技能目录路径而不是函数）
            actor_handle = SkillActorClass.remote(
                skill_id=skill_id,
                manifest=manifest_dict,
                skill_dir_path=str(skill_dir)
            )

            logger.info(f"Created Ray Actor for skill: {skill_id}")
            return actor_handle

        except Exception as e:
            logger.error(f"Failed to load skill {skill_id}: {e}", exc_info=True)
            return None
