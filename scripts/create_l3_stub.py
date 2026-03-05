#!/usr/bin/env python3
"""
创建最小 Windows PE 占位符，供 Tauri 构建通过。
占位符仅退出 0，不运行 L3。运行 build_l3_sidecar.py 后会被替换为真实二进制。
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = ROOT / "clients" / "desktop" / "src-tauri" / "bin"
SIDECAR_NAME = "l3_node"


def create_minimal_pe(out_path: Path) -> None:
    """创建最小有效 Windows PE（约 1024 字节），退出码 0。"""
    # 来自 ActiveState Recipe 579029，适配 Python 3
    mz = b"MZ" + b"\x00" * 58  # 60s
    pe = b"PE\x00\x00"  # 4s
    text = b".text" + b"\x00" * 8  # 13s
    code = b"3\xc0\xc3" + b"\x00" * 509  # 512s: xor eax,eax; ret
    a = [mz, 176, pe, 332, 1, 224, 259, 267, 9, 16, 64, 16, 2, 5, 32, 2, 2, 132] + [16] * 5 + [text, 16, 2, 2, 16, 96, code]
    f = "60sL112x4sHH12x3HB14xB12xHxB3xB10xH7xB3xB6xHxBxxBxxB4xBxxB6xB131x13sB3xB3xB14xB2xB48x512s"
    b = struct.pack(f, *a)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(b)


def main() -> int:
    ext = ".exe" if sys.platform == "win32" else ""
    target = "x86_64-pc-windows-msvc" if sys.platform == "win32" else "x86_64-apple-darwin" if sys.platform == "darwin" else "x86_64-unknown-linux-gnu"
    dst = BIN_DIR / f"{SIDECAR_NAME}-{target}{ext}"
    if sys.platform == "win32":
        create_minimal_pe(dst)
        print(f"已创建占位符: {dst}")
        print("运行 python scripts/build_l3_sidecar.py 替换为真实 L3 引擎")
        return 0
    # 非 Windows：创建空占位（构建会失败，需用户自行打包）
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.touch()
    print(f"已创建占位: {dst}（非 Windows 请运行 build_l3_sidecar.py）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
