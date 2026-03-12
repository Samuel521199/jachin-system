"""
JMP (Jachin Module Protocol) Schema
JMP 规范的数据模型 - 与 docs/JMP_SPEC.md 对齐

用于 .jmp 包的 manifest.json 校验与构建
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class JMPManifest(BaseModel):
    """
    JMP manifest.json 结构
    与 PluginManifest 兼容，简化用于分发
    """
    id: str = Field(description="插件唯一标识，如 com.jachin.weather")
    version: str = Field(description="语义化版本")
    name: str = Field(description="显示名称")
    entry: str = Field(default="main.py", description="入口文件")
    permissions: List[str] = Field(default_factory=list, description="所需权限")
    description: Optional[str] = None
    author: Optional[str] = None
    capabilities: Optional[List[Dict[str, Any]]] = None
    runtime: Optional[Dict[str, Any]] = None


class JMPPackageContents(BaseModel):
    """JMP 包必需文件清单"""
    manifest_json: bool = True
    main_py: bool = True
    prompt_txt: bool = False
    requirements_txt: bool = False
    signature_sig: bool = False
