#!/usr/bin/env python3
"""
Lark Webhook PyInstaller 打包脚本 — 产出 webhook.exe

用法（在项目根目录执行）:
  python scripts/build_webhook.py [--force]

产出:
  dist_jachin_desktop/webhook.exe

依赖:
  pip install pyinstaller flask requests python-dotenv websockets
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "dist_jachin_desktop"
ENTRY = ROOT / "skills_repo" / "plugin" / "scripts" / "webhook_entry.py"
NAME = "webhook"


def main() -> int:
    os.chdir(ROOT)
    try:
        import PyInstaller
    except ImportError:
        print("请先安装 PyInstaller: pip install pyinstaller")
        return 1
    if not ENTRY.exists():
        print(f"错误: 入口文件不存在 {ENTRY}")
        return 1

    ext = ".exe" if sys.platform == "win32" else ""
    dst = OUT_DIR / f"{NAME}{ext}"
    force = "--force" in sys.argv
    if dst.exists() and not force:
        if ENTRY.stat().st_mtime <= dst.stat().st_mtime:
            print(f"[跳过] 已存在且比源码新: {dst}")
            return 0

    build_dir = ROOT / "build_webhook"
    dist_dir = ROOT / "dist_webhook"
    for d in [build_dir, dist_dir]:
        if d.exists():
            shutil.rmtree(d)
    (build_dir / NAME).mkdir(parents=True, exist_ok=True)
    dist_dir.mkdir(parents=True, exist_ok=True)

    # 限制 PYTHONPATH，避免拉入 jachin-cli/sagot-cli 等（含 IPython/matplotlib）
    plugin_root = ROOT / "skills_repo" / "plugin"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(plugin_root)

    excludes = [
        "IPython", "matplotlib", "PIL", "PyQt5", "tkinter", "sphinx", "black",
        "jedi", "parso", "pytest", "setuptools", "distutils",
    ]
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--noconsole",
        "-n", NAME,
        "--clean",
        "--distpath", str(dist_dir),
        "--workpath", str(build_dir),
        "--specpath", str(ROOT),
        "--hidden-import", "flask",
        "--hidden-import", "requests",
        "--hidden-import", "dotenv",
        "--hidden-import", "websockets",
        "--hidden-import", "openai",
        "--hidden-import", "google.genai",
        *[x for m in excludes for x in ("--exclude-module", m)],
        str(ENTRY),
    ]
    print("[1/2] 运行 PyInstaller...")
    r = subprocess.run(cmd, env=env)
    if r.returncode != 0:
        return r.returncode

    src = dist_dir / f"{NAME}{ext}"
    if not src.exists():
        print(f"错误: 打包产物不存在 {src}")
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    print(f"[2/2] 已复制到 {dst}")

    for d in [build_dir, dist_dir]:
        if d.exists():
            shutil.rmtree(d)
    spec = ROOT / f"{NAME}.spec"
    if spec.exists():
        spec.unlink()
    print(f"\n完成。Webhook exe: {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
