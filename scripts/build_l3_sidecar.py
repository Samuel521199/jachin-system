#!/usr/bin/env python3
"""
L3 节点 PyInstaller 打包脚本 — 产出 Tauri Sidecar 二进制

用法（在项目根目录执行）:
  python scripts/build_l3_sidecar.py [--force]
  --force  强制重新打包，忽略「二进制比源码新」的跳过逻辑

产出:
  clients/desktop/src-tauri/bin/l3_node-{target_triple}[.exe]

依赖:
  pip install pyinstaller
  pip install -r core/requirements.txt  # l3_node 依赖
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# 项目根目录
ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = ROOT / "clients" / "desktop" / "src-tauri" / "bin"
SIDECAR_NAME = "l3_node"


def get_target_triple() -> str:
    """通过 rustc 获取当前平台 target triple。"""
    try:
        out = subprocess.run(
            ["rustc", "--print", "host-tuple"],
            capture_output=True,
            text=True,
            check=True,
            cwd=str(ROOT),
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        # 无 rustc 时按 platform 推断
        machine = platform.machine().lower()
        if machine in ("amd64", "x64"):
            machine = "x86_64"
        system = platform.system().lower()
        if system == "windows":
            return f"{machine}-pc-windows-msvc"
        if system == "darwin":
            return f"{machine}-apple-darwin"
        return f"{machine}-unknown-linux-gnu"


def main() -> int:
    os.chdir(ROOT)

    # 检查 PyInstaller
    try:
        import PyInstaller
    except ImportError:
        print("请先安装 PyInstaller: pip install pyinstaller")
        return 1

    # 检查 l3_node 存在
    l3_main = ROOT / "l3_node" / "__main__.py"
    if not l3_main.exists():
        print("错误: l3_node/__main__.py 未找到")
        return 1

    # 提前计算目标路径，若已存在且比源码新则跳过
    ext = ".exe" if sys.platform == "win32" else ""
    target = get_target_triple()
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    dst = BIN_DIR / f"{SIDECAR_NAME}-{target}{ext}"
    force = "--force" in sys.argv
    if dst.exists() and not force:
        py_files = [p for p in (ROOT / "l3_node").rglob("*.py") if p.is_file()]
        src_mtime = max((p.stat().st_mtime for p in py_files), default=0)
        if py_files and dst.stat().st_mtime >= src_mtime:
            print(f"[跳过] 二进制已存在且比源码新: {dst}")
            return 0

    # exe 仅含 agent+im 核心，MCP/Skill 通过订阅下载到 l3_mcp_cache/l3_skill_cache 使用
    # 排除 Anaconda 中 L3 不需要的重型包（torch/transformers 等会触发 DLL 错误、pandas 等会拖慢构建）
    exclude_modules = [
        "torch", "torchvision", "transformers",  # WinError 1114 DLL 初始化失败
        "pandas", "scipy", "sklearn", "dask", "distributed",  # 非 L3 依赖
        "bokeh", "matplotlib", "PIL", "cv2", "h5py", "tables",  # 非 L3 依赖
        "PyQt5", "qtpy", "onnxruntime", "numba", "llvmlite",  # 非 L3 依赖
    ]
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--noconsole",  # Windows: 不弹黑框; Unix: 无影响
        "-n", SIDECAR_NAME,
        "--clean",
        "--distpath", str(ROOT / "dist_l3"),
        "--workpath", str(ROOT / "build_l3"),
        "--specpath", str(ROOT),
    ]
    for mod in exclude_modules:
        cmd.extend(["--exclude-module", mod])
    cmd.extend(["--exclude-module", "l3_node.mcp_tools"])
    cmd += [
        "--hidden-import", "l3_node",
        "--hidden-import", "l3_node.paths",
        "--hidden-import", "l3_node.early_log",
        "--hidden-import", "l3_node.bootstrap",
        "--hidden-import", "l3_node.agent_core",
        "--hidden-import", "l3_node.llm_client",
        "--hidden-import", "l3_node.ws_server",
        "--hidden-import", "l3_node.crypto",
        "--hidden-import", "l3_node.engine.hooks_pipeline",
        "--hidden-import", "l3_node.skills.loader",
        "--hidden-import", "l3_node.hr_loader",
        "--hidden-import", "l3_node.http_server",
        "--hidden-import", "l3_node.config_writeout",
        "--hidden-import", "l3_node.im_channels",
        "--hidden-import", "l3_node.im_channels.lark_channel",
        "--hidden-import", "l3_node.channels.lark.long_connection",
        "--hidden-import", "lark_oapi",
        "--hidden-import", "yaml",
        "--hidden-import", "core.wasm_runner",
        "--hidden-import", "core.single_instance",
        "--hidden-import", "wasmtime",
        "--collect-all", "wasmtime",
        "--hidden-import", "websockets",
        "--hidden-import", "litellm",
        "--hidden-import", "litellm.litellm_core_utils",
        "--collect-all", "litellm",
        "--collect-data", "litellm",  # 显式收集 model_prices_and_context_window_backup.json 等，避免 frozen 下 FileNotFoundError
        "--hidden-import", "tiktoken",
        "--hidden-import", "tiktoken_ext",
        "--hidden-import", "tiktoken_ext.openai_public",
        "--collect-all", "tiktoken",
        "--collect-all", "tiktoken_ext",
        "--hidden-import", "openai",
        "--hidden-import", "httpx",
        "--hidden-import", "cryptography",
        "--hidden-import", "playwright",
        "--hidden-import", "playwright.sync_api",
        str(l3_main),
    ]

    # 彻底清理并预创建构建目录，避免 FileNotFoundError: base_library.zip（父目录不存在）
    for d in ["dist_l3", "build_l3"]:
        p = ROOT / d
        if p.exists():
            shutil.rmtree(p)
    (ROOT / "build_l3" / SIDECAR_NAME).mkdir(parents=True, exist_ok=True)
    (ROOT / "dist_l3").mkdir(parents=True, exist_ok=True)

    print("[1/3] 运行 PyInstaller...")
    r = subprocess.run(cmd, cwd=str(ROOT))
    if r.returncode != 0:
        return r.returncode

    # 复制到 bin 目录（target 已在上面计算）
    src = ROOT / "dist_l3" / f"{SIDECAR_NAME}{ext}"
    if not src.exists():
        print(f"错误: 打包产物不存在 {src}")
        return 1

    BIN_DIR.mkdir(parents=True, exist_ok=True)
    dst = BIN_DIR / f"{SIDECAR_NAME}-{target}{ext}"
    shutil.copy2(src, dst)
    print(f"[2/3] 已复制到 {dst}")

    # 同时复制到 dist_jachin_desktop/bin（便携包最终运行目录）
    dist_bin = ROOT / "dist_jachin_desktop" / "bin"
    dist_bin.mkdir(parents=True, exist_ok=True)
    dst_dist = dist_bin / f"{SIDECAR_NAME}-{target}{ext}"
    dst_new = dist_bin / f"{SIDECAR_NAME}-{target}.new{ext}"
    try:
        shutil.copy2(src, dst_new)
        try:
            dst_dist.unlink(missing_ok=True)
            shutil.move(str(dst_new), str(dst_dist))
            print(f"      已复制到 {dst_dist}")
        except (PermissionError, OSError) as e:
            print(f"      [WARN] 无法覆盖 {dst_dist}（可能 L3 正在运行）: {e}")
            print(f"      新 exe 已保存为 {dst_new}")
            print("      请关闭 L3 后执行: ren 或 move 将 .new 替换为正式文件")
    except Exception as e:
        print(f"      [ERR] 复制到 dist_jachin_desktop 失败: {e}")

    # 清理临时目录
    for d in ["dist_l3", "build_l3"]:
        p = ROOT / d
        if p.exists():
            shutil.rmtree(p)
    spec = ROOT / f"{SIDECAR_NAME}.spec"
    if spec.exists():
        spec.unlink()
    print("[3/3] 临时文件已清理")

    print(f"\n完成。Sidecar 二进制: {dst}")
    print("下一步: cd clients/desktop && npm run tauri dev")
    return 0


if __name__ == "__main__":
    sys.exit(main())
