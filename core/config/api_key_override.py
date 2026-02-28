"""
API Key 覆盖存储 - 用户通过桌面端保存的 API Key

优先于 .env 中的配置，存储到 JACHIN_DATA_DIR 或 ~/.jachin 下的 .qwen_api_key 文件。
"""

import os
from pathlib import Path
from typing import Optional

_OVERRIDE_FILENAME = ".qwen_api_key"
_cache: Optional[str] = None


def _get_override_path() -> Path:
    """获取覆盖文件路径"""
    data_dir = os.environ.get("JACHIN_DATA_DIR", "").strip()
    if data_dir:
        return Path(data_dir).expanduser().resolve() / _OVERRIDE_FILENAME
    return Path.home() / ".jachin" / _OVERRIDE_FILENAME


def get_qwen_api_key_override() -> Optional[str]:
    """读取用户保存的 Qwen API Key（桌面端保存）"""
    global _cache
    if _cache is not None:
        return _cache if _cache else None
    try:
        path = _get_override_path()
        if path.exists():
            key = path.read_text(encoding="utf-8").strip()
            _cache = key
            return key if key else None
    except Exception:
        pass
    _cache = ""
    return None


def set_qwen_api_key_override(key: Optional[str]) -> bool:
    """保存 Qwen API Key 到覆盖文件"""
    global _cache
    path = _get_override_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if key and key.strip():
            path.write_text(key.strip(), encoding="utf-8")
            _cache = key.strip()
        else:
            if path.exists():
                path.unlink()
            _cache = ""
        return True
    except Exception:
        return False


def clear_cache():
    """清除内存缓存（保存后由调用方决定是否刷新）"""
    global _cache
    _cache = None
