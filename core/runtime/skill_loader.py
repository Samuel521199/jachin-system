"""
技能加载器
Skill Loader
"""

import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
from core.runtime.manifest import ManifestParser, ManifestError
from core.config import settings

logger = logging.getLogger(__name__)


class SkillLoader:
    """技能加载器"""

    def __init__(self, repo_path: Optional[str] = None):
        """
        初始化技能加载器

        Args:
            repo_path: 技能存储库路径，默认使用settings中的路径
        """
        # 如果路径是相对路径，转换为绝对路径（相对于项目根目录）
        if repo_path:
            self.repo_path = Path(repo_path)
        else:
            repo_path_str = settings.SKILLS_REPO_PATH
            self.repo_path = Path(repo_path_str)

        # 如果是相对路径，尝试从项目根目录解析
        if not self.repo_path.is_absolute():
            # 尝试从当前工作目录或项目根目录解析
            import os
            # 检查是否在项目根目录
            if Path("core").exists() and Path("skills_repo").exists():
                # 在项目根目录
                self.repo_path = Path("skills_repo").resolve()
            else:
                # 尝试从当前文件位置向上查找项目根
                current_file = Path(__file__).resolve()
                project_root = current_file.parent.parent.parent  # core/runtime/skill_loader.py -> project root
                self.repo_path = (project_root / repo_path_str).resolve()

        logger.debug("SkillLoader repo: %s", self.repo_path.absolute())
        self.repo_path.mkdir(parents=True, exist_ok=True)

    def discover_skills(self) -> List[str]:
        """
        发现所有已安装的技能。
        TODO: v6.0 Semantic Vector Router will take over skill matching here.
        已移除遍历本地目录查找 manifest 的旧逻辑，由 vector_router.match_local_skill 接管。
        """
        # TODO: v6.0 Semantic Vector Router will take over skill matching here.
        logger.debug("discover_skills: 已由 Vector Router 接管，返回空列表占位")
        return []

    def load_skill_manifest(self, skill_id: str) -> Optional[Any]:
        """
        加载技能的Manifest（优先检查 _bundled 目录）

        Args:
            skill_id: 技能ID

        Returns:
            SkillManifest: Manifest对象，如果不存在则返回None
        """
        # 先检查 _bundled 目录
        bundled_dir = self.repo_path / "_bundled" / skill_id
        skill_dir = None

        if bundled_dir.exists():
            skill_dir = bundled_dir
        else:
            # 检查普通技能目录
            skill_dir = self.repo_path / skill_id
            if not skill_dir.exists():
                logger.warning(f"Skill directory not found: {skill_id}")
                return None

        # 查找manifest文件
        manifest_files = [
            skill_dir / "manifest.yaml",
            skill_dir / "manifest.yml",
            skill_dir / "manifest.json",
        ]

        for manifest_file in manifest_files:
            if manifest_file.exists():
                try:
                    return ManifestParser.load_from_file(str(manifest_file))
                except ManifestError as e:
                    logger.error(f"Failed to load manifest from {manifest_file}: {e}")
                    return None

        logger.warning(f"Manifest file not found for skill: {skill_id}")
        return None

    def get_skill_path(self, skill_id: str) -> Optional[Path]:
        """
        获取技能目录路径（优先检查 _bundled 目录）

        Args:
            skill_id: 技能ID

        Returns:
            Path: 技能目录路径，如果不存在则返回None
        """
        # 先检查 _bundled 目录
        bundled_dir = self.repo_path / "_bundled" / skill_id
        if bundled_dir.exists() and bundled_dir.is_dir():
            return bundled_dir

        # 检查普通技能目录
        skill_dir = self.repo_path / skill_id
        if skill_dir.exists() and skill_dir.is_dir():
            return skill_dir

        return None

    def install_skill(
        self,
        skill_archive_path: str,
        overwrite: bool = False
    ) -> Optional[str]:
        """
        安装技能（从zip包）

        Args:
            skill_archive_path: 技能zip包路径
            overwrite: 是否覆盖已存在的技能

        Returns:
            str: 技能ID，如果安装失败则返回None
        """
        import zipfile
        import tempfile
        import shutil

        archive_path = Path(skill_archive_path)
        if not archive_path.exists():
            logger.error(f"Skill archive not found: {skill_archive_path}")
            return None

        try:
            # 解压到临时目录
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)

                with zipfile.ZipFile(archive_path, 'r') as zip_ref:
                    zip_ref.extractall(temp_path)

                # 查找manifest文件
                manifest_files = [
                    temp_path / "manifest.yaml",
                    temp_path / "manifest.yml",
                    temp_path / "manifest.json",
                ]

                manifest_path = None
                for mf in manifest_files:
                    if mf.exists():
                        manifest_path = mf
                        break

                if not manifest_path:
                    logger.error("Manifest file not found in skill archive")
                    return None

                # 解析manifest获取skill_id
                manifest = ManifestParser.load_from_file(str(manifest_path))
                skill_id = manifest.skill_id

                # 检查是否已存在
                skill_dir = self.repo_path / skill_id
                if skill_dir.exists():
                    if not overwrite:
                        logger.error(f"Skill {skill_id} already exists")
                        return None
                    # 删除旧版本
                    shutil.rmtree(skill_dir)

                # 移动到技能存储库
                shutil.move(str(temp_path), str(skill_dir))

                logger.info(f"Skill {skill_id} installed successfully")
                return skill_id

        except Exception as e:
            logger.error(f"Failed to install skill: {e}", exc_info=True)
            return None

    def uninstall_skill(self, skill_id: str) -> bool:
        """
        卸载技能

        Args:
            skill_id: 技能ID

        Returns:
            bool: 是否成功卸载
        """
        skill_dir = self.repo_path / skill_id

        if not skill_dir.exists():
            logger.warning(f"Skill directory not found: {skill_dir}")
            return False

        try:
            import shutil
            shutil.rmtree(skill_dir)
            logger.info(f"Skill {skill_id} uninstalled successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to uninstall skill {skill_id}: {e}", exc_info=True)
            return False
