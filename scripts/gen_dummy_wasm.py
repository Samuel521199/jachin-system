#!/usr/bin/env python3
"""
生成极简测试用 dummy.wasm

用法: python scripts/gen_dummy_wasm.py
输出: plugins/dummy.wasm (导出 run 函数，返回 42)

等价 WAT:
  (module
    (func $run (export "run") (result i32)
      i32.const 42
    )
  )
"""
from __future__ import annotations

from pathlib import Path

WAT = """
(module
  (func $run (export "run") (result i32)
    i32.const 42
  )
)
"""


def main() -> None:
    try:
        from wasmtime import wat2wasm
    except ImportError:
        print("需要 wasmtime: pip install wasmtime")
        return

    root = Path(__file__).resolve().parent.parent
    out_dir = root / "plugins"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "dummy.wasm"

    wasm = wat2wasm(WAT)
    out_path.write_bytes(bytes(wasm))
    print(f"[OK] Generated: {out_path}")


if __name__ == "__main__":
    main()
