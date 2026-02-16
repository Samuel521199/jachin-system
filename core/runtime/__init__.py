"""
技能运行时模块
Skill Runtime Module
"""

from core.runtime.skill_loader import SkillLoader
from core.runtime.manifest import ManifestParser, SkillManifest, ManifestError
from core.runtime.interfaces import SkillRuntime, Sandbox

# 懒加载：SkillRunner 依赖 ray，sandbox 依赖 docker
def __getattr__(name: str):
    if name == "SkillRunner":
        from core.runtime.skill_runner import SkillRunner
        return SkillRunner
    if name in ("BaseSandbox", "DockerSandbox"):
        from core.runtime.sandbox import BaseSandbox, DockerSandbox
        return BaseSandbox if name == "BaseSandbox" else DockerSandbox
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "SkillRunner",
    "SkillLoader",
    "ManifestParser",
    "SkillManifest",
    "ManifestError",
    "SkillRuntime",
    "Sandbox",
    "BaseSandbox",
    "DockerSandbox",
]
