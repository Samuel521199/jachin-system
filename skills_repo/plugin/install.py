#!/usr/bin/env python3
"""
HR 招聘插件 - 一键安装脚本
将 **四大原语** 相关组件（MCP、Skills/SKILL.md、Tools·jpp/Wasm）及 HR 规则一次性部署到本地 / jachin-system。
当 Jachin 插件商店支持依赖自动安装时，用户下载 Wasm 主体即可自动拉取本脚本部署的依赖；
在此之前，运行本脚本实现「模拟自动安装」效果。
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
JACHIN_HOME = Path.home() / ".jachin"
WORKSPACE = JACHIN_HOME / "workspace"
MCP_CONFIG = JACHIN_HOME / "mcp_servers.json"


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def step_hr_rules() -> bool:
    """1. HR 规则 → ~/.jachin/workspace/hr_rules/"""
    src = ROOT / "1-config-template" / "hr_rules"
    dst = WORKSPACE / "hr_rules"
    if not src.exists():
        print("[跳过] 1-config-template/hr_rules 不存在")
        return False
    ensure_dir(dst)
    for f in src.glob("*"):
        if f.is_file():
            shutil.copy2(f, dst / f.name)
    print(f"[OK] HR 规则已复制到 {dst}")
    return True


def step_skills_md(jachin_path: Path | None) -> bool:
    """2. Skills：SKILL.md → skills_repo/"""
    ok = False
    for skill_name, rel_path in [
        ("hr-recruiter", "4-track-b-skill/SKILL.md"),
        ("hr-job-manager", "4-track-b-skill/hr-job-manager/SKILL.md"),
        ("hr-progress-query", "4-track-b-skill/hr-progress-query/SKILL.md"),
    ]:
        src = ROOT / rel_path
        if not src.exists():
            continue
        if jachin_path:
            dst_dir = jachin_path / "skills_repo" / skill_name
        else:
            dst_dir = JACHIN_HOME / "skills_repo" / skill_name
        ensure_dir(dst_dir)
        shutil.copy2(src, dst_dir / "SKILL.md")
        print(f"[OK] {skill_name} 已复制到 {dst_dir}")
        ok = True
    if not ok:
        print("[跳过] 4-track-b-skill 下无 SKILL.md")
    return ok


def step_tools_jpp_wasm(jachin_path: Path | None) -> bool:
    """3. Tools(jpp)：Wasm → 编译并放入 skills_repo 或 plugins/"""
    wasm_dir = ROOT / "3-track-c-swarm-wasm"
    plugin_json = wasm_dir / "plugin.json"
    dist_wasm = wasm_dir / "dist" / "plugin.wasm"
    if not plugin_json.exists():
        print("[跳过] 3-track-c-swarm-wasm/plugin.json 不存在")
        return False
    # 若已编译，直接复制
    if dist_wasm.exists():
        if jachin_path:
            dst_dir = jachin_path / "skills_repo" / "com.jachin.hr-swarm-engine"
        else:
            dst_dir = JACHIN_HOME / "plugins" / "com.jachin.hr-swarm-engine"
        ensure_dir(dst_dir)
        shutil.copy2(dist_wasm, dst_dir / "plugin.wasm")
        shutil.copy2(plugin_json, dst_dir / "plugin.json")
        print(f"[OK] Wasm 已复制到 {dst_dir}")
        return True
    # 尝试编译
    try:
        import subprocess
        r = subprocess.run(["make", "build"], cwd=wasm_dir, capture_output=True, text=True)
        if r.returncode == 0 and dist_wasm.exists():
            if jachin_path:
                dst_dir = jachin_path / "skills_repo" / "com.jachin.hr-swarm-engine"
            else:
                dst_dir = JACHIN_HOME / "plugins" / "com.jachin.hr-swarm-engine"
            ensure_dir(dst_dir)
            shutil.copy2(dist_wasm, dst_dir / "plugin.wasm")
            shutil.copy2(plugin_json, dst_dir / "plugin.json")
            print(f"[OK] Wasm 已编译并复制到 {dst_dir}")
            return True
    except Exception as e:
        print(f"[提示] Wasm 未编译，请手动执行: cd 3-track-c-swarm-wasm && make build")
    return False


def step_mcp_register(jachin_path: Path | None) -> bool:
    """4. MCP：注册到 ~/.jachin/mcp_servers.json（兼容 Jachin v8 mcp_servers 格式）"""
    server_path = ROOT / "com.jachin.hr.recruitment" / "server.py"
    if not server_path.exists():
        print("[跳过] com.jachin.hr.recruitment/server.py 不存在")
        return False
    abs_server = str(server_path.resolve())
    python_cmd = sys.executable
    entry = {
        "id": "hr-atomic-tools",
        "name": "HR 原子工具箱",
        "command": python_cmd,
        "args": [abs_server],
    }
    entry_obj = {"command": python_cmd, "args": [abs_server]}
    ensure_dir(JACHIN_HOME)

    # 输出格式：优先 mcp_servers（Jachin v8），兼容 mcpServers（Cursor）
    out_key = "mcp_servers"

    def _merge_and_write(servers_list: list, wrap_key: str = "mcp_servers") -> None:
        lst = [e for e in servers_list if isinstance(e, dict) and e.get("id") != "hr-atomic-tools"]
        lst.append(entry)
        MCP_CONFIG.write_text(
            json.dumps({wrap_key: lst}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    if MCP_CONFIG.exists():
        try:
            raw = MCP_CONFIG.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict):
                key = "mcp_servers" if "mcp_servers" in data else "mcpServers"
                if key in data:
                    servers = data[key]
                    if isinstance(servers, dict):
                        servers["hr-atomic-tools"] = entry_obj
                        MCP_CONFIG.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                    else:
                        _merge_and_write(list(servers) if isinstance(servers, list) else [], key)
                    print(f"[OK] MCP 已注册到 {MCP_CONFIG}")
                    return True
            elif isinstance(data, list):
                _merge_and_write(data)
                print(f"[OK] MCP 已注册到 {MCP_CONFIG}")
                return True
        except Exception:
            pass
    _merge_and_write([])
    print(f"[OK] MCP 已注册到 {MCP_CONFIG}")
    return True


def main():
    ap = argparse.ArgumentParser(description="HR 招聘插件 - 一键安装（四大原语：MCP + Skills + Tools·jpp + HR 规则）")
    ap.add_argument("--jachin", "-j", default="", help="jachin-system 项目根目录，不填则仅部署到 ~/.jachin/")
    ap.add_argument("--skip-wasm", action="store_true", help="跳过 Wasm 编译与复制")
    ap.add_argument("--skip-mcp", action="store_true", help="跳过 MCP 注册")
    args = ap.parse_args()
    jachin_path = Path(args.jachin).resolve() if args.jachin else None
    if jachin_path and not jachin_path.is_dir():
        print(f"错误: {jachin_path} 不是有效目录")
        sys.exit(1)
    print("=== HR 招聘插件 一键安装 ===\n")
    step_hr_rules()
    step_skills_md(jachin_path)
    if not args.skip_wasm:
        step_tools_jpp_wasm(jachin_path)
    if not args.skip_mcp:
        step_mcp_register(jachin_path)
    print("\n安装完成。若需 PII 脱敏，请将 5-privacy-hook/hook_desensitize.py 注册到 Jachin 的 Hook Pipeline。")


if __name__ == "__main__":
    main()
