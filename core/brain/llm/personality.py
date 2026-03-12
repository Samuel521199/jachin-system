"""
Personality Manager - 人格管理器

管理多个AI助手人格配置，支持动态切换人格
"""

import yaml
import os
import logging
from typing import Optional, Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)


class Personality:
    """AI助手人格配置"""
    
    def __init__(
        self,
        name: str,
        description: str,
        system_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 2000
    ):
        self.name = name
        self.description = description
        self.system_prompt = system_prompt
        self.temperature = temperature
        self.max_tokens = max_tokens
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "name": self.name,
            "description": self.description,
            "system_prompt": self.system_prompt,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens
        }


class PersonalityManager:
    """人格管理器"""
    
    def __init__(self, config_path: Optional[str] = None):
        """
        初始化人格管理器
        
        Args:
            config_path: 配置文件路径，默认为 backend/config/personalities.yaml
        """
        if config_path is None:
            # 默认路径：backend/config/personalities.yaml
            backend_dir = Path(__file__).parent.parent.parent
            config_path = backend_dir / "config" / "personalities.yaml"
        
        self.config_path = Path(config_path)
        self.personalities: Dict[str, Personality] = {}
        self.default_personality: str = "default"
        
        self._load_config()
    
    def _load_config(self):
        """加载配置文件"""
        if not self.config_path.exists():
            logger.warning(f"Personality config file not found: {self.config_path}")
            # 创建默认人格
            self._create_default_personality()
            return
        
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
            
            # 加载所有人格
            personalities_config = config.get("personalities", {})
            for key, value in personalities_config.items():
                self.personalities[key] = Personality(
                    name=value.get("name", key),
                    description=value.get("description", ""),
                    system_prompt=value.get("system_prompt", ""),
                    temperature=value.get("temperature", 0.7),
                    max_tokens=value.get("max_tokens", 2000)
                )
            
            # 设置默认人格
            self.default_personality = config.get("default_personality", "default")
            
            logger.info(f"Loaded {len(self.personalities)} personalities from {self.config_path}")
            
        except Exception as e:
            logger.error(f"Failed to load personality config: {e}", exc_info=True)
            self._create_default_personality()
    
    def _create_default_personality(self):
        """创建默认人格"""
        default_prompt = """你是一个名为 Jachin 的分布式 AI 智能体系统的大脑。

你的职责：
1. 理解用户的自然语言指令
2. 通过查询设备注册表，发现可用的设备能力
3. 选择最合适的设备执行任务
4. 协调多个设备完成复杂任务

你的性格特点：
- 友好、专业、乐于助人
- 回答简洁明了，避免冗长
- 主动提供有用的建议
- 在不确定时会询问用户澄清"""
        
        self.personalities["default"] = Personality(
            name="Jachin",
            description="默认AI助手",
            system_prompt=default_prompt,
            temperature=0.7,
            max_tokens=2000
        )
        logger.info("Created default personality")
    
    def get_personality(self, personality_id: Optional[str] = None) -> Personality:
        """
        获取指定的人格
        
        Args:
            personality_id: 人格ID，如果为None则返回默认人格
        
        Returns:
            Personality对象
        """
        if personality_id is None:
            personality_id = self.default_personality
        
        if personality_id not in self.personalities:
            logger.warning(f"Personality '{personality_id}' not found, using default")
            personality_id = self.default_personality
        
        return self.personalities[personality_id]
    
    def list_personalities(self) -> Dict[str, Dict[str, Any]]:
        """
        列出所有人格
        
        Returns:
            人格字典，key为人格ID，value为人格信息
        """
        return {
            key: personality.to_dict()
            for key, personality in self.personalities.items()
        }
    
    def get_system_message(self, personality_id: Optional[str] = None) -> str:
        """
        获取指定人格的系统提示词
        
        Args:
            personality_id: 人格ID
        
        Returns:
            系统提示词字符串
        """
        personality = self.get_personality(personality_id)
        return personality.system_prompt
    
    def get_temperature(self, personality_id: Optional[str] = None) -> float:
        """获取指定人格的temperature参数"""
        personality = self.get_personality(personality_id)
        return personality.temperature
    
    def get_max_tokens(self, personality_id: Optional[str] = None) -> int:
        """获取指定人格的max_tokens参数"""
        personality = self.get_personality(personality_id)
        return personality.max_tokens


# 全局单例
_personality_manager: Optional[PersonalityManager] = None


def get_personality_manager() -> PersonalityManager:
    """获取全局人格管理器单例"""
    global _personality_manager
    if _personality_manager is None:
        _personality_manager = PersonalityManager()
    return _personality_manager
