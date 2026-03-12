#!/usr/bin/env python3
"""
轨道 C 独立验证：无需 Jachin 主项目，验证 stdin/stdout JSON 协议。
规范要求：echo '{"key":"val"}' | python src/main.py 输出正确 JSON。
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MAIN = ROOT / "src" / "main.py"
INPUT = '{"resume_text":"张三 Java 3年 SpringCloud","hr_criteria":"要求本科"}'

def main() -> int:
    if not MAIN.exists():
        print(f"[错误] 未找到 {MAIN}", file=sys.stderr)
        return 1
    r = subprocess.run(
        [sys.executable, str(MAIN)],
        input=INPUT.encode("utf-8"),
        capture_output=True,
        cwd=str(ROOT),
    )
    if r.returncode != 0:
        print(r.stderr.decode("utf-8", errors="replace"), file=sys.stderr)
        return r.returncode
    out = r.stdout.decode("utf-8", errors="replace").strip()
    print(out)
    try:
        import json
        json.loads(out)
        print("\n[OK] 独立验证通过，协议正确。")
        return 0
    except Exception:
        print("\n[警告] 输出非有效 JSON，请检查。", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
