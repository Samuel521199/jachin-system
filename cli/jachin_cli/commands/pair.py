"""
jachin pair - OOBE 配对流程
"""
import sys
import time
from pathlib import Path

import httpx


def _config_path() -> Path:
    return Path.home() / ".jachin" / "nexus_config.json"


def run_pair(args) -> int:
    base_url = args.base_url.rstrip("/")
    no_daemon = getattr(args, "no_daemon", False)

    print("\n[ Jachin Nexus ] 配对流程启动")
    print("-" * 50)

    # 1. POST pairing/request
    try:
        resp = httpx.post(
            f"{base_url}/api/v1/pairing/request",
            json={
                "device_fingerprint": None,
                "environment_type": "bare_metal",
                "core_version": "1.0.0",
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.RequestError as e:
        print(f"\n[ERROR] 无法连接 Layer 1: {e}")
        print(f"        请确认 {base_url} 可访问，且 Nexus 服务已启动。")
        return 1
    except httpx.HTTPStatusError as e:
        print(f"\n[ERROR] 配对请求失败: HTTP {e.response.status_code}")
        print(f"        {e.response.text[:200]}")
        return 1

    session_id = data.get("session_id")
    short_code = data.get("short_code")
    pair_url = data.get("pair_url", base_url + "/pair")
    expires_in = data.get("expires_in", 300)

    if not session_id or not short_code:
        print("\n[ERROR] Layer 1 返回数据异常，缺少 session_id 或 short_code")
        return 1

    # 2. 打印 6 位码
    print("\n  未检测到指挥部授权。请在浏览器访问:")
    print(f"  {pair_url}")
    print("\n  并输入以下 6 位配对码:\n")
    print("  " + "=" * 20)
    print(f"  >>  {short_code}  <<")
    print("  " + "=" * 20)
    print(f"\n  (该验证码将在 {expires_in} 秒后失效...)\n")
    print("  等待指挥官授权中...")

    # 3. 轮询 pairing/status
    poll_interval = 2
    deadline = time.time() + expires_in

    while time.time() < deadline:
        time.sleep(poll_interval)
        try:
            r = httpx.get(
                f"{base_url}/api/v1/pairing/status",
                params={"session_id": session_id},
                timeout=5.0,
            )
            r.raise_for_status()
            status_data = r.json()
        except Exception:
            print(".", end="", flush=True)
            continue

        st = status_data.get("status")
        if st == "success":
            access_token = status_data.get("access_token")
            instance_id = status_data.get("instance_id", "dev-layer2-001")
            nexus_base_url = status_data.get("nexus_base_url", base_url)

            # 4. 保存配置
            cfg_path = _config_path()
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg = {
                "instance_id": instance_id,
                "access_token": access_token,
                "nexus_base_url": nexus_base_url.rstrip("/"),
            }
            import json
            cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")

            print("\n\n[OK] 授权成功，边缘智能体已连接至指挥部。")
            print(f"     配置已写入: {cfg_path}")
            print(f"     instance_id: {instance_id}")

            if not no_daemon:
                print("\n  正在启动 nexus_daemon...")
                from jachin_cli.commands.daemon import run_daemon
                return run_daemon(argparse.Namespace(foreground=True))

            return 0

        if st == "expired":
            print("\n\n[ERROR] 配对码已过期，请重新执行 jachin pair")
            return 1

        print(".", end="", flush=True)

    print("\n\n[ERROR] 配对超时，请重新执行 jachin pair")
    return 1
