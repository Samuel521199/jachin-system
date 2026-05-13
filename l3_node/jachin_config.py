"""
Jachin 配置根 — 遵循 075-config-root-and-cloud-sync 规范

配置必须写入 ~/.jachin/config/，支持 JACHIN_HOME 环境变量覆盖。
禁止依赖项目根作为配置路径。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

_JACHIN_ROOT = Path(os.environ.get("JACHIN_HOME", str(Path.home() / ".jachin")))


def get_jachin_root() -> Path:
    """Jachin 根目录 (~/.jachin，可由 JACHIN_HOME 覆盖)"""
    return _JACHIN_ROOT


def get_config_root() -> Path:
    """配置根目录 (~/.jachin/config)"""
    return _JACHIN_ROOT / "config"


def get_mcp_config_dir(mcp_id: str) -> Path:
    """MCP 配置目录 (~/.jachin/config/mcps/{mcp_id})"""
    return get_config_root() / "mcps" / mcp_id


def get_skill_config_dir(skill_id: str) -> Path:
    """Skill 配置目录 (~/.jachin/config/skills/{skill_id})"""
    return get_config_root() / "skills" / skill_id


def get_hr_jds_dir(project_root: Path | None = None) -> Path:
    """
    HR JD 模板目录。优先 ~/.jachin/config/skills/com.jachin.hr.analyzer4/hr_jds，
    若不存在则回退到 project_root/config/skills/com.jachin.hr.analyzer4/hr_jds。
    """
    jd_dir = get_skill_config_dir("com.jachin.hr.analyzer4") / "hr_jds"
    if jd_dir.exists():
        return jd_dir
    if project_root:
        fallback = project_root / "config" / "skills" / "com.jachin.hr.analyzer4" / "hr_jds"
        if fallback.exists():
            return fallback
    return jd_dir  # 默认返回 ~/.jachin 路径，调用方负责 mkdir


def _expand_env_placeholder(val: str) -> str:
    """将 ${VAR} 替换为环境变量值，缺失时保留原样"""
    if not isinstance(val, str) or "${" not in val:
        return val
    def repl(m: re.Match[str]) -> str:
        name = m.group(1)
        return os.environ.get(name, m.group(0))
    return re.sub(r"\$\{([^}]+)\}", repl, val)


# 内置默认配置（文件不存在时写出，含占位符）
_MCP_DEFAULTS: dict[str, str] = {
    "atom_lark_notifier": """# atom_lark_notifier MCP 配置
default_webhook_url: "${BI_LARK_WEBHOOK_URL}"
app_id: "${BI_LARK_APP_ID}"
app_secret: "${BI_LARK_APP_SECRET}"
default_chat_id: "${BI_LARK_CHAT_ID}"
lark_use_feishu: true
# 含 GFM 表时优先 Schema 2.0 tag:table；显式 false 或 JACHIN_LARK_NATIVE_TABLE_CARD=0 可关闭
native_table_card: true
""",
    "atom_email_sender": """# atom_email_sender MCP 配置
smtp:
  host: "smtp.qq.com"
  port: 587
  user: "${BI_SMTP_USER}"
  password: "${BI_SMTP_PASSWORD}"
default_to_addrs:
  - "${BI_SMTP_TO}"
""",
    "atom_bi_project_context": """# atom_bi_project_context — BI 项目知识库同步（写入 docs/bi_daily_report/bi_project）
app_id: "${LARK_APP_ID}"
app_secret: "${LARK_APP_SECRET}"
lark_use_feishu: false
output_dir_relative: docs/bi_daily_report/bi_project
max_records_per_table: 2000
max_discovered_links: 40
recurse_children_depth: 2
# wiki_urls 省略时使用工具内置默认链接列表；若需自定义请取消下行注释并编辑
# wiki_urls: []
""",
}


def load_mcp_config(
    mcp_id: str,
    config_name: str = "config.yaml",
    init_if_missing: bool = True,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """
    加载 MCP 配置。优先 ~/.jachin/config/mcps/{mcp_id}/，若不存在则回退到项目 config/（团队共享）。
    若已从 ~/.jachin 读取，则将项目 config 中「本地未出现的键」合并进来（团队仓库可维护 defaults，
    用户 home 覆盖仍优先：同键不覆盖）。
    对字符串值中的 ${VAR} 做环境变量替换。
    """
    import yaml
    home_cfg_path = get_mcp_config_dir(mcp_id) / config_name
    path = home_cfg_path
    if not path.exists() or not path.is_file():
        # 回退：项目 config（团队共享，config.yaml 已 gitignore）
        if project_root:
            proj_cfg = project_root / "config" / "mcps" / mcp_id / config_name
            if not proj_cfg.exists():
                proj_cfg = project_root / "config" / "mcps" / mcp_id / (config_name + ".example")
            if proj_cfg.exists() and proj_cfg.is_file():
                path = proj_cfg
        if not path.exists() or not path.is_file():
            if init_if_missing and mcp_id in _MCP_DEFAULTS:
                ensure_mcp_config_dir(mcp_id)
                path = get_mcp_config_dir(mcp_id) / config_name
                path.write_text(_MCP_DEFAULTS[mcp_id], encoding="utf-8")
            else:
                return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if (
        project_root
        and path.resolve() == home_cfg_path.resolve()
        and isinstance(raw, dict)
    ):
        proj_cfg = project_root / "config" / "mcps" / mcp_id / config_name
        if not proj_cfg.is_file():
            proj_cfg = project_root / "config" / "mcps" / mcp_id / (config_name + ".example")
        if proj_cfg.is_file():
            try:
                proj_raw = yaml.safe_load(proj_cfg.read_text(encoding="utf-8")) or {}
                if isinstance(proj_raw, dict):
                    for k, v in proj_raw.items():
                        if k not in raw:
                            raw[k] = v
            except Exception:
                pass
    # 递归展开 ${VAR}
    def expand(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: expand(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [expand(v) for v in obj]
        if isinstance(obj, str):
            return _expand_env_placeholder(obj)
        return obj
    return expand(raw)


def ensure_mcp_config_dir(mcp_id: str) -> Path:
    """确保 MCP 配置目录存在"""
    d = get_mcp_config_dir(mcp_id)
    d.mkdir(parents=True, exist_ok=True)
    return d
