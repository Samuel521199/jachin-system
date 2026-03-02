"""
jachin status - 查看配对状态
"""
import json
from pathlib import Path


def _config_path() -> Path:
    return Path.home() / ".jachin" / "nexus_config.json"


def run_status(args) -> int:
    cfg_path = _config_path()

    print("\n[ Jachin Nexus ] 配对状态")
    print("-" * 50)

    if not cfg_path.exists():
        print("\n  状态: 未配对")
        print(f"  配置: {cfg_path} (不存在)")
        print("\n  执行 jachin pair 开始配对")
        return 0

    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"\n  [ERROR] 配置文件解析失败: {e}")
        return 1

    instance_id = data.get("instance_id", "?")
    nexus_base_url = data.get("nexus_base_url", "?")
    has_token = bool(data.get("access_token"))

    print(f"\n  状态: 已配对")
    print(f"  配置: {cfg_path}")
    print(f"  instance_id: {instance_id}")
    print(f"  nexus_base_url: {nexus_base_url}")
    print(f"  access_token: {'已配置' if has_token else '未配置'}")

    print("\n  执行 jachin daemon 启动点火总控")
    return 0
