"""
技能Manifest解析和验证
Skill Manifest Parser and Validator
"""

import yaml
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
from jsonschema import validate, ValidationError
from core.runtime.schemas import MANIFEST_SCHEMA
from core.config import settings
from core.exceptions import ManifestError

logger = logging.getLogger(__name__)


class SkillManifest:
    """技能Manifest"""

    def __init__(self, data: Dict[str, Any]):
        """
        初始化Manifest

        Args:
            data: Manifest数据字典
        """
        self.data = data
        self.skill_id = self._generate_skill_id()

    def _generate_skill_id(self) -> str:
        """生成技能ID（优先使用 manifest 中的 id 字段）"""
        # 优先使用 manifest 中的 id 字段
        if "id" in self.data:
            return self.data["id"]

        # 如果没有 id 字段，则基于 name 和 version 生成
        name = self.data.get("name", "").lower().replace(" ", "-")
        version = self.data.get("version", "1.0.0")
        return f"{name}-{version}"

    @property
    def name(self) -> str:
        """技能名称"""
        return self.data.get("name", "")

    @property
    def version(self) -> str:
        """技能版本"""
        return self.data.get("version", "1.0.0")

    @property
    def description(self) -> Optional[str]:
        """技能描述"""
        return self.data.get("description")

    @property
    def author(self) -> Optional[str]:
        """作者"""
        return self.data.get("author")

    @property
    def license(self) -> Optional[str]:
        """许可证"""
        return self.data.get("license")

    @property
    def runtime(self) -> Dict[str, Any]:
        """运行时配置"""
        return self.data.get("runtime", {})

    @property
    def runtime_type(self) -> str:
        """运行时类型"""
        return self.runtime.get("type", "native")

    @property
    def capabilities(self) -> List[Dict[str, Any]]:
        """能力列表"""
        return self.data.get("capabilities", [])

    @property
    def resources(self) -> Dict[str, Any]:
        """资源需求"""
        return self.data.get("resources", {})

    @property
    def permissions(self) -> List[Any]:
        """权限列表（每项为字符串或带 scope 的字典）"""
        return self.data.get("permissions", [])

    @property
    def lifecycle(self) -> Dict[str, Any]:
        """生命周期钩子"""
        return self.data.get("lifecycle", {})

    def get_capability(self, capability_name: str) -> Optional[Dict[str, Any]]:
        """
        获取指定能力

        Args:
            capability_name: 能力名称

        Returns:
            Dict: 能力定义，如果不存在则返回None
        """
        for cap in self.capabilities:
            if cap.get("name") == capability_name:
                return cap
        return None

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return self.data.copy()


class ManifestParser:
    """Manifest解析器"""

    @staticmethod
    def load_from_file(manifest_path: str) -> SkillManifest:
        """
        从文件加载Manifest

        Args:
            manifest_path: Manifest文件路径

        Returns:
            SkillManifest: Manifest对象

        Raises:
            ManifestError: 如果加载或验证失败
        """
        path = Path(manifest_path)
        if not path.exists():
            raise ManifestError(f"Manifest file not found: {manifest_path}")

        try:
            with open(path, "r", encoding="utf-8") as f:
                if path.suffix == ".yaml" or path.suffix == ".yml":
                    data = yaml.safe_load(f)
                elif path.suffix == ".json":
                    data = json.load(f)
                else:
                    # 尝试YAML格式
                    data = yaml.safe_load(f)

            # 验证Manifest
            ManifestParser.validate(data)

            return SkillManifest(data)

        except yaml.YAMLError as e:
            raise ManifestError(f"Failed to parse YAML: {e}")
        except json.JSONDecodeError as e:
            raise ManifestError(f"Failed to parse JSON: {e}")
        except Exception as e:
            raise ManifestError(f"Failed to load manifest: {e}")

    @staticmethod
    def load_from_dict(data: Dict[str, Any]) -> SkillManifest:
        """
        从字典加载Manifest

        Args:
            data: Manifest数据字典

        Returns:
            SkillManifest: Manifest对象

        Raises:
            ManifestError: 如果验证失败
        """
        ManifestParser.validate(data)
        return SkillManifest(data)

    @staticmethod
    def validate(data: Dict[str, Any]) -> None:
        """
        验证Manifest是否符合Schema

        Args:
            data: Manifest数据字典

        Raises:
            ManifestError: 如果验证失败
        """
        try:
            validate(instance=data, schema=MANIFEST_SCHEMA)
        except ValidationError as e:
            raise ManifestError(f"Manifest validation failed: {e.message}")
        except Exception as e:
            raise ManifestError(f"Manifest validation error: {e}")
